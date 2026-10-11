"""Constants and small helpers shared by the sync engine modules."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

# The whole package logs under the name the single sync_engine module had.
logger = logging.getLogger("backend.services.sync_engine")

# First retry delay after the provider rate-limited a sync (doubles per attempt).
RATE_LIMIT_BASE_DELAY = float(os.environ.get("OMNISYNC_RATE_LIMIT_DELAY", "30"))

TRASH_STAMP_FORMAT = "%Y-%m-%dT%H-%M-%SZ"

STOPPED_BY_USER = "Stopped by user."

ENGINE_STOPPED = "Interrupted: the sync engine was stopped."

_FILTER_SPECIAL = set("\\*?[]{}")


def filter_escape(path: str) -> str:
    """Escape a literal path for an rclone filter rule.

    Whitespace at the end goes in brackets (``[ ]``): rclone strips it from
    the lines of a filter file, so ``- /notes.txt `` would match
    ``notes.txt`` instead (verified with rclone 1.75.1).
    """
    body = path.rstrip()
    escaped = "".join("\\" + c if c in _FILTER_SPECIAL else c for c in body)
    return escaped + "".join(f"[{c}]" for c in path[len(body):])


def remote_join(base: str, rel: str) -> str:
    """Join a path onto a remote or local base, e.g. gdrive: + a/b -> gdrive:a/b."""
    rel = rel.lstrip("/")
    return f"{base}{rel}" if base.endswith(":") else f"{base.rstrip('/')}/{rel}"


def _write_text(path: str, text: str) -> None:
    """Write ``text`` to ``path`` (UTF-8); blocking, so async callers use a thread."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _utc(value: datetime | None) -> datetime | None:
    """A time in UTC, for storing: SQLite keeps the wall time and drops the offset."""
    return value.astimezone(timezone.utc) if value is not None and value.tzinfo is not None else value


# The engine modules call this as common.calculate_backoff_delay, so tests
# can patch it here.
def calculate_backoff_delay(attempt: int, base_delay: float) -> float:
    """Calculate exponential backoff delay for a given attempt number.

    Args:
        attempt: The retry attempt number (1-based).
        base_delay: The base delay in seconds.

    Returns:
        Delay in seconds: base_delay * 2^(attempt - 1)
    """
    return base_delay * (2 ** (attempt - 1))
