"""Backup and restore jobs: their record, their error codes, running them in the background, shutdown.

Every run has a BackupJob row from its start (status "running") to its end.
The API starts runs with start_backup(), start_restore() and
start_restore_files(): the profile's sync lock is taken (or the start is
refused while another operation holds or waits for it), the job is
recorded, and the run continues in the background while the request
answers 202 with the running job. run_backup() (the scheduler), restore()
and restore_files() run the same work to the end and return the finished
job; they wait for the lock instead (common.LOCK_TIMEOUT).

A run that raises unexpectedly ends its job with the code "crashed"; the
exception is logged with its traceback. stop_jobs() (shutdown) cancels the
runs still going and records them as stopped by the shutdown.

Clients get a stable ``error_code`` and a fixed message for it
(public_job_error); rclone's and the OS's text stays in the log and,
redacted, in the job row. Messages OmniSync writes itself (a refused
backup, a restore that cannot run as asked, an unreachable target) are
shown as they are.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from sqlalchemy import select

from backend.api.schemas import RestoreScope
from backend.db.models import BackupJob, BackupJobStatus, BackupTarget
from backend.exceptions import RcloneAuthError, SyncBusyError
from backend.services.history import INTERRUPTED
from backend.services.notification_events import engine_crash_event
from backend.services.rclone import redact_secrets
from backend.services.sync_engine import is_busy
from backend.services.backup_service.common import SEE_LOG, RestoreRefused, logger
from backend.services.backup_service.base import BackupBase

# Error codes of a failed or skipped job -> the message clients get. None:
# the job's own message, which OmniSync wrote itself (no rclone or OS text).
JOB_ERRORS: dict[str, str | None] = {
    "path_overlap": None,
    "target_unreachable": None,
    "backup_refused": None,
    "restore_refused": None,
    "sync_busy": None,
    "interrupted": None,
    "auth_failed": f"Signing in to the remote failed: reconnect it under Remotes. {SEE_LOG}",
    "backup_failed": f"The backup failed. {SEE_LOG}",
    "restore_failed": f"The restore failed. {SEE_LOG}",
    "snapshot_not_found": "The snapshot was not found at the backup target.",
    "crashed": f"The run failed unexpectedly. {SEE_LOG}",
    "stopped": "Stopped before it finished.",
    "shutdown": "Stopped because OmniSync shut down. Run it again.",
}

# The job's message when the profile's sync lock stayed taken (run_backup, restore).
LOCK_TIMEOUT_MESSAGE = "Timed out waiting for the running sync of this profile to finish."
BUSY_MESSAGE = "A sync, backup or restore of this profile is running or waiting. Try again when it is done."
# How long stop_jobs() waits for the cancelled runs to end (rclone gets SIGTERM, then SIGKILL after 10 s).
STOP_WAIT = 15.0


class BackupRunning(RuntimeError):
    """A backup of the target runs already."""


def public_job_error(job: BackupJob) -> tuple[str | None, str | None]:
    """(error code, message for clients) of a job; (None, None) while running or for a success."""
    if job.status in (BackupJobStatus.RUNNING.value, BackupJobStatus.COMPLETED.value):
        return None, None
    code = job.error_code
    if code is None:
        # Recorded before error codes existed (or by recover_interrupted_jobs).
        if job.error_message == INTERRUPTED:
            return "interrupted", INTERRUPTED
        if job.status == BackupJobStatus.SKIPPED.value:
            return "target_unreachable", f"The backup target could not be reached. {SEE_LOG}"
        code = "restore_failed" if job.direction == "restore" else "backup_failed"
    if code not in JOB_ERRORS:
        return code, f"The run failed. {SEE_LOG}"
    message = JOB_ERRORS[code]
    if message is None:
        message = job.error_message or f"The run failed. {SEE_LOG}"
    return code, message


def restore_error_code(exc: Exception) -> str:
    """The error code of a restore that raised ``exc``."""
    if isinstance(exc, RestoreRefused):
        return "restore_refused"
    if isinstance(exc, ValueError):
        return "snapshot_not_found"
    if isinstance(exc, RcloneAuthError):
        return "auth_failed"
    return "restore_failed"


def set_job_end(
    job: BackupJob, status: BackupJobStatus, code: str | None = None, message: str | None = None, **fields: object,
) -> None:
    """End a job record (the caller commits): status, finish time, error code and redacted details."""
    job.status = status.value
    job.finished_at = datetime.now(timezone.utc)
    job.error_code = code
    job.error_message = redact_secrets(message)[:4096] if message else None
    for name, value in fields.items():
        setattr(job, name, value)


class JobsMixin(BackupBase):
    """The job record of each run, the background starts for the API, and stopping runs at shutdown."""

    # ── Job records ──────────────────────────────────────────────────

    async def _new_job(self, target_id: int, direction: str, snapshot_id: str | None = None) -> BackupJob:
        """Record a running job; ValueError for an unknown target."""
        async with self._db() as session:
            if await session.get(BackupTarget, target_id) is None:
                raise ValueError(f"BackupTarget {target_id} not found")
            job = BackupJob(
                target_id=target_id, started_at=datetime.now(timezone.utc), status=BackupJobStatus.RUNNING.value,
                direction=direction, snapshot_id=snapshot_id,
            )
            session.add(job)
            await session.commit()
            return job

    async def get_job(self, job_id: int) -> BackupJob | None:
        async with self._db() as session:
            return await session.get(BackupJob, job_id)

    async def _end_job(
        self, job_id: int, status: BackupJobStatus, code: str | None = None, message: str | None = None,
        *, only_if_running: bool = False, **fields: object,
    ) -> BackupJob | None:
        """End a job record in its own session (see set_job_end)."""
        async with self._db() as session:
            job = await session.get(BackupJob, job_id)
            if job is None or (only_if_running and job.status != BackupJobStatus.RUNNING.value):
                return job
            set_job_end(job, status, code, message, **fields)
            await session.commit()
            return job

    # ── Running ──────────────────────────────────────────────────────

    async def _run_job(self, job_id: int, direction: str, body: Callable[[], Awaitable[object]]) -> BackupJob:
        """Run a job's work; a crash or a cancellation still ends its record. Returns the ended job.

        The run counts as going on (``_job_tasks``, stop_jobs()) until it returns.
        """
        task = asyncio.current_task()
        # A launched run is registered by _launch_job, whose done callback ends it.
        registered = task is not None and task not in self._job_tasks
        if task is not None and registered:
            self._job_tasks[task] = job_id
        try:
            await self._run_body(job_id, direction, body)
            job = await self.get_job(job_id)
            assert job is not None
            return job
        finally:
            if task is not None and registered:
                self._job_tasks.pop(task, None)

    async def _run_body(self, job_id: int, direction: str, body: Callable[[], Awaitable[object]]) -> None:
        try:
            await body()
        except asyncio.CancelledError:
            code = "shutdown" if self._stopping else "stopped"
            try:
                await self._end_job(job_id, BackupJobStatus.FAILED, code, JOB_ERRORS[code], only_if_running=True)
            except Exception:
                logger.exception("Could not record the stopped %s job %d", direction, job_id)
            raise
        except Exception as exc:
            logger.exception("The %s job %d failed unexpectedly", direction, job_id)
            try:
                await self._end_job(job_id, BackupJobStatus.FAILED, "crashed", redact_secrets(str(exc)),
                                    only_if_running=True)
            except Exception:
                logger.exception("Could not record the failed %s job %d", direction, job_id)
            try:
                await self._dispatcher.dispatch(engine_crash_event(
                    type(exc).__name__, redact_secrets(str(exc)),
                    component="Restore" if direction == "restore" else "Backup",
                ))
            except Exception as notify_exc:  # never mask the crash itself
                logger.warning("Could not report the crash of job %d: %s", job_id, notify_exc)

    async def _launch_job(
        self, target_id: int, direction: str, snapshot_id: str | None,
        body: Callable[[int], Awaitable[object]], release: Callable[[], None],
    ) -> BackupJob:
        """Record the job and run ``body(job_id)`` in the background; ``release`` runs when it ends.

        The caller holds the profile's sync lock; ``release`` gives it back.
        """
        try:
            job = await self._new_job(target_id, direction, snapshot_id)
        except BaseException:
            release()
            raise
        job_id = job.id
        task = asyncio.ensure_future(self._run_job(job_id, direction, lambda: body(job_id)))
        self._job_tasks[task] = job_id

        def finished(done: asyncio.Task) -> None:
            # Also for a task cancelled before it started (stop_jobs() records that job).
            release()
            self._job_tasks.pop(done, None)
            if not done.cancelled() and done.exception() is not None:
                logger.error("The %s job %d ended with an error: %s", direction, job_id, done.exception())

        task.add_done_callback(finished)
        return job

    async def _take_profile_lock(self, profile_id: int) -> Callable[[], None]:
        """Take the profile's sync lock now, or raise SyncBusyError; returns what gives it back.

        Refused while another operation holds the lock or waits for it: a
        run the API starts never waits in that queue.
        """
        lock = self.profile_lock(profile_id)
        if is_busy(lock):
            raise SyncBusyError(BUSY_MESSAGE)
        await lock.acquire()  # free and wanted by nobody: taken without suspending
        return lock.release

    async def _target_profile_id(self, target_id: int) -> int:
        async with self._db() as session:
            profile_id = (await session.execute(
                select(BackupTarget.profile_id).where(BackupTarget.id == target_id)
            )).scalar_one_or_none()
        if profile_id is None:
            raise ValueError(f"BackupTarget {target_id} not found")
        return profile_id

    # ── Background starts (the API) ──────────────────────────────────

    async def start_backup(self, target_id: int) -> BackupJob:
        """Start a backup in the background; returns its running job.

        Refused at once: ValueError (unknown target), BackupRunning (a backup
        of the target runs), SyncBusyError (the profile's sync lock is held
        or wanted).
        """
        if target_id in self._running_targets:
            raise BackupRunning(f"Backup already running for target {target_id}")
        profile_id = await self._target_profile_id(target_id)
        if target_id in self._running_targets:
            raise BackupRunning(f"Backup already running for target {target_id}")
        release_lock = await self._take_profile_lock(profile_id)
        self._running_targets.add(target_id)

        def release() -> None:
            self._running_targets.discard(target_id)
            release_lock()

        return await self._launch_job(
            target_id, "backup", None, lambda job_id: self._backup(target_id, job_id, lock_held=True), release,
        )

    async def start_restore(self, target_id: int, snapshot_id: str, scope: RestoreScope) -> BackupJob:
        """Start a full restore in the background; returns its running job.

        Refused at once: ValueError (unknown target), SyncBusyError (the
        profile's sync lock is held or wanted).
        """
        profile_id = await self._target_profile_id(target_id)
        release = await self._take_profile_lock(profile_id)
        return await self._launch_job(
            target_id, "restore", snapshot_id,
            lambda job_id: self._restore(target_id, snapshot_id, scope, job_id, lock_held=True), release,
        )

    async def start_restore_files(
        self, target_id: int, snapshot_id: str, paths: list[str], target_dir: str | None = None,
    ) -> BackupJob:
        """Start restoring chosen files in the background; returns its running job.

        The snapshot is read first (from the cache when it was just
        browsed): ValueError for an unknown snapshot or a selection that
        matches nothing, RestoreRefused for one that cannot be read. Then
        SyncBusyError while the profile's sync lock is held or wanted.
        """
        profile_id, _chosen = await self._chosen_files(target_id, snapshot_id, paths)
        release = await self._take_profile_lock(profile_id)
        return await self._launch_job(
            target_id, "restore", snapshot_id,
            lambda job_id: self._restore_files(target_id, snapshot_id, paths, target_dir, job_id, lock_held=True),
            release,
        )

    # ── Shutdown ─────────────────────────────────────────────────────

    async def stop_jobs(self, timeout: float = STOP_WAIT) -> None:
        """Cancel the runs still going (rclone is stopped) and record them as stopped by the shutdown.

        Waits at most ``timeout`` seconds for them to end.
        """
        self._stopping = True
        running = dict(self._job_tasks)
        if not running:
            return
        logger.info("Stopping %d running backup/restore job(s)", len(running))
        for task in running:
            task.cancel()
        await asyncio.wait(running.keys(), timeout=timeout)
        for job_id in running.values():
            try:
                await asyncio.wait_for(self._end_job(job_id, BackupJobStatus.FAILED, "shutdown",
                                                     JOB_ERRORS["shutdown"], only_if_running=True), timeout=5)
            except Exception:
                logger.exception("Could not record the stopped job %d", job_id)
