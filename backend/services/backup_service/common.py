"""Constants and types shared by the backup modules."""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from backend.api.errors import SEE_LOG as SEE_LOG  # re-exported for the backup modules
from backend.services.rclone import TRASH_FILTER
from backend.services.sync_engine import BISYNC_PARTIAL_FILTER

# The whole package logs under the name the single backup_service module had.
logger = logging.getLogger("backend.services.backup_service")

# Read as common.LOCK_TIMEOUT by the modules that wait for the lock, so
# tests can patch it here.
# Seconds a backup waits for the running sync of its profile to finish.
LOCK_TIMEOUT = 300

# Never backed up: the trash folder (at the top of the folder, like
# TRASH_FILTER) and rclone's in-progress files (<name>.<8 hex>.partial).
BACKUP_FILTER = [TRASH_FILTER, BISYNC_PARTIAL_FILTER]


class RestoreRefused(RuntimeError):
    """A restore that cannot run as asked; the message is OmniSync's own, for the user."""


@dataclass(frozen=True)
class SnapshotFile:
    """One file of a snapshot."""
    path: str
    size: int | None
    mtime: datetime | None


@dataclass
class _BackupOutcome:
    size: int
    snapshot_id: str
    verify_status: str | None = None
    verify_message: str | None = None


@asynccontextmanager
async def scratch_dir(prefix: str) -> AsyncIterator[str]:
    """A private temporary folder, removed when the block ends: normally, on an error or on cancellation.

    The folder is created synchronously (mkdtemp is quick), so no
    cancellation can land between creating it and owning it. The removal
    runs in a thread; if the task is cancelled again while it waits, the
    removal finishes on the event loop instead of leaving the folder behind.
    """
    path = tempfile.mkdtemp(prefix=prefix)
    try:
        yield path
    finally:
        try:
            await asyncio.to_thread(shutil.rmtree, path, True)
        except asyncio.CancelledError:
            shutil.rmtree(path, ignore_errors=True)
            raise
