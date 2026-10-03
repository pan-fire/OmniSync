"""Running a backup: the job record, the safety refusals, snapshot naming and path conflicts."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.api.schemas import BackupMode
from backend.db.models import BackupJob, BackupJobStatus, BackupTarget, SyncProfile
from backend.exceptions import RcloneAuthError
from backend.services.notification_events import (
    auth_error_event,
    backup_completed_event,
    backup_failed_event,
    backup_target_unreachable_event,
    backup_verify_failed_event,
)
from backend.services.rclone import SENTINEL_FILE, TRASH_DIR, redact_secrets
from backend.services.sync_engine import remote_join
from backend.services.backup_service import common
from backend.services.backup_service.common import logger
from backend.services.backup_service.base import BackupBase
from backend.services.backup_service.jobs import LOCK_TIMEOUT_MESSAGE, set_job_end
from backend.services.backup_service.verify import VERIFY_FAILED

# A new snapshot must be named after the newest existing one: restores pick
# layers by name order. A clock at most this many seconds behind it (two
# backups within a second) is waited out; further behind, the backup is refused.
CLOCK_WAIT_SECONDS = 5


class BackupRefused(Exception):
    """A backup run refused before it wrote anything; the message is for the user."""


def _split_location(path: str) -> tuple[str, tuple[str, ...]]:
    """(remote name or "" for the local filesystem, normalised path parts)."""
    if ":" in path and not path.startswith(("/", ".")):
        remote, _, rest = path.partition(":")
    else:
        remote, rest = "", os.path.realpath(path)
    parts = tuple(p for p in PurePosixPath(rest.replace("\\", "/")).parts if p not in ("/", "."))
    return remote, parts


def backup_paths_overlap(a: str, b: str) -> bool:
    """Whether two backup/sync locations are the same folder or one is inside the other.

    Works for local paths (symlinks resolved) and ``remote:path`` specs.
    Two targets that overlap push each other's files into versions/, where
    retention then deletes them; a target inside a synced folder gets synced.
    """
    remote_a, parts_a = _split_location(a)
    remote_b, parts_b = _split_location(b)
    if remote_a != remote_b:
        return False
    shorter = min(len(parts_a), len(parts_b))
    return parts_a[:shorter] == parts_b[:shorter]


class ExecutionMixin(BackupBase):
    """run_backup(): liveness, the profile's sync lock, the mirror or archive run, the record."""

    # ── Execution ────────────────────────────────────────────────────

    async def run_backup(self, target_id: int) -> BackupJob:
        """Run a backup to the end and return its job (the scheduler).

        liveness -> the profile's sync lock (waited for, common.LOCK_TIMEOUT)
        -> execute -> record. RuntimeError while a backup of the target
        runs, ValueError for an unknown target. The API starts backups with
        start_backup() instead.
        """
        if target_id in self._running_targets:
            logger.warning("Backup already running for target %d, skipping", target_id)
            raise RuntimeError(f"Backup already running for target {target_id}")

        self._running_targets.add(target_id)
        try:
            job = await self._new_job(target_id, "backup")
            return await self._run_job(job.id, "backup", lambda: self._backup(target_id, job.id, lock_held=False))
        finally:
            self._running_targets.discard(target_id)

    async def _backup(self, target_id: int, job_id: int, *, lock_held: bool) -> None:
        """The work of a backup run; ends its job record.

        ``lock_held``: the caller holds the profile's sync lock (and gives
        it back); otherwise it is taken here, waiting at most
        common.LOCK_TIMEOUT.
        """
        async with self._db() as session:
            stmt = (
                select(BackupTarget)
                .where(BackupTarget.id == target_id)
                .options(selectinload(BackupTarget.profile))
            )
            result = await session.execute(stmt)
            target = result.scalar_one_or_none()
            job = await session.get(BackupJob, job_id)
            if target is None or job is None:
                raise ValueError(f"BackupTarget {target_id} or its job {job_id} not found")

            profile = target.profile
            profile_slug = profile.slug
            profile_name = profile.name

            # A target sharing its path with another target or a synced
            # folder would mix and later delete other data: refuse it.
            conflict = await self.find_path_conflict(session, target.target_path, exclude_target_id=target.id)
            if conflict is not None:
                set_job_end(job, BackupJobStatus.FAILED, "path_overlap", conflict)
                await session.commit()
                await self._dispatcher.dispatch(
                    backup_failed_event(target.name, conflict, profile_name=profile_name, profile_slug=profile_slug)
                )
                return

            # Liveness check. A remote folder that never held a backup may
            # not exist yet: the first run creates it.
            first_run = (await session.execute(
                select(BackupJob.id).where(
                    BackupJob.target_id == target_id,
                    BackupJob.direction == "backup",
                    BackupJob.status == BackupJobStatus.COMPLETED.value,
                ).limit(1)
            )).first() is None
            alive, error_msg = await self.check_liveness(target, first_run=first_run)
            if not alive:
                set_job_end(job, BackupJobStatus.SKIPPED, "target_unreachable", error_msg)
                target.last_liveness_ok = False
                target.last_liveness_error = error_msg
                target.updated_at = datetime.now(timezone.utc)
                await session.commit()

                await self._dispatcher.dispatch(
                    backup_target_unreachable_event(
                        target.name, error_msg or "unknown",
                        profile_name=profile_name, profile_slug=profile_slug,
                    )
                )
                return

            # Hold the profile's sync lock for the whole backup, so no push,
            # pull or restore runs on the same folder at the same time.
            lock = self.profile_lock(profile.id)
            if not lock_held:
                try:
                    await asyncio.wait_for(lock.acquire(), timeout=common.LOCK_TIMEOUT)
                except asyncio.TimeoutError:
                    set_job_end(job, BackupJobStatus.FAILED, "sync_busy", LOCK_TIMEOUT_MESSAGE)
                    await session.commit()
                    await self._dispatcher.dispatch(backup_failed_event(
                        target.name, LOCK_TIMEOUT_MESSAGE, profile_name=profile_name, profile_slug=profile_slug,
                    ))
                    return

            try:
                try:
                    # Checked under the lock: nothing changes the folder until the run ends.
                    refusal = await self._source_refusal(target, profile)
                    if refusal is not None:
                        raise BackupRefused(refusal)
                    timestamp = await self._new_snapshot_timestamp(target)
                    if target.backup_mode == BackupMode.MIRROR.value:
                        outcome = await self._execute_mirror(target, profile, timestamp)
                    else:
                        outcome = await self._execute_archive(target, profile, timestamp)
                except Exception as exc:
                    details = redact_secrets(str(exc))
                    if isinstance(exc, BackupRefused):
                        code = "backup_refused"
                    elif isinstance(exc, RcloneAuthError):
                        code = "auth_failed"
                    else:
                        code = "backup_failed"
                    logger.error("Backup of target %d failed: %s", target_id, details)
                    set_job_end(job, BackupJobStatus.FAILED, code, details)
                    await session.commit()

                    if isinstance(exc, RcloneAuthError):
                        # The fix is signing in again, not anything about the backup.
                        event = auth_error_event(
                            f"backup to '{target.name}': {details}",
                            profile_name=profile_name, profile_slug=profile_slug,
                        )
                    else:
                        event = backup_failed_event(
                            target.name, details, profile_name=profile_name, profile_slug=profile_slug,
                        )
                    await self._dispatcher.dispatch(event)
                    return

                size_bytes = outcome.size
                set_job_end(
                    job, BackupJobStatus.COMPLETED, size_bytes=size_bytes, snapshot_id=outcome.snapshot_id,
                    verify_status=outcome.verify_status, verify_message=outcome.verify_message,
                )
                target.last_liveness_ok = True
                target.last_liveness_error = None
                target.updated_at = datetime.now(timezone.utc)
                await session.commit()
            finally:
                if not lock_held:
                    lock.release()

            # Cleanup old snapshots
            try:
                await self.cleanup_old_snapshots(target)
            except Exception as exc:
                logger.warning("Retention cleanup failed for target %d: %s", target_id, redact_secrets(str(exc)))

            await self._dispatcher.dispatch(
                backup_completed_event(
                    target.name, target.backup_mode, size_bytes,
                    profile_name=profile_name, profile_slug=profile_slug,
                )
            )
            if outcome.verify_status == VERIFY_FAILED:
                logger.warning("Verification of backup %s of target %d failed: %s",
                               outcome.snapshot_id, target_id, outcome.verify_message)
                await self._dispatcher.dispatch(backup_verify_failed_event(
                    target.name, outcome.snapshot_id, outcome.verify_message or "",
                    profile_name=profile_name, profile_slug=profile_slug,
                ))

    async def _source_refusal(self, target: BackupTarget, profile: SyncProfile) -> str | None:
        """Why backing up the local folder now would damage the backup, or None.

        The rails of the sync engine's preflight, applied to the backup: a
        missing (unmounted) folder, an empty folder while the backup holds
        files, or a mirror backup that has the sync marker the folder lacks.
        Run anyway, a mirror would move every backed-up file into versions/
        and an archive target would fill with empty archives, and retention
        would later delete the real backups.
        """
        local_dir = profile.local_dir
        if not os.path.isdir(local_dir):
            return (f"Local folder '{local_dir}' is missing or not mounted. Nothing was backed up; "
                    "the existing backup is unchanged.")
        try:
            entries = [e for e in await asyncio.to_thread(os.listdir, local_dir) if e != TRASH_DIR]
        except OSError as exc:
            return f"Local folder '{local_dir}' cannot be read: {exc.strerror}. Nothing was backed up."
        source_items = [e for e in entries if e != SENTINEL_FILE]

        if target.backup_mode == BackupMode.MIRROR.value:
            backed_up = await self._rclone.list_top_level(remote_join(self.storage_root(target), "current"))
            if SENTINEL_FILE in backed_up and SENTINEL_FILE not in entries:
                return (f"The sync marker {SENTINEL_FILE} is in the backup but missing from the local "
                        f"folder '{local_dir}'. That usually means the folder was replaced, emptied or "
                        "is not mounted. Nothing was backed up; check the folder, then run the backup again.")
            held = [e for e in backed_up if e != SENTINEL_FILE]
            if not source_items and held:
                return (f"Refusing to back up: the local folder '{local_dir}' is empty while the backup "
                        f"holds {len(held)} item(s). Backing up an empty or unmounted folder would move "
                        "them all out of the current backup, and retention would later delete them. "
                        "Nothing was backed up.")
        elif not source_items:
            archives = await self._list_archive_snapshots(target)
            if archives:
                return (f"Refusing to back up: the local folder '{local_dir}' is empty while the target "
                        f"holds {len(archives)} archive(s). An empty or unmounted folder would only add "
                        "empty archives while retention deletes the real ones. Nothing was backed up.")
        return None

    async def _newest_snapshot_time(self, target: BackupTarget) -> datetime | None:
        """When the newest snapshot at the target was taken, by its name."""
        if target.backup_mode == BackupMode.MIRROR.value:
            manifests, versions = await self._mirror_index(self.storage_root(target))
            names = manifests + versions
            return self._parse_timestamp(max(names)) if names else None
        archives = await self._list_archive_snapshots(target)
        return archives[0].created_at if archives else None

    async def _new_snapshot_timestamp(self, target: BackupTarget) -> str:
        """The name of the next snapshot: now, and later than every snapshot at the target.

        Restores order snapshots (and a mirror's version layers) by name, so
        a snapshot named before an existing one, after the clock went back,
        would rebuild the wrong files. A clock just behind (two backups in
        one second) is waited out; one further behind refuses the backup.
        """
        newest = await self._newest_snapshot_time(target)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        if newest is not None and now <= newest:
            wait = (newest - now).total_seconds() + 1
            if wait <= CLOCK_WAIT_SECONDS:
                await asyncio.sleep(wait)
                now = datetime.now(timezone.utc).replace(microsecond=0)
            if now <= newest:
                stamp = newest.strftime("%Y-%m-%d %H:%M:%S UTC")
                raise BackupRefused(
                    f"The system clock ({now.strftime('%Y-%m-%d %H:%M:%S UTC')}) is behind the newest snapshot "
                    f"at this target ({stamp}). A snapshot named before an existing one would make restores "
                    "pick the wrong files. Nothing was backed up; correct the clock (for example with NTP), "
                    "then run the backup again."
                )
        return now.strftime("%Y-%m-%dT%H-%M-%S")

    @staticmethod
    async def find_path_conflict(
        session: AsyncSession, target_path: str, exclude_target_id: int | None = None,
    ) -> str | None:
        """Why ``target_path`` cannot be used as a backup target, or None.

        Refused: the same folder as, or a folder inside/around, another
        backup target or any profile's local or remote folder. Route-level
        validation can call this with the session it already has.
        """
        stmt = select(BackupTarget)
        if exclude_target_id is not None:
            stmt = stmt.where(BackupTarget.id != exclude_target_id)
        for other in (await session.execute(stmt)).scalars().all():
            if backup_paths_overlap(target_path, other.target_path):
                return (
                    f"Backup target path '{target_path}' overlaps backup target '{other.name}' "
                    f"({other.target_path}). Each target needs its own folder."
                )
        for profile in (await session.execute(select(SyncProfile))).scalars().all():
            for label, path in (("local", profile.local_dir), ("remote", profile.remote_dir)):
                if path and backup_paths_overlap(target_path, path):
                    return (
                        f"Backup target path '{target_path}' overlaps the {label} folder of profile "
                        f"'{profile.name}' ({path}). A backup must live outside synced folders."
                    )
        return None
