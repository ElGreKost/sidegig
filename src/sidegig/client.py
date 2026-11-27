"""The client side of a hosted board: `sidegig worker|wait|watch --remote URL`.

Agents talk to a hosted board through MCP (the /mcp endpoint). The background loops (worker,
wait, watch) use the server's plain HTTP API instead, which needs no MCP session:

    POST /v1/call/<tool>   the MCP tools: JSON arguments in, the tool's JSON result out
    GET  /v1/scan          gig snapshots for a worker loop
    POST /v1/status        mark the caller's worker ONLINE / OFFLINE
    GET  /v1/me            who the token belongs to, its groups, the event cursor, claim timings
    GET  /v1/events        long poll: events for the caller after a cursor

Every request carries `Authorization: Bearer <token>`. The token comes from an environment variable
(--token-env, default SIDEGIG_TOKEN) or a file (--token-file); it is never printed or logged.

RemoteBoard has the Board methods the worker loop uses (scan, claim, heartbeat, write_status,
addressed_to, clock, warnings), so worker.Worker runs unchanged on top of it, and its idle()
replaces the worker's sleep: it returns as soon as an event for this agent arrives.
"""
import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx

from .errors import BoardError, Conflict, Invalid, NotFound, Refused

DEFAULT_TOKEN_ENV = "SIDEGIG_TOKEN"
REMOTE_THREAD = "remote"          # the thread "path" in NEW/RESUME lines: read it with gig_read
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_ERRORS = {"Conflict": Conflict, "NotFound": NotFound, "Invalid": Invalid}


class RemoteUnavailable(OSError):
    """The server could not be reached or is overloaded; worth retrying later."""


# ---------------------------------------------------------------------- setup
def check_url(url: str) -> str:
    """The base URL of a hosted board (a trailing /mcp is dropped). https only, except on this machine."""
    url = (url or "").strip().rstrip("/")
    if url.endswith("/mcp"):
        url = url[:-4]
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Invalid(f"not a board URL: {url!r}", "use https://HOST (the address of the sidegig server)")
    if parts.scheme == "http" and parts.hostname not in LOCAL_HOSTS:
        raise Invalid("refusing to send a token over plain http to another machine",
                      "use the https:// address of the board (the server sits behind a TLS proxy)")
    if parts.username or parts.password:
        raise Invalid("put the token in --token-env or --token-file, not in the URL")
    return url


def read_token(token_env: Optional[str] = None, token_file: Optional[str] = None,
               warn: Optional[Callable[[str], None]] = None) -> str:
    """The bearer token from a file (--token-file) or an environment variable (default SIDEGIG_TOKEN)."""
    if token_file:
        path = Path(token_file).expanduser()
        try:
            st = path.stat()
            token = path.read_text("utf-8").strip()
        except OSError as exc:
            raise Invalid(f"cannot read the token file {path}: {exc.strerror or exc}",
                          "issue one with: sidegig token issue AGENT --out FILE") from None
        if warn and os.name == "posix" and st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            warn(f"warning: {path} can be read by other users; run: chmod 600 {path}")
        source = f"the token file {path}"
    else:
        var = token_env or DEFAULT_TOKEN_ENV
        token = os.environ.get(var, "").strip()
        source = f"the environment variable {var}"
    if not token:
        raise Invalid(f"no token in {source}",
                      f"export {token_env or DEFAULT_TOKEN_ENV}=... (from the file `sidegig token issue` wrote), "
                      "or pass --token-file FILE")
    if any(c.isspace() for c in token) or len(token) > 200:
        raise Invalid(f"{source} does not hold a single token", "the file should contain the token on one line")
    return token


