"""Running sync operations: the sync lock, background launches, stops and the sync window."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal, TypeVar, overload

from backend.api.schemas import SyncDirection
from backend.exceptions import SyncBusyError
from backend.services.sync_window import next_window_start, window_open
from backend.services.sync_engine.common import STOPPED_BY_USER, logger
from backend.services.sync_engine.lock import is_busy
from backend.services.sync_engine.reporting import ReportingMixin

T = TypeVar("T")

# What the job and last_error say when a sync started with launch() raised
# unexpectedly; the exception itself goes to the log.
SYNC_CRASHED = "The sync failed unexpectedly. The OmniSync log has the details."

# How long a sync start waits for the run's own safety checks (sync marker,
# empty side, two-way delete probe, pending resync) before answering that the
# sync runs. A refusal within that time is answered at once; a later one is
# recorded on the job and in last_error, like any failure of the run.
SYNC_START_WAIT = float(os.environ.get("OMNISYNC_SYNC_START_WAIT", "60"))


class SyncLaunch:
    """A push, pull, two-way sync or resync that SyncEngine.launch() runs in the background."""

    def __init__(self) -> None:
        self.task: asyncio.Task[int | None] | None = None
        self.job_id: int | None = None
        self.job_created = asyncio.Event()
        # Set once the safety checks passed and rclone is about to change files.
        self.working = asyncio.Event()

    async def wait_started(self, timeout: float) -> bool:
        """Wait until the run changes files or ends, at most ``timeout`` seconds.

        Returns True when it ended before changing anything (a refusal or a
        crash): the caller can report that outcome at once. Always returns
        with the job recorded, unless the run ended without one.
        """
        assert self.task is not None
        waiters = {asyncio.ensure_future(self.working.wait())}
        try:
            await asyncio.wait({self.task, *waiters}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if self.job_id is None and not self.task.done():
                waiters.add(asyncio.ensure_future(self.job_created.wait()))
                await asyncio.wait({self.task, *waiters}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()
        return self.task.done() and not self.working.is_set()


class RunsMixin(ReportingMixin):
    """Push, pull, two-way sync and resync entry points, run one at a time under the sync lock."""

    async def push(self, automatic: bool = False) -> int | None:
        """Execute a push sync, return job ID.

        ``automatic`` (the watcher, the scheduler): skipped, returning None,
        when syncing is paused by the time the sync lock is free.
        """
        return await self._exclusive(lambda: self._run_sync(SyncDirection.PUSH), automatic,
                                     defer_as="push" if automatic else None)

    async def pull(self, automatic: bool = False) -> int | None:
        """Execute a pull sync, return job ID (``automatic``: see push())."""
        return await self._exclusive(lambda: self._run_sync(SyncDirection.PULL), automatic,
                                     defer_as="pull" if automatic else None)

    async def two_way_sync(self, automatic: bool = False) -> int | None:
        """Run a two-way sync (rclone bisync) now; return the job ID.

        Two-way profiles only. The first run for a pair of folders is a
        resync (the union); a run that needs a resync for another reason
        records the job as failed, pauses the profile and waits for resync().
        ``automatic``: see push().
        """
        return await self._exclusive(lambda: self._run_two_way(explicit_resync=False), automatic,
                                     defer_as="two_way" if automatic else None)

    async def resync(self) -> int:
        """Run a confirmed two-way resync: the union of both sides; return the job ID.

        Nothing is deleted: files missing on one side are copied there,
        and where a file differs the newer version wins (the older one goes
        to that side's trash). Clears a pending resync and resumes syncing.
        """
        return await self._exclusive(lambda: self._run_two_way(explicit_resync=True))

    async def _auto_sync(self) -> int | None:
        """What the file watcher runs: a two-way sync, or a push (mirror)."""
        if self._profile.two_way:
            return await self.two_way_sync(automatic=True)
        job_id = await self.push(automatic=True)
        if job_id is None and "push" not in self._deferred:
            # Paused meanwhile: the change is still not pushed. (Held back
            # by the sync window instead, it is pushed when the window opens.)
            self._paused_edits += 1
        return job_id

    def window_open(self) -> bool:
        """Whether automatic syncs may run now (the profile's sync window)."""
        return window_open(self._profile.sync_window)

    def next_window_start(self) -> datetime | None:
        """When the closed sync window opens next (None while open or without one)."""
        return next_window_start(self._profile.sync_window)

    async def _window_tick(self) -> None:
        """Every minute (profiles with a sync window): run what the closed window held back."""
        if not self._deferred or not self.window_open():
            return
        deferred, self._deferred = self._deferred, set()
        self._state.waiting_for_window = False
        logger.info("Profile '%s': the sync window opened, running %s", self._profile.slug,
                    ", ".join(sorted(deferred)))
        if "two_way" in deferred:
            await self.two_way_sync(automatic=True)
            return
        if "push" in deferred:
            await self._auto_sync()
        if "pull" in deferred:
            await self.pull(automatic=True)

    async def _scheduled_sync(self) -> int | None:
        """What the interval runs: a two-way sync, or a pull (mirror)."""
        return await (self.two_way_sync if self._profile.two_way else self.pull)(automatic=True)

    async def launch(self, direction: SyncDirection, resync: bool = False) -> SyncLaunch:
        """Start a push, pull, two-way sync or (``resync``) resync in the background.

        For the API, whose request must not wait for the whole run. The
        profile's sync lock is taken now, or SyncBusyError is raised while
        another operation holds it or waits for it (SyncLock counts the
        waiting ones). The run records its job
        and reports like push(), pull(), two_way_sync() and resync(), and is
        stopped the same way (stop_current_sync(), stop()). An unexpected
        exception is logged and recorded on the job and in last_error.
        """
        self._check_not_busy()
        if resync or direction == SyncDirection.TWO_WAY:
            def operation() -> Awaitable[int]:
                return self._run_two_way(explicit_resync=resync)
        else:
            def operation() -> Awaitable[int]:
                return self._run_sync(direction)
        await self._sync_lock.acquire()
        return self._start_launch(lambda: self._run_launched(operation))

    def _check_not_busy(self) -> None:
        """Raise SyncBusyError while another operation holds the sync lock or waits for it.

        Free and wanted by nobody, the lock is then taken by acquire()
        without suspending, so nothing can come in between.
        """
        if is_busy(self._sync_lock):
            raise SyncBusyError()

    def _start_launch(self, body: Callable[[], Awaitable[int | None]], job_id: int | None = None) -> SyncLaunch:
        """Run ``body`` as the background launch; the caller holds the sync lock, which the launch releases."""
        lock = self._sync_lock
        launch = SyncLaunch()
        if job_id is not None:
            launch.job_id = job_id
            launch.job_created.set()
        self._launch = launch
        launch.task = asyncio.ensure_future(body())

        def finished(_task: asyncio.Task) -> None:
            # Also runs for a task cancelled before its first step.
            if self._launch is launch:
                self._launch = None
            lock.release()

        launch.task.add_done_callback(finished)
        return launch

    async def _run_launched(self, operation: Callable[[], Awaitable[int]]) -> int | None:
        """The body of a launch(): the operation, and a record of any crash."""
        try:
            return await self._run_current(operation)
        except Exception:
            logger.exception("Sync for '%s' failed unexpectedly", self._profile.slug)
            return await self._record_crash()

    async def _record_crash(self) -> int | None:
        """After an unexpected exception: fail the job that is still running, if any."""
        launch = self._launch
        job_id = launch.job_id if launch is not None else None
        if job_id is not None and self._state.current_job_id != job_id:
            return job_id  # the job was already closed; only a later step failed
        self._state.set_error(SYNC_CRASHED)
        if job_id is None:
            return None
        try:
            await self._record_error(job_id, SYNC_CRASHED, 0)
            await self._fail_job(job_id)
        except Exception:
            logger.exception("Could not record the failed job %d", job_id)
        return job_id

    def _job_recorded(self, job_id: int) -> None:
        """The running operation created its job (for launch())."""
        if self._launch is not None:
            self._launch.job_id = job_id
            self._launch.job_created.set()

    def _changing_files(self) -> None:
        """The safety checks passed; rclone is about to change files (for launch())."""
        if self._launch is not None:
            self._launch.working.set()

    @overload
    async def _exclusive(self, operation: Callable[[], Awaitable[T]], automatic: Literal[False] = False,
                         defer_as: None = None) -> T: ...
    @overload
    async def _exclusive(self, operation: Callable[[], Awaitable[T]], automatic: bool,
                         defer_as: str | None = None) -> T | None: ...

    async def _exclusive(self, operation: Callable[[], Awaitable[T]], automatic: bool = False,
                         defer_as: str | None = None) -> T | None:
        """Run an operation under the sync lock as its own task, so it can be stopped.

        A stop through _cancel_current() ends the operation normally (it
        records the job and returns); only a cancellation of the caller
        itself (e.g. shutdown) propagates as CancelledError.

        An ``automatic`` run (watcher, scheduler) may have waited for the
        lock behind e.g. a one-sided restore that paused the profile: it
        checks the pause again once it holds the lock, and is skipped (None).
        Outside the profile's sync window it is skipped too, and remembered
        (``defer_as``) to run when the window opens.
        """
        if automatic and defer_as is not None and not self.window_open():
            self._defer(defer_as)
            return None
        async with self._sync_lock:
            if automatic and self._state.auto_paused:
                logger.info("Profile '%s': automatic sync skipped, syncing is paused", self._profile.slug)
                return None
            if automatic and defer_as is not None and not self.window_open():
                self._defer(defer_as)
                return None
            self._automatic_run = automatic
            try:
                return await self._run_current(operation)
            finally:
                self._automatic_run = False

    def _defer(self, kind: str) -> None:
        """An automatic run waits for the sync window to open (see _window_tick)."""
        if kind not in self._deferred:
            logger.info("Profile '%s': outside the sync window, the %s waits until %s", self._profile.slug,
                        kind.replace("_", "-"), self.next_window_start() or "the window opens")
        self._deferred.add(kind)
        self._state.waiting_for_window = True

    def _stop_retrying(self) -> bool:
        """An automatic run is not retried once syncing is paused."""
        if self._automatic_run and self._state.auto_paused:
            logger.info("Profile '%s': not retrying, syncing is paused", self._profile.slug)
            return True
        return False

    async def _run_current(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Run an operation (the sync lock is held) as the task a stop cancels."""
        self._stop_reason = None
        task = asyncio.ensure_future(operation())
        self._current_task = task
        try:
            return await task
        finally:
            self._current_task = None

    @property
    def is_running_operation(self) -> bool:
        return self._current_task is not None and not self._current_task.done()

    async def stop_current_sync(self) -> bool:
        """Stop the running push, pull or per-file operation, if any.

        rclone is sent SIGTERM (then SIGKILL after a grace period), the job
        is recorded as failed with "Stopped by user.", and the engine stays
        fully usable: watcher, scheduler and pause state are untouched.
        Returns False when nothing was running.
        """
        return await self._cancel_current(STOPPED_BY_USER)

    async def _cancel_current(self, reason: str, timeout: float = 60.0) -> bool:
        task = self._current_task
        if task is None or task.done():
            return False
        self._stop_reason = reason
        task.cancel()
        # Wait for rclone to exit and the job to be recorded (bounded).
        await asyncio.wait({task}, timeout=max(timeout, 0.0))
        return True

    def _consume_stop(self) -> str | None:
        """In a cancelled operation: the stop reason if _cancel_current() cancelled it.

        The cancellation is then absorbed (the operation records its job and
        returns). None means the caller itself was cancelled: re-raise.
        """
        reason = self._stop_reason
        if reason is None:
            return None
        task = asyncio.current_task()
        if task is not None:
            task.uncancel()
        return reason
