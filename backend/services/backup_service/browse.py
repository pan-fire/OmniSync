"""Browsing a snapshot's files, restoring selected ones, and previewing a full restore."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.api.schemas import BackupMode, RestorePreviewResponse, RestorePreviewSide, RestoreScope, SnapshotFileEntry
from backend.db.models import BackupJob, BackupJobStatus, BackupTarget
from backend.exceptions import RcloneAuthError, RcloneError
from backend.services.notification_events import backup_restore_completed_event, backup_restore_failed_event
from backend.services.rclone import SENTINEL_FILE, redact_secrets
from backend.services.sync_engine import SyncEngine
from backend.services.backup_service import common
from backend.services.backup_service.common import RestoreRefused, SnapshotFile, logger
from backend.services.backup_service.base import BackupBase
from backend.services.backup_service.jobs import LOCK_TIMEOUT_MESSAGE, restore_error_code, set_job_end
from backend.services.backup_service import archive
from backend.services.backup_service.archive import _ARCHIVE_ERRORS, _list_archive

# Archive member lists (for browsing and restoring single files) of this many
# snapshots stay in memory: an archive on a remote is downloaded to list it,
# and snapshots never change.
ARCHIVE_LIST_CACHE = 8
# Two modification times this close count as the same (tar keeps seconds,
# some remotes milliseconds).
MODTIME_TOLERANCE = timedelta(seconds=1)
PREVIEW_EXAMPLES = 5


def _parse_modtime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return SyncEngine._parse_rclone_modtime(value)
    except ValueError:
        return None


def path_selector(paths: Iterable[str]) -> Callable[[str], bool]:
    """Whether a snapshot path is one of ``paths`` or inside one of them (a folder)."""
    chosen = {p.strip("/") for p in paths}

    def select(path: str) -> bool:
        if path in chosen:
            return True
        parts = path.split("/")
        return any("/".join(parts[:i]) in chosen for i in range(1, len(parts)))

    return select


def browse_snapshot(
    files: list[SnapshotFile], path: str = "", search: str | None = None,
) -> list[SnapshotFileEntry]:
    """The entries of one folder of a snapshot (folders first), or every file matching ``search``.

    A folder entry carries the number and total size of the files in it at
    any depth. ``search`` matches anywhere in the path, ignoring case, below
    ``path``.
    """
    prefix = path.strip("/")
    under = f"{prefix}/" if prefix else ""
    if search:
        needle = search.casefold()
        hits = sorted((f for f in files if f.path.startswith(under) and needle in f.path.casefold()),
                      key=lambda f: f.path)
        return [SnapshotFileEntry(path=f.path, name=f.path.rsplit("/", 1)[-1], is_dir=False,
                                  size=f.size, mod_time=f.mtime) for f in hits]
    folders: dict[str, list[int]] = {}
    entries: list[SnapshotFileEntry] = []
    for f in files:
        if not f.path.startswith(under):
            continue
        rest = f.path[len(under):]
        if "/" in rest:
            agg = folders.setdefault(rest.split("/", 1)[0], [0, 0])
            agg[0] += 1
            agg[1] += max(f.size or 0, 0)
        else:
            entries.append(SnapshotFileEntry(path=f.path, name=rest, is_dir=False, size=f.size, mod_time=f.mtime))
    dirs = [SnapshotFileEntry(path=f"{under}{name}", name=name, is_dir=True, size=size, file_count=count)
            for name, (count, size) in folders.items()]
    return (sorted(dirs, key=lambda e: e.name.casefold())
            + sorted(entries, key=lambda e: e.name.casefold()))


def _same_file(a: tuple[int | None, datetime | None], b: tuple[int | None, datetime | None]) -> bool:
    """Whether two (size, modification time) pairs describe the same file, as far as they tell."""
    (size_a, time_a), (size_b, time_b) = a, b
    if isinstance(size_a, int) and isinstance(size_b, int) and min(size_a, size_b) >= 0 and size_a != size_b:
        return False
    if time_a is not None and time_b is not None and abs(time_a - time_b) > MODTIME_TOLERANCE:
        return False
    return True


class BrowseMixin(BackupBase):
    """snapshot_files(), restore_files() and restore_preview()."""

    # ── Browsing and partial restores ────────────────────────────────

    async def snapshot_files(self, target: BackupTarget, snapshot_id: str) -> list[SnapshotFile]:
        """Every file of a snapshot (path, size, modification time when known).

        A mirror snapshot is read from its manifest; an archive is streamed and its member list
        read, without extracting anything. Archive lists are cached: an
        archive on a remote is downloaded to read it. Raises ValueError for
        an unknown snapshot.
        """
        root = self.storage_root(target)
        if target.backup_mode != BackupMode.MIRROR.value:
            self._check_archive_id(snapshot_id)
            key = (target.id, target.target_path, bool(target.encryption_password), snapshot_id)
            cached = self._archive_lists.get(key)
            if cached is not None:
                self._archive_lists.move_to_end(key)
                return cached
            source = self._archive_source(target, snapshot_id)
            if isinstance(source, str) and not await asyncio.to_thread(os.path.isfile, source):
                raise ValueError(f"Snapshot '{snapshot_id}' not found at the target")
            try:
                files = await asyncio.to_thread(archive._read_archive_stream, source, _list_archive)
            except RcloneError as exc:
                if "not found" in str(exc).lower():
                    raise ValueError(f"Snapshot '{snapshot_id}' not found at the target")
                raise
            except _ARCHIVE_ERRORS as exc:
                logger.warning("Reading the archive %s failed: %s", snapshot_id, redact_secrets(str(exc)))
                raise RestoreRefused(f"The archive {snapshot_id} cannot be read; it may be damaged.")
            self._archive_lists[key] = files
            while len(self._archive_lists) > ARCHIVE_LIST_CACHE:
                self._archive_lists.popitem(last=False)
            return files
        if self._parse_timestamp(snapshot_id) is None:
            raise ValueError(f"Invalid snapshot id '{snapshot_id}'")
        manifests, _ = await self._mirror_index(root)
        if snapshot_id not in manifests:
            raise ValueError(f"Snapshot '{snapshot_id}' not found at the target")
        return [
            SnapshotFile(path, f.get("size"), _parse_modtime(f.get("mtime")))
            for path, f in (await self._read_manifest_files(root, snapshot_id)).items()
            if path != SENTINEL_FILE
        ]

    async def _load_target(self, session: AsyncSession, target_id: int) -> BackupTarget:
        target = (await session.execute(
            select(BackupTarget).where(BackupTarget.id == target_id).options(selectinload(BackupTarget.profile))
        )).scalar_one_or_none()
        if target is None:
            raise ValueError(f"BackupTarget {target_id} not found")
        return target

    async def _chosen_files(self, target_id: int, snapshot_id: str, paths: list[str]) -> tuple[int, list[str]]:
        """(profile id, the snapshot files ``paths`` select); ValueError when nothing matches."""
        async with self._db() as session:
            target = await self._load_target(session, target_id)
        files = await self.snapshot_files(target, snapshot_id)
        select_path = path_selector(paths)
        chosen = [f.path for f in files if select_path(f.path) and f.path != SENTINEL_FILE]
        if not chosen:
            raise ValueError(f"None of the selected paths is in snapshot {snapshot_id}")
        return target.profile_id, chosen

    async def restore_files(
        self, target_id: int, snapshot_id: str, paths: list[str], target_dir: str | None = None,
    ) -> BackupJob:
        """Restore chosen files and folders of a snapshot, into the local folder or ``target_dir``; returns the job.

        Only the selected files are written; every file they replace goes to
        ``.omnisync-trash/pre-restore/<time>/`` in the destination, and
        nothing is deleted. Restored into the local folder of a mirror
        profile, automatic syncing is paused first (like a one-sided
        restore): the next scheduled pull would otherwise remove the
        restored files that the remote does not have. A two-way profile is
        not paused: its next sync carries them to the remote. The sync lock
        is held while the files are written (waited for, common.LOCK_TIMEOUT).
        A missing local folder is never created; ``target_dir`` is (its
        parent must exist).

        Raises ValueError for an unknown snapshot or a selection that
        matches nothing; restore failures are recorded as a failed job.
        The API starts this with start_restore_files() instead.
        """
        await self._chosen_files(target_id, snapshot_id, paths)
        job = await self._new_job(target_id, "restore", snapshot_id)
        return await self._run_job(job.id, "restore", lambda: self._restore_files(
            target_id, snapshot_id, paths, target_dir, job.id, lock_held=False,
        ))

    async def _restore_files(
        self, target_id: int, snapshot_id: str, paths: list[str], target_dir: str | None, job_id: int,
        *, lock_held: bool,
    ) -> None:
        """The work of restore_files(); ends its job record (``lock_held``: see _backup)."""
        async with self._db() as session:
            target = await self._load_target(session, target_id)
            job = await session.get(BackupJob, job_id)
            if job is None:
                raise ValueError(f"Job {job_id} not found")
            profile = target.profile
            dest = target_dir or profile.local_dir
            select_path = path_selector(paths)

            lock = self.profile_lock(profile.id)
            if not lock_held:
                try:
                    await asyncio.wait_for(lock.acquire(), timeout=common.LOCK_TIMEOUT)
                except asyncio.TimeoutError:
                    set_job_end(job, BackupJobStatus.FAILED, "sync_busy", LOCK_TIMEOUT_MESSAGE)
                    await session.commit()
                    await self._dispatcher.dispatch(backup_restore_failed_event(
                        target.name, snapshot_id, LOCK_TIMEOUT_MESSAGE,
                        profile_name=profile.name, profile_slug=profile.slug,
                    ))
                    return
            label = f"selected files to {dest}"
            # Into a mirror profile's own folder: the next scheduled pull (it
            # may be queued on the sync lock right now) would remove the
            # restored files the remote does not have. Paused before any file
            # is written, so a queued pull skips itself.
            hold = target_dir is None and profile.sync_mode != "two_way"
            try:
                files = await self.snapshot_files(target, snapshot_id)
                count = sum(1 for f in files if select_path(f.path) and f.path != SENTINEL_FILE)
                label = f"{count} selected file(s) to {dest}"
                if target_dir is None:
                    if not os.path.isdir(dest):
                        raise RestoreRefused(f"Local folder '{dest}' is missing or not mounted; nothing was restored.")
                else:
                    if not os.path.isdir(os.path.dirname(dest.rstrip("/")) or "/"):
                        raise RestoreRefused(f"The parent folder of '{dest}' does not exist; nothing was restored.")
                    await asyncio.to_thread(os.makedirs, dest, exist_ok=True)
                if hold:
                    await self._hold_for_restore(profile, self._file_restore_reason(snapshot_id, count, done=False))
                if target.backup_mode == BackupMode.MIRROR.value:
                    plan = await self._mirror_plan(self.storage_root(target), snapshot_id, select_path)
                    await self._apply_copy(plan, snapshot_id, [dest], self.transfer_args(profile))
                else:
                    await self._restore_archive_files(target, snapshot_id, select_path, dest,
                                                      self.transfer_args(profile))
                if hold:
                    await self._hold_for_restore(profile, self._file_restore_reason(snapshot_id, count, done=True))
                set_job_end(job, BackupJobStatus.COMPLETED)
                await session.commit()
                await self._dispatcher.dispatch(backup_restore_completed_event(
                    target.name, snapshot_id, label, profile_name=profile.name, profile_slug=profile.slug,
                ))
            except Exception as exc:
                details = redact_secrets(str(exc))
                logger.error("Restoring files of snapshot %s of target %d failed: %s", snapshot_id, target_id, details)
                set_job_end(job, BackupJobStatus.FAILED, restore_error_code(exc), details)
                await session.commit()
                await self._dispatcher.dispatch(backup_restore_failed_event(
                    target.name, snapshot_id, details, profile_name=profile.name, profile_slug=profile.slug,
                ))
            finally:
                if not lock_held:
                    lock.release()

    @staticmethod
    def _file_restore_reason(snapshot_id: str, count: int, *, done: bool) -> str:
        return (
            f"{count} file(s) of snapshot {snapshot_id} {'were' if done else 'are being'} restored to the local "
            "folder. Automatic syncing is paused so the next pull does not remove them; review the diff, "
            "then push or resume."
        )

    async def restore_preview(self, target_id: int, snapshot_id: str, scope: RestoreScope) -> RestorePreviewResponse:
        """What a full restore of a snapshot would change in each destination, without changing anything.

        Files are compared by size and modification time (what the restore's
        copy compares; a file whose times differ by at most a second counts
        as unchanged). ``removed`` counts files a restore moves to the
        pre-restore folder because the snapshot does not have them. Raises ValueError for an unknown snapshot and
        RuntimeError when the restore could not run (a damaged backup, a
        missing local folder).
        """
        async with self._db() as session:
            target = await self._load_target(session, target_id)
            profile = target.profile
        if target.backup_mode == BackupMode.MIRROR.value:
            plan = await self._mirror_plan(self.storage_root(target), snapshot_id)
            snap = {p: (e.get("Size"), _parse_modtime(e.get("ModTime"))) for p, e in plan.entries.items()}
            members = set(plan.files)
        else:
            snap = {f.path: (f.size, f.mtime) for f in await self.snapshot_files(target, snapshot_id)}
            members = set(snap)
        snap.pop(SENTINEL_FILE, None)

        sides: list[RestorePreviewSide] = []
        for side, dest in self._restore_sides(profile, scope):
            try:
                live_entries = await self._rclone.lsjson(dest)
            except RcloneAuthError:
                raise
            except RcloneError as exc:
                if "directory not found" not in str(exc).lower():
                    raise
                live_entries = []
            live = {e["Path"]: (e.get("Size"), _parse_modtime(e.get("ModTime")))
                    for e in live_entries if not e.get("IsDir") and e["Path"] != SENTINEL_FILE}
            added, replaced, unchanged = [], [], 0
            for path in sorted(snap):
                if path not in live:
                    added.append(path)
                elif _same_file(snap[path], live[path]):
                    unchanged += 1
                else:
                    replaced.append(path)
            removed = sorted(p for p in live if p not in members)
            sides.append(RestorePreviewSide(
                side=side, path=dest, added=len(added), replaced=len(replaced), removed=len(removed),
                unchanged=unchanged, added_examples=added[:PREVIEW_EXAMPLES],
                replaced_examples=replaced[:PREVIEW_EXAMPLES], removed_examples=removed[:PREVIEW_EXAMPLES],
            ))
        return RestorePreviewResponse(snapshot_id=snapshot_id, restore_scope=scope, sides=sides)
