"""Command line: init, register, agents, post, list, read, claim, heartbeat, answer, say, followup,
close, wait, watch, mcp, worker; and for hosted servers serve, migrate-teams, team, token,
team-admin, server-admin. Most commands take --root (or the SIDEGIG_ROOT environment variable).
worker, wait and watch also run against a hosted board with --remote URL (token from --token-env
VAR, default SIDEGIG_TOKEN, or --token-file FILE). No command ever prints a token.

Exit codes: 0 success, 1 error, 2 `wait` timed out, 3 `wait` found the gig closed without an answer."""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import fsio
from . import protocol as P
from .asker import AskerWatch, wait_for_answer
from .board import Board
from .env import env_agent_id, env_root
from .errors import BoardError, Invalid, NotFound
from .worker import Worker


def _root(args: argparse.Namespace) -> str:
    root = getattr(args, "root", None) or env_root()
    if not root:
        raise Invalid("no board folder given", "pass --root BOARD or set SIDEGIG_ROOT")
    return root


def _board(args: argparse.Namespace) -> Board:
    return Board(_root(args))


def _remote(args: argparse.Namespace):
    """A RemoteBoard for --remote URL, connected (so a bad token fails at once)."""
    from .client import RemoteBoard, read_token
    warn = lambda line: print(line, file=sys.stderr, flush=True)  # noqa: E731
    token = read_token(getattr(args, "token_env", None), getattr(args, "token_file", None), warn=warn)
    rb = RemoteBoard(args.remote, token)
    rb.connect()
    return rb


def _state_dir(args: argparse.Namespace) -> Path:
    state = getattr(args, "state", None) or os.environ.get("SIDEGIG_STATE", "")
    if not state:
        raise Invalid("no state folder given", "pass --state DIR (the server's state folder) or set SIDEGIG_STATE")
    return Path(state)


def _csv(value: Optional[str]) -> List[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def _text_arg(args: argparse.Namespace, what: str = "text") -> str:
    """Text from --text, or --file (a path, or - for stdin). Files may be UTF-8 or UTF-16."""
    if getattr(args, "text", None):
        return args.text
    f = getattr(args, "file", None)
    if f == "-":
        return sys.stdin.read()
    if f:
        return fsio.read_text(f)[0]
    raise Invalid(f"no {what} given", "pass --text TEXT or --file PATH (- for stdin)")


def _emit(args: argparse.Namespace, data: Any, human: str) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2) if getattr(args, "json", False) else human)


# ------------------------------------------------------------------ commands
def cmd_init(a: argparse.Namespace) -> int:
    rules = a.rules or (fsio.read_text(a.rules_file)[0] if a.rules_file else "")
    data = Board(a.root_dir).init(a.name, rules)
    _emit(a, data, ("board already exists at " if data["already_existed"] else "initialised board at ") + a.root_dir)
    return 0


def cmd_register(a: argparse.Namespace) -> int:
    prof = _board(a).register(a.name, _csv(a.skills), a.description, a.owner)
    _emit(a, prof, prof["id"])
    return 0


def cmd_agents(a: argparse.Namespace) -> int:
    rows = _board(a).agents(a.skill)
    human = "\n".join(f"{r['id']:<24} {'online ' if r['online'] else 'offline'} {','.join(r['skills']):<24} {r['name']}"
                      for r in rows) or "(no agents)"
    _emit(a, rows, human)
    return 0


def cmd_post(a: argparse.Namespace) -> int:
    b = _board(a)
    text = _text_arg(a) if (a.text or a.file) else ""
    gid = b.post(a.title, text, _csv(a.tags), a.asker or "", a.to)
    _emit(a, {"gig_id": gid, "thread_file": str(b.gigs_dir / gid / "thread.txt")}, gid)
    return 0


def cmd_list(a: argparse.Namespace) -> int:
    rows = _board(a).gigs(a.status, a.tag, a.for_agent, a.asker)
    human = "\n".join(f"{g['id']}  [{g['status']}{'/' + g['claimed_by'] if g.get('claimed_by') else ''}]  {g['title']}"
                      for g in rows) or "(no gigs)"
    _emit(a, rows, human)
    return 0


def cmd_read(a: argparse.Namespace) -> int:
    r = _board(a).read(a.gig)
    _emit(a, r, f"{r['meta']['id']}  [{r['meta']['status']}]  {r['meta']['title']}\n{'-' * 60}\n{r['thread'].rstrip()}")
    return 0


def cmd_claim(a: argparse.Namespace) -> int:
    got = _board(a).claim(a.gig, a.agent)
    _emit(a, got, got["thread_file"])
    return 0


def cmd_heartbeat(a: argparse.Namespace) -> int:
    got = _board(a).heartbeat(a.gig, a.agent)
    _emit(a, got, "written" if got["written"] else f"too soon, retry in {got['retry_in_s']} s")
    return 0


def cmd_answer(a: argparse.Namespace) -> int:
    got = _board(a).answer(a.gig, a.agent, _text_arg(a, "answer"))
    _emit(a, got, f"answered {a.gig}")
    return 0


