"""Agent tokens for the remote board: one bearer token per agent, the token decides the agent id.

The store is a JSON file kept OUTSIDE the board folder (agents can read the board, never the
store). Only the SHA-256 of each token is kept; the plain token is shown once, at issue time.
The file is re-read when it changes, so a revocation applies to the next request without a restart.
"""
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import protocol as P
from .errors import Invalid, NotFound

PREFIX = "sgk_"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenStore:
    def __init__(self, path):
        self.path = Path(path)
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{secrets.token_hex(4)}.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"format": 1, "tokens": tokens}, f, indent=2)
        os.replace(tmp, self.path)
        self._sig = None

    # ------------------------------------------------------------------ admin
    def issue(self, agent_id: str, label: str = "") -> str:
        """A new token for agent_id. Returns the plain token; it is not stored and cannot be shown again."""
        if not P.valid_agent_id(agent_id or ""):
            raise Invalid(f"bad agent id: {agent_id!r}", "lowercase letters, digits, - and _")
        tokens = dict(self._load_admin())
        token = PREFIX + secrets.token_urlsafe(32)
        tokens[_hash(token)] = {"agent": agent_id, "label": label[:80], "hint": token[:10],
                                "created": P.iso_utc(time.time()), "revoked": None}
        self._save(tokens)
        return token

    def revoke(self, agent_id: Optional[str] = None, hint: Optional[str] = None) -> int:
        """Revoke every live token of an agent, or the one whose first characters are hint. Returns the count."""
        tokens = {k: dict(v) for k, v in self._load_admin().items()}
        n = 0
        for rec in tokens.values():
            if rec.get("revoked"):
                continue
            if (agent_id and rec.get("agent") == agent_id) or (hint and rec.get("hint") == hint[:10]):
                rec["revoked"] = P.iso_utc(time.time())
                n += 1
        if not n:
            raise NotFound("no live token matched", "list them with: sidegig token list")
        self._save(tokens)
        return n

    def list(self) -> List[Dict[str, Any]]:
        return sorted(({k: v for k, v in rec.items()} for rec in self._load_admin().values()),
                      key=lambda r: (str(r.get("agent", "")), str(r.get("created", ""))))

    def live_agents(self) -> set:
        """Agent ids that hold at least one live token (empty when the store is unreadable)."""
        try:
            return {r.get("agent") for r in self._load().values()
                    if not r.get("revoked") and P.valid_agent_id(str(r.get("agent") or ""))}
        except Exception:
            return set()

    # ------------------------------------------------------------------ requests
    def verify(self, token: str) -> Optional[str]:
        """The agent id a live token belongs to, else None. Lookup by hash: no timing side channel on the token.
        Anything wrong with the store (missing, unreadable, damaged) refuses everyone; it never raises."""
        if not token or not token.startswith(PREFIX) or len(token) > 200:
            return None
        try:
            rec = self._load().get(_hash(token))
            if not rec or rec.get("revoked"):
                return None
            agent = rec.get("agent")
            return agent if P.valid_agent_id(str(agent or "")) else None
        except Exception:
            return None
