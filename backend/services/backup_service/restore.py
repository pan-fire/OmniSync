"""Full restores: the job, pausing a one-sided restore, safety copies, and rebuilding mirror snapshots."""

from __future__ import annotations

import asyncio
import os
import secrets
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.api.schemas import BackupMode, RestoreScope
from backend.db.models import BackupJob, BackupJobStatus, BackupTarget, SyncProfile
from backend.exceptions import RcloneAuthError, RcloneError
from backend.services.notification_events import backup_restore_completed_event, backup_restore_failed_event
from backend.services.rclone import SENTINEL_FILE, TRASH_DIR, redact_secrets
from backend.services.sync_engine import remote_join, store_pause_reason
from backend.services.backup_service import common
from backend.services.backup_service.common import RestoreRefused, logger
from backend.services.backup_service.jobs import LOCK_TIMEOUT_MESSAGE, restore_error_code, set_job_end
from backend.services.backup_service.base import BackupBase

# Restore safety copies: <destination>/.omnisync-trash/pre-restore/<timestamp>/
PRE_RESTORE_DIR = "pre-restore"


@dataclass
class _MirrorPlan:
    """Where each file of a mirror snapshot comes from.

    ``files`` is the snapshot's whole tree, from its manifest (the restore
    removes everything else). ``entries`` holds each file's listing in the
    layer it is taken from; ``layers`` the files to copy from each layer.
    """
    files: dict[str, int | None]
    entries: dict[str, dict] = field(default_factory=dict)
    layers: dict[str, list[str]] = field(default_factory=dict)


