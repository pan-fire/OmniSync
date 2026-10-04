"""Constants and small helpers shared by the rclone modules."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import PurePosixPath

# The whole package logs under the name the single rclone module had.
logger = logging.getLogger("backend.services.rclone")

DEFAULT_TIMEOUT = 300  # 5 minutes, for listing and metadata commands only

# rclone check hashes the local files; a large tree takes a while.
CHECK_TIMEOUT = int(os.environ.get("OMNISYNC_CHECK_TIMEOUT", "900"))

# Longest single output line read from rclone (a JSON log line or a path).
STREAM_LINE_LIMIT = 4 * 1024 * 1024

# FileChange rows stored per job; every change is still counted.
MAX_RECORDED_CHANGES = int(os.environ.get("OMNISYNC_MAX_RECORDED_CHANGES", "10000"))

# Where overwritten and deleted files go (inside the destination, excluded
# from every sync and listing), and the marker file that proves a folder is
# the one this profile syncs (not an empty, unmounted mount point).
TRASH_DIR = ".omnisync-trash"
SENTINEL_FILE = ".omnisync-check"
TRASH_FILTER = f"- /{TRASH_DIR}/**"
# rclone's in-progress files (<name>.<8 hex>.partial), left behind when a
# run is killed: never carried to the other side. PARTIAL_NAME is the same
# pattern for a file name (the last path segment).
PARTIAL_PATTERN = r"{{.*\.[0-9a-f]{8}\.partial}}"
PARTIAL_FILTER = f"- {PARTIAL_PATTERN}"
PARTIAL_NAME = re.compile(r".*\.[0-9a-f]{8}\.partial", re.DOTALL)

# How long a stopped two-way sync may take to shut down gracefully (SIGINT)
# before rclone is killed; below SyncEngine's 60 s wait for a stop.
BISYNC_STOP_GRACE = 45.0


def generate_conflict_rename(path: str, timestamp: datetime) -> str:
    """Generate a conflict rename path for keep-both resolution.

    Format: <stem>.conflict-<YYYYMMDDTHHMMSS><ext>
    For paths without an extension: <name>.conflict-<YYYYMMDDTHHMMSS>

    Examples:
        file.txt → file.conflict-20240115T143022.txt
        docs/report.pdf → docs/report.conflict-20240115T143022.pdf
        README → README.conflict-20240115T143022
    """
    p = PurePosixPath(path)
    ts = timestamp.strftime("%Y%m%dT%H%M%S")
    stem = p.stem
    ext = p.suffix  # e.g. ".txt" or "" if no extension
    parent = str(p.parent)
    renamed = f"{stem}.conflict-{ts}{ext}"
    if parent and parent != ".":
        return f"{parent}/{renamed}"
    return renamed


def _is_remote_name(name: str) -> bool:
    from backend.services.provider_registry import validate_remote_name

    return validate_remote_name(name)


def _require_remote_name(name: str) -> None:
    """Refuse a remote name rclone could read as something else (e.g. a flag)."""
    if not _is_remote_name(name):
        raise ValueError(f"Invalid remote name: {name!r}")
