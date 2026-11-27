"""Tokens of a hosted server: random bearer tokens, each bound to one team, a name and a role.

The store is a JSON file in the server's state folder, never in a board folder (agents may be able
to read a board, never the store). Only the SHA-256 of each token is kept; the plain token is shown
once, when it is issued. The file is re-read when it changes, so issuing and revoking apply to the
next request without a restart.

    {"format": 2, "tokens": {"<sha256 hex of the token>": {
        "id": "3f2a9c01b7e4",        a random handle for lists and revocation (not derived from the token)
        "team": "acme",              the team the token belongs to
        "name": "carol",             the agent id on the team's board (or the admin's name)
        "agent": "carol",            the same as name, so a server from before teams can still read the file
        "role": "member",            member: uses the board; admin: manages the team's tokens, no board access
        "label": "laptop",           a note for the operator
        "created": "2026-11-23T10:00:00Z",
        "expires": null,             or a time; the token is refused from then on
        "revoked": null}}}           or the time it was revoked

Format 1 (one board per server) records have no team; `sidegig migrate-teams` moves them into a team.
"""
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from . import protocol as P
from .errors import Conflict, Invalid, NotFound

try:                                  # POSIX: serialise writers (the server and the CLI) on a lock file
    import fcntl
except ImportError:                   # pragma: no cover - Windows has no fcntl; the server runs on POSIX
    fcntl = None

PREFIX = "sgk_"
ADMIN_PREFIX = "sgadm_"
ROLES = ("member", "admin")
FORMAT = 2
MAX_LIVE_PER_TEAM = 1000
MAX_EXPIRES_DAYS = 3650
ADMIN_ENV = "SIDEGIG_ADMIN_TOKEN"
PUBLIC_KEYS = ("id", "team", "name", "role", "label", "created", "expires", "revoked")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token(prefix: str = PREFIX) -> str:
    """A fresh token: the prefix and 32 random bytes from `secrets` (43 URL-safe characters)."""
    return prefix + secrets.token_urlsafe(32)


def valid_team_id(team: str) -> bool:
    """Team ids follow the agent id rules: lowercase letters, digits, - and _, at most 64."""
    return P.valid_agent_id(team)


@dataclass(frozen=True)
class Principal:
    """Who a request speaks for: the team and name of its token, the role, and the token's handle."""
    team: str
    name: str
    role: str
    token_id: str

    @property
    def is_member(self) -> bool:
        return self.role == "member"


SERVER_ADMIN = Principal(team="", name="server-admin", role="server-admin", token_id="server-admin")


