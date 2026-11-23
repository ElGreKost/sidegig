"""The thread protocol: exact marker strings, formatters, parsers and the open/answered rule.

The marker strings are the protocol; do not reword them. See PROTOCOL.md.
"""
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List

RULE = "=" * 20
PICKUP_TEXT = "Picked up. Working on it; a heartbeat line follows every 2 minutes."
BEAT_TEXT = "still working on it."
ANSWER_END_PREFIX = f"{RULE} END OF ANSWER"
ANSWER_END_LINE = f"{ANSWER_END_PREFIX} · to follow up, type below this line, then save and close the file {RULE}"

# A status line: "[agent_id] HH:MM text". Ignored by the open/answered rule.
STATUS_RE = re.compile(r"^\s*\[[a-z0-9][a-z0-9_-]*\] \d{2}:\d{2} \S")
END_RE = re.compile(r"^" + re.escape(ANSWER_END_PREFIX), re.M)
HEAD_RE = re.compile(
    r"^" + RULE + r" ANSWER from (?P<name>.*) \((?P<id>[^()]*)\) · "
    r"(?P<when>\d{4}-\d{2}-\d{2} \d{2}:\d{2}) " + RULE + r"[ \t]*$"
)
AGENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_IGNORED_SUFFIXES = (".tmp", ".swp", ".swx", ".swo", ".bak", "~", ".lnk", ".part", ".crdownload")
_IGNORED_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}


def normalize(text: str) -> str:
    """CRLF / CR -> LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def slugify(text: str, max_len: int = 40, default: str = "x") -> str:
    """Lowercase ascii slug (accents folded, everything else becomes a dash)."""
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:max_len].strip("-")
    return s or default


def valid_agent_id(agent_id: str) -> bool:
    return isinstance(agent_id, str) and bool(AGENT_ID_RE.match(agent_id))


GROUP_RE = re.compile(r"^[a-z][a-z0-9_-]{0,30}:[a-z0-9][a-z0-9_-]{0,63}$")


def is_group(name) -> bool:
    """A to_agent value naming a group (dept:marketing, person:dave) rather than one agent."""
    return isinstance(name, str) and ":" in name


def valid_group(name: str) -> bool:
    return isinstance(name, str) and bool(GROUP_RE.match(name))


def iso_utc(epoch: float) -> str:
    """Epoch seconds -> YYYY-MM-DDTHH:MM:SSZ."""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> float:
    """Inverse of iso_utc."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def is_ignored_name(name: str) -> bool:
    """Names the board ignores: leading _ . ~, editor temp/backup files."""
    n = name.lower()
    return (not n or n[0] in "_.~" or n in _IGNORED_NAMES or n.endswith(_IGNORED_SUFFIXES)
            or (n.startswith("#") and n.endswith("#")))


def status_line(agent_id: str, when: datetime, text: str) -> str:
    """[agent_id] HH:MM text."""
    return f"[{agent_id}] {when:%H:%M} {text}"


MSG_HEAD_RE = re.compile(r"^-----\s.*·.*-----\s*$")


def sanitize_body(text: str) -> str:
    """Neutralise lines that start with the 20-char rule or look like a message header, so a body
    cannot fake a marker or a message from somebody else."""
    return "\n".join((" " + ln) if ln.startswith(RULE) or MSG_HEAD_RE.match(ln) else ln
                     for ln in normalize(text).split("\n"))


def message_block(author: str, when: datetime, text: str) -> str:
    """The ----- author · YYYY-MM-DD HH:MM ----- header followed by the text."""
    body = sanitize_body(text).strip("\n")
    return f"----- {author} · {when:%Y-%m-%d %H:%M} -----\n{body}\n"


def answer_block(agent_name: str, agent_id: str, when: datetime, text: str) -> str:
    """The ANSWER ... END OF ANSWER block."""
    head = f"{RULE} ANSWER from {agent_name} ({agent_id}) · {when:%Y-%m-%d %H:%M} {RULE}"
    body = sanitize_body(text).strip("\n")
    return f"{head}\n\n{body}\n\n{ANSWER_END_LINE}\n"


def _after_last_end(text: str) -> str:
    last = None
    for m in END_RE.finditer(text):
        last = m
    if last is None:
        return text
    nl = text.find("\n", last.end())
    return "" if nl < 0 else text[nl + 1:]


def open_text(thread: str) -> str:
    """Non-blank text after the last END OF ANSWER line, status lines removed."""
    tail = _after_last_end(normalize(thread))
    return "\n".join(ln for ln in tail.split("\n") if not STATUS_RE.match(ln)).strip()


def is_open(thread: str) -> bool:
    """True when the thread has text that still needs an answer."""
    return bool(open_text(thread))


STATUS_PARTS_RE = re.compile(r"^\s*\[(?P<id>[a-z0-9][a-z0-9_-]*)\] \d{2}:\d{2} (?P<text>.*)$")
GIVE_UP_PREFIX = "No answer after "


def last_holder(thread: str):
    """The agent that picked up the open part of the thread (after the last END OF ANSWER) and
    has not given up since, or None. A new claimer that finds one is taking over from it."""
    holder = None
    for line in _after_last_end(normalize(thread)).split("\n"):
        m = STATUS_PARTS_RE.match(line)
        if not m:
            continue
        if m["text"].startswith(PICKUP_TEXT[:10]):
            holder = m["id"]
        elif m["text"].startswith((GIVE_UP_PREFIX, "Closed by ")):
            holder = None
    return holder


def has_answer(thread: str) -> bool:
    return bool(END_RE.search(normalize(thread)))


def parse_answers(thread: str) -> List[Dict[str, Any]]:
    """All complete answer blocks -> [{agent_id, agent_name, when, text}]."""
    out: List[Dict[str, Any]] = []
    cur = None
    for line in normalize(thread).split("\n"):
        if cur is None:
            m = HEAD_RE.match(line)
            if m:
                cur = {"agent_id": m["id"], "agent_name": m["name"], "when": m["when"], "body": []}
        elif END_RE.match(line):
            cur["text"] = "\n".join(cur.pop("body")).strip("\n")
            out.append(cur)
            cur = None
        else:
            cur["body"].append(line)
    return out