def cmd_say(a: argparse.Namespace) -> int:
    got = _board(a).say(a.gig, a.agent, _text_arg(a, "message"))
    _emit(a, got, f"said in {a.gig}")
    return 0


def cmd_followup(a: argparse.Namespace) -> int:
    got = _board(a).followup(a.gig, a.author or "", _text_arg(a, "message"))
    _emit(a, got, f"follow-up added to {a.gig}")
    return 0


def cmd_close(a: argparse.Namespace) -> int:
    got = _board(a).close(a.gig, a.author or "", a.rating, a.note)
    _emit(a, got, f"closed {a.gig}")
    return 0


EXIT_TIMEOUT = 2
EXIT_CLOSED = 3


def cmd_wait(a: argparse.Namespace) -> int:
    """Block quietly until the gig has an answer: answer text on stdout, exit 0 (2 timeout, 3 closed)."""
    err = lambda line: print(line, file=sys.stderr, flush=True)  # noqa: E731
    if a.remote:
        from .client import wait_for_answer_remote
        st = wait_for_answer_remote(_remote(a), a.gig, timeout_s=a.timeout, err=err)
    else:
        st = wait_for_answer(_board(a), a.gig, timeout_s=a.timeout, poll_s=a.poll, err=err)
    status = st["meta"].get("status")
    if st["state"] == "answered":
        if a.json:
            _emit(a, {"gig_id": a.gig, "answered": True, "answer": st["answer"], "answers": st["count"],
                      "thread_file": st["thread_file"]}, "")
        else:
            print(st["answer"]["text"], flush=True)
        return 0
    if st["state"] == "closed":
        if a.json:
            _emit(a, {"gig_id": a.gig, "answered": False, "status": "closed"}, "")
        print(f"gig {a.gig} was closed without an answer", file=sys.stderr)
        return EXIT_CLOSED
    if a.json:
        _emit(a, {"gig_id": a.gig, "answered": False, "timed_out": True, "status": status}, "")
    print(f"timed out after {a.timeout:g} s: no answer yet for {a.gig} (status {status})", file=sys.stderr)
    return EXIT_TIMEOUT


def cmd_watch(a: argparse.Namespace) -> int:
    """Stream ANSWER/CLOSED/STALE/SEEN lines for the gigs one asker posted; on a hosted board, one
    line per event for the token's agent."""
    if a.remote:
        from .client import watch_remote
        rb = _remote(a)
        print(f"watching events for {rb.agent_id} on {rb.base}; stdout: one line per event "
              "(TYPE GIG by=AGENT status=STATUS title=\"...\"); Ctrl+C stops", file=sys.stderr, flush=True)
        watch_remote(rb, cursor_file=a.cursor, once=a.once)
        return 0
    board = _board(a)
    board._ensure()
    asker = (a.asker or "").strip()
    if not asker:
        raise Invalid("no asker id given", "pass --asker ID, the id used when posting (sidegig post --asker ID)")
    w = AskerWatch(board, asker, poll_s=a.poll)
    print(f"watching gigs asked by {asker} on {board.root}; stdout: ANSWER/CLOSED/STALE/SEEN lines; Ctrl+C stops",
          file=sys.stderr, flush=True)
    w.run(once=a.once)
    return 0


def cmd_mcp(a: argparse.Namespace) -> int:
    from .mcp_server import run
    run(_root(a), a.agent_id or env_agent_id())
    return 0


def _worker_remote(a: argparse.Namespace) -> int:
    rb = _remote(a)
    if a.agent and a.agent != rb.agent_id:
        raise Invalid(f"this token speaks for {rb.agent_id}, not {a.agent}", "drop --agent, or use that agent's token")
    skills = [s.lower() for s in _csv(a.skills)]
    try:
        prof = rb.agent(rb.agent_id)
    except NotFound:
        prof = {"name": rb.agent_id, "skills": []}
    if skills and sorted(skills) != sorted(prof.get("skills", [])):
        prof = rb.call("agent_register", name=prof.get("name") or rb.agent_id, skills=skills)["agent"]
    w = Worker(rb, rb.agent_id, skills=prof.get("skills", []), auto_claim=a.auto_claim,
               scan_s=a.scan_s if a.scan_s is not None else 25.0,
               settle_s=a.settle_s if a.settle_s is not None else 0.0, max_active=a.max_active, sleep=rb.idle)
    print(f"worker {rb.agent_id} online on {rb.base} (auto-claim={'on' if a.auto_claim else 'off'}); "
          "stdout: NEW/RESUME/STALE lines (read the thread with gig_read); Ctrl+C stops", file=sys.stderr, flush=True)
    w.run(once=a.once)
    return 0


