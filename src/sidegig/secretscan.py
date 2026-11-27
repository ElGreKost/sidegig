"""Refuse gig texts that contain an API key, token or private key.

A gig is read by every agent on the board (and stored in plain files), so a key pasted into one
leaks to all of them. The patterns are the well-known, high-confidence prefixes; they are a seat
belt, not a guarantee, and the error never echoes the matched text back.
"""
import re
from typing import Optional

from .errors import Invalid

# (what it is, pattern). Order matters: the more specific sk-ant- before the generic sk-.
_PATTERNS = [
    ("an Anthropic API key (sk-ant-...)", re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")),
    ("an OpenAI-style secret key (sk-...)",
     re.compile(r"(?<![A-Za-z0-9_\-])sk-(?:proj-|svcacct-|admin-)?(?=[A-Za-z_\-]*[0-9])[A-Za-z0-9_\-]{20,}")),
    ("an AWS access key id (AKIA...)", re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])")),
    ("a GitHub token (ghp_...)", re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}")),
    ("a GitHub fine-grained token (github_pat_...)", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("a private key block (-----BEGIN ... PRIVATE KEY-----)",
     re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")),
    ("a Slack token (xoxb-/xoxp-...)", re.compile(r"(?<![A-Za-z0-9])xox[abposr]-[A-Za-z0-9\-]{10,}")),
    ("a Stripe live key (sk_live_/rk_live_...)", re.compile(r"(?<![A-Za-z0-9])(?:sk|rk)_live_[A-Za-z0-9]{10,}")),
    ("a sidegig token (sgk_...)", re.compile(r"(?<![A-Za-z0-9])sgk_[A-Za-z0-9_\-]{20,}")),
]


def find_secret(text: Optional[str]) -> Optional[str]:
    """What kind of secret the text seems to contain (a short description), or None."""
    if not text:
        return None
    for label, rx in _PATTERNS:
        if rx.search(text):
            return label
    return None


def refuse_secrets(*texts: Optional[str]) -> None:
    """Raise Invalid when any of the texts contains something that looks like a secret."""
    for text in texts:
        label = find_secret(text)
        if label:
            raise Invalid(f"refused: the text looks like it contains {label}. Nothing was written.",
                          "remove the secret and send again. Never put keys, tokens or passwords in a gig: "
                          "everyone on the board can read it. Refer to the secret by name and share it "
                          "through your secret manager.")
