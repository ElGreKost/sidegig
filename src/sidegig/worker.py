"""The worker loop behind the sidegig worker command.

Every few seconds: scan the board, claim gigs addressed to this agent (or matching its
skills with auto-claim), keep heartbeats going, refresh _status/<id>.txt, and print ONE
stdout line per event for an agent harness to watch (flushed):

    NEW <gig_id> <thread path>      newly claimed
    RESUME <gig_id> <thread path>   already claimed before a restart (printed once)
    STALE <gig_id>                  gave up after the time limit

The loop only claims and keeps the claim alive. The agent behind it reads the thread and
answers (gig_answer / sidegig answer), which releases the claim.
"""
import sys
import time
from typing import Callable, Dict, Iterable, Optional, Set, Tuple

from . import protocol as P
from .board import Board
from .errors import BoardError, Conflict


class Worker:
    def __init__(self, board: Board, agent_id: str, *, skills: Iterable[str] = (), auto_claim: bool = False,
                 out: Optional[Callable[[str], None]] = None, err: Optional[Callable[[str], None]] = None,
                 sleep: Optional[Callable[[float], None]] = None, scan_s: float = 5.0, beat_s: float = 120.0,
                 status_s: float = 120.0, settle_s: float = 10.0, max_active: int = 3):
        """settle_s: a gig must show the same text for this long before it is claimed, so a person who
        saves a half-written question is not picked up too early. max_active caps simultaneous claims."""
        self.board = board
        self.agent_id = agent_id
        self.skills = [str(s).strip().lower() for s in skills if str(s).strip()]
        self.auto_claim = auto_claim
        self.out = out or (lambda line: print(line, flush=True))
        self.err = err or (lambda line: print(line, file=sys.stderr, flush=True))
        self.sleep = sleep or time.sleep
        self.scan_s, self.beat_s, self.status_s = scan_s, beat_s, status_s
        self.settle_s, self.max_active = settle_s, max_active
        self.held: Set[str] = set()
        self.seen: Dict[str, Tuple[str, float]] = {}
        self.given_up: Dict[str, str] = {}
        self.last_status = float("-inf")
        self._warned = 0

    # ------------------------------------------------------------------ helpers
    def _matches(self, snap: dict) -> bool:
        """Skills match a gig when one is a tag or appears in the title; no skills = take anything."""
        if not self.skills:
            return True
        tags = set(snap.get("tags") or [])
        title = str(snap.get("title", "")).lower()
        return any(s in tags or P.slugify(s, 30, "") in tags or s in title for s in self.skills)

    def _eligible(self, snap: dict, now: float) -> bool:
        if not snap["open"] or snap.get("asker") == self.agent_id:
            return False
        status = snap["status"]
        if status == "claimed":
            if snap.get("claimed_by") == self.agent_id or not snap.get("claim_stale"):
                return False
        elif status not in ("open", "stale"):
            return False
        to_agent = snap.get("to_agent")
        if to_agent:
            if not self.board.addressed_to(snap, self.agent_id):     # to this agent, or a group it is in
                return False
        elif not (self.auto_claim and self._matches(snap)):
            return False
        if self.given_up.get(snap["id"]) == snap["sig"]:
            return False
        first = self.seen.get(snap["id"])
        if first is None or first[0] != snap["sig"]:
            first = self.seen[snap["id"]] = (snap["sig"], now)
        return now - first[1] >= self.settle_s

    def _report_warnings(self) -> None:
        new = self.board.warnings[self._warned:] if len(self.board.warnings) >= self._warned else self.board.warnings
        for w in new:
            self.err(f"warning: {w}")
        self._warned = len(self.board.warnings)

    def _write_status(self, now: float, state: str = "ONLINE") -> None:
        try:
            self.board.write_status(self.agent_id, state)
            self.last_status = now
        except (BoardError, OSError) as exc:
            self.err(f"warning: could not write status file: {exc}")

    # ------------------------------------------------------------------ loop
    def start(self) -> None:
        """Announce ONLINE and take stock of claims this agent already holds (RESUME lines)."""
        self.board.write_status(self.agent_id)
        self.last_status = self.board.clock()
        for snap in self.board.scan():
            if snap["status"] == "claimed" and snap.get("claimed_by") == self.agent_id and snap["open"]:
                self.held.add(snap["id"])
                self.out(f"RESUME {snap['id']} {snap['thread_file']}")
        self._report_warnings()

    def step(self) -> None:
        """One iteration: scan, heartbeat what we hold, claim what is ours, refresh the status file."""
        now = self.board.clock()
        snaps = self.board.scan()
        by_id = {s["id"]: s for s in snaps}
        for gid in sorted(self.held):
            snap = by_id.get(gid)
            if snap and snap["status"] == "claimed" and snap.get("claimed_by") == self.agent_id and snap["open"]:
                if snap.get("beat") and now - P.parse_iso(snap["beat"]) >= self.beat_s:
                    try:
                        self.board.heartbeat(gid, self.agent_id)
                    except (BoardError, OSError) as exc:
                        self.err(f"warning: heartbeat for {gid} failed: {exc}")
                continue
            self.held.discard(gid)
            if snap and snap["status"] == "stale":
                self.given_up[gid] = snap["sig"]
                self.out(f"STALE {gid}")
        for snap in snaps:
            if len(self.held) >= self.max_active:
                break
            if not self._eligible(snap, now):
                continue
            try:
                got = self.board.claim(snap["id"], self.agent_id)
            except Conflict:
                continue                                  # somebody else was faster
            except (BoardError, OSError) as exc:
                self.err(f"warning: could not claim {snap['id']}: {exc}")
                continue
            self.held.add(snap["id"])
            self.seen.pop(snap["id"], None)
            self.out(f"NEW {snap['id']} {got['thread_file']}")
        self.seen = {k: v for k, v in self.seen.items() if k in by_id}
        if now - self.last_status >= self.status_s:
            self._write_status(now)
        self._report_warnings()

    def run(self, once: bool = False) -> None:
        """Loop until interrupted (or one step with once=True). Marks the agent OFFLINE on exit."""
        self.start()
        try:
            while True:
                try:
                    self.step()
                except (BoardError, OSError) as exc:
                    self.err(f"warning: scan failed: {exc}")
                if once:
                    break
                self.sleep(self.scan_s)
        except KeyboardInterrupt:
            pass
        finally:
            self._write_status(self.board.clock(), "OFFLINE (worker stopped)")