class RestoreMixin(BackupBase):
    """restore() of a whole snapshot, and the plan that rebuilds a mirror snapshot."""

    # ── Restore ──────────────────────────────────────────────────────

    async def restore(
        self, target_id: int, snapshot_id: str, scope: RestoreScope
    ) -> BackupJob:
        """Restore from a snapshot to the end and return its job: lock, (pause), restore, record.

        Waits for the profile's sync lock (common.LOCK_TIMEOUT); a timeout
        fails the job and is notified. ValueError for an unknown target.
        The API starts restores with start_restore() instead.
        """
        job = await self._new_job(target_id, "restore", snapshot_id)
        return await self._run_job(
            job.id, "restore", lambda: self._restore(target_id, snapshot_id, scope, job.id, lock_held=False),
        )

    async def _restore(
        self, target_id: int, snapshot_id: str, scope: RestoreScope, job_id: int, *, lock_held: bool,
    ) -> None:
        """The work of a full restore; ends its job record (``lock_held``: see _backup)."""
        async with self._db() as session:
            stmt = (
                select(BackupTarget)
                .where(BackupTarget.id == target_id)
                .options(selectinload(BackupTarget.profile))
            )
            target = (await session.execute(stmt)).scalar_one_or_none()
            job = await session.get(BackupJob, job_id)
            if target is None or job is None:
                raise ValueError(f"BackupTarget {target_id} or its job {job_id} not found")
            profile = target.profile

            # Hold the profile's sync lock for the whole restore: a scheduled
            # pull or a watcher push must not run halfway through it.
            lock = self.profile_lock(profile.id)
            if not lock_held:
                try:
                    await asyncio.wait_for(lock.acquire(), timeout=common.LOCK_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.error("Restore of snapshot %s of target %d: %s", snapshot_id, target_id,
                                 LOCK_TIMEOUT_MESSAGE)
                    set_job_end(job, BackupJobStatus.FAILED, "sync_busy", LOCK_TIMEOUT_MESSAGE)
                    await session.commit()
                    await self._dispatcher.dispatch(backup_restore_failed_event(
                        target.name, snapshot_id, LOCK_TIMEOUT_MESSAGE,
                        profile_name=profile.name, profile_slug=profile.slug,
                    ))
                    return

            try:
                if scope != RestoreScope.BOTH:
                    await self._hold_for_one_sided_restore(profile, snapshot_id, scope)
                if target.backup_mode == BackupMode.MIRROR.value:
                    await self._restore_mirror(target, profile, snapshot_id, scope)
                else:
                    await self._restore_archive(target, profile, snapshot_id, scope)
                if scope != RestoreScope.BOTH:
                    await self._hold_for_one_sided_restore(profile, snapshot_id, scope, done=True)

                set_job_end(job, BackupJobStatus.COMPLETED)
                await session.commit()

                await self._dispatcher.dispatch(
                    backup_restore_completed_event(
                        target.name, snapshot_id, scope.value,
                        profile_name=profile.name, profile_slug=profile.slug,
                    )
                )

            except Exception as exc:
                details = redact_secrets(str(exc))
                logger.error("Restore of snapshot %s of target %d failed: %s", snapshot_id, target_id, details)
                set_job_end(job, BackupJobStatus.FAILED, restore_error_code(exc), details)
                await session.commit()

                await self._dispatcher.dispatch(
                    backup_restore_failed_event(
                        target.name, snapshot_id, details,
                        profile_name=profile.name, profile_slug=profile.slug,
                    )
                )

            finally:
                if not lock_held:
                    lock.release()

    async def _hold_for_one_sided_restore(
        self, profile: SyncProfile, snapshot_id: str, scope: RestoreScope, done: bool = False,
    ) -> None:
        """Hold automatic syncing for a restore that changes only one side.

        Otherwise the next scheduled pull reverts a local restore, and the
        watcher spreads it to the remote before the user has looked. Set
        before the restore starts (syncs queued on the sync lock then skip
        themselves) and stored with the profile, so it also holds after a
        restart or a profile edit, until the user resumes or syncs. A
        failed restore keeps it: the folder may be partly restored.
        """
        side = "local folder" if scope == RestoreScope.LOCAL_ONLY else "remote folder"
        await self._hold_for_restore(profile, (
            f"Snapshot {snapshot_id} {'was' if done else 'is being'} restored to the {side} only. "
            "Automatic syncing is paused so it is not undone; review the diff, then sync or resume."
        ))

    async def _hold_for_restore(self, profile: SyncProfile, reason: str) -> None:
        """Pause automatic syncing of the profile for a restore (see _hold_for_one_sided_restore)."""
        try:
            engine = self._manager.get_engine(profile.slug)
        except Exception:
            engine = None  # profile not running: stored for when it starts
        if engine is not None:
            await engine.hold(reason)
        else:
            await store_pause_reason(self._db, profile.id, reason)

    @staticmethod
    def _restore_sides(profile: SyncProfile, scope: RestoreScope) -> list[tuple[Literal["local", "remote"], str]]:
        """(side, folder) a restore writes to. A missing local folder is never created."""
        sides: list[tuple[Literal["local", "remote"], str]] = []
        if scope in (RestoreScope.LOCAL_ONLY, RestoreScope.BOTH):
            if not os.path.isdir(profile.local_dir):
                raise RestoreRefused(
                    f"Local folder '{profile.local_dir}' is missing or not mounted; nothing was restored."
                )
            sides.append(("local", profile.local_dir))
        if scope in (RestoreScope.REMOTE_ONLY, RestoreScope.BOTH):
            sides.append(("remote", profile.remote_dir))
        return sides

    @classmethod
    def _restore_destinations(cls, profile: SyncProfile, scope: RestoreScope) -> list[str]:
        """The folders a restore writes to. A missing local folder is never created."""
        return [path for _, path in cls._restore_sides(profile, scope)]

    @staticmethod
    def pre_restore_dir(dest: str) -> str:
        """A new, timestamped safety folder for the files a restore replaces or removes.

        It lives inside the destination's trash folder: on the same remote as
        the destination (rclone's --backup-dir must be), excluded from every
        sync and diff, and never pruned by trash retention. The random suffix
        keeps two restores in the same second apart, so no safety copy is
        ever overwritten.
        """
        # Microseconds keep the folders in restore order when two restores
        # fall in the same second; the suffix keeps them unique.
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S.%fZ")
        rel = f"{TRASH_DIR}/{PRE_RESTORE_DIR}/{stamp}-{secrets.token_hex(3)}"
        return f"{dest}{rel}" if dest.endswith(":") else f"{dest.rstrip('/')}/{rel}"

    async def _restore_mirror(
        self,
        target: BackupTarget,
        profile: SyncProfile,
        snapshot_id: str,
        scope: RestoreScope,
    ) -> None:
        """Restore a mirror snapshot exactly.

        The destination ends up with the files of the tree right after the
        backup at T, from its manifest, see _apply_exact.
        """
        plan = await self._mirror_plan(self.storage_root(target), snapshot_id)
        dests = self._restore_destinations(profile, scope)
        await self._apply_exact(plan, snapshot_id, dests, self.transfer_args(profile))

    async def _mirror_plan(
        self, root: str, snapshot_id: str, select: Callable[[str], bool] | None = None,
    ) -> _MirrorPlan:
        """Locate every file of a mirror snapshot (only the selected ones, with ``select``).

        Raises ValueError for an unknown or malformed snapshot and
        RuntimeError for a backup that cannot rebuild it; nothing is written.
        """
        if self._parse_timestamp(snapshot_id) is None:
            raise ValueError(f"Invalid snapshot id '{snapshot_id}'")
        manifests, versions = await self._mirror_index(root)
        if snapshot_id in manifests:
            files = await self._read_manifest(root, snapshot_id)
            # Oldest first: the first backup after T that replaced or deleted
            # a file moved its T-state into versions/; untouched files are
            # still in current/.
            layers = [remote_join(root, f"versions/{v}") for v in versions if v > snapshot_id]
            return await self._plan_exact(snapshot_id, files, layers + [remote_join(root, "current")], select)
        raise ValueError(f"Snapshot '{snapshot_id}' not found at the target")

    async def _plan_exact(
        self,
        snapshot_id: str,
        files: dict[str, int | None],
        layers: list[str],
        select: Callable[[str], bool] | None,
    ) -> _MirrorPlan:
        """Find each file of ``files`` in the first layer that has it, size-checked.

        Everything is located before any destination is touched, so a
        damaged backup fails the restore without changing anything.
        """
        remaining = {p: size for p, size in files.items()
                     if p != SENTINEL_FILE and (select is None or select(p))}
        plan = _MirrorPlan(files=files)
        for layer in layers:
            if not remaining:
                break
            found = await self._rclone.lsjson_paths(layer, list(remaining))
            paths = [p for p in found if p in remaining]
            for path in paths:
                expected, size = remaining.pop(path), found[path].get("Size")
                # -1: size unknown to the backend
                if isinstance(expected, int) and isinstance(size, int) and min(expected, size) >= 0 \
                        and size != expected:
                    raise RestoreRefused(
                        f"Snapshot {snapshot_id} cannot be rebuilt: '{path}' in the backup has another size "
                        "than when it was backed up. Nothing was restored."
                    )
                plan.entries[path] = found[path]
            if paths:
                plan.layers[layer] = paths
        if remaining:
            example = sorted(remaining)[0]
            raise RestoreRefused(
                f"Snapshot {snapshot_id} cannot be rebuilt: {len(remaining)} file(s) are missing from the "
                f"backup, e.g. '{example}'. Nothing was restored."
            )
        return plan

    async def _apply_exact(
        self, plan: _MirrorPlan, snapshot_id: str, dests: list[str], rclone_args: list[str] | None = None,
    ) -> None:
        """Make each destination hold exactly the snapshot's files.

        Files the snapshot does not have are moved, and files it replaces
        are copied, into one timestamped pre-restore safety folder: nothing
        is deleted. The trash folder and the sync marker are left alone.
        Empty folders are not part of a snapshot and are left in place.
        """
        restored = sum(len(p) for p in plan.layers.values())
        for dest in dests:
            safety = self.pre_restore_dir(dest)
            try:
                live = await self._rclone.lsjson(dest)
            except RcloneAuthError:
                raise
            except RcloneError as exc:
                if "directory not found" not in str(exc).lower():
                    raise
                live = []
            extra = [e["Path"] for e in live
                     if not e.get("IsDir") and e["Path"] not in plan.files and e["Path"] != SENTINEL_FILE]
            # Extras first: a file where the snapshot has a folder must go
            # before the folder's files can be copied in.
            if extra:
                await self._move_files(dest, safety, extra)
            for layer, paths in plan.layers.items():
                await self._rclone.copy_files(layer, dest, paths, backup_dir=safety, rclone_args=rclone_args)
            logger.info("Restored snapshot %s (%d file(s)) to %s; %d file(s) not in it and every "
                        "replaced file kept in %s", snapshot_id, restored, dest, len(extra), safety)

    async def _apply_copy(
        self, plan: _MirrorPlan, snapshot_id: str, dests: list[str], rclone_args: list[str] | None = None,
    ) -> None:
        """Copy the planned files into each destination; replaced files go to a pre-restore folder.

        Nothing else in the destination changes and nothing is deleted.
        """
        restored = sum(len(p) for p in plan.layers.values())
        for dest in dests:
            safety = self.pre_restore_dir(dest)
            for layer, paths in plan.layers.items():
                await self._rclone.copy_files(layer, dest, paths, backup_dir=safety, rclone_args=rclone_args)
            logger.info("Restored %d file(s) of snapshot %s to %s (replaced files kept in %s)",
                        restored, snapshot_id, dest, safety)

    async def _move_files(self, root: str, dest: str, paths: list[str]) -> None:
        """Move exactly these files (relative to root) to dest, keeping their paths."""
        list_file = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        try:
            for path in paths:
                list_file.write(path.lstrip("/") + "\n")
            list_file.close()
            await self._rclone._run(
                ["move", "--files-from-raw", list_file.name],
                use_config_args=False, positional=[root, dest], no_timeout=True,
            )
        finally:
            os.unlink(list_file.name)
