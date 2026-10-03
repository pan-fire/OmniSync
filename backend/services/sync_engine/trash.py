"""Trash retention of the sync engine: pruning old .omnisync-trash/<timestamp> folders."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from datetime import datetime, timedelta, timezone

from backend.services.rclone import TRASH_DIR
from backend.services.sync_engine.common import TRASH_STAMP_FORMAT, logger, remote_join
from backend.services.sync_engine.reporting import ReportingMixin

# Trash retention: .omnisync-trash/<timestamp> folders older than this many
# days are deleted after a successful sync (at most once an hour per side).
# 0 (or less) keeps the trash forever: nothing is ever pruned.
TRASH_RETENTION_DAYS = int(os.environ.get("OMNISYNC_TRASH_DAYS", "30"))
TRASH_PRUNE_INTERVAL = 3600.0

_TRASH_STAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z")


def expired_trash_folders(names: list[str], cutoff: datetime) -> list[str]:
    """The trash folder names that are sync timestamps older than cutoff.

    Only names exactly in the engine's own timestamp format qualify, so a
    folder the user put into the trash, or a pre-restore safety copy, is
    never selected.
    """
    expired = []
    for name in names:
        if not _TRASH_STAMP_RE.fullmatch(name):
            continue
        try:
            stamp = datetime.strptime(name, TRASH_STAMP_FORMAT).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if stamp < cutoff:
            expired.append(name)
    return expired


class TrashMixin(ReportingMixin):
    """Deletes trash folders older than TRASH_RETENTION_DAYS after successful syncs."""

    # --- Trash retention ---

    async def _maybe_prune_trash(self, side: str) -> None:
        """Prune one side's trash after a successful sync, at most once an hour."""
        now = time.monotonic()
        last = self._last_trash_prune.get(side)
        if last is not None and now - last < TRASH_PRUNE_INTERVAL:
            return
        self._last_trash_prune[side] = now
        try:
            await self.prune_trash(side)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # retention must never fail a sync
            logger.warning("Trash cleanup on the %s side of '%s' failed: %s", side, self._profile.slug, exc)

    async def prune_trash(self, side: str, now: datetime | None = None) -> list[str]:
        """Delete ``.omnisync-trash/<timestamp>`` folders older than TRASH_RETENTION_DAYS.

        ``side`` is "local" or "remote". Only folders named exactly like the
        engine's own timestamps are touched; anything else in the trash
        (pre-restore copies, the user's own folders, loose files) is kept.
        With TRASH_RETENTION_DAYS 0 (or less) nothing is pruned. Returns the
        deleted folder names.
        """
        if TRASH_RETENTION_DAYS <= 0:
            return []
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=TRASH_RETENTION_DAYS)
        if side == "local":
            removed = await asyncio.to_thread(self._prune_local_trash, cutoff)
        else:
            trash = remote_join(self._profile.remote_dir, TRASH_DIR)
            removed = []
            for name in expired_trash_folders(await self._rclone.list_dirs(trash), cutoff):
                await self._rclone.delete_path(remote_join(trash, name))
                removed.append(name)
        if removed:
            logger.info("Removed %d trash folder(s) older than %d days on the %s side of '%s'",
                        len(removed), TRASH_RETENTION_DAYS, side, self._profile.slug)
        return removed

    def _prune_local_trash(self, cutoff: datetime) -> list[str]:
        """Local half of prune_trash (runs in a thread)."""
        trash = os.path.join(self._profile.local_dir, TRASH_DIR)
        # Never follow a symlinked trash folder somewhere else.
        if os.path.islink(trash) or not os.path.isdir(trash):
            return []
        with os.scandir(trash) as it:
            dirs = [e.name for e in it if e.is_dir(follow_symlinks=False)]
        removed = []
        for name in expired_trash_folders(dirs, cutoff):
            shutil.rmtree(os.path.join(trash, name))  # does not follow symlinks inside
            removed.append(name)
        return removed