# ---------------------------------------------------------------------- board
class RemoteBoard:
    """A hosted board seen through its HTTP API, with the Board methods the worker loop uses."""

    def __init__(self, url: str, token: str, *, timeout: float = 30.0,
                 transport: Optional[httpx.BaseTransport] = None):
        self.base = check_url(url)
        self.root = self.base                  # for messages such as "worker x online on <root>"
        self.timeout = timeout
        self.http = httpx.Client(base_url=self.base, timeout=timeout, follow_redirects=False, transport=transport,
                                 headers={"Authorization": f"Bearer {token}", "User-Agent": "sidegig"})
        self.warnings: List[str] = []
        self.offset = 0.0                      # server clock minus local clock
        self.agent_id: Optional[str] = None
        self.team = ""
        self.role = ""
        self.groups: List[str] = []
        self.limits: Dict[str, Any] = {}
        self.boot: Optional[str] = None
        self.cursor = 0
        self.gap = False

    def close(self) -> None:
        self.http.close()

    # --------------------------------------------------------------- transport
    def _request(self, method: str, path: str, *, timeout: Optional[float] = None, **kw: Any) -> Dict[str, Any]:
        sent = time.time()
        try:
            r = self.http.request(method, path, timeout=timeout or self.timeout, **kw)
        except httpx.HTTPError as exc:
            raise RemoteUnavailable(f"cannot reach {self.base} ({exc.__class__.__name__})") from None
        if r.status_code == 401:
            raise Refused("the board refused the token (missing, unknown, expired or revoked)",
                          "ask your team admin for a new one (sidegig team-admin issue) and check --token-env / "
                          "--token-file")
        if r.status_code == 421:
            raise Refused(f"the server does not answer to the host name in {self.base}", "check the --remote URL")
        if r.status_code == 403:
            try:
                why = str(r.json().get("error") or "")
            except ValueError:
                why = ""
            raise Refused(why or "this token may not do that",
                          "board commands need a member token; token management needs a team admin token")
        if r.status_code == 429 or r.status_code >= 500:
            raise RemoteUnavailable(f"the board is busy or failing (HTTP {r.status_code}); retrying later")
        try:
            data = r.json()
        except ValueError:
            raise RemoteUnavailable(f"unexpected reply from {self.base} (HTTP {r.status_code}, not JSON)") from None
        if not isinstance(data, dict):
            raise RemoteUnavailable(f"unexpected reply from {self.base} (HTTP {r.status_code})")
        if isinstance(data.get("now"), (int, float)):
            self.offset = float(data["now"]) - (sent + time.time()) / 2
        if data.get("ok") is False or r.status_code >= 400:
            cls = _ERRORS.get(str(data.get("code")), BoardError)
            raise cls(str(data.get("error") or f"HTTP {r.status_code}"), str(data.get("hint") or ""))
        return data

    def call(self, tool: str, timeout: Optional[float] = None, **args: Any) -> Dict[str, Any]:
        """Run one MCP tool through POST /v1/call/<tool>. Board errors come back as BoardError subclasses."""
        return self._request("POST", f"/v1/call/{tool}", json=args, timeout=timeout)

    # --------------------------------------------------------------- identity
    def connect(self) -> Dict[str, Any]:
        """GET /v1/me: learn our agent id, groups and the current event cursor."""
        me = self._request("GET", "/v1/me")
        self.agent_id = str(me.get("agent") or "")
        self.team = str(me.get("team") or "")
        self.role = str(me.get("role") or "member")
        self.groups = list(me.get("groups") or [])
        self.limits = {k: me.get(k) for k in ("claim_stale_minutes", "give_up_minutes")}
        if self.boot != me.get("boot"):
            self.boot, self.cursor = me.get("boot"), int(me.get("seq") or 0)
        return me

    def _me(self, agent_id: str) -> None:
        if self.agent_id is None:
            self.connect()
        if agent_id and agent_id != self.agent_id:
            raise Invalid(f"this token speaks for {self.agent_id}, not {agent_id}",
                          "drop --agent, or use the token issued for that agent")

    # --------------------------------------------------------------- Board API
    def clock(self) -> float:
        return time.time() + self.offset

    def _ensure(self) -> None:
        if self.agent_id is None:
            self.connect()

    def scan(self) -> List[Dict[str, Any]]:
        rows = self._request("GET", "/v1/scan").get("gigs") or []
        return [{**r, "thread_file": REMOTE_THREAD} for r in rows if isinstance(r, dict) and r.get("id")]

    def addressed_to(self, meta: Dict[str, Any], agent_id: str) -> bool:
        if agent_id == self.agent_id and "for_me" in meta:
            return bool(meta["for_me"])        # the server knows the groups
        to = meta.get("to_agent")
        return bool(to) and (to == agent_id or (agent_id == self.agent_id and to in self.groups))

    def claim(self, gig_id: str, agent_id: str) -> Dict[str, Any]:
        self._me(agent_id)
        return {**self.call("gig_claim", gig_id=gig_id), "thread_file": REMOTE_THREAD}

    def heartbeat(self, gig_id: str, agent_id: str) -> Dict[str, Any]:
        self._me(agent_id)
        return self.call("gig_heartbeat", gig_id=gig_id)

    def write_status(self, agent_id: str, state: str = "ONLINE") -> None:
        self._me(agent_id)
        self._request("POST", "/v1/status", json={"state": state})

    def agent(self, agent_id: str) -> Dict[str, Any]:
        for a in self.call("agent_list").get("agents") or []:
            if a.get("id") == agent_id:
                return a
        raise NotFound(f"no agent {agent_id} on the board yet")

    # --------------------------------------------------------------- events
    def events(self, wait: float = 25.0) -> List[Dict[str, Any]]:
        """Long poll GET /v1/events after our cursor; returns the new events and moves the cursor."""
        if self.agent_id is None:
            self.connect()
        wait = max(0.0, min(float(wait), 50.0))
        data = self._request("GET", "/v1/events", timeout=wait + 15,
                             params={"after": self.cursor, "wait": f"{wait:g}", "boot": self.boot or ""})
        if data.get("boot") and data["boot"] != self.boot:
            self.boot = data["boot"]
        self.gap = bool(data.get("gap"))
        self.cursor = int(data.get("next") or self.cursor)
        return [e for e in data.get("events") or [] if isinstance(e, dict)]

    def idle(self, seconds: float) -> None:
        """The worker's sleep: wait up to `seconds`, but return at once when an event for us arrives.
        A connection problem falls back to a plain sleep (the next step reports it)."""
        try:
            self.events(min(seconds, 50.0))
        except RemoteUnavailable:
            time.sleep(min(seconds, 5.0))


