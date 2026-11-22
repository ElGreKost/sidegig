"""The board library: a gig board stored as plain files in a folder.

Board(root) is stateless between calls (everything lives on disk), so any number of
processes and machines can use the same folder. See PROTOCOL.md for the format.
"""
import hashlib
import os
import random
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from . import fsio
from . import protocol as P
from .errors import Conflict, Invalid, NotFound
from .guide import guide_text
from .secretscan import refuse_secrets

FORMAT = 1
STATUSES = ("open", "claimed", "answered", "closed", "stale")
LOCK_NAME = "claim.lock"
EVICT_NAME = LOCK_NAME + ".evict"   # held (O_EXCL) by the one process allowed to evict claim.lock
GIVE_UP_TEXT = "No answer after 60 minutes: the worker is busy or offline. This gig is open for other workers."
_COMPUTED = ("open", "thread_file", "sig", "beat", "claim_stale")
_GARBAGE_LOCK_S = 30  # an unreadable lock older than this (real seconds) is removed


def _one_line(value: Any, max_len: int = 200) -> str:
    return " ".join(str(value or "").split())[:max_len]


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",")
    out: List[str] = []
    for item in value:
        s = P.slugify(str(item), 30, "")
        if s and s not in out:
            out.append(s)
    return out


def _unlink(path) -> None:
    try:
        fsio._retry(lambda: os.unlink(path))
    except FileNotFoundError:
        pass


def public_meta(snap: Dict[str, Any]) -> Dict[str, Any]:
    """A snapshot without the computed keys: what gig.json holds."""
    return {k: v for k, v in snap.items() if k not in _COMPUTED}


def check_answer(meta: Dict[str, Any], is_open: bool, thread: str) -> Dict[str, Any]:
    """The one "is there an answer yet" rule, shared by the MCP gig_wait, `sidegig wait` and `sidegig watch`.

    state is "answered" when the thread holds an answer and nothing is waiting below it (a follow-up
    makes it "waiting" again until the next answer), else "closed" when the gig was closed, else
    "waiting". `answer` is the newest answer block (None if none) and `count` how many there are.

    A closed gig is reported as not open by the scan, so for it the thread text decides: a follow-up
    that was never answered before the gig was closed is "closed", not "answered" with the old answer.
    """
    answers = P.parse_answers(thread)
    pending = is_open or (meta.get("status") == "closed" and P.is_open(thread))
    if answers and not pending:
        state = "answered"
    elif meta.get("status") == "closed":
        state = "closed"
    else:
        state = "waiting"
    return {"state": state, "answer": answers[-1] if answers else None, "count": len(answers), "meta": meta}


