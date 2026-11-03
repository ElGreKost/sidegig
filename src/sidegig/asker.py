"""The asking side's wake-ups: `sidegig wait` (one gig, one exit) and `sidegig watch` (many gigs, a stream).

An agent harness learns that something happened by watching a background process: Claude Code
turns a background command's exit, or each stdout line, into a notification. So:

    sidegig wait GIG            blocks quietly, prints the answer and exits 0 (2 timeout, 3 closed)
    sidegig watch --asker ID    one flushed stdout line per event, forever:

        ANSWER <gig_id> <thread path>   a gig asked by ID got a new answer
        CLOSED <gig_id>                 one of ID's gigs was closed
        STALE <gig_id>                  a worker gave up on one of ID's gigs (it is open for others)
        SEEN <gig_id>                   printed once after a restart for a gig already reported before

Both use board.check_answer(), the rule behind the MCP gig_wait.
"""
import sys
import time
from typing import Any, Callable, Dict, List, Optional

from . import fsio
from . import protocol as P
from .board import Board, check_answer
from .errors import BoardError

NOTE_EVERY_S = 60.0


def wait_for_answer(board: Board, gig_id: str, *, timeout_s: float = 0.0, poll_s: float = 3.0,
                    sleep: Optional[Callable[[float], None]] = None,
                    monotonic: Optional[Callable[[], float]] = None,
                    err: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Poll until the gig has an answer or is closed. timeout_s <= 0 means no limit.

    Returns board.answer_state() with state "answered" or "closed", or state "timeout" (the last
    state read). Progress goes to err at most once a minute; nothing is printed otherwise. A board
    read that fails with an OS error (share briefly unreachable) is retried, not fatal; an unknown
    gig raises NotFound at once.
    """
    sleep = sleep or time.sleep
    monotonic = monotonic or time.monotonic
    start = last_note = monotonic()
    last: Dict[str, Any] = {"gig_id": gig_id, "state": "waiting", "meta": {}}
    while True:
        problem = ""
        try:
            last = board.answer_state(gig_id)
        except OSError as exc:
            problem = f"; board unreachable ({exc})"
        else:
            if last["state"] != "waiting":
                return last
        now = monotonic()
        if timeout_s > 0 and now - start >= timeout_s:
            return {**last, "state": "timeout"}
        if err and now - last_note >= NOTE_EVERY_S:
            last_note = now
            status = last.get("meta", {}).get("status", "unknown")
            err(f"still waiting for {gig_id} (status {status}, {int(now - start)} s){problem}")
        delay = poll_s if timeout_s <= 0 else min(poll_s, max(0.0, start + timeout_s - now))
        sleep(delay)


def _state_name(asker: str) -> str:
    return asker if P.valid_agent_id(asker) else P.slugify(asker, 64, "asker")


class AskerWatch:
    """Reports events on the gigs one asker posted, one line each; remembers what it reported.

    The memory is a dict kept in the object and mirrored (atomic write) to
    <board>/_status/<asker>.asker.json, so a restart does not repeat itself: gigs already reported
    come out as one SEEN line each on the first scan, then only new events are printed.
    """

    def __init__(self, board: Board, asker: str, *, out: Optional[Callable[[str], None]] = None,
                 err: Optional[Callable[[str], None]] = None, sleep: Optional[Callable[[float], None]] = None,
                 poll_s: float = 5.0):
        self.board = board
        self.asker = asker.strip()
        self.out = out or (lambda line: print(line, flush=True))
        self.err = err or (lambda line: print(line, file=sys.stderr, flush=True))
        self.sleep = sleep or time.sleep
        self.poll_s = poll_s
        self.state_path = board.status_dir / f"{_state_name(self.asker)}.asker.json"
        self.state: Dict[str, Dict[str, Any]] = {}
        self._first = True
        self._warned = 0

    # ------------------------------------------------------------------ memory
    def _load(self) -> None:
        try:
            data = fsio.read_json(self.state_path)
            gigs = data.get("gigs", {}) if isinstance(data, dict) else {}
            self.state = {str(k): {"answers": int(v.get("answers", 0)), "closed": bool(v.get("closed")),
                                   "stale": bool(v.get("stale"))}
                          for k, v in gigs.items() if isinstance(v, dict)}
        except FileNotFoundError:
            self.state = {}
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            self.err(f"warning: could not read {self.state_path.name} ({exc}); starting without memory")
            self.state = {}

    def _save(self) -> None:
        try:
            self.board.status_dir.mkdir(exist_ok=True)
            fsio.write_json(self.state_path, {"asker": self.asker, "gigs": self.state})
        except OSError as exc:
            self.err(f"warning: could not write {self.state_path.name}: {exc}")

    def _report_warnings(self) -> None:
        new = self.board.warnings[self._warned:] if len(self.board.warnings) >= self._warned else self.board.warnings
        for w in new:
            self.err(f"warning: {w}")
        self._warned = len(self.board.warnings)

    # ------------------------------------------------------------------ loop
    def start(self) -> None:
        self.board._ensure()
        self._load()

    def step(self) -> List[str]:
        """One scan. Prints (and returns) the event lines. The first call prints SEEN for gigs
        reported before the restart."""
        first, self._first = self._first, False
        mine = [s for s in self.board.scan() if str(s.get("asker", "")).strip().lower() == self.asker.lower()]
        lines: List[str] = []
        dirty = False
        for snap in mine:
            gid, tf = snap["id"], snap["thread_file"]
            rec = dict(self.state.get(gid) or {"answers": 0, "closed": False, "stale": False})
            try:
                text = fsio.read_text(tf)[0]
            except OSError as exc:
                self.err(f"warning: could not read {gid}: {exc}")
                continue
            chk = check_answer(snap, snap["open"], text)
            events: List[str] = []
            reported = False
            if chk["state"] == "answered":
                if chk["count"] > rec["answers"]:
                    events.append(f"ANSWER {gid} {tf}")
                elif rec["answers"] > 0:
                    reported = True
                rec["answers"] = chk["count"]
            if snap["status"] == "closed":
                if rec["closed"]:
                    reported = True
                else:
                    events.append(f"CLOSED {gid}")
                    rec["closed"] = True
            if snap["status"] == "stale":
                if rec["stale"]:
                    reported = True
                else:
                    events.append(f"STALE {gid}")
                    rec["stale"] = True
            else:
                rec["stale"] = False
            if first and reported and not events:
                events.append(f"SEEN {gid}")
            lines.extend(events)
            if self.state.get(gid) != rec:
                self.state[gid] = rec
                dirty = True
        for gid in [g for g in self.state if not (self.board.gigs_dir / g).is_dir()]:
            del self.state[gid]
            dirty = True
        for line in lines:
            self.out(line)
        if dirty:
            self._save()
        self._report_warnings()
        return lines

    def run(self, once: bool = False) -> None:
        """Loop until interrupted (or one step with once=True)."""
        self.start()
        try:
            while True:
                try:
                    self.step()
                except (BoardError, OSError) as exc:
                    self.err(f"warning: scan failed: {exc}")
                if once:
                    break
                self.sleep(self.poll_s)
        except KeyboardInterrupt:
            pass
