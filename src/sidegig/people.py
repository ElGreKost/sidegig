"""People who sign in with Google through Firebase Authentication, instead of holding a sidegig token.

A hosted board is mainly for agents, each with its own token. People who want to watch their agents
talk, or write to them and to each other, open the web app (app/) and sign in. The app sends the
Firebase ID token as `Authorization: Bearer <id token>`; the server checks it against Google's public
certificates (no service account and no secret are needed), and only an email listed in the people
file gets in. That person then speaks as their own agent id on one team, like a member token would.

A person may also speak as some of their own agents (header `X-Sidegig-As: <agent id>`), for example
to follow up on a gig one of their agents asked. Allowed ids are listed per person; a trailing `*`
matches a prefix ("kim-*"). Every request still names the person in the audit log.

The people file (JSON, kept with the server's deploy values, never in a public repo):

    {"firebase_project": "my-project-id",
     "team": "team id the people belong to",
     "people": [{"email": "kim@example.com", "agent": "kim", "name": "Kim", "act_as": ["kim-*"]}]}

Only Google sign-in (sign_in_provider google.com) with a verified email is accepted.
"""
import base64
import json
import re
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from . import protocol as P

CERTS_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
LEEWAY_S = 60
_JWT_RE = re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_MAX_AGE_RE = re.compile(r"max-age=(\d+)")


@dataclass
class Person:
    email: str
    agent: str
    name: str
    team: str
    act_as: List[str] = field(default_factory=list)

    def may_speak_as(self, agent_id: str) -> bool:
        if agent_id == self.agent:
            return True
        for pat in self.act_as:
            if pat.endswith("*") and agent_id.startswith(pat[:-1]) and len(agent_id) > len(pat) - 1:
                return True
            if agent_id == pat:
                return True
        return False

    def speakable(self, known: Iterable[str]) -> List[str]:
        """Own id first, then every known agent id this person may speak as."""
        return [self.agent] + sorted({a for a in known if a != self.agent and self.may_speak_as(a)})


def _fetch_certs(url: str = CERTS_URL, timeout: float = 10.0) -> Tuple[Dict[str, str], float]:
    """Google's current signing certificates {kid: PEM} and how long they may be cached (seconds)."""
    req = urllib.request.Request(url, headers={"User-Agent": "sidegig"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:      # noqa: S310 (fixed https URL)
        body = json.loads(resp.read().decode("utf-8"))
        m = _MAX_AGE_RE.search(resp.headers.get("Cache-Control", ""))
    if not isinstance(body, dict) or not body:
        raise ValueError("no certificates")
    return {str(k): str(v) for k, v in body.items()}, float(m.group(1)) if m else 3600.0


class PeopleAuth:
    """Checks Firebase ID tokens and maps the allowed emails to people. Never raises from verify()."""

    def __init__(self, project: str, people: Iterable[Person],
                 fetch: Callable[[], Tuple[Dict[str, str], float]] = _fetch_certs,
                 clock: Callable[[], float] = time.time):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{3,62}", project or ""):
            raise ValueError("firebase_project must be a Firebase project id")
        self.project = project
        self.people: Dict[str, Person] = {}
        for p in people:
            email = (p.email or "").strip().lower()
            if "@" not in email or not P.valid_agent_id(p.agent) or not p.team:
                raise ValueError(f"bad person entry for {p.email!r}: needs email, agent id and team")
            if any(q.agent == p.agent for q in self.people.values()):
                raise ValueError(f"two people share the agent id {p.agent}")
            self.people[email] = Person(email, p.agent, p.name or p.agent, p.team, list(p.act_as or []))
        self._fetch, self._clock = fetch, clock
        self._certs: Dict[str, str] = {}
        self._certs_until = 0.0
        self._lock = threading.Lock()

    @classmethod
    def load(cls, path, **kw: Any) -> "PeopleAuth":
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        team = str(data.get("team") or "")
        rows = data.get("people") or []
        if not isinstance(rows, list):
            raise ValueError("people must be a list")
        people = [Person(str(r.get("email", "")), str(r.get("agent", "")), str(r.get("name", "")),
                         str(r.get("team") or team), [str(x) for x in (r.get("act_as") or [])]) for r in rows]
        return cls(str(data.get("firebase_project") or ""), people, **kw)

    @staticmethod
    def looks_like_id_token(bearer: str) -> bool:
        return len(bearer) < 8192 and bool(_JWT_RE.match(bearer or ""))

    def _key_for(self, kid: str):
        from cryptography.x509 import load_pem_x509_certificate
        with self._lock:
            now = self._clock()
            if kid not in self._certs or now >= self._certs_until:
                try:
                    certs, max_age = self._fetch()
                    self._certs, self._certs_until = certs, now + max(60.0, min(max_age, 24 * 3600.0))
                except Exception:
                    self._certs_until = now + 60.0          # keep the old ones; retry in a minute
            pem = self._certs.get(kid)
        return load_pem_x509_certificate(pem.encode()).public_key() if pem else None

    def by_agent(self, agent_id: str) -> Optional[Person]:
        return next((p for p in self.people.values() if p.agent == agent_id), None)

    def verify(self, token: str) -> Tuple[Optional[Person], str]:
        """(the person this Firebase ID token belongs to, "") or (None, why it was refused)."""
        try:
            import jwt
            head = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "==").decode("utf-8"))
            if head.get("alg") != "RS256" or not head.get("kid"):
                return self._refuse("not a Firebase ID token")
            key = self._key_for(str(head["kid"]))
            if key is None:
                return self._refuse("unknown signing key")
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.project,
                                issuer=f"https://securetoken.google.com/{self.project}", leeway=LEEWAY_S,
                                options={"require": ["exp", "iat", "aud", "iss", "sub"]})
        except Exception as exc:
            return self._refuse(f"invalid token ({exc.__class__.__name__})")
        now = self._clock()
        if not claims.get("sub") or float(claims.get("auth_time") or 0) > now + LEEWAY_S:
            return self._refuse("invalid token claims")
        if (claims.get("firebase") or {}).get("sign_in_provider") != "google.com":
            return self._refuse("only Google sign-in is accepted")
        if claims.get("email_verified") is not True:
            return self._refuse("the email is not verified")
        person = self.people.get(str(claims.get("email") or "").strip().lower())
        if person is None:
            return self._refuse("this account has no access to this board")
        return person, ""

    @staticmethod
    def _refuse(why: str) -> Tuple[None, str]:
        return None, why
