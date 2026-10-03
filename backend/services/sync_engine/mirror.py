"""Mirror syncs (push and pull) of the sync engine."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from backend.api.schemas import DiffResponse, SyncDirection, SyncState
from backend.db.models import SyncJob
from backend.exceptions import RcloneAuthError, RcloneError, RcloneRateLimitError
from backend.services.rclone import TRASH_DIR, ChangeRecorder, without_flag
from backend.services.sync_engine import common
from backend.services.sync_engine.common import ENGINE_STOPPED, RATE_LIMIT_BASE_DELAY, filter_escape, logger
from backend.services.sync_engine.reporting import ReportingMixin


class MirrorMixin(ReportingMixin):
    """A push or pull: one rclone sync with retries, recorded as a job."""

    async def _run_sync(self, direction: SyncDirection) -> int:
        """Execute a sync operation with retry logic and DB recording.

        Before syncing, loads manual flags from the DB and unresolved conflict
        paths from the cached diff.  If any paths need to be excluded, a
        temporary filter file is written with ``- <path>`` entries and passed
        to rclone via ``--filter-from``.

        rclone's JSON transfer log is recorded as FileChange rows of the job
        (capped, see MAX_RECORDED_CHANGES) and ``files_changed`` counts every
        change, also for failed and stopped runs.
        """
        config = self._profile
        sync_state = SyncState.PUSHING if direction == SyncDirection.PUSH else SyncState.PULLING

        # Create job record
        async with self._db_session_factory() as session:
            job = SyncJob(
                direction=direction.value,
                started_at=datetime.now(timezone.utc),
                status="running",
                files_changed=0,
                conflicts=0,
                errors=0,
                profile_id=self._profile.profile_id,
            )
            session.add(job)
            await session.commit()
            job_id = job.id

        self._state.set_syncing(sync_state, job_id)
        self._job_recorded(job_id)
        recorder = ChangeRecorder(
            max_rows=self.max_recorded_changes, side="remote" if direction == SyncDirection.PUSH else "local",
        )
        self._state.recorder = recorder  # its rclone stats are the live progress
        try:
            return await self._run_sync_attempts(direction, job_id, recorder)
        except asyncio.CancelledError:
            reason = self._consume_stop()
            message = reason or ENGINE_STOPPED
            logger.warning("Sync %s for '%s' (job %d): %s", direction.value, config.slug, job_id, message)
            try:
                await self._record_error(job_id, message, 0)
                await self._finish_job(job_id, "failed", recorder=recorder)
            except Exception as exc:  # never mask the stop itself
                logger.warning("Could not record stopped job %d: %s", job_id, exc)
            self._state.set_stopped(message)
            if reason is None:
                raise
            return job_id

    async def _run_sync_attempts(self, direction: SyncDirection, job_id: int, recorder: ChangeRecorder) -> int:
        """The body of _run_sync: preflight, retries, recording."""
        import tempfile

        config = self._profile

        # --- Safety checks before anything is transferred or deleted ---
        try:
            refusal, first_sync = await self._preflight(direction)
        except RcloneError as exc:
            refusal, first_sync = f"Could not list {config.remote_dir}: {exc}", False
        if refusal is not None:
            logger.error("Sync %s refused for '%s': %s", direction.value, config.slug, refusal)
            await self._record_error(job_id, refusal, 0)
            await self._fail_job(job_id)
            self._state.set_error(refusal)
            await self._emit_notification("sync_failed", direction=direction.value, error=refusal)
            return job_id

        # Determine source/dest based on direction
        if direction == SyncDirection.PUSH:
            source, dest = config.local_dir, config.remote_dir
        else:
            source, dest = config.remote_dir, config.local_dir

        # --- Build exclude filter for manual flags & unresolved conflicts ---
        exclude_paths: set[str] = set()

        # Load manual flags from DB
        manual_flags = await self.get_manual_flags()
        exclude_paths.update(manual_flags)

        # Load unresolved conflict paths from cached diff
        if self._state.cached_diff is not None:
            for f in self._state.cached_diff.files:
                if f.is_conflict:
                    exclude_paths.add(f.path)

        # Write temp filter file if there are paths to exclude
        filter_path: str | None = None
        filter_fd: int | None = None
        try:
            if exclude_paths:
                filter_fd, filter_path = tempfile.mkstemp(
                    prefix="omnisync_exclude_", suffix=".filter", text=True,
                )
                with os.fdopen(filter_fd, "w") as fh:
                    filter_fd = None  # os.fdopen takes ownership of the fd
                    for p in sorted(exclude_paths):
                        # Anchored and escaped: exactly this path, nothing else
                        fh.write(f"- /{filter_escape(p.lstrip('/'))}\n")
                logger.info(
                    "Bulk sync excluding %d paths (manual flags + conflicts)",
                    len(exclude_paths),
                )

            # Retry loop with exponential backoff
            max_retries = config.max_retries
            base_delay = 1.0
            last_error: Exception | None = None

            self._changing_files()
            paused_edits = self._paused_edits
            limit = self.max_delete
            for attempt in range(1, max_retries + 1):
                rclone_args, max_delete = self._transfer_args, self._max_delete()
                if limit is not None and recorder.deleted:
                    # The delete limit is per sync, not per attempt: a retry
                    # may only delete what the failed attempts left of it
                    # (0 makes rclone refuse any deletion).
                    rclone_args = without_flag(self._transfer_args, "--max-delete")
                    max_delete = max(limit - recorder.deleted, 0)
                try:
                    await self._rclone.sync(
                        source, dest, direction,
                        exclude_filter_path=filter_path,
                        rclone_filter=config.rclone_filter,
                        rclone_args=rclone_args,
                        backup_dir=self._backup_dir(dest),
                        max_delete=max_delete,
                        recorder=recorder,
                    )
                    if first_sync:
                        await self._write_sentinels()
                    # Success — record the job with what rclone changed
                    await self._finish_job(job_id, "completed", recorder=recorder)
                    if self._paused_edits == paused_edits:
                        self._paused_edits = 0  # this sync covered them
                    self._after_successful_sync(recorder.total)
                    await self._release_hold()
                    logger.info("Sync %s completed (job %d, %d file(s) changed)",
                                direction.value, job_id, recorder.total)
                    await self._emit_notification(
                        "sync_completed", direction=direction.value, files=recorder.total
                    )
                    await self._maybe_prune_trash("local" if direction == SyncDirection.PULL else "remote")
                    return job_id

                except RcloneAuthError as exc:
                    # Auth errors: no retry
                    logger.error("Auth error during %s sync: %s", direction.value, exc)
                    await self._record_error(job_id, str(exc), attempt)
                    await self._fail_job(job_id, recorder)
                    self._state.set_error(str(exc))
                    await self._emit_notification("auth_error", error=str(exc))
                    return job_id

                except RcloneError as exc:
                    if "max-delete" in str(exc):
                        # A retry would hit the same limit: stop and tell the user.
                        message = (
                            f"Stopped: this {direction.value} would delete more than the allowed "
                            f"number of files. Deleted and replaced files are kept in {TRASH_DIR}. "
                            "Check the other side, then raise --max-delete for this profile "
                            "if the deletions are intended."
                        )
                        logger.error("Sync %s for '%s': %s", direction.value, config.slug, message)
                        await self._record_error(job_id, message, attempt)
                        await self._fail_job(job_id, recorder)
                        self._state.set_error(message)
                        await self._emit_notification("sync_failed", direction=direction.value, error=message)
                        return job_id
                    last_error = exc
                    logger.warning(
                        "Sync %s attempt %d/%d failed: %s",
                        direction.value, attempt, max_retries, exc,
                    )
                    await self._record_error(job_id, str(exc), attempt)

                    if attempt < max_retries:
                        # A rate limit needs a real pause before the next try.
                        first = RATE_LIMIT_BASE_DELAY if isinstance(exc, RcloneRateLimitError) else base_delay
                        await asyncio.sleep(common.calculate_backoff_delay(attempt, first))
                        if self._stop_retrying():
                            break

            # All retries exhausted
            logger.error(
                "Sync %s failed after %d retries: %s",
                direction.value, max_retries, last_error,
            )
            await self._fail_job(job_id, recorder)
            self._state.set_error(str(last_error))
            await self._emit_notification(
                "sync_failed", direction=direction.value, error=str(last_error), attempts=max_retries,
            )
            return job_id

        finally:
            # Clean up temp filter file
            if filter_path and os.path.exists(filter_path):
                try:
                    os.unlink(filter_path)
                except OSError:
                    logger.warning("Failed to remove temp filter file: %s", filter_path)

    def _after_successful_sync(self, files_changed: int) -> None:
        """Update state after a bulk sync succeeded.

        The sync excluded unresolved conflicts, so they are still different:
        they stay in the cached diff (and pending), and intervals stay paused
        while any remains, since a resolution is in progress. With nothing
        left, the resolution is over: the diff and skip decisions are
        cleared and paused intervals resume.
        """
        before = self._state.cached_diff
        unresolved = [
            f for f in (before.files if before is not None else [])
            if f.is_conflict and not f.manual_flag and f.path not in self._skipped
        ]
        self._state.set_idle(files_processed=files_changed)
        if unresolved:
            self._state.cached_diff = DiffResponse(files=unresolved, summary=self._summarize(unresolved))
            self._state.pending_changes = self._pending_count(unresolved)
            logger.info("Sync done; %d conflict(s) still unresolved, intervals stay paused" if
                        self._state.intervals_paused else "Sync done; %d conflict(s) still unresolved",
                        len(unresolved))
            return
        self._skipped.clear()
        self._resume("bulk sync succeeded with nothing left unresolved")