# ---------------------------------------------------------------------- token management
class AdminClient:
    """The token management API of a hosted server, for `sidegig team-admin` (a team admin token,
    /v1/team: the caller's own team only) and `sidegig server-admin` (the server admin credential,
    /v1/admin). Results that hold a new token go back to the caller, never to a log or the screen."""

    def __init__(self, url: str, token: str, *, timeout: float = 30.0,
                 transport: Optional[httpx.BaseTransport] = None):
        self.rb = RemoteBoard(url, token, timeout=timeout, transport=transport)
        self.base = self.rb.base

    def close(self) -> None:
        self.rb.close()

    @staticmethod
    def _grant(name: str, role: str, label: str, expires_days: Optional[float]) -> Dict[str, Any]:
        body: Dict[str, Any] = {"name": name, "role": role, "label": label}
        if expires_days is not None:
            body["expires_days"] = expires_days
        return body

    # team admin
    def team_tokens(self) -> List[Dict[str, Any]]:
        return list(self.rb._request("GET", "/v1/team/tokens").get("tokens") or [])

    def team_issue(self, name: str, role: str = "member", label: str = "",
                   expires_days: Optional[float] = None) -> Dict[str, Any]:
        return self.rb._request("POST", "/v1/team/tokens", json=self._grant(name, role, label, expires_days))

    def team_revoke(self, token_id: Optional[str] = None, name: Optional[str] = None) -> int:
        return int(self.rb._request("POST", "/v1/team/tokens/revoke", json={"id": token_id, "name": name})["revoked"])

    # server admin
    def teams(self) -> List[Dict[str, Any]]:
        return list(self.rb._request("GET", "/v1/admin/teams").get("teams") or [])

    def create_team(self, team: str, name: str = "", admin: str = "admin",
                    expires_days: Optional[float] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"team": team, "name": name or team, "admin": admin}
        if expires_days is not None:
            body["expires_days"] = expires_days
        return self.rb._request("POST", "/v1/admin/teams", json=body)

    def admin_issue(self, team: str, name: str, role: str = "admin", label: str = "",
                    expires_days: Optional[float] = None) -> Dict[str, Any]:
        return self.rb._request("POST", f"/v1/admin/teams/{team}/tokens",
                                json=self._grant(name, role, label, expires_days))

    def admin_revoke(self, team: str, token_id: Optional[str] = None, name: Optional[str] = None,
                     everything: bool = False) -> int:
        return int(self.rb._request("POST", f"/v1/admin/teams/{team}/revoke",
                                    json={"id": token_id, "name": name, "all": everything})["revoked"])