class Board:
    def __init__(self, root, *, clock: Optional[Callable[[], float]] = None,
                 claim_stale_s: float = 600, give_up_s: float = 3600, beat_min_s: float = 60):
        """clock returns epoch seconds (inject it in tests)."""
        self.root = Path(root)
        self.clock = clock or time.time
        self.claim_stale_s = claim_stale_s
        self.give_up_s = give_up_s
        self.beat_min_s = beat_min_s
        self.warnings: List[str] = []
        self._last_scan: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ paths and time
    @property
    def gigs_dir(self) -> Path:
        return self.root / "gigs"

    @property
    def agents_dir(self) -> Path:
        return self.root / "agents"

    @property
    def status_dir(self) -> Path:
        return self.root / "_status"

    def _iso(self) -> str:
        return P.iso_utc(self.clock())

    def _dt(self) -> datetime:
        return datetime.fromtimestamp(self.clock())

    def _ensure(self) -> None:
        if not (self.root / "board.json").is_file():
            raise NotFound(f"not a sidegig: {self.root} has no board.json",
                           "create one with: sidegig init <root> --name <name>")

    # ------------------------------------------------------------------ board
    def init(self, name: str, rules: str = "") -> Dict[str, Any]:
        """Create the folder layout, board.json and BOARD_GUIDE.txt. Safe to repeat."""
        name = _one_line(name, 80) or "sidegig"
        self.root.mkdir(parents=True, exist_ok=True)
        for d in (self.gigs_dir, self.agents_dir, self.status_dir):
            d.mkdir(exist_ok=True)
        bj = self.root / "board.json"
        existed = bj.is_file()
        if existed:
            data = fsio.read_json(bj)
        else:
            data = {"format": FORMAT, "name": name, "created": self._iso(), "rules": rules.strip()}
            fsio.write_json(bj, data)
        guide = self.root / "BOARD_GUIDE.txt"
        if not guide.exists():
            fsio.write_text_atomic(guide, guide_text(data.get("name", name), data.get("rules", "")))
        return {**data, "already_existed": existed}

    def info(self) -> Dict[str, Any]:
        """board.json plus live counts of gigs per status and of agents."""
        self._ensure()
        data = fsio.read_json(self.root / "board.json")
        counts = {s: 0 for s in STATUSES}
        for g in self.scan():
            counts[g["status"]] = counts.get(g["status"], 0) + 1
        agents = self.agents()
        return {**data, "root": str(self.root), "gigs": counts, "agents": len(agents),
                "agents_online": sum(1 for a in agents if a["online"])}

    # ------------------------------------------------------------------ agents
    def _profiles(self) -> List[Dict[str, Any]]:
        out = []
        try:
            entries = list(os.scandir(self.agents_dir))
        except FileNotFoundError:
            return out
        for e in entries:
            if not e.name.endswith(".json") or P.is_ignored_name(e.name):
                continue
            try:
                prof = fsio.read_json(e.path)
            except (OSError, ValueError):
                continue
            if isinstance(prof, dict) and prof.get("id"):
                out.append(prof)
        return out

    def _agent(self, agent_id: str) -> Dict[str, Any]:
        if not P.valid_agent_id(agent_id or ""):
            raise Invalid(f"bad agent id: {agent_id!r}", "agent ids are lowercase letters, digits, - and _")
        try:
            prof = fsio.read_json(self.agents_dir / f"{agent_id}.json")
        except (FileNotFoundError, ValueError):
            raise NotFound(f"agent {agent_id!r} is not registered", "register it first (agent_register / sidegig register)")
        return prof

    def agent(self, agent_id: str) -> Dict[str, Any]:
        """One agent profile; NotFound when it is not registered."""
        return self._agent(agent_id)

    def register(self, name: str, skills: Iterable[str] = (), description: str = "", owner: str = "") -> Dict[str, Any]:
        """Create (or update, matched by exact name) an agent profile. The id is a slug of the name."""
        self._ensure()
        name = _one_line(name, 80)
        if not name:
            raise Invalid("agent name is empty", "pass a short descriptive name")
        refuse_secrets(name, description, owner)
        skills = _as_list(skills)
        description = (description or "").strip()[:500]
        owner = _one_line(owner, 80)
        now = self._iso()
        self.agents_dir.mkdir(exist_ok=True)
        for prof in self._profiles():
            if prof.get("name") == name:
                if skills:
                    prof["skills"] = skills
                if description:
                    prof["description"] = description
                if owner:
                    prof["owner"] = owner
                prof["last_seen"] = now
                fsio.write_json(self.agents_dir / f"{prof['id']}.json", prof)
                return prof
        base = P.slugify(name, 40, "agent")
        for i in range(1, 1000):
            aid = base if i == 1 else f"{base}-{i}"
            prof = {"id": aid, "name": name, "owner": owner, "skills": skills,
                    "description": description, "created": now, "last_seen": now}
            try:
                fsio.create_exclusive(self.agents_dir / f"{aid}.json", fsio.json_bytes(prof))
                return prof
            except FileExistsError:
                continue
        raise Conflict("could not find a free agent id", "use a different name")

    def ensure_agent(self, agent_id: str, name: str = "", skills: Iterable[str] = (), description: str = "",
                     owner: str = "") -> Dict[str, Any]:
        """Create the profile with exactly this id (O_EXCL) or update the given fields of an existing one.
        Used by the remote server, where the token, not the name, decides the id."""
        self._ensure()
        if not P.valid_agent_id(agent_id or ""):
            raise Invalid(f"bad agent id: {agent_id!r}", "agent ids are lowercase letters, digits, - and _")
        refuse_secrets(name, description, owner)
        now = self._iso()
        self.agents_dir.mkdir(exist_ok=True)
        path = self.agents_dir / f"{agent_id}.json"
        prof = {"id": agent_id, "name": _one_line(name, 80) or agent_id, "owner": _one_line(owner, 80),
                "skills": _as_list(skills), "description": (description or "").strip()[:500],
                "created": now, "last_seen": now}
        try:
            fsio.create_exclusive(path, fsio.json_bytes(prof))
            return prof
        except FileExistsError:
            pass
        prof = self._agent(agent_id)
        for key, value in (("name", _one_line(name, 80)), ("owner", _one_line(owner, 80)),
                           ("skills", _as_list(skills)), ("description", (description or "").strip()[:500])):
            if value:
                prof[key] = value
        prof["last_seen"] = now
        fsio.write_json(path, prof)
        return prof

    def _status_info(self, agent_id: str) -> Dict[str, Any]:
        try:
            text, _ = fsio.read_text(self.status_dir / f"{agent_id}.txt")
        except OSError:
            return {"online": False, "state": "unknown", "last_seen": None}
        lines = P.normalize(text).split("\n")
        state = lines[0].strip() if lines else ""
        seen = None
        for ln in lines:
            if ln.startswith("last seen utc:"):
                seen = ln.split(":", 1)[1].strip()
        online = False
        if state.startswith("ONLINE") and seen:
            try:
                online = self.clock() - P.parse_iso(seen) <= 300
            except ValueError:
                pass
        return {"online": online, "state": state, "last_seen": seen}

    def worker_status(self, agent_id: str) -> Dict[str, Any]:
        """{online, state, last_seen} from _status/<id>.txt; online = the worker reported in within 5 minutes."""
        return self._status_info(agent_id)

    def agents(self, skill: Optional[str] = None) -> List[Dict[str, Any]]:
        """All profiles (optionally those with a skill), each with an online flag."""
        want = P.slugify(skill, 30, "") if skill else ""
        out = []
        for prof in sorted(self._profiles(), key=lambda p: p["id"]):
            if want and want not in [P.slugify(str(s), 30, "") for s in prof.get("skills", [])]:
                continue
            st = self._status_info(prof["id"])
            out.append({**prof, "online": st["online"], "status": st["state"]})
        return out

    def write_status(self, agent_id: str, state: str = "ONLINE") -> None:
        """Write _status/<id>.txt (what a person opens to see if a worker is up) and bump last_seen."""
        self._ensure()
        prof = self._agent(agent_id)
        now = self.clock()
        text = (f"{state}\nagent: {agent_id}\n"
                f"last seen: {datetime.fromtimestamp(now):%Y-%m-%d %H:%M}\n"
                f"last seen utc: {P.iso_utc(now)}\n"
                "If the last seen time is more than 5 minutes old, this agent is offline; gigs addressed to it wait.\n")
        self.status_dir.mkdir(exist_ok=True)
        fsio.write_text_atomic(self.status_dir / f"{agent_id}.txt", text)
        if state.startswith("ONLINE"):
            prof["last_seen"] = P.iso_utc(now)
            fsio.write_json(self.agents_dir / f"{agent_id}.json", prof)

    # ------------------------------------------------------------------ gig plumbing
    def _gig_dir(self, gig_id: str) -> Path:
        if (not isinstance(gig_id, str) or not gig_id or "/" in gig_id or "\\" in gig_id
                or gig_id != Path(gig_id).name or P.is_ignored_name(gig_id)):
            raise Invalid(f"bad gig id: {gig_id!r}", "use an id from gig_list")
        d = self.gigs_dir / gig_id
        if not d.is_dir():
            raise NotFound(f"no such gig: {gig_id}", "list gigs with gig_list / sidegig list")
        return d

    @staticmethod
    def _new_meta(gid: str, title: str, asker: str, tags: Iterable[str], to_agent: Optional[str], epoch: float) -> Dict[str, Any]:
        ts = P.iso_utc(epoch)
        return {"id": gid, "title": title, "tags": _as_list(tags), "asker": asker, "to_agent": to_agent,
                "created": ts, "updated": ts, "status": "open", "claimed_by": None, "claimed_at": None,
                "rating": None, "close_note": ""}

    def _txt_files(self, d: Path) -> List[Path]:
        """Non-ignored .txt files of a gig folder, oldest first."""
        files = []
        try:
            entries = list(os.scandir(d))
        except OSError:
            return []
        for e in entries:
            if e.name.lower().endswith(".txt") and not P.is_ignored_name(e.name) and e.is_file():
                try:
                    files.append((e.stat().st_mtime, e.name, Path(e.path)))
                except OSError:
                    continue
        files.sort()
        return [f[2] for f in files]

    def _thread_path(self, d: Path, meta: Optional[Dict[str, Any]] = None) -> Optional[Path]:
        if meta and meta.get("thread") and (d / meta["thread"]).is_file():
            return d / meta["thread"]
        if (d / "thread.txt").is_file():
            return d / "thread.txt"
        files = self._txt_files(d)
        return files[0] if files else None

    def _load_meta(self, d: Path) -> Optional[Dict[str, Any]]:
        try:
            meta = fsio.read_json(d / "gig.json")
        except FileNotFoundError:
            return None
        if not isinstance(meta, dict):
            raise ValueError("gig.json is not an object")
        base = self._new_meta(d.name, d.name, "human", [], None, self.clock())
        for k, v in base.items():
            meta.setdefault(k, v)
        meta["id"] = d.name
        return meta

    def _adopt(self, d: Path) -> Optional[Dict[str, Any]]:
        """A folder made by a person (txt, no gig.json): create gig.json. Title = first line."""
        tp = self._thread_path(d)
        if tp is None:
            return None
        text, _ = fsio.read_text(tp)
        lines = [ln.strip() for ln in P.normalize(text).split("\n") if ln.strip()]
        if not lines:
            return None
        meta = self._new_meta(d.name, _one_line(lines[0], 120), "human", [], None, os.path.getmtime(tp))
        meta["thread"] = tp.name
        if not (d / "gig.json").exists():
            fsio.write_json(d / "gig.json", meta)
        return self._load_meta(d)

    def _load_or_adopt(self, d: Path) -> Optional[Dict[str, Any]]:
        meta = self._load_meta(d)
        return meta if meta is not None else self._adopt(d)

    def _update_meta(self, d: Path, **changes: Any) -> Dict[str, Any]:
        meta = self._load_meta(d) or {}
        meta.update(changes)
        meta["updated"] = self._iso()
        fsio.write_json(d / "gig.json", meta)
        return meta

    # locks
    def _read_lock(self, d: Path) -> Optional[Dict[str, Any]]:
        """None: no lock. {}: a lock that cannot be read yet (being written) or is garbage."""
        return self._parse_lock(d / LOCK_NAME)

    @staticmethod
    def _parse_lock(path: Path) -> Optional[Dict[str, Any]]:
        try:
            data = fsio.read_json(path)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return {}
        try:
            if not isinstance(data, dict) or not isinstance(data.get("agent"), str):
                return {}
            P.parse_iso(data["at"])
            P.parse_iso(data["beat"])
        except (KeyError, ValueError, TypeError):
            return {}
        return data

    def _lock_age(self, d: Path) -> float:
        try:
            return time.time() - os.path.getmtime(d / LOCK_NAME)
        except OSError:
            return 0.0

    def _evict_lock(self, d: Path, expected: Optional[Dict[str, Any]]) -> bool:
        """Remove a lock the caller judged stale (expected) or garbage (expected None).
        True only for the one caller that removed it.

        Evictions are serialised by a second O_EXCL file, claim.lock.evict. Only its holder may
        move claim.lock away, and it reads the lock again first: a taker whose view is out of date
        (another taker already evicted the stale lock and made a fresh one) finds the fresh lock
        and stops, so it never moves somebody's live lock away. Without the gate, that taker
        renamed the new holder's lock, a third taker created its own lock in the gap, the
        put-back failed and two takers both won.

        Between the re-read and the rename only the lock's own holder can change it (a heartbeat
        that arrives in that instant, after 10 silent minutes). Then the moved lock is restored
        with O_EXCL, best effort, and False is returned; if somebody claimed the free slot in
        between, that one holds the gig and the late holder learns it at its next heartbeat.
        """
        gate = d / EVICT_NAME
        mark = secrets.token_hex(8)
        try:
            fsio.create_exclusive(gate, mark.encode("ascii"))
        except FileExistsError:
            self._clear_dead_gate(gate)
            return False
        except OSError:
            return False
        try:
            current = self._read_lock(d)
            if expected is None:
                if current != {} or self._lock_age(d) <= _GARBAGE_LOCK_S:
                    return False
            elif current != expected:
                return False
            lock = d / LOCK_NAME
            tomb = d / f"{LOCK_NAME}.{secrets.token_hex(4)}.gone"
            try:
                os.rename(lock, tomb)
            except (FileNotFoundError, PermissionError):
                return False
            moved = self._parse_lock(tomb)
            if moved and moved != expected:      # replaced in the instant before the rename
                try:
                    fsio.create_exclusive(lock, fsio.json_bytes(moved))
                except OSError:
                    pass
                _unlink(tomb)
                return False
            _unlink(tomb)
            return True
        finally:
            try:
                if gate.read_bytes() == mark.encode("ascii"):
                    _unlink(gate)
            except OSError:
                pass

    def _clear_dead_gate(self, gate: Path) -> None:
        """An eviction gate left by a process that died mid-eviction (older than 30 s) is removed;
        the caller retries later. A live gate is held for milliseconds."""
        try:
            if time.time() - os.path.getmtime(gate) <= _GARBAGE_LOCK_S:
                return
            tomb = gate.with_name(f"{gate.name}.{secrets.token_hex(4)}.gone")
            os.rename(gate, tomb)
            _unlink(tomb)
        except OSError:
            pass

    # ------------------------------------------------------------------ scan
    def _snap(self, meta: Dict[str, Any], tp: Path, is_open: bool, sig: str, lock: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        snap = dict(meta)
        beat = lock.get("beat") if lock else None
        stale = bool(beat) and self.clock() - P.parse_iso(beat) >= self.claim_stale_s
        snap.update(open=is_open, thread_file=str(tp), sig=sig, beat=beat, claim_stale=stale)
        return snap

    def _refresh(self, d: Path) -> Optional[Dict[str, Any]]:
        """Adopt a human folder, bring gig.json in line with the thread and the lock, give up on
        claims older than give_up_s. Returns the snapshot (gig.json fields + computed keys)."""
        meta = self._load_or_adopt(d)
        if meta is None:
            return None
        tp = self._thread_path(d, meta)
        if tp is None:
            return None
        if meta["status"] == "closed":
            return self._snap(meta, tp, False, "", None)
        text, _ = fsio.read_text(tp)
        now = self.clock()
        is_open = P.is_open(text)
        sig = hashlib.sha1(P.open_text(text).encode("utf-8")).hexdigest()[:16]
        lock = self._read_lock(d)
        if lock == {}:
            if self._lock_age(d) > _GARBAGE_LOCK_S:
                self._evict_lock(d, None)
                lock = None
            else:  # being written right now: leave the state alone this round
                return self._snap(meta, tp, is_open, sig, None)
        status = meta["status"]
        changes: Dict[str, Any] = {}
        if not is_open:
            if lock is not None:
                _unlink(d / LOCK_NAME)
                lock = None
            if P.has_answer(text) and status != "answered":
                changes = {"status": "answered"}
        elif lock is not None:
            if now - P.parse_iso(lock["at"]) >= self.give_up_s:
                if self._evict_lock(d, lock):
                    fsio.append_text(tp, P.status_line(lock["agent"], self._dt(), GIVE_UP_TEXT))
                    changes = {"status": "stale", "claimed_by": None, "claimed_at": None}
                lock = None
            elif (status, meta.get("claimed_by"), meta.get("claimed_at")) != ("claimed", lock["agent"], lock["at"]):
                changes = {"status": "claimed", "claimed_by": lock["agent"], "claimed_at": lock["at"]}
        elif status in ("claimed", "answered"):
            changes = {"status": "open", "claimed_by": None, "claimed_at": None}
        if changes:
            meta = self._update_meta(d, **changes)
        return self._snap(meta, tp, is_open, sig, lock)

    def scan(self) -> List[Dict[str, Any]]:
        """Adopt human folders and refresh every gig. Returns snapshots (gig.json fields plus
        open, thread_file, sig, beat, claim_stale). Unreadable gigs are skipped (see .warnings)."""
        self._ensure()
        out: List[Dict[str, Any]] = []
        try:
            entries = sorted(os.scandir(self.gigs_dir), key=lambda e: e.name)
        except FileNotFoundError:
            entries = []
        for e in entries:
            if not e.is_dir() or P.is_ignored_name(e.name):
                continue
            try:
                snap = self._refresh(Path(e.path))
            except (OSError, ValueError, KeyError, TypeError) as ex:
                self.warnings = (self.warnings + [f"{e.name}: {ex}"])[-50:]
                continue
            if snap is not None:
                out.append(snap)
        self._last_scan = out
        return out

    @staticmethod
    def is_open(text: str) -> bool:
        """True when the thread text still needs an answer (see PROTOCOL.md)."""
        return P.is_open(text)

    # ------------------------------------------------------------------ gigs
    def post(self, title: str, text: str = "", tags: Iterable[str] = (), asker: str = "",
             to_agent: Optional[str] = None) -> str:
        """Create a gig and return its id. gig.json is written before the thread."""
        self._ensure()
        title = _one_line(title, 200)
        if not title:
            raise Invalid("title is empty", "give the gig a one-line title")
        refuse_secrets(title, text)
        to_agent = (to_agent or "").strip() or None
        if to_agent:
            self._agent(to_agent)
        asker = _one_line(asker, 80) or "anonymous"
        now = self.clock()
        dt = datetime.fromtimestamp(now)
        slug = P.slugify(title, 40, "gig")
        self.gigs_dir.mkdir(exist_ok=True)
        for _ in range(20):
            gid = f"{dt:%Y-%m-%d}-{slug}-{secrets.token_hex(2)}"
            d = self.gigs_dir / gid
            try:
                d.mkdir()
                break
            except FileExistsError:
                continue
        else:
            raise Conflict("could not allocate a gig id", "retry")
        fsio.write_json(d / "gig.json", self._new_meta(gid, title, asker, tags, to_agent, now))
        body = title + (("\n\n" + text.strip("\n")) if (text or "").strip() else "")
        fsio.create_text(d / "thread.txt", P.message_block(asker, dt, body))
        return gid

    def gigs(self, status: Optional[str] = None, tag: Optional[str] = None, for_agent: Optional[str] = None,
             asker: Optional[str] = None, refresh: bool = True) -> List[Dict[str, Any]]:
        """Snapshots, newest first. status may also be 'available' (open or stale);
        for_agent keeps gigs addressed to that agent or to nobody."""
        snaps = self.scan() if refresh else list(self._last_scan)
        want_tag = P.slugify(tag, 30, "") if tag else ""
        out = []
        for g in snaps:
            if status == "available":
                if g["status"] not in ("open", "stale"):
                    continue
            elif status and g["status"] != status:
                continue
            if want_tag and want_tag not in g.get("tags", []):
                continue
            if for_agent and g.get("to_agent") not in (None, for_agent):
                continue
            if asker and str(g.get("asker", "")).lower() != asker.lower():
                continue
            out.append(g)
        out.sort(key=lambda g: (g["created"], g["id"]), reverse=True)
        return out

    def read(self, gig_id: str) -> Dict[str, Any]:
        """{meta, thread, extra, open, thread_file, answers}. Other .txt files in the folder are 'extra'."""
        self._ensure()
        d = self._gig_dir(gig_id)
        snap = self._refresh(d)
        if snap is None:
            raise NotFound(f"gig {gig_id} has no readable thread", "put a non-empty .txt file in the gig folder")
        tp = Path(snap["thread_file"])
        text = P.normalize(fsio.read_text(tp)[0])
        extra = []
        for f in self._txt_files(d):
            if f.name != tp.name:
                try:
                    extra.append({"file": f.name, "text": P.normalize(fsio.read_text(f)[0])[:100000]})
                except OSError:
                    continue
        return {"meta": public_meta(snap), "thread": text, "extra": extra, "open": snap["open"],
                "thread_file": snap["thread_file"], "answers": len(P.parse_answers(text))}

    def answer_state(self, gig_id: str) -> Dict[str, Any]:
        """check_answer() for one gig, read fresh from disk; adds gig_id and thread_file."""
        r = self.read(gig_id)
        return {**check_answer(r["meta"], r["open"], r["thread"]), "gig_id": gig_id, "thread_file": r["thread_file"]}

    def claim(self, gig_id: str, agent_id: str) -> Dict[str, Any]:
        """Take the gig with an O_EXCL lock. Idempotent for the holder. A claim with no heartbeat
        for claim_stale_s may be taken over (recorded in the thread)."""
        self._ensure()
        self._agent(agent_id)
        d = self._gig_dir(gig_id)
        snap = self._refresh(d)
        if snap is None:
            raise NotFound(f"gig {gig_id} has no readable thread", "put a non-empty .txt file in the gig folder")
        if snap["status"] == "closed":
            raise Conflict("gig is closed", "post a new gig instead")
        if not snap["open"]:
            raise Conflict("nothing to answer: the last block of the thread is an answer",
                           "add a follow-up with gig_followup to open it again")
        if snap.get("to_agent") and snap["to_agent"] != agent_id:
            raise Conflict(f"gig is addressed to {snap['to_agent']}",
                           "only that agent can claim it; post a new gig if it is not available")
        tp = Path(snap["thread_file"])
        now = self.clock()
        lockp = d / LOCK_NAME
        payload = {"agent": agent_id, "at": P.iso_utc(now), "beat": P.iso_utc(now)}
        took: Optional[str] = None
        resumed = False
        for _ in range(12):
            try:
                fsio.create_exclusive(lockp, fsio.json_bytes(payload))
                break
            except FileExistsError:
                pass
            lock = self._read_lock(d)
            if lock is None:
                continue
            if lock == {}:
                if self._lock_age(d) > _GARBAGE_LOCK_S and self._evict_lock(d, None):
                    continue
                raise Conflict("another agent is claiming this gig right now", "retry in a few seconds")
            if lock["agent"] == agent_id:
                resumed = True
                lock["beat"] = P.iso_utc(now)
                fsio.write_json(lockp, lock)
                payload = lock
                break
            if now - P.parse_iso(lock["beat"]) < self.claim_stale_s:
                raise Conflict(f"already claimed by {lock['agent']}",
                               f"pick another gig; a claim is free after {int(self.claim_stale_s // 60)} minutes without a heartbeat")
            if self._evict_lock(d, lock):
                took = lock["agent"]
            else:       # others are reading or moving it (Windows refuses to rename an open file): back off
                time.sleep(random.uniform(0.005, 0.04))
        else:
            raise Conflict("could not take the claim (busy)", "retry in a few seconds")
        if took is None and not resumed:
            # Another taker may have evicted the stale lock and we won the free slot; the thread
            # still names the agent that picked it up (snap may already show no claim at all).
            prev = P.last_holder(fsio.read_text(tp)[0])
            took = prev if prev not in (None, agent_id) else None
        meta = self._update_meta(d, status="claimed", claimed_by=agent_id, claimed_at=payload["at"])
        if not resumed:
            dt = datetime.fromtimestamp(now)
            lines = []
            if took:
                lines.append(P.status_line(agent_id, dt, f"Took over from [{took}]: no heartbeat for "
                                                         f"{int(self.claim_stale_s // 60)} minutes."))
            lines.append(P.status_line(agent_id, dt, P.PICKUP_TEXT))
            fsio.append_text(tp, "\n".join(lines))
        return {"meta": meta, "thread_file": str(tp), "resumed": resumed, "took_over_from": took}

    def heartbeat(self, gig_id: str, agent_id: str) -> Dict[str, Any]:
        """Append a 'still working on it.' status line; at most one per beat_min_s per gig.
        Returns {"written": bool, "retry_in_s": n}."""
        self._ensure()
        d = self._gig_dir(gig_id)
        lock = self._read_lock(d)
        if not lock or lock.get("agent") != agent_id:
            raise Conflict("you do not hold the claim on this gig", "claim it first with gig_claim")
        now = self.clock()
        since = now - P.parse_iso(lock["beat"])
        if since < self.beat_min_s:
            return {"written": False, "retry_in_s": int(self.beat_min_s - since) + 1}
        tp = self._thread_path(d, self._load_meta(d))
        fsio.append_text(tp, P.status_line(agent_id, datetime.fromtimestamp(now), P.BEAT_TEXT))
        lock["beat"] = P.iso_utc(now)
        fsio.write_json(d / LOCK_NAME, lock)
        return {"written": True, "retry_in_s": int(self.beat_min_s)}

    def answer(self, gig_id: str, agent_id: str, text: str) -> Dict[str, Any]:
        """Append the ANSWER block, release the claim, status 'answered'. Claims the gig first if
        the caller does not hold it yet (and nobody else does)."""
        if not (text or "").strip():
            raise Invalid("answer text is empty", "write the answer")
        refuse_secrets(text)
        got = self.claim(gig_id, agent_id)
        d = self._gig_dir(gig_id)
        tp = Path(got["thread_file"])
        prof = self._agent(agent_id)
        fsio.append_text(tp, "\n" + P.answer_block(prof["name"], agent_id, self._dt(), text))
        _unlink(d / LOCK_NAME)
        meta = self._update_meta(d, status="answered")
        return {"meta": meta, "thread_file": str(tp)}

    def say(self, gig_id: str, agent_id: str, text: str) -> Dict[str, Any]:
        """One more message from the agent, so a thread reads like a chat. On an open thread this
        is answer(). After an answer it appends another answer block and the gig stays answered,
        so the asker's wait/watch sees a new answer. Only the agent that answered last (or the one
        the gig is addressed to) may speak while nobody has asked anything new."""
        if not (text or "").strip():
            raise Invalid("message text is empty", "write the message")
        refuse_secrets(text)
        d = self._gig_dir(gig_id)
        snap = self._refresh(d)
        if snap is None:
            raise NotFound(f"gig {gig_id} has no readable thread", "put a non-empty .txt file in the gig folder")
        if snap["status"] == "closed":
            raise Conflict("gig is closed", "post a new gig instead")
        if snap["open"]:
            return self.answer(gig_id, agent_id, text)
        prof = self._agent(agent_id)
        tp = Path(snap["thread_file"])
        answers = P.parse_answers(fsio.read_text(tp)[0])
        speaker = snap.get("to_agent") or (answers[-1]["agent_id"] if answers else None)
        if speaker != agent_id:
            raise Conflict(f"{agent_id} is not in this conversation",
                           "only the agent that answered (or the addressed agent) can add a message")
        lock = self._read_lock(d)
        if lock and lock.get("agent") not in (None, agent_id):
            raise Conflict(f"already claimed by {lock['agent']}", "wait for that agent to answer")
        fsio.append_text(tp, "\n" + P.answer_block(prof["name"], agent_id, self._dt(), text))
        meta = self._update_meta(d, status="answered")
        return {"meta": meta, "thread_file": str(tp)}

    def followup(self, gig_id: str, author: str, text: str) -> Dict[str, Any]:
        """Append a message block below the last answer; the gig is open again."""
        self._ensure()
        if not (text or "").strip():
            raise Invalid("follow-up text is empty", "write the message")
        refuse_secrets(text)
        author = _one_line(author, 80) or "anonymous"
        d = self._gig_dir(gig_id)
        snap = self._refresh(d)
        if snap is None:
            raise NotFound(f"gig {gig_id} has no readable thread", "put a non-empty .txt file in the gig folder")
        if snap["status"] == "closed":
            raise Conflict("gig is closed", "post a new gig instead")
        tp = Path(snap["thread_file"])
        fsio.append_text(tp, "\n" + P.message_block(author, self._dt(), text))
        if snap["status"] == "answered":
            meta = self._update_meta(d, status="open", claimed_by=None, claimed_at=None)
        else:
            meta = public_meta(snap)
        return {"meta": meta, "thread_file": str(tp)}

    def close(self, gig_id: str, author: str, rating: Optional[int] = None, note: str = "") -> Dict[str, Any]:
        """Close the gig for good. Optional rating 1-5 and a one-line note."""
        self._ensure()
        if isinstance(rating, str) and rating.strip().isdigit():
            rating = int(rating.strip())
        if rating is not None and (isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5):
            raise Invalid(f"rating must be a whole number from 1 to 5, got {rating!r}", "use 1 (poor) to 5 (great), or omit it")
        author = _one_line(author, 80) or "anonymous"
        note = _one_line(note, 500)
        refuse_secrets(note)
        d = self._gig_dir(gig_id)
        snap = self._refresh(d)
        if snap is None:
            raise NotFound(f"gig {gig_id} has no readable thread", "put a non-empty .txt file in the gig folder")
        if snap["status"] == "closed":
            raise Conflict("gig is already closed", "")
        line = f"Closed by {author}." + (f" Rating: {rating}/5." if rating else "") + (f" {note}" if note else "")
        fsio.append_text(Path(snap["thread_file"]), P.status_line(P.slugify(author, 40, "asker"), self._dt(), line))
        _unlink(d / LOCK_NAME)
        meta = self._update_meta(d, status="closed", rating=rating, close_note=note, closed_at=self._iso())
        return {"meta": meta, "thread_file": snap["thread_file"]}