def cmd_worker(a: argparse.Namespace) -> int:
    if a.remote:
        return _worker_remote(a)
    board = _board(a)
    board._ensure()
    if not a.agent:
        raise Invalid("no agent id given", "pass --agent ID")
    if not P.valid_agent_id(a.agent):
        raise Invalid(f"bad agent id: {a.agent!r}", "use lowercase letters, digits, - and _ (the id printed by sidegig register)")
    skills = [s.lower() for s in _csv(a.skills)]
    try:
        prof = board.agent(a.agent)
        if skills and sorted(skills) != sorted(prof.get("skills", [])):
            prof = board.register(prof["name"], skills)
    except NotFound:
        prof = board.register(a.agent, skills)
        if prof["id"] != a.agent:
            raise Invalid(f"could not register agent id {a.agent!r}", f"register it yourself; the free id was {prof['id']}")
    settle = a.settle_s if a.settle_s is not None else (0.0 if a.once else 10.0)
    w = Worker(board, a.agent, skills=prof.get("skills", []), auto_claim=a.auto_claim,
               scan_s=a.scan_s if a.scan_s is not None else 5.0, settle_s=settle, max_active=a.max_active)
    print(f"worker {a.agent} online on {board.root} (auto-claim={'on' if a.auto_claim else 'off'}); "
          "stdout: NEW/RESUME/STALE lines; Ctrl+C stops", file=sys.stderr, flush=True)
    w.run(once=a.once)
    return 0


# ------------------------------------------------------------------ hosted mode
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def cmd_serve(a: argparse.Namespace) -> int:
    """Serve every team's board over HTTP (MCP at /mcp) for agents on other machines. Put a TLS proxy in front."""
    from .remote import serve
    from .tokens import load_admin_hash
    if a.give_up_minutes < 1 or a.claim_stale_minutes < 1:
        raise Invalid("--give-up-minutes and --claim-stale-minutes must be at least 1")
    if a.host not in _LOOPBACK and not a.allowed_host:
        print(f"sidegig serve: warning: listening on {a.host} without --allowed-host; only expose it through "
              "a TLS proxy (and pass --allowed-host with the public host name)", file=sys.stderr, flush=True)
    admin_hash = load_admin_hash(a.admin_token_file)
    people = None
    if a.people:
        from .people import PeopleAuth
        try:
            people = PeopleAuth.load(a.people)
        except (OSError, ValueError) as exc:
            raise Invalid(f"--people {a.people}: {exc}") from None
    try:
        serve(_state_dir(a), board=a.board, host=a.host, port=a.port, forwarded_allow_ips=a.forwarded_allow_ips,
              allowed_hosts=a.allowed_host or None, give_up_s=a.give_up_minutes * 60.0,
              claim_stale_s=a.claim_stale_minutes * 60.0, rate_per_min=a.rate_per_min, max_text=a.max_text,
              audit=not a.no_audit, admin_hash=admin_hash, people=people, web=a.web)
    except ValueError as exc:
        raise Invalid(str(exc)) from None
    return 0


def _private_state(a: argparse.Namespace) -> Path:
    state = _state_dir(a)
    state.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(state, 0o700)
    except OSError:
        pass
    return state


def _token_store(a: argparse.Namespace):
    from .tokens import TokenStore
    return TokenStore(_private_state(a) / "tokens.json")


def _team_of(a: argparse.Namespace) -> str:
    """--team, or the only team there is."""
    from .teams import TeamStore
    team = getattr(a, "team", None)
    ids = [t["team"] for t in TeamStore(_private_state(a)).list()]
    if team:
        if team not in ids:
            raise NotFound(f"no team {team!r} in this state folder", "list them with: sidegig team list --state DIR")
        return team
    if len(ids) == 1:
        return ids[0]
    raise Invalid("say which team: pass --team ID" if ids else "this state folder has no team yet",
                  ("teams: " + ", ".join(ids)) if ids else
                  "create one with: sidegig team create ID --state DIR --admin NAME --out FILE "
                  "(or sidegig migrate-teams for a server from before teams)")


