"""Teams of a hosted server. A team (a company, a group of people) owns one board, its groups
(members.json) and its event log; its tokens can reach nothing else.

    <state>/teams.json          {"format": 1, "teams": {"<team id>": {"name", "created", "board"}}}
    <state>/teams/<id>/         mode 700: members.json, events.jsonl and, by default, board/
    <state>/tokens.json         every token of every team (hashes only, see tokens.py)
    <state>/admin-audit.jsonl   one line per team or token change and per migration (never a token)

"board" in teams.json is the team's board folder, relative to the state folder (or absolute). A new
team gets teams/<id>/board; a board migrated from a single-board server stays where it was.

migrate() turns the state folder of a single-board server (format-1 tokens.json, members.json and
events.jsonl next to it) into one team, so its agents keep working with the same URL and token.
"""
import getpass
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import protocol as P
from .board import Board
from .errors import Conflict, Invalid, NotFound
from .tokens import TokenStore, file_lock, valid_team_id, write_private_json

FORMAT = 1
DEFAULT_TEAM = "default"
ADMIN_AUDIT = "admin-audit.jsonl"


def _one_line(value: Any, max_len: int = 80) -> str:
    return " ".join(str(value or "").split())[:max_len]


def admin_audit(state, row: Dict[str, Any], clock: Callable[[], float] = time.time) -> None:
    """Append one line to <state>/admin-audit.jsonl (mode 600). Callers pass ids, names and roles,
    never a token or a hash. A failing disk never fails the change itself."""
    line = json.dumps({"ts": P.iso_utc(clock()), **row}, ensure_ascii=False) + "\n"
    try:
        path = Path(state) / ADMIN_AUDIT
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def local_actor() -> str:
    """Who ran an admin command on the server's machine, for the admin audit log."""
    try:
        return f"local:{getpass.getuser()}"
    except Exception:
        return "local"


def inside(child: Path, parent: Path) -> bool:
    """True when child is parent or lies under it (both resolved)."""
    c, p = Path(child).resolve(), Path(parent).resolve()
    return c == p or p in c.parents


