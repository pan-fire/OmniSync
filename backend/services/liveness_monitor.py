"""Periodic liveness monitor for OmniSync subsystems.

Checks the schedulers, the database and rclone every LIVENESS_INTERVAL
seconds and notifies only on state transitions (ok -> degraded). It runs
once per interval on a scheduler of its own that main.py owns
(start_liveness_monitor), independent of the profiles' engines: it also
runs with no profile at all, and engines that start and stop never add or
remove a run of it.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable, Iterable
from typing import Any, cast

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.services.notification_dispatcher import NotificationDispatcher
from backend.services.notification_events import (
    rclone_unavailable_event,
    subsystem_degraded_event,
)

logger = logging.getLogger(__name__)

LIVENESS_INTERVAL = 60.0
LIVENESS_JOB = "liveness_monitor"

# (name, scheduler) pairs whose scheduler must be running, e.g. each engine's and the backups'.
SchedulerSource = Callable[[], Iterable[tuple[str, Any]]]


class LivenessMonitor:
    """Periodic subsystem health checker with transition-only notifications."""

    def __init__(
        self,
        schedulers: SchedulerSource,
        dispatcher: NotificationDispatcher,
        db_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._schedulers = schedulers
        self._dispatcher = dispatcher
        self._db_session_factory = db_session_factory

        # Previous state — start as True (healthy) so first degradation triggers
        self._last_scheduler_ok: bool = True
        self._last_db_ok: bool = True
        self._last_rclone_ok: bool = True

    async def check(self) -> None:
        """Run all subsystem checks. Called periodically by APScheduler."""
        await self._check_scheduler()
        await self._check_db()
        await self._check_rclone()

    async def _check_scheduler(self) -> None:
        """Check that every scheduler that should run (engines, backups) is running."""
        stopped = [name for name, scheduler in self._schedulers() if scheduler is None or not scheduler.running]
        is_ok = not stopped

        if self._last_scheduler_ok and not is_ok:
            logger.warning("Schedulers not running: %s", ", ".join(stopped))
            await self._dispatcher.dispatch(
                subsystem_degraded_event("scheduler", "APScheduler has stopped running: " + ", ".join(stopped))
            )
        self._last_scheduler_ok = is_ok

    async def _check_db(self) -> None:
        """Check database connectivity with SELECT 1."""
        is_ok = False
        try:
            async with self._db_session_factory() as session:
                await session.execute(text("SELECT 1"))
                is_ok = True
        except Exception as exc:
            logger.debug("DB liveness check failed: %s", exc)

        if self._last_db_ok and not is_ok:
            await self._dispatcher.dispatch(
                subsystem_degraded_event("database", "Database connection failed")
            )
        self._last_db_ok = is_ok

    async def _check_rclone(self) -> None:
        """Check if the rclone binary is available."""
        is_ok = shutil.which("rclone") is not None

        if self._last_rclone_ok and not is_ok:
            await self._dispatcher.dispatch(
                rclone_unavailable_event("rclone binary not found in PATH")
            )
        self._last_rclone_ok = is_ok


def start_liveness_monitor(monitor: LivenessMonitor, interval: float = LIVENESS_INTERVAL) -> AsyncIOScheduler:
    """Run ``monitor.check`` once per ``interval`` seconds on a scheduler of its own; returns it.

    One job, never run twice at once (a slow check makes the next one wait,
    and missed runs are coalesced into one). The caller shuts the scheduler
    down at shutdown (``scheduler.shutdown(wait=False)``).
    """
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        monitor.check,
        # APScheduler takes fractional seconds (it builds a timedelta); its annotation says int.
        trigger=IntervalTrigger(seconds=cast(int, interval)),
        id=LIVENESS_JOB,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    return scheduler
