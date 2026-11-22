"""Groups for the hosted board: departments and people that run several agents.

members.json lives in the server's state folder (never in the board folder, so agents cannot
edit their own membership) and maps a group name to agent ids:

    {"dept:marketing": ["kostis-marketing", "giannis-marketing"],
     "person:giannis": ["giannis", "giannis-laptop"]}

(the same object under a top-level "groups" key is accepted too). A group name is
<kind>:<name>, lowercase. A gig addressed to a group can be claimed by any member, and every
member hears its events. The file is re-read when it changes. An unreadable file means no groups
at all (fail closed): posts to a group are refused until it is fixed.
"""
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from . import protocol as P


class MemberStore:
    def __init__(self, path):
        self.path = Path(path)
        self._sig: Optional[tuple] = None
        self._groups: Dict[str, List[str]] = {}
        self.problem = ""      # why the file could not be used, for error hints and the server log

    def groups(self) -> Dict[str, List[str]]:
        """{group: [agent ids]}, re-read when the file changed; {} when it is missing or unreadable."""
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            self._sig, self._groups, self.problem = None, {}, ""
            return self._groups
        except OSError as exc:
            self._sig, self._groups, self.problem = None, {}, f"members file unreadable: {exc.__class__.__name__}"
            return self._groups
        sig = (st.st_ino, st.st_mtime_ns, st.st_size)
        if sig == self._sig:
            return self._groups
        try:
            data = json.loads(self.path.read_text("utf-8-sig"))
        except (OSError, ValueError) as exc:
            self._sig, self._groups, self.problem = sig, {}, f"members file unreadable: {exc.__class__.__name__}"
            return self._groups
        if isinstance(data, dict) and isinstance(data.get("groups"), dict):
            data = data["groups"]
        groups: Dict[str, List[str]] = {}
        skipped: List[str] = []
        for name, ids in (data.items() if isinstance(data, dict) else []):
            name = str(name).strip().lower()
            if not P.valid_group(name) or not isinstance(ids, list):
                skipped.append(str(name)[:40])
                continue
            groups[name] = sorted({str(i).strip() for i in ids if P.valid_agent_id(str(i).strip())})
        self._sig, self._groups = sig, groups
        self.problem = ("skipped invalid entries: " + ", ".join(skipped)) if skipped else ""
        return self._groups