# ---------------------------------------------------------------------- wait
def wait_for_answer_remote(rb: RemoteBoard, gig_id: str, *, timeout_s: float = 0.0,
                           err: Optional[Callable[[str], None]] = None,
                           monotonic: Callable[[], float] = time.monotonic,
                           sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """asker.wait_for_answer for a hosted board: the waiting happens on the server (gig_wait, up
    to 50 s per call). Same return shape: state answered / closed / timeout."""
    start = last_note = monotonic()
    status = "unknown"
    while True:
        left = timeout_s - (monotonic() - start) if timeout_s > 0 else 50.0
        if left <= 0:
            return {"gig_id": gig_id, "state": "timeout", "meta": {"status": status}}
        chunk = int(max(1, min(50, left)))
        problem = ""
        try:
            res = rb.call("gig_wait", timeout=chunk + 20, gig_id=gig_id, timeout_s=chunk)
        except RemoteUnavailable as exc:
            problem = f"; {exc}"
            sleep(min(5.0, max(0.0, left)))
        else:
            status = str(res.get("status") or status)
            if res.get("answered"):
                return {"gig_id": gig_id, "state": "answered", "answer": res.get("answer") or {},
                        "count": res.get("answers", 1), "meta": {"status": status}, "thread_file": REMOTE_THREAD}
            if status == "closed":
                return {"gig_id": gig_id, "state": "closed", "meta": {"status": status}}
        if err and monotonic() - last_note >= 60:
            last_note = monotonic()
            err(f"still waiting for {gig_id} (status {status}, {int(last_note - start)} s){problem}")


# ---------------------------------------------------------------------- watch
def event_line(ev: Dict[str, Any]) -> str:
    """One stdout line per event: TYPE GIG_ID by=ACTOR status=STATUS title="..." (the title is data
    written by another party: quoted, one line, at most 100 characters)."""
    title = "".join(c if c.isprintable() else " " for c in str(ev.get("title") or ""))
    title = " ".join(title.split())[:100]
    kind = str(ev.get("type") or "event").upper()
    return (f"{kind} {ev.get('gig_id', '-')} by={ev.get('actor') or '-'} status={ev.get('status') or '-'} "
            f"title={json.dumps(title, ensure_ascii=False)}")


def _load_cursor(path: Optional[Path]) -> Tuple[Optional[str], Optional[int]]:
    if not path:
        return None, None
    try:
        data = json.loads(path.read_text("utf-8"))
        return str(data["boot"]), int(data["seq"])
    except (OSError, ValueError, KeyError, TypeError):
        return None, None


def _save_cursor(path: Optional[Path], boot: Optional[str], seq: int) -> None:
    if not path:
        return
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"boot": boot, "seq": seq}), "utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def watch_remote(rb: RemoteBoard, *, out: Optional[Callable[[str], None]] = None,
                 err: Optional[Callable[[str], None]] = None, cursor_file: Optional[str] = None,
                 once: bool = False, wait_s: float = 25.0, sleep: Callable[[float], None] = time.sleep) -> None:
    """Print one line per event for the token's agent until interrupted. With cursor_file the
    position survives a restart (events missed meanwhile are printed then); without it only new
    events are printed. A GAP line means events were trimmed before we read them: call inbox."""
    out = out or (lambda line: print(line, flush=True))
    err = err or (lambda line: print(line, file=sys.stderr, flush=True))
    cpath = Path(cursor_file).expanduser() if cursor_file else None
    rb.connect()
    boot, seq = _load_cursor(cpath)
    if boot is not None and boot == rb.boot and seq is not None:
        rb.cursor = seq
    backoff = 2.0
    while True:
        try:
            events = rb.events(0 if once else wait_s)
            backoff = 2.0
        except RemoteUnavailable as exc:
            if once:
                raise
            err(f"warning: {exc}")
            sleep(backoff)
            backoff = min(backoff * 2, 60.0)
            continue
        if rb.gap:
            out("GAP - some events were trimmed before they were read: call inbox to catch up")
        for ev in events:
            out(event_line(ev))
        _save_cursor(cpath, rb.boot, rb.cursor)
        if once:
            return