def private_dir(path: Path) -> None:
    """Create the folder with mode 700 when it is missing. An existing folder keeps its mode."""
    if not path.is_dir():
        path.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(path, 0o700)


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """An exclusive lock on <dir>/.<name>.lock for one read-modify-write of path (no-op without fcntl)."""
    if fcntl is None:
        yield
        return
    private_dir(path.parent)
    fd = os.open(str(path.with_name(f".{path.name}.lock")), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def write_private_json(path: Path, obj: Any) -> None:
    """Write JSON through a mode-600 temp file and os.replace (readers never see a partial file)."""
    private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _epoch_or_none(value: Any) -> Optional[float]:
    if not value:
        return None
    try:
        return P.parse_iso(str(value))
    except ValueError:
        return 0.0                    # an unreadable expiry counts as expired: fail closed


class TokenStore:
    def __init__(self, path, clock: Optional[Callable[[], float]] = None):
        self.path = Path(path)
        self.clock = clock or time.time
        self._sig: Optional[tuple] = None
        self._tokens: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ file
    def _load(self) -> Dict[str, Dict[str, Any]]:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            self._sig, self._tokens = None, {}
            return self._tokens
        sig = (st.st_ino, st.st_mtime_ns, st.st_size)   # os.replace gives a new inode on every save
        if sig != self._sig:
            data = json.loads(self.path.read_text("utf-8"))
            tokens = data.get("tokens") if isinstance(data, dict) else None
            if not isinstance(tokens, dict) or not all(isinstance(v, dict) for v in tokens.values()):
                raise ValueError("the token store is damaged (expected {\"tokens\": {hash: record}})")
            self._tokens, self._sig = tokens, sig
        return self._tokens

    def _load_admin(self) -> Dict[str, Dict[str, Any]]:
        """_load for the admin commands: a damaged store is a clear error, never silently replaced."""
        try:
            return self._load()
        except ValueError as exc:
            raise Invalid(f"cannot use {self.path.name}: {exc}",
                          "restore it from a backup, or move it away and issue new tokens") from None

    def _save(self, tokens: Dict[str, Dict[str, Any]]) -> None:
        write_private_json(self.path, {"format": FORMAT, "tokens": tokens})
        self._sig = None

    @contextlib.contextmanager
    def _editing(self) -> Iterator[Dict[str, Dict[str, Any]]]:
        """Lock, read the newest file, let the caller change a copy, save it (nothing is saved if the
        caller raises)."""
        with file_lock(self.path):
            self._sig = None
            tokens = {k: dict(v) for k, v in self._load_admin().items()}
            yield tokens
            self._save(tokens)

    # ------------------------------------------------------------------ records
    def _status(self, rec: Dict[str, Any]) -> str:
        if rec.get("revoked"):
            return f"revoked {rec['revoked']}"
        exp = _epoch_or_none(rec.get("expires"))
        if exp is not None and self.clock() >= exp:
            return f"expired {rec.get('expires')}"
        return "live"

    def _live(self, rec: Dict[str, Any]) -> bool:
        return self._status(rec) == "live"

    def _public(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        out = {k: rec.get(k) for k in PUBLIC_KEYS}
        out["status"] = self._status(rec)
        return out

    def legacy_count(self) -> int:
        """Records from format 1 (no team). The server refuses to start while there are any."""
        return sum(1 for r in self._load_admin().values() if not r.get("team"))

    # ------------------------------------------------------------------ admin
    def issue(self, team: str, name: str, *, role: str = "member", label: str = "",
              expires_days: Optional[float] = None) -> Tuple[str, Dict[str, Any]]:
        """A new token for name in team. Returns (plain token, public record). The token is not
        stored and cannot be shown again."""
        if not valid_team_id(team or ""):
            raise Invalid(f"bad team id: {team!r}", "lowercase letters, digits, - and _")
        if not P.valid_agent_id(name or ""):
            raise Invalid(f"bad name: {name!r}", "lowercase letters, digits, - and _ (it is the agent id on the board)")
        if role not in ROLES:
            raise Invalid(f"bad role: {role!r}", "member or admin")
        expires = None
        if expires_days is not None:
            try:
                days = float(expires_days)
            except (TypeError, ValueError):
                raise Invalid("expires_days is a number of days") from None
            if not 0 < days <= MAX_EXPIRES_DAYS:
                raise Invalid(f"expires_days must be more than 0 and at most {MAX_EXPIRES_DAYS}")
            expires = P.iso_utc(self.clock() + days * 86400)
        token = new_token()
        rec = {"id": secrets.token_hex(6), "team": team, "name": name, "agent": name, "role": role,
               "label": str(label or "")[:80], "created": P.iso_utc(self.clock()), "expires": expires,
               "revoked": None}
        with self._editing() as tokens:
            if sum(1 for r in tokens.values() if r.get("team") == team and self._live(r)) >= MAX_LIVE_PER_TEAM:
                raise Conflict(f"the team has {MAX_LIVE_PER_TEAM} live tokens", "revoke unused tokens first")
            tokens[hash_token(token)] = rec
        return token, self._public(rec)

    def revoke(self, team: str, *, token_id: Optional[str] = None, name: Optional[str] = None,
               everything: bool = False, keep_an_admin: bool = False) -> int:
        """Revoke the live tokens of team that match token_id, or name, or all of them. Returns the count.
        Tokens of other teams never match: asking for one answers exactly like an unknown id.
        keep_an_admin refuses a revocation that would leave the team without a live admin token."""
        if not (token_id or name or everything):
            raise Invalid("say which token to revoke", "pass a token id or a name")
        with self._editing() as tokens:
            hits = [r for r in tokens.values()
                    if team and r.get("team") == team and self._live(r)
                    and (everything or (token_id and r.get("id") == token_id) or (name and r.get("name") == name))]
            if not hits:
                raise NotFound("no live token matched", "list them first (token list)")
            if keep_an_admin:
                admins = [r for r in tokens.values() if r.get("team") == team and r.get("role") == "admin" and self._live(r)]
                if admins and all(any(r is h for h in hits) for r in admins):
                    raise Conflict("that would leave the team without a live admin token",
                                   "issue another admin token first, or ask the server operator")
            now = P.iso_utc(self.clock())
            for r in hits:
                r["revoked"] = now
        return len(hits)

    def records(self, team: Optional[str] = None) -> List[Dict[str, Any]]:
        """Public records (never hashes or tokens), of one team or all, sorted by team, name, created."""
        rows = [self._public(r) for r in self._load_admin().values() if team is None or r.get("team") == team]
        return sorted(rows, key=lambda r: (str(r.get("team") or ""), str(r.get("name") or ""), str(r.get("created") or "")))

    def live_names(self, team: str, role: str = "member") -> set:
        """Names in team that hold at least one live token of role (empty when the store is unreadable)."""
        try:
            return {r.get("name") for r in self._load().values()
                    if r.get("team") == team and r.get("role") == role and self._live(r)
                    and P.valid_agent_id(str(r.get("name") or ""))}
        except Exception:
            return set()

    def assign_legacy(self, team: str) -> int:
        """Move every format-1 record (no team) into team as a member token and drop its stored token
        prefix ("hint"). Idempotent: records that have a team are left alone. Returns the count."""
        moved = 0
        with self._editing() as tokens:
            for rec in tokens.values():
                if rec.get("team"):
                    continue
                agent = str(rec.get("agent") or "")
                rec.update({"id": rec.get("id") or secrets.token_hex(6), "team": team, "name": agent,
                            "agent": agent, "role": "member", "expires": rec.get("expires")})
                rec.pop("hint", None)
                moved += 1
        return moved

    # ------------------------------------------------------------------ requests
    def verify(self, token: str) -> Optional[Principal]:
        """The principal of a live token, else None. Anything wrong with the store (missing,
        unreadable, damaged) refuses everyone; it never raises.

        The plain token is never compared with anything: it is hashed and the hash is looked up.
        The time that takes can depend only on the hash, which tells an attacker nothing about any
        stored token (finding a token for a hash means inverting SHA-256)."""
        if not token or not isinstance(token, str) or not token.startswith(PREFIX) or len(token) > 200:
            return None
        try:
            rec = self._load().get(hash_token(token))
            if not rec or not self._live(rec):
                return None
            team, name, role = rec.get("team"), str(rec.get("name") or ""), rec.get("role")
            if not team or not valid_team_id(str(team)) or not P.valid_agent_id(name) or role not in ROLES:
                return None                    # format-1 records (no team) are refused until migrated
            return Principal(team=str(team), name=name, role=str(role), token_id=str(rec.get("id") or ""))
        except Exception:
            return None


# ---------------------------------------------------------------------- server admin credential
def load_admin_hash(file: Optional[str] = None, environ: Optional[Dict[str, str]] = None) -> Optional[str]:
    """The SHA-256 of the server admin credential, from a file only its owner can read (preferred) or
    the SIDEGIG_ADMIN_TOKEN environment variable, which is then removed from this process's
    environment. None when neither is set: the /v1/admin API is then switched off. The plain
    credential is not kept."""
    environ = os.environ if environ is None else environ
    if file:
        path = Path(file).expanduser()
        try:
            st = path.stat()
            value = path.read_text("utf-8").strip()
        except OSError as exc:
            raise Invalid(f"cannot read the admin credential file {path}: {exc.strerror or exc}") from None
        if os.name == "posix" and st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise Invalid(f"{path} can be read by other users", f"run: chmod 600 {path}")
        environ.pop(ADMIN_ENV, None)
    else:
        value = (environ.pop(ADMIN_ENV, "") or "").strip()
        if not value:
            return None
    if len(value) < 32 or any(c.isspace() for c in value):
        raise Invalid("the server admin credential must be one line of at least 32 characters",
                      "make one with: sidegig server-admin new-credential --out FILE")
    return hash_token(value)


def admin_matches(expected_hash: Optional[str], presented: str) -> bool:
    """Constant-time check of a presented server admin credential against the stored hash."""
    if not expected_hash or not presented or len(presented) > 200:
        return False
    return hmac.compare_digest(hash_token(presented), expected_hash)
