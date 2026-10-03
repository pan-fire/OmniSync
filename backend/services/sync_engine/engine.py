"""SyncEngine: the per-profile sync engine, assembled from its mixins; start, stop and the file watcher."""

from __future__ import annotations

import asyncio
import os
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import concurrent.futures

from backend.services.rclone import SENTINEL_FILE, TRASH_DIR
from backend.services.sync_engine.common import ENGINE_STOPPED, logger
from backend.services.sync_engine.conflicts import ConflictsMixin
from backend.services.sync_engine.diff import DiffMixin
from backend.services.sync_engine.mirror import MirrorMixin
from backend.services.sync_engine.pause import PauseMixin
from backend.services.sync_engine.reporting import ReportingMixin
from backend.services.sync_engine.runs import RunsMixin
from backend.services.sync_engine.safety import SafetyMixin
from backend.services.sync_engine.selective import SelectiveMixin
from backend.services.sync_engine.trash import TrashMixin
from backend.services.sync_engine.two_way import TwoWayMixin


class SyncEngine(
    RunsMixin,
    PauseMixin,
    SafetyMixin,
    DiffMixin,
    SelectiveMixin,
    ConflictsMixin,
    MirrorMixin,
    TwoWayMixin,
    TrashMixin,
    ReportingMixin,
):
    """Core orchestrator for file watching, scheduling, and sync operations.

    Interval state machine
    ----------------------
    RUNNING: the file watcher pushes local edits (debounced) and the
    scheduler pulls every ``pull_interval_minutes``.
    PAUSED (``intervals_paused``): both are held. Only explicit actions run:
    a forced push/pull, per-file actions, backups and restores.

    RUNNING -> PAUSED when
      * the startup check finds pending differences, or fails or times out
        (fail closed: ``last_error`` says why);
      * a diff (check or enhanced diff) finds pending differences;
      * the local folder is missing at start;
      * a restore changes only one side (``hold``: stored with the profile,
        so it also holds after a restart or a profile edit).
    PAUSED -> RUNNING when
      * the user resumes and no pending differences remain, or
      * a bulk sync succeeds and leaves no unresolved conflict behind.

    The watcher push and the scheduled run check the pause again once they
    hold the sync lock (they may have waited for it behind a restore), and
    an automatic run is not retried once syncing is paused.

    Mirror: local changes seen while paused are not pushed then; resume
    first compares the folders (see resume_intervals), so the next
    interval pull cannot overwrite them.

    Pending differences are the cached diff's entries that are neither
    skipped nor manually flagged:
      * Skip: the file leaves the cached diff and is remembered in
        ``_skipped``; later diffs keep it out of the pending count. The skip
        set lives as long as the cached diff: it is cleared when a bulk sync
        leaves nothing unresolved (the file is then synced normally).
      * Manual flag: the file is excluded from bulk syncs and never counts
        as pending, so it never blocks resume.
      * Conflict (changed on both sides): excluded from bulk syncs. It stays
        in the cached diff after a bulk sync, and that sync does not resume
        intervals while any remains (a resolution is in progress).

    User pause (``pause_by_user``, "Pause" / "Pause all"): holds automatic
    syncing like PAUSED, but apart from it: stored with the profile
    (``user_paused``), reported as ``user_paused`` (and ``intervals_paused``),
    and lifted only by the user's resume, never by a successful sync.
    Syncs the user starts still run. Resuming a user pause leaves a pause
    the engine set (pending differences, a restore, a needed resync) in
    place: that one needs its own resume.

    Sync window (``profile.sync_window``): outside it, the watcher push and
    the scheduled run wait (they are remembered and run when the window
    opens); syncs the user starts still run.

    Stop (``stop_current_sync``) cancels the running push, pull or per-file
    operation: rclone gets SIGTERM, the job is recorded as failed with
    "Stopped by user.", and the watcher, scheduler and pause state are left
    as they were, so syncing continues normally afterwards.
    """

    # Makes the watcher's debounce timer; tests substitute a manual clock.
    timer_factory = staticmethod(threading.Timer)
    # The longest stop() may take, every wait included. Below the compose
    # stop_grace_period (90 s) with margin for the rest of the shutdown, and
    # above BISYNC_STOP_GRACE so a stopped two-way sync can still record its job.
    stop_timeout: float = 60.0

    async def start(self) -> None:
        """Initialize file watcher and scheduler.

        Order matters: the startup check decides whether intervals start
        paused before the scheduler starts. The watcher observes from the
        beginning, but its events are held until that decision: a save
        during the check never fires a push before the check has ruled on
        the folder, and is pushed afterwards if the check found it safe.
        """
        self._loop = asyncio.get_running_loop()
        self._startup_pending = True
        self._changed_during_startup = False

        # A missing local_dir is never created: it usually means an external
        # drive or network share is not mounted, and syncing into a fresh
        # empty folder would delete everything on the other side.
        local_dir = self._profile.local_dir
        if not os.path.isdir(local_dir):
            message = (
                f"Local folder '{local_dir}' does not exist or is not mounted. "
                "Syncing is paused until it is back."
            )
            logger.error("Profile '%s': %s", self._profile.slug, message)
            self._state.set_error(message)
            self._state.set_paused()
            await self._emit_notification("startup_failure", error=message)

        if os.path.isdir(local_dir):
            try:
                from watchdog.observers.polling import PollingObserver
                from watchdog.events import FileSystemEventHandler

                class _Handler(FileSystemEventHandler):
                    def __init__(self, engine: SyncEngine) -> None:
                        self._engine = engine

                    def on_any_event(self, event: object) -> None:
                        self._engine._on_file_change(event)

                # Use PollingObserver instead of the default inotify Observer.
                # inotify doesn't work across Docker bind mounts — the host
                # kernel generates the events but the container never sees them.
                # Polling checks every 3 seconds, which is fine for a sync tool.
                observer = PollingObserver(timeout=3)
                observer.schedule(_Handler(self), local_dir, recursive=True)
                observer.start()
                # Kept only once running: stop() joins its thread.
                self._observer = observer
                logger.info("File watcher started on %s (polling mode)", local_dir)
            except Exception as exc:
                logger.warning("Could not start file watcher: %s", exc)

        await self._load_hold()
        if self._profile.two_way:
            # No startup diff: bisync carries the offline changes of both
            # sides. Only a pending resync keeps the profile paused.
            self._load_resync_required()

        # Start scheduler for periodic pulls (mirror) or two-way syncs. A
        # two-way profile that is not paused syncs right away as well, which
        # brings in what changed on either side while OmniSync was not running.
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.interval import IntervalTrigger

            from apscheduler.events import EVENT_JOB_ERROR

            self._scheduler = AsyncIOScheduler()
            self._scheduler.add_listener(self._on_job_error, EVENT_JOB_ERROR)
            first_run: dict[str, Any] = (
                {"next_run_time": datetime.now(timezone.utc)}
                if self._profile.two_way and not self._state.auto_paused else {}
            )
            self._scheduler.add_job(
                self._scheduled_sync,
                trigger=IntervalTrigger(minutes=self._profile.pull_interval_minutes),
                id="periodic_pull",
                replace_existing=True,
                **first_run,
            )
            if self._profile.sync_window:
                # Runs what the closed window held back once it opens.
                self._scheduler.add_job(
                    self._window_tick, trigger=IntervalTrigger(minutes=1), id="sync_window",
                    replace_existing=True,
                )
        except Exception as exc:
            logger.warning("Could not create scheduler: %s", exc)

        # Run startup diff check BEFORE starting scheduler to prevent race
        if not self._profile.two_way and os.path.isdir(local_dir) and self._profile.remote_dir \
                and ":" in self._profile.remote_dir:
            await self._startup_check()

        # Start scheduler — pause the job if intervals are paused
        if self._scheduler is not None:
            try:
                self._scheduler.start()
                if self._state.auto_paused:
                    job = self._scheduler.get_job("periodic_pull")
                    if job is not None:
                        job.pause()
                    logger.info("Scheduler started with pull job paused")
                else:
                    logger.info(
                        "Scheduler started with %d minute pull interval",
                        self._profile.pull_interval_minutes,
                    )
            except Exception as exc:
                logger.warning("Could not start scheduler: %s", exc)

        # The startup decision is made: release the watcher.
        self._startup_pending = False
        if self._changed_during_startup and self._state.auto_paused:
            self._paused_edits += 1
        elif self._changed_during_startup:
            logger.info("Local changes during the startup check: scheduling a push")
            self._arm_debounced_push()

    async def stop(self) -> None:
        """Stop the engine for good (shutdown, profile disabled or reloaded).

        Also cancels a running push/pull/per-file operation, which ends
        rclone cleanly. For stopping only the running sync, see
        stop_current_sync().
        """
        # One deadline for every wait below, so a stop stays well inside the
        # container's stop_grace_period (90 s) even when each step is slow.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.stop_timeout

        def remaining() -> float:
            return max(deadline - loop.time(), 0.0)

        await self._cancel_current(ENGINE_STOPPED, timeout=remaining())
        launch = self._launch
        if launch is not None and launch.task is not None:
            # The stopped operation has recorded its job; let the launch end,
            # or cancel it if it had not started its operation yet.
            done, _ = await asyncio.wait({launch.task}, timeout=min(5.0, remaining()))
            if not done:
                launch.task.cancel()
                await asyncio.wait({launch.task}, timeout=remaining())

        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=min(5.0, remaining()))
            self._observer = None
            logger.info("File watcher stopped")

        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("Scheduler stopped")

        # Cancel any pending debounce timer
        with self._debounce_lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None

    # --- File watcher ---

    def _on_file_change(self, event: object) -> None:
        """Debounced handler for file system events.

        Cancels any previous timer and starts a new one. The sync fires
        after the configured debounce delay.
        """
        # Ignore OmniSync's own files: test-sync probes, the trash folder, the
        # sync marker, and rclone's in-progress downloads.
        event_path = str(getattr(event, "src_path", ""))
        if (
            ".omnisync-test" in event_path
            or f"/{TRASH_DIR}" in event_path
            or event_path.endswith((f"/{SENTINEL_FILE}", ".partial"))
        ):
            return

        if self._state.auto_paused:
            # Not pushed now; remembered so resume compares first (see resume_intervals).
            logger.debug("Watchdog push suppressed — intervals paused")
            self._paused_edits += 1
            return

        if self._startup_pending:
            # Held until the startup check has decided (see start()).
            self._changed_during_startup = True
            return

        logger.info("File change detected: %s (type=%s)", event_path, getattr(event, "event_type", "unknown"))
        self._arm_debounced_push()

    def _arm_debounced_push(self) -> None:
        """(Re)start the debounce timer that fires a push."""
        delay = self._profile.debounce_seconds

        with self._debounce_lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()

            self._debounce_timer = self.timer_factory(
                delay, self._trigger_debounced_push
            )
            self._debounce_timer.start()

    def _trigger_debounced_push(self) -> None:
        """Called by the debounce timer (from watchdog thread) to fire a push sync.

        Uses run_coroutine_threadsafe to safely schedule the async push()
        on the main event loop from this background thread.
        """
        logger.info("Debounce timer fired — scheduling push sync")
        if self._loop is None or self._loop.is_closed():
            logger.warning("_trigger_debounced_push: no event loop available, skipping")
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self._auto_sync(), self._loop)
            # Don't block waiting for result — fire and forget
            future.add_done_callback(self._on_debounced_done)
        except Exception as exc:
            logger.error("_trigger_debounced_push failed to schedule: %s", exc)

    def _on_debounced_done(self, future: concurrent.futures.Future[int | None]) -> None:
        """A watcher-triggered sync raised instead of recording a failure: report the crash."""
        if future.cancelled() or future.exception() is None:
            return
        exc = future.exception()
        logger.error("Debounced push failed: %s", exc)
        self._emit_threadsafe("engine_crash", exc_type=type(exc).__name__, error=str(exc))