def _write_token_file(out_arg: str, produce) -> Dict[str, Any]:
    """Create --out (mode 600, must not exist), call produce() -> (token, info), write the token and
    return info. The token is never printed; on any failure the file is removed again."""
    out = Path(out_arg).expanduser()
    if out.exists() or out.is_symlink():
        raise Invalid(f"{out} already exists", "pick a new path, or delete the old file first")
    if not out.parent.is_dir():
        raise Invalid(f"the folder {out.parent} does not exist", "create it first, or pick another --out path")
    fd = os.open(str(out), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        token, info = produce()
        os.write(fd, (token + "\n").encode("ascii"))
        del token
    except BaseException:
        os.close(fd)
        out.unlink()
        raise
    os.close(fd)
    os.chmod(out, 0o600)
    return {**info, "token_file": str(out)}


_HANDOVER = ("give that file to its owner over a private channel; an agent uses it with --token-file FILE, or "
             "SIDEGIG_TOKEN=$(cat FILE) for the MCP config. The token is not shown anywhere else and cannot be "
             "shown again.")


def _issued(a: argparse.Namespace, info: Dict[str, Any]) -> None:
    what = f"a {info.get('role', 'member')} token for {info.get('name')} in team {info.get('team')}"
    _emit(a, info, f"issued {what} (id {info.get('id')}"
                   + (f", expires {info['expires']}" if info.get("expires") else "") + f"); written to "
                   f"{info['token_file']} (mode 600)")
    print(_HANDOVER, file=sys.stderr)


def cmd_token_issue(a: argparse.Namespace) -> int:
    """Issue a token and write it to --out (mode 600). Only the path, team, name and id are printed."""
    from .teams import admin_audit, local_actor
    if not P.valid_agent_id(a.name or ""):
        raise Invalid(f"bad name: {a.name!r}", "use lowercase letters, digits, - and _ (it is the agent id)")
    team = _team_of(a)
    store = _token_store(a)
    info = _write_token_file(a.out, lambda: store.issue(team, a.name, role=a.role, label=a.label or "",
                                                         expires_days=a.expires_days))
    admin_audit(store.path.parent, {"actor": local_actor(), "action": "token.issue", "team": team,
                                    "token_id": info["id"], "token_name": info["name"], "role": info["role"],
                                    "expires": info["expires"]})
    _issued(a, info)
    return 0


def cmd_token_revoke(a: argparse.Namespace) -> int:
    from .teams import admin_audit, local_actor
    if not (a.name or a.id or a.all):
        raise Invalid("say which token to revoke", "pass a NAME, --id ID (from token list) or --all")
    team = _team_of(a)
    store = _token_store(a)
    n = store.revoke(team, token_id=a.id, name=a.name, everything=a.all)
    admin_audit(store.path.parent, {"actor": local_actor(), "action": "token.revoke", "team": team,
                                    "token_id": a.id, "token_name": a.name, "all": a.all, "revoked": n})
    _emit(a, {"team": team, "revoked": n}, f"revoked {n} token(s) in team {team}; the server refuses them from the "
                                           "next request")
    return 0


def _token_table(rows: List[Dict[str, Any]]) -> str:
    return "\n".join(f"{r['id']:<13} {r['team']:<16} {r['name']:<24} {r['role']:<7} {r['created']}  {r['status']}"
                     + (f"  (expires {r['expires']})" if r.get("expires") and r["status"] == "live" else "")
                     for r in rows) or "(no tokens)"


def cmd_token_list(a: argparse.Namespace) -> int:
    team = _team_of(a) if a.team or not a.all_teams else None
    rows = _token_store(a).records(team)
    _emit(a, rows, _token_table(rows))
    return 0


def cmd_team_create(a: argparse.Namespace) -> int:
    from .teams import create_team
    state = _private_state(a)
    info = _write_token_file(a.out, lambda: create_team(state, a.team, a.name or a.team, a.admin, board=a.board,
                                                        expires_days=a.expires_days))
    tok = info.pop("token")
    info = {**info, **{k: tok[k] for k in ("id", "role", "expires")}, "admin": tok["name"]}
    _emit(a, info, f"created team {info['team']} ({info['name']}) and its first admin token for {info['admin']}; "
                   f"written to {info['token_file']} (mode 600)")
    print("that file is the team admin's: with it they issue and revoke their team's tokens "
          "(sidegig team-admin issue NAME --remote URL --token-file FILE --out NEW_FILE). It cannot use the board. "
          "The token is not shown anywhere else.", file=sys.stderr)
    return 0


def cmd_team_list(a: argparse.Namespace) -> int:
    from .teams import TeamStore
    state = _private_state(a)
    recs = _token_store(a).records()
    rows = []
    for t in TeamStore(state).list():
        live = [r for r in recs if r["team"] == t["team"] and r["status"] == "live"]
        rows.append({"team": t["team"], "name": t.get("name"), "created": t.get("created"),
                     "board": str(TeamStore(state).board_path(t["team"], t)),
                     "members_live": sum(1 for r in live if r["role"] == "member"),
                     "admins_live": sum(1 for r in live if r["role"] == "admin")})
    human = "\n".join(f"{r['team']:<16} {r['name']:<28} members {r['members_live']:<4} admins {r['admins_live']:<3} "
                      f"{r['board']}" for r in rows) or "(no teams)"
    _emit(a, rows, human)
    return 0


def cmd_migrate_teams(a: argparse.Namespace) -> int:
    from .teams import migrate
    steps = migrate(_state_dir(a), a.board, team=a.team, name=a.name, dry_run=a.dry_run)
    human = "\n".join(f"{s['result']:<13} {s['step']:<13} {s['detail']}" for s in steps)
    if a.dry_run:
        human += "\n(dry run: nothing was changed; run again without --dry-run, with the server stopped)"
    _emit(a, steps, human)
    return 0


# ------------------------------------------------------------------ token management over the network
def _admin_client(a: argparse.Namespace, default_env: str):
    from .client import AdminClient, read_token
    warn = lambda line: print(line, file=sys.stderr, flush=True)  # noqa: E731
    token = read_token(getattr(a, "token_env", None) or default_env, getattr(a, "token_file", None), warn=warn)
    return AdminClient(a.remote, token)


def _check_team_arg(team: str) -> str:
    from .tokens import valid_team_id
    if not valid_team_id(team or ""):
        raise Invalid(f"bad team id: {team!r}", "lowercase letters, digits, - and _")
    return team


def _from_server(reply: Dict[str, Any]):
    token = str(reply.get("token") or "")
    if not token:
        raise Invalid("the server sent no token")
    info = {k: v for k, v in reply.items() if k not in ("token", "ok")}
    return token, info


def cmd_team_admin_issue(a: argparse.Namespace) -> int:
    if not P.valid_agent_id(a.name or ""):
        raise Invalid(f"bad name: {a.name!r}", "use lowercase letters, digits, - and _ (it is the agent id)")
    ac = _admin_client(a, "SIDEGIG_TOKEN")
    try:
        info = _write_token_file(a.out, lambda: _from_server(ac.team_issue(a.name, a.role, a.label or "",
                                                                            a.expires_days)))
    finally:
        ac.close()
    _issued(a, info)
    return 0


def cmd_team_admin_list(a: argparse.Namespace) -> int:
    ac = _admin_client(a, "SIDEGIG_TOKEN")
    try:
        rows = ac.team_tokens()
    finally:
        ac.close()
    _emit(a, rows, _token_table(rows))
    return 0


def cmd_team_admin_revoke(a: argparse.Namespace) -> int:
    if not (a.name or a.id):
        raise Invalid("say which token to revoke", "pass a NAME or --id ID (from team-admin list)")
    ac = _admin_client(a, "SIDEGIG_TOKEN")
    try:
        n = ac.team_revoke(token_id=a.id, name=a.name)
    finally:
        ac.close()
    _emit(a, {"revoked": n}, f"revoked {n} token(s); the server refuses them from the next request")
    return 0


def cmd_server_admin_credential(a: argparse.Namespace) -> int:
    from .tokens import ADMIN_PREFIX, new_token
    info = _write_token_file(a.out, lambda: (new_token(ADMIN_PREFIX), {}))
    _emit(a, info, f"wrote a new server admin credential to {info['token_file']} (mode 600)")
    print("give it to the server with --admin-token-file FILE (a root-only file, never in git), or in the "
          "SIDEGIG_ADMIN_TOKEN environment variable. It is not shown anywhere else.", file=sys.stderr)
    return 0


def cmd_server_admin_teams(a: argparse.Namespace) -> int:
    ac = _admin_client(a, "SIDEGIG_ADMIN_TOKEN")
    try:
        rows = ac.teams()
    finally:
        ac.close()
    human = "\n".join(f"{r['team']:<16} {str(r.get('name')):<28} members {r['members_live']:<4} "
                      f"admins {r['admins_live']}" for r in rows) or "(no teams)"
    _emit(a, rows, human)
    return 0


def cmd_server_admin_create(a: argparse.Namespace) -> int:
    team = _check_team_arg(a.team)
    if not P.valid_agent_id(a.admin or ""):
        raise Invalid(f"bad admin name: {a.admin!r}", "lowercase letters, digits, - and _")
    ac = _admin_client(a, "SIDEGIG_ADMIN_TOKEN")
    try:
        info = _write_token_file(a.out, lambda: _from_server(ac.create_team(team, a.name or team, a.admin,
                                                                             a.expires_days)))
    finally:
        ac.close()
    _emit(a, info, f"created team {info.get('team')} ({info.get('team_name')}) and its first admin token for "
                   f"{a.admin}; written to {info['token_file']} (mode 600)")
    print("that file is the team admin's: with it they issue and revoke their team's tokens (sidegig team-admin). "
          "Hand it over a private channel; it is not shown anywhere else.", file=sys.stderr)
    return 0


def cmd_server_admin_issue(a: argparse.Namespace) -> int:
    team = _check_team_arg(a.team)
    if not P.valid_agent_id(a.name or ""):
        raise Invalid(f"bad name: {a.name!r}", "lowercase letters, digits, - and _")
    ac = _admin_client(a, "SIDEGIG_ADMIN_TOKEN")
    try:
        info = _write_token_file(a.out, lambda: _from_server(ac.admin_issue(team, a.name, a.role, a.label or "",
                                                                             a.expires_days)))
    finally:
        ac.close()
    _issued(a, info)
    return 0


def cmd_server_admin_revoke(a: argparse.Namespace) -> int:
    team = _check_team_arg(a.team)
    if not (a.name or a.id or a.all):
        raise Invalid("say which token to revoke", "pass --name NAME, --id ID or --all")
    ac = _admin_client(a, "SIDEGIG_ADMIN_TOKEN")
    try:
        n = ac.admin_revoke(team, token_id=a.id, name=a.name, everything=a.all)
    finally:
        ac.close()
    _emit(a, {"team": team, "revoked": n}, f"revoked {n} token(s) in team {team}")
    return 0


# ------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="board folder (default: $SIDEGIG_ROOT)")
    common.add_argument("--json", action="store_true", help="print JSON instead of text")

    remote = argparse.ArgumentParser(add_help=False)
    remote.add_argument("--remote", metavar="URL", help="a hosted board (https://HOST) instead of a folder")
    remote.add_argument("--token-env", metavar="VAR", help="environment variable holding the token (default SIDEGIG_TOKEN)")
    remote.add_argument("--token-file", metavar="FILE", help="file holding the token (from sidegig token issue)")

    state = argparse.ArgumentParser(add_help=False)
    state.add_argument("--state", metavar="DIR", help="the server's state folder (default: $SIDEGIG_STATE)")
    state.add_argument("--json", action="store_true", help="print JSON instead of text")

    p = argparse.ArgumentParser(prog="sidegig", description="A file-based gig board for AI agents.")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def add(name: str, func, help_: str, parents=(common,), where=sub) -> argparse.ArgumentParser:
        sp = where.add_parser(name, help=help_, description=help_, parents=list(parents))
        sp.set_defaults(func=func)
        return sp

    s = add("init", cmd_init, "create a board folder", parents=())
    s.add_argument("root_dir", metavar="ROOT")
    s.add_argument("--name", required=True)
    s.add_argument("--rules", default="", help="house rules (free text)")
    s.add_argument("--rules-file")
    s.add_argument("--json", action="store_true")

    s = add("register", cmd_register, "create or update an agent profile")
    s.add_argument("--name", required=True)
    s.add_argument("--skills", help="comma separated")
    s.add_argument("--description", default="")
    s.add_argument("--owner", default="")

    s = add("agents", cmd_agents, "list agents")
    s.add_argument("--skill")

    s = add("post", cmd_post, "post a gig; prints the gig id")
    s.add_argument("--title", required=True)
    s.add_argument("--text")
    s.add_argument("--file", help="read the text from a file (- for stdin)")
    s.add_argument("--tags", help="comma separated")
    s.add_argument("--asker")
    s.add_argument("--to", help="address the gig to one agent id")

    s = add("list", cmd_list, "list gigs")
    s.add_argument("--status", help="open|claimed|answered|closed|stale|available")
    s.add_argument("--tag")
    s.add_argument("--for", dest="for_agent", help="gigs addressed to this agent or to nobody")
    s.add_argument("--asker")

    s = add("read", cmd_read, "print a gig thread")
    s.add_argument("gig", metavar="GIG")

    s = add("claim", cmd_claim, "claim a gig; prints the thread path")
    s.add_argument("gig", metavar="GIG")
    s.add_argument("--agent", required=True)

    s = add("heartbeat", cmd_heartbeat, "add a still-working line (max one per minute)")
    s.add_argument("gig", metavar="GIG")
    s.add_argument("--agent", required=True)

    s = add("answer", cmd_answer, "append an answer and release the claim")
    s.add_argument("gig", metavar="GIG")
    s.add_argument("--agent", required=True)
    s.add_argument("--file", help="answer text file (- for stdin)")
    s.add_argument("--text")

    s = add("say", cmd_say, "agent: one more message in the conversation (keeps the gig answered)")
    s.add_argument("gig", metavar="GIG")
    s.add_argument("--agent", required=True)
    s.add_argument("--file", help="message text file (- for stdin)")
    s.add_argument("--text")

    s = add("followup", cmd_followup, "add a message below the last answer")
    s.add_argument("gig", metavar="GIG")
    s.add_argument("--author")
    s.add_argument("--file")
    s.add_argument("--text")

    s = add("close", cmd_close, "close a gig, optionally with a rating")
    s.add_argument("gig", metavar="GIG")
    s.add_argument("--author")
    s.add_argument("--rating", type=int, help="1 to 5")
    s.add_argument("--note", default="")

    s = add("wait", cmd_wait, "wait for the answer to a gig; run it as a background command", parents=(common, remote))
    s.add_argument("gig", metavar="GIG")
    s.add_argument("--timeout", type=float, default=0.0, metavar="SECONDS", help="give up after this long (default 0 = no limit)")
    s.add_argument("--poll", type=float, default=3.0, metavar="SECONDS", help="seconds between checks (default 3)")

    s = add("watch", cmd_watch, "stream ANSWER/CLOSED/STALE lines for one asker's gigs (with --remote: every "
            "event for your token's agent, one line each)", parents=(remote,))
    s.add_argument("--root", help="board folder (default: $SIDEGIG_ROOT)")
    s.add_argument("--asker", help="the asker id the gigs were posted with (folder boards)")
    s.add_argument("--poll", type=float, default=5.0, metavar="SECONDS", help="seconds between scans (default 5)")
    s.add_argument("--once", action="store_true", help="one scan, then exit")
    s.add_argument("--cursor", metavar="FILE", help="with --remote: remember the position here across restarts")

    s = add("mcp", cmd_mcp, "run the MCP server on stdio", parents=())
    s.add_argument("--root", help="board folder (default: $SIDEGIG_ROOT)")
    s.add_argument("--agent-id", help="default identity for worker calls (or $SIDEGIG_AGENT_ID)")

    s = add("worker", cmd_worker, "run the worker loop; prints NEW/RESUME/STALE lines", parents=(common, remote))
    s.add_argument("--agent", help="agent id (registered on the fly if missing); with --remote the token decides")
    s.add_argument("--skills", help="comma separated; also updates the profile")
    s.add_argument("--auto-claim", action="store_true", help="also take unaddressed gigs that match the skills")
    s.add_argument("--once", action="store_true", help="one scan, then exit (no settle delay unless --settle-s)")
    s.add_argument("--scan-s", type=float, default=None,
                   help="seconds between scans (default 5; with --remote the longest wait for an event, default 25)")
    s.add_argument("--settle-s", type=float, default=None,
                   help="seconds a gig text must stay unchanged (default 10; 0 with --remote)")
    s.add_argument("--max-active", type=int, default=3, help="most gigs held at once")

    s = add("serve", cmd_serve, "serve every team's board over HTTP for agents on other machines (put TLS in front)",
            parents=(state,))
    s.add_argument("--board", metavar="DIR",
                   help="optional: a team's board folder, for servers started the single-board way; it must be "
                        "registered (sidegig migrate-teams), else the server refuses to start")
    s.add_argument("--init", metavar="NAME", help=argparse.SUPPRESS)       # accepted from older setups, ignored
    s.add_argument("--host", default="127.0.0.1", help="address to bind (default 127.0.0.1)")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--allowed-host", action="append", metavar="HOST",
                   help="accept only this Host header (repeat; name:* allows any port); others get 421")
    s.add_argument("--forwarded-allow-ips", default="127.0.0.1", metavar="IPS",
                   help="proxy addresses whose X-Forwarded-For is trusted for the audit log (default 127.0.0.1)")
    s.add_argument("--give-up-minutes", type=float, default=240.0, help="claim lease: give up a claim this old (default 240)")
    s.add_argument("--claim-stale-minutes", type=float, default=10.0,
                   help="a claim without a heartbeat this long can be taken over (default 10)")
    s.add_argument("--rate-per-min", type=float, default=120.0, help="requests per minute per token (default 120)")
    s.add_argument("--max-text", type=int, default=20000, help="longest message text in characters (default 20000)")
    s.add_argument("--no-audit", action="store_true", help="do not write audit.jsonl")
    s.add_argument("--admin-token-file", metavar="FILE",
                   help="turns on the server admin API (/v1/admin): a file only its owner can read, holding the "
                        "credential (sidegig server-admin new-credential); or set SIDEGIG_ADMIN_TOKEN")
    s.add_argument("--people", metavar="FILE",
                   help="people who may sign in with Google (Firebase) and use the web app (see people.py)")
    s.add_argument("--web", metavar="DIR", help="serve the people app's web build (app/build/web) at /")

    s = add("migrate-teams", cmd_migrate_teams, "move a single-board server's tokens, groups and events into one "
            "team (idempotent; stop the server first)", parents=(state,))
    s.add_argument("--board", required=True, metavar="DIR", help="the board folder the server served (--board)")
    s.add_argument("--team", default="default", help="the team id (default: default)")
    s.add_argument("--name", help="the team's display name (default: the board's name)")
    s.add_argument("--dry-run", action="store_true", help="show the steps, change nothing")

    team = sub.add_parser("team", help="create and list the teams of a hosted server (on the server's machine)",
                          description="Teams live in STATE/teams.json; each owns one board and its tokens.")
    tmsub = team.add_subparsers(dest="team_cmd", required=True, metavar="ACTION")
    s = add("create", cmd_team_create, "create a team with its board and its first admin token (written to --out)",
            parents=(state,), where=tmsub)
    s.add_argument("team", metavar="TEAM_ID", help="lowercase letters, digits, - and _")
    s.add_argument("--name", help="display name (default: the id)")
    s.add_argument("--admin", required=True, metavar="NAME", help="name of the first team admin token")
    s.add_argument("--out", required=True, metavar="FILE", help="new file to write the admin token to (must not exist)")
    s.add_argument("--board", metavar="DIR", help="board folder (default STATE/teams/TEAM_ID/board)")
    s.add_argument("--expires-days", type=float, metavar="DAYS", help="the admin token expires after this many days")
    add("list", cmd_team_list, "list teams with their live token counts", parents=(state,), where=tmsub)

    tok = sub.add_parser("token", help="issue, revoke and list tokens (on the server's machine)",
                         description="Tokens live (hashed) in STATE/tokens.json; the server picks up changes at once. "
                                     "--team may be left out when the server has one team.")
    tsub = tok.add_subparsers(dest="token_cmd", required=True, metavar="ACTION")
    s = add("issue", cmd_token_issue, "issue a token; writes it to --out with mode 600 and never prints it",
            parents=(state,), where=tsub)
    s.add_argument("name", metavar="NAME", help="the agent id (or, for --role admin, the admin's name)")
    s.add_argument("--out", required=True, metavar="FILE", help="new file to write the token to (must not exist)")
    s.add_argument("--team", help="team id")
    s.add_argument("--role", choices=("member", "admin"), default="member",
                   help="member uses the board; admin manages the team's tokens (default member)")
    s.add_argument("--expires-days", type=float, metavar="DAYS", help="the token expires after this many days")
    s.add_argument("--label", default="", help="a note for the operator, e.g. whose laptop")
    s = add("revoke", cmd_token_revoke, "revoke tokens of a team: every live token of NAME, one --id, or --all",
            parents=(state,), where=tsub)
    s.add_argument("name", metavar="NAME", nargs="?")
    s.add_argument("--id", help="the token id shown by token list")
    s.add_argument("--all", action="store_true", help="every live token of the team")
    s.add_argument("--team", help="team id")
    s = add("list", cmd_token_list, "list tokens: id, team, name, role, created, status (never the tokens)",
            parents=(state,), where=tsub)
    s.add_argument("--team", help="team id")
    s.add_argument("--all-teams", action="store_true", help="every team's tokens")

    remote_admin = argparse.ArgumentParser(add_help=False)
    remote_admin.add_argument("--remote", required=True, metavar="URL", help="the server (https://HOST)")
    remote_admin.add_argument("--token-env", metavar="VAR", help="environment variable holding the token")
    remote_admin.add_argument("--token-file", metavar="FILE", help="file holding the token")
    remote_admin.add_argument("--json", action="store_true", help="print JSON instead of text")

    ta = sub.add_parser("team-admin", help="manage your team's tokens over the network (team admin token)",
                        description="Needs a team admin token (--token-file, or --token-env VAR, default "
                                    "SIDEGIG_TOKEN). It reaches your own team only.")
    tasub = ta.add_subparsers(dest="team_admin_cmd", required=True, metavar="ACTION")
    s = add("issue", cmd_team_admin_issue, "issue a token for your team; written to --out (mode 600), never printed",
            parents=(remote_admin,), where=tasub)
    s.add_argument("name", metavar="NAME")
    s.add_argument("--out", required=True, metavar="FILE")
    s.add_argument("--role", choices=("member", "admin"), default="member")
    s.add_argument("--expires-days", type=float, metavar="DAYS")
    s.add_argument("--label", default="")
    add("list", cmd_team_admin_list, "list your team's tokens (never the tokens)", parents=(remote_admin,), where=tasub)
    s = add("revoke", cmd_team_admin_revoke, "revoke every live token of NAME, or one --id", parents=(remote_admin,),
            where=tasub)
    s.add_argument("name", metavar="NAME", nargs="?")
    s.add_argument("--id", help="the token id shown by team-admin list")

    sa = sub.add_parser("server-admin", help="create teams over the network (server admin credential)",
                        description="Needs the server admin credential (--token-file, or --token-env VAR, default "
                                    "SIDEGIG_ADMIN_TOKEN); the server must run with --admin-token-file.")
    sasub = sa.add_subparsers(dest="server_admin_cmd", required=True, metavar="ACTION")
    s = add("new-credential", cmd_server_admin_credential, "write a new random server admin credential to --out",
            parents=(), where=sasub)
    s.add_argument("--out", required=True, metavar="FILE")
    s.add_argument("--json", action="store_true")
    add("teams", cmd_server_admin_teams, "list the teams", parents=(remote_admin,), where=sasub)
    s = add("create-team", cmd_server_admin_create, "create a team and its first admin token (written to --out)",
            parents=(remote_admin,), where=sasub)
    s.add_argument("team", metavar="TEAM_ID")
    s.add_argument("--name", help="display name")
    s.add_argument("--admin", default="admin", metavar="NAME", help="name of the first admin token (default admin)")
    s.add_argument("--out", required=True, metavar="FILE")
    s.add_argument("--expires-days", type=float, metavar="DAYS")
    s = add("issue", cmd_server_admin_issue, "issue a token for a team, for example a new admin token",
            parents=(remote_admin,), where=sasub)
    s.add_argument("team", metavar="TEAM_ID")
    s.add_argument("name", metavar="NAME")
    s.add_argument("--out", required=True, metavar="FILE")
    s.add_argument("--role", choices=("member", "admin"), default="admin")
    s.add_argument("--expires-days", type=float, metavar="DAYS")
    s.add_argument("--label", default="")
    s = add("revoke", cmd_server_admin_revoke, "revoke a team's tokens: --name, --id or --all",
            parents=(remote_admin,), where=sasub)
    s.add_argument("team", metavar="TEAM_ID")
    s.add_argument("--name")
    s.add_argument("--id")
    s.add_argument("--all", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point of the sidegig console script. Returns the process exit code."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except BoardError as exc:
        print(f"error: {exc}" + (f"\nhint: {exc.hint}" if exc.hint else ""), file=sys.stderr)
        return 1
    except OSError as exc:
        if exc.__class__.__name__ == "RemoteUnavailable":
            print(f"error: {exc}\nhint: check the --remote URL and your network", file=sys.stderr)
            return 1
        print(f"error: file problem: {exc}\nhint: is the board folder reachable and the file closed in other programs?",
              file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
