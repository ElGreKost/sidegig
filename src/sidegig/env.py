"""Environment-variable lookup: SIDEGIG_* first, the pre-rename GIGBOARD_* names silently as a fallback."""
import os
from typing import Optional

# Deprecated spellings from before the package was renamed (sidegig used to be called gigboard).
# Existing worker processes and MCP client configs still set them, so they keep working, silently.
_LEGACY_PREFIX = "GIGBOARD_"
_PREFIX = "SIDEGIG_"


def _get(suffix: str) -> Optional[str]:
    return os.environ.get(_PREFIX + suffix) or os.environ.get(_LEGACY_PREFIX + suffix) or None


def env_root() -> Optional[str]:
    """The board folder from $SIDEGIG_ROOT (or the deprecated $GIGBOARD_ROOT)."""
    return _get("ROOT")


def env_agent_id() -> Optional[str]:
    """The default agent id from $SIDEGIG_AGENT_ID (or the deprecated $GIGBOARD_AGENT_ID)."""
    return _get("AGENT_ID")
