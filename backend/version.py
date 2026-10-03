"""The OmniSync release version, read from the repository's VERSION file.

VERSION at the repository root is the single source of the version: the
backend image copies it next to the ``backend`` package (/app/VERSION), the
release workflow checks the pushed tag against it, and the web UI's
package.json and the TUI's build flags are kept in step with it by
scripts/release/prepare.sh.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"

# Reported when the file is missing (e.g. a backend copied without it), so
# /health still answers instead of failing.
UNKNOWN_VERSION = "0.0.0+unknown"


@lru_cache(maxsize=1)
def get_version() -> str:
    """The release version, e.g. "0.9.0"; UNKNOWN_VERSION when unreadable."""
    try:
        value = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return UNKNOWN_VERSION
    return value or UNKNOWN_VERSION
