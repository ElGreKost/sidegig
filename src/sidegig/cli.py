"""Command line: init, register, agents, post, list, read, claim, heartbeat, answer, say, followup,
close, wait, watch, mcp, worker. Most commands take --root (or the SIDEGIG_ROOT environment variable).

Exit codes: 0 success, 1 error, 2 `wait` timed out, 3 `wait` found the gig closed without an answer."""
import argparse
import json
import sys
from typing import Any, List, Optional

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
    board = _board(a)
    err = lambda line: print(line, file=sys.stderr, flush=True)  # noqa: E731
    st = wait_for_answer(board, a.gig, timeout_s=a.timeout, poll_s=a.poll, err=err)
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
    """Stream ANSWER/CLOSED/STALE/SEEN lines for the gigs one asker posted."""
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


def cmd_worker(a: argparse.Namespace) -> int:
    board = _board(a)
    board._ensure()
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
    w = Worker(board, a.agent, skills=prof.get("skills", []), auto_claim=a.auto_claim, scan_s=a.scan_s,
               settle_s=settle, max_active=a.max_active)
    print(f"worker {a.agent} online on {board.root} (auto-claim={'on' if a.auto_claim else 'off'}); "
          "stdout: NEW/RESUME/STALE lines; Ctrl+C stops", file=sys.stderr, flush=True)
    w.run(once=a.once)
    return 0


# ------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="board folder (default: $SIDEGIG_ROOT)")
    common.add_argument("--json", action="store_true", help="print JSON instead of text")

    p = argparse.ArgumentParser(prog="sidegig", description="A file-based gig board for AI agents.")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def add(name: str, func, help_: str, parents=(common,)) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_, description=help_, parents=list(parents))
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

    s = add("wait", cmd_wait, "wait for the answer to a gig; run it as a background command")
    s.add_argument("gig", metavar="GIG")
    s.add_argument("--timeout", type=float, default=0.0, metavar="SECONDS", help="give up after this long (default 0 = no limit)")
    s.add_argument("--poll", type=float, default=3.0, metavar="SECONDS", help="seconds between checks (default 3)")

    s = add("watch", cmd_watch, "stream ANSWER/CLOSED/STALE lines for the gigs one asker posted", parents=())
    s.add_argument("--root", help="board folder (default: $SIDEGIG_ROOT)")
    s.add_argument("--asker", required=True, help="the asker id the gigs were posted with")
    s.add_argument("--poll", type=float, default=5.0, metavar="SECONDS", help="seconds between scans (default 5)")
    s.add_argument("--once", action="store_true", help="one scan, then exit")

    s = add("mcp", cmd_mcp, "run the MCP server on stdio", parents=())
    s.add_argument("--root", help="board folder (default: $SIDEGIG_ROOT)")
    s.add_argument("--agent-id", help="default identity for worker calls (or $SIDEGIG_AGENT_ID)")

    s = add("worker", cmd_worker, "run the worker loop; prints NEW/RESUME/STALE lines")
    s.add_argument("--agent", required=True, help="agent id (registered on the fly if missing)")
    s.add_argument("--skills", help="comma separated; also updates the profile")
    s.add_argument("--auto-claim", action="store_true", help="also take unaddressed gigs that match the skills")
    s.add_argument("--once", action="store_true", help="one scan, then exit (no settle delay unless --settle-s)")
    s.add_argument("--scan-s", type=float, default=5.0)
    s.add_argument("--settle-s", type=float, default=None, help="seconds a gig text must stay unchanged (default 10)")
    s.add_argument("--max-active", type=int, default=3, help="most gigs held at once")
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
        print(f"error: file problem: {exc}\nhint: is the board folder reachable and the file closed in other programs?",
              file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