class TeamStore:
    def __init__(self, state, clock: Optional[Callable[[], float]] = None):
        self.state = Path(state)
        self.path = self.state / "teams.json"
        self.clock = clock or time.time
        self._sig: Optional[tuple] = None
        self._teams: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ file
    def _load(self) -> Dict[str, Dict[str, Any]]:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            self._sig, self._teams = None, {}
            return self._teams
        sig = (st.st_ino, st.st_mtime_ns, st.st_size)
        if sig != self._sig:
            data = json.loads(self.path.read_text("utf-8"))
            teams = data.get("teams") if isinstance(data, dict) else None
            if not isinstance(teams, dict) or not all(isinstance(v, dict) and valid_team_id(k) for k, v in teams.items()):
                raise ValueError("teams.json is damaged (expected {\"teams\": {id: record}})")
            self._teams, self._sig = teams, sig
        return self._teams

    def _load_admin(self) -> Dict[str, Dict[str, Any]]:
        try:
            return self._load()
        except ValueError as exc:
            raise Invalid(f"cannot use teams.json: {exc}", "restore it from a backup") from None

    def exists(self) -> bool:
        return self.path.exists()

    def get(self, team: str) -> Optional[Dict[str, Any]]:
        """The team's record, or None (also when teams.json is unreadable: fail closed). Never raises."""
        try:
            rec = self._load().get(team)
            return dict(rec) if isinstance(rec, dict) else None
        except Exception:
            return None

    def ids(self) -> List[str]:
        try:
            return sorted(self._load())
        except Exception:
            return []

    def list(self) -> List[Dict[str, Any]]:
        return [{"team": k, **v} for k, v in sorted(self._load_admin().items())]

    # ------------------------------------------------------------------ paths
    def team_dir(self, team: str) -> Path:
        if not valid_team_id(team or ""):
            raise Invalid(f"bad team id: {team!r}", "lowercase letters, digits, - and _")
        return self.state / "teams" / team

    def board_path(self, team: str, rec: Optional[Dict[str, Any]] = None) -> Path:
        """The team's board folder: teams/<id>/board, or where teams.json says (relative to the state folder)."""
        rec = rec if rec is not None else (self.get(team) or {})
        ref = rec.get("board")
        if not ref:
            return self.team_dir(team) / "board"
        p = Path(str(ref))
        return p if p.is_absolute() else self.state / p

    def _board_ref(self, board: Path) -> str:
        """How teams.json names a board: relative to the state folder when that works, so the same
        file is right inside a container and on the host when both mount the folders side by side."""
        try:
            return os.path.relpath(Path(board).resolve(), self.state.resolve())
        except ValueError:            # another drive on Windows
            return str(Path(board).resolve())

    def _check_board(self, board: Path) -> None:
        if inside(self.state, board):
            raise Invalid(f"the state folder is inside the board folder {board}: keep tokens out of the board",
                          "use a board folder that does not contain the state folder")

    def sharing(self, team: str, board: Path, teams: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
        """The id of another team whose board or team folder overlaps board (one inside the other,
        or the same folder), else "". Two teams on one board would see each other's gigs."""
        teams = self._load_admin() if teams is None else teams
        for other, rec in sorted(teams.items()):
            if other == team or not isinstance(rec, dict):
                continue
            for folder in (self.board_path(other, rec), self.team_dir(other)):
                if inside(board, folder) or inside(folder, board):
                    return other
        return ""

    # ------------------------------------------------------------------ changes
    def _save(self, teams: Dict[str, Dict[str, Any]]) -> None:
        write_private_json(self.path, {"format": FORMAT, "teams": teams})
        self._sig = None

    def add(self, team: str, name: str, board: Optional[Path] = None, *, init: bool = True) -> Dict[str, Any]:
        """Register a team (Conflict if the id is taken), create its folder (mode 700) and, with init,
        its board. board=None puts the board in teams/<id>/board."""
        tdir = self.team_dir(team)
        name = _one_line(name) or team
        board_path = Path(board) if board else tdir / "board"
        self._check_board(board_path)
        with file_lock(self.path):
            self._sig = None
            teams = {k: dict(v) for k, v in self._load_admin().items()}
            if team in teams:
                raise Conflict(f"team {team!r} exists already", "pick another id, or list them: sidegig team list")
            other = self.sharing(team, board_path, teams)
            if other:
                raise Conflict(f"that board folder overlaps the folders of team {other!r}",
                               "every team needs its own board folder, outside the other teams' folders")
            tdir.mkdir(parents=True, exist_ok=True)
            os.chmod(tdir, 0o700)
            if init and not (board_path / "board.json").is_file():
                Board(board_path, clock=self.clock).init(name)
            elif not (board_path / "board.json").is_file():
                raise NotFound(f"{board_path} is not a board (no board.json)", "create it with: sidegig init DIR --name NAME")
            rec = {"name": name, "created": P.iso_utc(self.clock())}
            if board:
                rec["board"] = self._board_ref(board_path)
            teams[team] = rec
            self._save(teams)
        return {"team": team, **rec}


def create_team(state, team: str, name: str, admin: str, *, board=None, expires_days: Optional[float] = None,
                actor: str = "", clock: Optional[Callable[[], float]] = None) -> Tuple[str, Dict[str, Any]]:
    """Create a team and its first admin token. Returns (plain token, {team, name, token record})."""
    state = Path(state)
    teams, tokens = TeamStore(state, clock), TokenStore(state / "tokens.json", clock)
    if not P.valid_agent_id(admin or ""):
        raise Invalid(f"bad admin name: {admin!r}", "lowercase letters, digits, - and _")
    rec = teams.add(team, name, Path(board) if board else None)
    token, trec = tokens.issue(team, admin, role="admin", label="first admin", expires_days=expires_days)
    admin_audit(state, {"actor": actor or local_actor(), "action": "team.create", "team": team, "name": rec["name"]}, teams.clock)
    admin_audit(state, {"actor": actor or local_actor(), "action": "token.issue", "team": team, "token_id": trec["id"],
                        "token_name": admin, "role": "admin", "expires": trec["expires"]}, teams.clock)
    return token, {"team": team, "name": rec["name"], "token": trec}


# ---------------------------------------------------------------------- migration
def legacy_problem(state) -> str:
    """Why a state folder still needs `sidegig migrate-teams`, or "" when it does not."""
    state = Path(state)
    try:
        legacy = TokenStore(state / "tokens.json").legacy_count()
    except Invalid as exc:
        return str(exc)
    if legacy:
        return f"tokens.json holds {legacy} token(s) from before teams"
    if not (state / "teams.json").exists():
        left = [n for n in ("members.json", "events.jsonl") if (state / n).exists()]
        if left:
            return f"{' and '.join(left)} from a single-board server are still in the state folder"
    return ""


def migrate(state, board, *, team: str = DEFAULT_TEAM, name: Optional[str] = None, dry_run: bool = False,
            clock: Optional[Callable[[], float]] = None) -> List[Dict[str, str]]:
    """Move a single-board server's data into one team. Safe to repeat: every step checks first and
    is skipped when already done. With dry_run nothing is written. Returns the steps:
    [{"step", "result": done | would do | already done | skipped, "detail"}].

    1. register the existing board folder as the team's board (it is not moved),
    2. give every format-1 token the team (role member, same name); tokens.json is copied to
       tokens.json.v1.bak first (hashes only),
    3. move members.json and 4. events.jsonl into teams/<team>/ (the event log keeps its epoch, so
       clients with a saved cursor neither replay nor miss events).
    Run it with the server stopped."""
    state, board = Path(state), Path(board)
    teams, tokens = TeamStore(state, clock), TokenStore(state / "tokens.json", clock)
    steps: List[Dict[str, str]] = []
    act = "would do" if dry_run else "done"

    def note(step: str, result: str, detail: str) -> None:
        steps.append({"step": step, "result": result, "detail": detail})

    if not valid_team_id(team or ""):
        raise Invalid(f"bad team id: {team!r}", "lowercase letters, digits, - and _")
    if not (board / "board.json").is_file():
        raise NotFound(f"{board} is not a board (no board.json)", "pass the board folder the server used (--board)")
    if inside(state, board):
        raise Invalid("the state folder is inside the board folder", "keep the state folder outside the board")
    try:
        board_name = json.loads((board / "board.json").read_text("utf-8-sig")).get("name")
    except (OSError, ValueError, AttributeError):
        board_name = None
    name = _one_line(name or board_name or team)

    # 1. the team
    rec = teams.get(team) if teams.exists() else None
    if teams.exists() and rec is None:
        teams._load_admin()                       # a damaged teams.json is an error, not "no team"
    if rec is None:
        if not dry_run:
            state.mkdir(parents=True, exist_ok=True)
            teams.add(team, name, board, init=False)
        note("team", act, f"register team {team!r} ({name}) with the board {board}")
    elif teams.board_path(team, rec).resolve() == board.resolve():
        note("team", "already done", f"team {team!r} uses this board")
    else:
        raise Conflict(f"team {team!r} exists already with another board ({teams.board_path(team, rec)})",
                       "pass --team with another id, or the board that team uses")

    # 2. the tokens
    legacy = tokens.legacy_count()
    if legacy:
        backup = state / "tokens.json.v1.bak"
        if not dry_run:
            if not backup.exists():
                write_private_json(backup, json.loads(tokens.path.read_text("utf-8")))
            tokens.assign_legacy(team)
        note("tokens", act, f"move {legacy} token(s) into team {team!r} as members (same tokens, same names); "
                            f"backup {backup.name}")
    else:
        note("tokens", "already done", "no token without a team")

    # 3, 4. members.json and events.jsonl
    tdir = teams.team_dir(team)
    for fname in ("members.json", "events.jsonl"):
        src, dst = state / fname, tdir / fname
        if not src.exists():
            note(fname, "already done" if dst.exists() else "skipped",
                 f"in teams/{team}/" if dst.exists() else "the server had none")
        elif dst.exists():
            note(fname, "skipped", f"both {fname} and teams/{team}/{fname} exist: compare them and remove one by hand")
        else:
            if not dry_run:
                tdir.mkdir(parents=True, exist_ok=True)
                os.chmod(tdir, 0o700)
                os.replace(src, dst)
            note(fname, act, f"move {fname} to teams/{team}/{fname}")

    if not dry_run and any(s["result"] == "done" for s in steps):
        admin_audit(state, {"actor": local_actor(), "action": "migrate", "team": team,
                            "steps": [s["step"] for s in steps if s["result"] == "done"], "tokens_moved": legacy},
                    teams.clock)
    return steps
