"""Conflict records of the sync engine and resolving them (mirror and two-way)."""

from __future__ import annotations

import asyncio
import os
import shutil
from datetime import datetime, timezone

from sqlalchemy import select

from backend.api.schemas import ConflictResolution, FileDiff
from backend.db.models import Conflict, SyncJob
from backend.exceptions import ConflictResolutionError, RcloneError
from backend.services.rclone import TRASH_DIR, ChangeRecorder, FileChangeRecord
from backend.services.sync_engine.common import ENGINE_STOPPED, TRASH_STAMP_FORMAT, _utc, logger, remote_join
from backend.services.sync_engine.reporting import ReportingMixin


class ConflictsMixin(ReportingMixin):
    """Keeps the Conflict rows in line with diffs and two-way runs, and resolves them."""

    # --- Conflict records ---

    async def _record_conflicts(self, files: list[FileDiff]) -> None:
        """Bring this profile's Conflict rows in line with a fresh diff.

        Every file changed on both sides has exactly one unresolved row
        (created, or refreshed with the current modification times; extra
        duplicates are closed as dismissed). An unresolved row whose file no
        longer differs at all is closed with no resolution. A failure is
        logged and never fails the diff.
        """
        conflicts = {f.path: f for f in files if f.is_conflict}
        differing = {f.path for f in files}
        try:
            async with self._db_session_factory() as session:
                # Rows a two-way sync recorded (both versions kept) are not
                # the diff's to refresh or close.
                rows = (await session.execute(
                    select(Conflict)
                    .where(
                        Conflict.profile_id == self.profile_id, Conflict.resolved == False,  # noqa: E712
                        Conflict.local_kept_as.is_(None), Conflict.remote_kept_as.is_(None),
                    )
                    .order_by(Conflict.id)
                )).scalars().all()
                seen: set[str] = set()
                for row in rows:
                    path = row.file_path
                    if path in seen:
                        row.resolved, row.resolution = True, ConflictResolution.DISMISS.value
                    elif path in conflicts:
                        row.local_modified = _utc(conflicts[path].local_mod_time)
                        row.remote_modified = _utc(conflicts[path].remote_mod_time)
                    elif path not in differing:
                        row.resolved, row.resolution = True, None
                    seen.add(path)
                added = 0
                for path, f in conflicts.items():
                    if path not in seen:
                        session.add(Conflict(
                            profile_id=self.profile_id, job_id=None, file_path=path,
                            local_modified=_utc(f.local_mod_time), remote_modified=_utc(f.remote_mod_time),
                            resolved=False,
                        ))
                        added += 1
                await session.commit()
        except Exception as exc:
            logger.warning("Could not record conflicts for '%s': %s", self._profile.slug, exc)
            return
        if added:  # only new conflicts: a repeated diff does not notify again
            await self._emit_notification("conflict_detected", count=added)

    async def _close_conflicts(self, resolved: dict[str, ConflictResolution]) -> None:
        """Mark this profile's unresolved conflicts on these paths as resolved."""
        if not resolved:
            return
        try:
            async with self._db_session_factory() as session:
                rows = (await session.execute(
                    select(Conflict).where(
                        Conflict.profile_id == self.profile_id,
                        Conflict.resolved == False,  # noqa: E712
                        Conflict.local_kept_as.is_(None), Conflict.remote_kept_as.is_(None),
                        Conflict.file_path.in_(list(resolved)),
                    )
                )).scalars().all()
                for row in rows:
                    row.resolved, row.resolution = True, resolved[row.file_path].value
                await session.commit()
        except Exception as exc:
            logger.warning("Could not close conflicts for '%s': %s", self._profile.slug, exc)

    async def resolve_conflict(self, conflict_id: int, resolution: ConflictResolution) -> int | None:
        """Act on a conflict of this profile and mark it resolved; returns the job id.

        keep_local copies the local file over the remote one, keep_remote
        the remote file over the local one; the replaced version goes to
        .omnisync-trash on that side. keep_both keeps both versions on both
        sides (see _keep_both). Runs under the profile's sync lock and can
        be stopped like any sync. Raises ConflictResolutionError when the
        action is not possible now (no file is changed then), RcloneError
        when rclone fails (the job records it).
        """
        if resolution == ConflictResolution.DISMISS:
            raise ValueError("dismiss changes no file; mark the record instead")
        return await self._exclusive(lambda: self._resolve_conflict(conflict_id, resolution))

    async def _resolve_conflict(self, conflict_id: int, resolution: ConflictResolution) -> int | None:
        config = self._profile
        async with self._db_session_factory() as session:
            conflict = await session.get(Conflict, conflict_id)
            if conflict is None or conflict.profile_id != self.profile_id:
                raise ConflictResolutionError(f"Conflict {conflict_id} does not belong to this profile.")
            if conflict.resolved:
                raise ConflictResolutionError("This conflict is already resolved.")
            path = conflict.file_path
            seen = {"local": conflict.local_modified, "remote": conflict.remote_modified}
            kept = (conflict.local_kept_as, conflict.remote_kept_as)

        if kept != (None, None):
            return await self._resolve_two_way_conflict(
                conflict_id, path, kept[0] or path, kept[1] or path, resolution,
            )

        if not os.path.isdir(config.local_dir):
            raise ConflictResolutionError(f"Local folder '{config.local_dir}' is missing or not mounted.")

        # The conflict may be old: act only on what the user saw.
        key = path.lstrip("/")
        current = {
            "local": (await self._rclone.lsjson_paths(config.local_dir, [path])).get(key),
            "remote": (await self._rclone.lsjson_paths(config.remote_dir, [path])).get(key),
        }
        rerun = "Run the diff again to refresh the conflict."
        needed = {
            ConflictResolution.KEEP_LOCAL: ("local",),
            ConflictResolution.KEEP_REMOTE: ("remote",),
            ConflictResolution.KEEP_BOTH: ("local", "remote"),
        }[resolution]
        for side in needed:
            if current[side] is None:
                raise ConflictResolutionError(f"The {side} file no longer exists. {rerun}")
        replaced = {ConflictResolution.KEEP_LOCAL: "remote", ConflictResolution.KEEP_REMOTE: "local"}.get(resolution)
        if replaced is not None and not self._unchanged_since(current[replaced], seen[replaced]):
            raise ConflictResolutionError(
                f"The {replaced} file changed since the conflict was found; not overwriting it unseen. {rerun}"
            )

        async with self._db_session_factory() as session:
            job = SyncJob(
                direction="selective", started_at=datetime.now(timezone.utc), status="running",
                files_changed=0, conflicts=1, errors=0, profile_id=self.profile_id,
            )
            session.add(job)
            await session.commit()
            job_id = job.id

        recorder = ChangeRecorder(max_rows=self.max_recorded_changes)
        extra: list[FileChangeRecord] = []
        try:
            if resolution == ConflictResolution.KEEP_BOTH:
                extra = await self._keep_both(path)
            else:
                source, dest = ((config.local_dir, config.remote_dir) if resolution == ConflictResolution.KEEP_LOCAL
                                else (config.remote_dir, config.local_dir))
                # --ignore-times: the chosen version is copied even if size
                # and time happen to match; the replaced one goes to the trash.
                recorder.side = "remote" if resolution == ConflictResolution.KEEP_LOCAL else "local"
                await self._rclone.copy_files(
                    source, dest, [path], recorder=recorder,
                    backup_dir=self._backup_dir(dest), ignore_times=True, rclone_args=self._copy_args,
                )
                if key not in await self._rclone.existing_paths(dest, [path]):
                    raise RcloneError("The file did not arrive on the other side.")
        except asyncio.CancelledError:
            reason = self._consume_stop()
            message = reason or ENGINE_STOPPED
            try:
                await self._record_error(job_id, message, 0)
                await self._finish_job(job_id, "failed", recorder=recorder, extra=extra, errors=1)
            except Exception as exc:  # never mask the stop itself
                logger.warning("Could not record stopped job %d: %s", job_id, exc)
            self._state.set_stopped(message)
            if reason is None:
                raise
            raise ConflictResolutionError(message)
        except Exception as exc:
            logger.error("Resolving the conflict on %s (%s) failed: %s", path, resolution.value, exc)
            await self._record_error(job_id, str(exc), 0)
            await self._finish_job(job_id, "failed", recorder=recorder, extra=extra, errors=1)
            raise

        await self._finish_job(job_id, "completed", recorder=recorder, extra=extra, errors=0)
        self._remove_resolved_from_diff([path])
        await self._close_conflicts({path: resolution})
        logger.info("Conflict on %s in '%s' resolved: %s (job %d)", path, config.slug, resolution.value, job_id)
        return job_id

    @classmethod
    def _unchanged_since(cls, entry: dict | None, seen: datetime | None) -> bool:
        """Whether a file is still as the conflict recorded it (by modification time).

        A file that is gone now replaces nothing; without a recorded time
        there is nothing to compare.
        """
        if entry is None or seen is None:
            return True
        try:
            current = cls._parse_rclone_modtime(entry["ModTime"])
        except (KeyError, ValueError, TypeError):
            return False
        if seen.tzinfo is None:  # SQLite returns naive UTC
            seen = seen.replace(tzinfo=timezone.utc)
        return current == seen

    def _local_mtime(self, rel: str) -> datetime | None:
        try:
            return datetime.fromtimestamp(os.stat(os.path.join(self._profile.local_dir, rel)).st_mtime, timezone.utc)
        except (OSError, ValueError):
            return None

    async def _record_two_way_conflicts(self, job_id: int, conflicts: dict[str, dict[str, str]]) -> int:
        """A Conflict row for every file a two-way run kept in two versions; returns the count.

        The rows say under which names the local and the remote version
        are now kept (both are on both sides). A diff's unresolved row for
        the same file is closed: this run settled it by keeping both.
        """
        if not conflicts:
            return 0
        found = dict(conflicts)
        conflicts.clear()  # recorded once, even if the job is finished twice
        try:
            async with self._db_session_factory() as session:
                old = (await session.execute(
                    select(Conflict).where(
                        Conflict.profile_id == self.profile_id,
                        Conflict.resolved == False,  # noqa: E712
                        Conflict.local_kept_as.is_(None), Conflict.remote_kept_as.is_(None),
                        Conflict.file_path.in_(list(found)),
                    )
                )).scalars().all()
                for row in old:
                    row.resolved, row.resolution = True, ConflictResolution.KEEP_BOTH.value
                for path, kept in found.items():
                    local_as, remote_as = kept.get("local", path), kept.get("remote", path)
                    session.add(Conflict(
                        profile_id=self.profile_id, job_id=job_id, file_path=path,
                        local_modified=self._local_mtime(local_as), remote_modified=self._local_mtime(remote_as),
                        resolved=False, local_kept_as=local_as, remote_kept_as=remote_as,
                    ))
                await session.commit()
        except Exception as exc:
            logger.warning("Could not record conflicts for '%s': %s", self._profile.slug, exc)
        return len(found)

    async def _resolve_two_way_conflict(
        self, conflict_id: int, path: str, local_as: str, remote_as: str, resolution: ConflictResolution,
    ) -> int | None:
        """Settle a conflict a two-way sync kept in two versions; returns the job id (None: no file changed).

        keep_both only closes the record. keep_local / keep_remote keep
        that version under the original name and move the other one to the
        trash, the same way on both sides (bisync copied both versions to
        both sides). Both sides must end up identical: bisync leaves a
        conflicted file out of its listings, so the next run compares the
        two sides afresh and would see a one-sided change as a new conflict.
        """
        config = self._profile
        if resolution == ConflictResolution.KEEP_BOTH:
            await self._close_conflict_ids({conflict_id: resolution})
            return None
        if not os.path.isdir(config.local_dir):
            raise ConflictResolutionError(f"Local folder '{config.local_dir}' is missing or not mounted.")
        keep, drop = (local_as, remote_as) if resolution == ConflictResolution.KEEP_LOCAL else (remote_as, local_as)
        root = os.path.realpath(config.local_dir)

        def inside(rel: str) -> str:
            full = os.path.realpath(os.path.join(root, rel))
            if os.path.commonpath([root, full]) != root or full == root:
                raise ConflictResolutionError(f"'{rel}' is not inside the local folder.")
            return full

        keep_file, drop_file, target = inside(keep), inside(drop), inside(path)
        side_name = "local" if resolution == ConflictResolution.KEEP_LOCAL else "remote"
        remote_now = await self._rclone.lsjson_paths(config.remote_dir, [keep, drop, path])
        if not os.path.isfile(keep_file) or keep.lstrip("/") not in remote_now:
            raise ConflictResolutionError(
                f"The {side_name} version ('{keep}') is no longer on both sides. Run a sync first."
            )

        async with self._db_session_factory() as session:
            job = SyncJob(
                direction="selective", started_at=datetime.now(timezone.utc), status="running",
                files_changed=0, conflicts=1, errors=0, profile_id=self.profile_id,
            )
            session.add(job)
            await session.commit()
            job_id = job.id

        stamp = datetime.now(timezone.utc).strftime(TRASH_STAMP_FORMAT)
        changes: list[FileChangeRecord] = []

        # The remote first: if it fails part-way, the local folder is untouched.
        remote_trash = remote_join(config.remote_dir, f"{TRASH_DIR}/{stamp}")
        present = set(remote_now)
        try:
            if drop != keep and drop.lstrip("/") in present:
                await self._rclone.move_file(remote_join(config.remote_dir, drop), remote_join(remote_trash, drop))
                present.discard(drop.lstrip("/"))
                changes.append(FileChangeRecord(drop, "deleted", side="remote"))
            if keep != path:
                existed = path.lstrip("/") in present
                if existed:
                    await self._rclone.move_file(remote_join(config.remote_dir, path), remote_join(remote_trash, path))
                await self._rclone.move_file(remote_join(config.remote_dir, keep), remote_join(config.remote_dir, path))
                changes.append(FileChangeRecord(keep, "deleted", side="remote"))
                changes.append(FileChangeRecord(path, "modified" if existed else "created", side="remote"))
        except RcloneError as exc:
            await self._record_error(job_id, str(exc), 0)
            await self._finish_job(job_id, "failed", extra=changes, errors=1)
            raise

        trash = os.path.join(root, TRASH_DIR, stamp)

        def to_trash(full: str, rel: str) -> None:
            dest = os.path.join(trash, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(full, dest)

        def apply() -> None:
            if drop_file != keep_file and os.path.isfile(drop_file):
                to_trash(drop_file, drop)
                changes.append(FileChangeRecord(drop, "deleted", side="local"))
            if keep_file != target:
                existed = os.path.exists(target)
                if existed:
                    to_trash(target, path)
                os.replace(keep_file, target)
                changes.append(FileChangeRecord(keep, "deleted", side="local"))
                changes.append(FileChangeRecord(path, "modified" if existed else "created", side="local"))

        try:
            await asyncio.to_thread(apply)
        except OSError as exc:
            await self._record_error(job_id, str(exc), 0)
            await self._finish_job(job_id, "failed", extra=changes, errors=1)
            raise ConflictResolutionError(f"Could not keep the {keep!r} version in the local folder: {exc}")
        await self._finish_job(job_id, "completed", extra=changes, errors=0)
        await self._close_conflict_ids({conflict_id: resolution})
        logger.info("Two-way conflict on %s in '%s' resolved: %s (job %d)", path, config.slug, resolution.value, job_id)
        return job_id

    async def _close_conflict_ids(self, resolved: dict[int, ConflictResolution]) -> None:
        async with self._db_session_factory() as session:
            for conflict_id, resolution in resolved.items():
                row = await session.get(Conflict, conflict_id)
                if row is not None:
                    row.resolved, row.resolution = True, resolution.value
            await session.commit()
