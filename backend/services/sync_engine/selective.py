"""Per-file actions on the cached diff: push, pull, skip, manual and keep both."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from sqlalchemy import select

from backend.api.errors import SEE_LOG
from backend.api.schemas import (
    ChangeCategory,
    ConflictResolution,
    FileAction,
    FileError,
    JobStatus,
    SelectiveSyncItem,
    SelectiveSyncResponse,
)
from backend.db.models import ManualFlag, SyncJob
from backend.exceptions import InvalidFilePathsError, NoCachedDiffError
from backend.services.rclone import ChangeRecorder, FileChangeRecord, generate_conflict_rename, redact_secrets
from backend.services.sync_engine.common import ENGINE_STOPPED, logger, remote_join
from backend.services.sync_engine.reporting import ReportingMixin


# Per-file results kept in memory for GET .../sync/selective/{job_id}.
SELECTIVE_RESULTS_KEPT = 32

# What a file's error says when its copy failed; rclone's text goes to the log.
COPY_FAILED = f"Copying the file failed. {SEE_LOG}"
KEEP_BOTH_FAILED = f"Keeping both versions failed. {SEE_LOG}"
# A per-file action on a name that is not valid UTF-8: rclone lists it with
# U+FFFD in place of the bad bytes, so the diff's path names no file.
NAME_NOT_UTF8 = ("The file name is not valid UTF-8 (shown with \ufffd in place of the bad bytes), so it "
                 "cannot be copied on its own. A push, pull or two-way sync of the whole folder carries it; "
                 "or rename the file.")
# A pull onto a local symbolic link would replace the link.
LOCAL_SYMLINK = ("A symbolic link of that name is in the local folder; OmniSync does not replace it. "
                 "Rename or remove the link to pull the file.")
# The job's error when launch_selective()'s run raised unexpectedly.
SELECTIVE_CRASHED = f"The per-file actions failed unexpectedly. {SEE_LOG}"


class SelectiveMixin(ReportingMixin):
    """selective_sync() and the checks that keep it from acting on a stale diff."""

    async def selective_sync(
        self, items: list[SelectiveSyncItem]
    ) -> SelectiveSyncResponse:
        """Execute per-file actions. Groups by action type, batches
        push/pull into single rclone calls. Records job in DB."""
        return await self._exclusive(lambda: self._selective_sync(items))

    async def launch_selective(self, items: list[SelectiveSyncItem]) -> SelectiveSyncResponse:
        """Start per-file actions in the background; returns the running result with its job id.

        For the API, whose request must not wait for the copies. Refused
        before anything runs: SyncBusyError while another operation holds or
        waits for the sync lock, NoCachedDiffError and InvalidFilePathsError
        as in selective_sync(). The run records its job, keeps its result
        for selective_result(), and is stopped like any other operation.
        An unexpected exception is logged and fails the job.
        """
        self._check_not_busy()
        self._check_selective(items)
        lock = self._sync_lock
        await lock.acquire()  # free: taken without suspending
        try:
            job_id = await self._create_selective_job()
        except BaseException:
            lock.release()
            raise
        running = SelectiveSyncResponse(job_id=job_id, status=JobStatus.RUNNING, total=len(items),
                                        succeeded=0, failed=0)
        self._remember_selective(running)
        self._start_launch(lambda: self._run_selective_launched(job_id, items), job_id=job_id)
        return running

    def selective_result(self, job_id: int) -> SelectiveSyncResponse | None:
        """The result of a recent per-file run (running while it runs), or None if not kept."""
        return self._selective_results.get(job_id)

    def _remember_selective(self, result: SelectiveSyncResponse) -> None:
        self._selective_results[result.job_id] = result
        self._selective_results.move_to_end(result.job_id)
        while len(self._selective_results) > SELECTIVE_RESULTS_KEPT:
            self._selective_results.popitem(last=False)

    async def _run_selective_launched(self, job_id: int, items: list[SelectiveSyncItem]) -> int | None:
        """The body of a launch_selective(): the actions, and a record of any crash."""
        try:
            await self._run_current(lambda: self._selective_sync(items, job_id))
        except Exception:
            logger.exception("Per-file actions for '%s' failed unexpectedly", self._profile.slug)
            self._remember_selective(SelectiveSyncResponse(
                job_id=job_id, status=JobStatus.FAILED, total=len(items), succeeded=0, failed=len(items),
                errors=[FileError(path=i.path, error=SELECTIVE_CRASHED) for i in items],
            ))
            try:
                await self._record_error(job_id, SELECTIVE_CRASHED, 0)
                await self._finish_job(job_id, "failed", errors=len(items))
            except Exception:
                logger.exception("Could not record the failed job %d", job_id)
        return job_id

    def _check_selective(self, items: list[SelectiveSyncItem]) -> None:
        """Raise unless every item names a file of the cached diff."""
        if self._state.cached_diff is None:
            raise NoCachedDiffError("No diff results cached. Run POST /sync/diff first.")
        cached_paths = self._state.get_cached_paths()
        invalid_paths = [item.path for item in items if item.path not in cached_paths]
        if invalid_paths:
            raise InvalidFilePathsError(invalid_paths)

    async def _create_selective_job(self) -> int:
        async with self._db_session_factory() as session:
            job = SyncJob(
                direction="selective",
                started_at=datetime.now(timezone.utc),
                status="running",
                files_changed=0,
                conflicts=0,
                errors=0,
                profile_id=self._profile.profile_id,
            )
            session.add(job)
            await session.commit()
            return job.id

    async def _selective_sync(
        self, items: list[SelectiveSyncItem], job_id: int | None = None,
    ) -> SelectiveSyncResponse:
        # 1./2. The cached diff exists and holds every path
        self._check_selective(items)

        config = self._profile

        # 3. The SyncJob record (launch_selective() created it already)
        if job_id is None:
            job_id = await self._create_selective_job()

        # 4. Group items by action
        push_paths: list[str] = []
        pull_paths: list[str] = []
        skip_paths: list[str] = []
        manual_paths: list[str] = []
        keep_both_paths: list[str] = []

        for item in items:
            if item.action == FileAction.PUSH:
                push_paths.append(item.path)
            elif item.action == FileAction.PULL:
                pull_paths.append(item.path)
            elif item.action == FileAction.SKIP:
                skip_paths.append(item.path)
            elif item.action == FileAction.MANUAL:
                manual_paths.append(item.path)
            elif item.action == FileAction.KEEP_BOTH:
                keep_both_paths.append(item.path)

        succeeded = 0
        failed = 0
        errors: list[FileError] = []
        finished: set[str] = set()  # paths whose action has run (either way)
        # What the copies actually changed, for the job's FileChange rows
        recorder = ChangeRecorder(max_rows=self.max_recorded_changes)
        extra_changes: list[FileChangeRecord] = []
        # path -> how the action settled a conflict on it (if there is one)
        resolved: dict[str, ConflictResolution] = {}

        def fail(paths: list[str], message: str) -> None:
            nonlocal failed
            for p in paths:
                failed += 1
                finished.add(p)
                errors.append(FileError(path=p, error=message))

        # Per-file actions write into the local folder, which is never
        # created: a missing one is usually an unmounted drive.
        if not os.path.isdir(config.local_dir):
            message = f"Local folder '{config.local_dir}' is missing or not mounted."
            fail(push_paths + pull_paths + keep_both_paths, message)
            push_paths, pull_paths, keep_both_paths = [], [], []

        try:
            # 5./6. Push and pull items: re-check each file against the diff
            # first (it may be hours old), then copy, then confirm arrival.
            for paths, action, source, dest in (
                (push_paths, FileAction.PUSH, config.local_dir, config.remote_dir),
                (pull_paths, FileAction.PULL, config.remote_dir, config.local_dir),
            ):
                if not paths:
                    continue
                try:
                    stale = await self._stale_paths(paths, action)
                    for p, reason in stale.items():
                        fail([p], reason)
                    ready = [p for p in paths if p not in stale]
                    if not ready:
                        continue
                    recorder.side = "remote" if action == FileAction.PUSH else "local"
                    await self._rclone.copy_files(
                        source, dest, ready, recorder=recorder, backup_dir=self._backup_dir(dest),
                        rclone_args=self._copy_args,
                    )
                    # copy --files-from-raw skips missing files silently: confirm arrival
                    arrived = await self._rclone.existing_paths(dest, ready)
                    done = [p for p in ready if p.lstrip("/") in arrived]
                    fail([p for p in ready if p.lstrip("/") not in arrived],
                         "File not found on the source side; nothing was copied.")
                    self._remove_resolved_from_diff(done)
                    keep = ConflictResolution.KEEP_LOCAL if action == FileAction.PUSH else ConflictResolution.KEEP_REMOTE
                    resolved.update(dict.fromkeys(done, keep))
                    finished.update(done)
                    succeeded += len(done)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error("Selective %s failed: %s", action.value, redact_secrets(str(exc)))
                    fail([p for p in paths if p not in finished], COPY_FAILED)

            # 7. Handle skip items: out of the cached diff, and remembered so
            #    later diffs do not count them as pending again.
            if skip_paths:
                self._skipped.update(skip_paths)
                self._remove_resolved_from_diff(skip_paths)
                finished.update(skip_paths)
                succeeded += len(skip_paths)
        except asyncio.CancelledError:
            return await self._selective_stopped(job_id, items, succeeded, errors, finished, recorder, extra_changes)

        # 8. Handle manual items — persist ManualFlag to DB
        if manual_paths:
            async with self._db_session_factory() as session:
                for p in manual_paths:
                    # Check if flag already exists
                    existing = await session.execute(
                        select(ManualFlag).where(
                            ManualFlag.profile_id == self.profile_id,
                            ManualFlag.file_path == p,
                        )
                    )
                    if existing.scalar_one_or_none() is None:
                        flag = ManualFlag(
                            profile_id=self.profile_id,
                            file_path=p,
                            created_at=datetime.now(timezone.utc),
                        )
                        session.add(flag)
                await session.commit()

            # Update cached diff entries to set manual_flag=True
            diff = self._state.cached_diff
            if diff is not None:
                manual_set = set(manual_paths)
                for f in diff.files:
                    if f.path in manual_set:
                        f.manual_flag = True
                # Recount manual in summary
                diff.summary.manual = sum(1 for f in diff.files if f.manual_flag)

            succeeded += len(manual_paths)

        # Manually flagged files never count as pending (they never block resume).
        finished.update(manual_paths)
        if self._state.cached_diff is not None:
            self._state.pending_changes = self._pending_count(self._state.cached_diff.files)

        # 9. Handle keep_both items: both versions end up on both sides —
        #    the local version under its own name, the remote version as
        #    <name>.conflict-<timestamp>. Each step copies; nothing is moved
        #    or deleted, so a failure part-way loses neither version.
        try:
            try:
                stale = await self._stale_paths(keep_both_paths, FileAction.KEEP_BOTH)
            except Exception as exc:
                logger.error("Checking the files to keep both of failed: %s", redact_secrets(str(exc)))
                stale = dict.fromkeys(keep_both_paths, KEEP_BOTH_FAILED)
            for p in keep_both_paths:
                if p in stale:
                    fail([p], stale[p])
                    continue
                try:
                    extra_changes += await self._keep_both(p)
                    self._remove_resolved_from_diff([p])
                    resolved[p] = ConflictResolution.KEEP_BOTH
                    finished.add(p)
                    succeeded += 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error("Keep-both failed for %s: %s", p, redact_secrets(str(exc)))
                    fail([p], KEEP_BOTH_FAILED)
        except asyncio.CancelledError:
            return await self._selective_stopped(job_id, items, succeeded, errors, finished, recorder, extra_changes)

        # Conflicts these actions settled are closed in the conflict list.
        await self._close_conflicts(resolved)

        # 10. Update SyncJob with results
        total = len(items)
        await self._finish_job(job_id, "completed" if failed == 0 else "failed",
                               recorder=recorder, extra=extra_changes, errors=failed)

        # 11. Return SelectiveSyncResponse
        result = SelectiveSyncResponse(
            job_id=job_id,
            status=JobStatus.COMPLETED if failed == 0 else JobStatus.FAILED,
            total=total,
            succeeded=succeeded,
            failed=failed,
            errors=errors,
        )
        self._remember_selective(result)
        return result

    async def _keep_both(self, path: str) -> list[FileChangeRecord]:
        """Keep both versions of a file on both sides; returns what changed.

        The local version stays under its own name, the remote version is
        kept as <name>.conflict-<timestamp>. Each step copies; nothing is
        moved or deleted, so a failure part-way loses neither version.
        """
        config = self._profile
        renamed = generate_conflict_rename(path, datetime.now(timezone.utc))
        local_copy = os.path.join(config.local_dir, renamed)
        # 1. remote version -> local conflict copy
        args = self._copy_args
        await self._rclone.copyto(remote_join(config.remote_dir, path), local_copy, rclone_args=args)
        # 2. local version -> remote (the remote version is now safe in the copy)
        await self._rclone.copyto(os.path.join(config.local_dir, path), remote_join(config.remote_dir, path),
                                  rclone_args=args)
        # 3. conflict copy -> remote
        await self._rclone.copyto(local_copy, remote_join(config.remote_dir, renamed), rclone_args=args)
        return [FileChangeRecord(renamed, "created"), FileChangeRecord(path, "modified")]

    async def _selective_stopped(
        self, job_id: int, items: list[SelectiveSyncItem], succeeded: int, errors: list[FileError],
        finished: set[str], recorder: ChangeRecorder, extra: list[FileChangeRecord],
    ) -> SelectiveSyncResponse:
        """Record a per-file run that was stopped; re-raise unless _cancel_current() stopped it."""
        reason = self._consume_stop()
        message = reason or ENGINE_STOPPED
        unfinished = [i.path for i in items if i.path not in finished]
        errors = errors + [FileError(path=p, error=message) for p in unfinished]
        try:
            await self._record_error(job_id, message, 0)
            await self._finish_job(job_id, "failed", recorder=recorder, extra=extra,
                                   errors=len(items) - succeeded)
        except Exception as exc:  # never mask the stop itself
            logger.warning("Could not record stopped job %d: %s", job_id, exc)
        self._state.set_stopped(message)
        result = SelectiveSyncResponse(job_id=job_id, status=JobStatus.FAILED, total=len(items),
                                       succeeded=succeeded, failed=len(items) - succeeded, errors=errors)
        self._remember_selective(result)
        if reason is None:
            raise asyncio.CancelledError
        return result

    async def _stale_paths(self, paths: list[str], action: FileAction) -> dict[str, str]:
        """Re-check a cached diff before acting on it: path -> why it is unsafe now.

        The diff may be hours old. A push/pull needs the source file to
        still exist and the destination to be as the diff saw it (absent,
        or the same size and modification time), so nothing is overwritten
        that the user has not seen. Keep-both needs both files to exist. A
        pull never replaces a local symbolic link, and a name rclone could
        not read (not UTF-8) is named as the reason.
        """
        if not paths:
            return {}
        diff = self._state.cached_diff
        by_path = {f.path: f for f in diff.files} if diff is not None else {}
        local = await self._rclone.lsjson_paths(self._profile.local_dir, paths)
        remote = await self._rclone.lsjson_paths(self._profile.remote_dir, paths)
        rerun = "Run the diff again."
        stale: dict[str, str] = {}
        for p in paths:
            f = by_path.get(p)
            key = p.lstrip("/")
            loc, rem = local.get(key), remote.get(key)
            if action == FileAction.KEEP_BOTH:
                if loc is None or rem is None:
                    side = "local" if loc is None else "remote"
                    stale[p] = f"The {side} file no longer exists. {rerun}"
                continue
            if action == FileAction.PUSH:
                src, dst, src_side, dst_side = loc, rem, "local", "remote"
                expected = f is None or f.category != ChangeCategory.LOCAL_ONLY
                size = f.remote_size if f else None
                mod = f.remote_mod_time if f else None
            else:
                src, dst, src_side, dst_side = rem, loc, "remote", "local"
                expected = f is None or f.category != ChangeCategory.REMOTE_ONLY
                size = f.local_size if f else None
                mod = f.local_mod_time if f else None
            if action == FileAction.PULL and os.path.islink(os.path.join(self._profile.local_dir, key)):
                stale[p] = LOCAL_SYMLINK
            elif src is None and "\ufffd" in p:
                stale[p] = NAME_NOT_UTF8
            elif src is None:
                stale[p] = f"The {src_side} file does not exist (any more). {rerun}"
            elif not self._as_diffed(dst, expected, size, mod):
                stale[p] = (f"The {dst_side} file changed since the diff; not overwriting it unseen. {rerun}")
        return stale

    @classmethod
    def _as_diffed(cls, entry: dict | None, expected: bool, size: int | None, mod: datetime | None) -> bool:
        """Whether a destination file is still as the diff saw it."""
        if not expected:
            return entry is None
        if entry is None:
            return False
        if size is not None and entry.get("Size") != size:
            return False
        if mod is not None:
            try:
                return cls._parse_rclone_modtime(entry["ModTime"]) == mod
            except (KeyError, ValueError, TypeError):
                return False
        return True
