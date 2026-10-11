"""Constants and types shared by the backup modules."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from backend.api.errors import SEE_LOG as SEE_LOG  # re-exported for the backup modules
from backend.services.local_paths import links_on_paths
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


def refuse_linked_paths(dest: str, paths: list[str], safety: str | None = None) -> None:
    """RestoreRefused, before anything is written, when a path the restore writes in a local
    ``dest`` leads through a symbolic link there (or is one).

    rclone follows a link on the way when it writes a path: a file
    ``ld/f`` of the snapshot would land in the folder the link ``ld``
    points to, outside ``dest``, and a link of the file's own name would
    be replaced. The safety folder (``safety``) is checked too. Remote
    destinations are not local paths and are not checked. Called right
    before each copy, after the slower listings (see local_paths.py).
    """
    if not os.path.isabs(dest):
        return
    rels = [p.lstrip("/") for p in paths]
    if safety is not None and safety.startswith(dest.rstrip("/") + "/"):
        rels.append(safety[len(dest.rstrip("/")) + 1:])
    linked = links_on_paths(dest, rels)
    if linked:
        shown = ", ".join(f"'{p}'" for p in linked[:5]) + (", ..." if len(linked) > 5 else "")
        raise RestoreRefused(
            f"{shown} in '{dest}' {'is a symbolic link' if len(linked) == 1 else 'are symbolic links'}: a "
            "restore never writes through a link or replaces one (the files below a link to a folder would "
            "land in the folder it points to). Move the link(s) away, then restore again; nothing was restored."
        )
