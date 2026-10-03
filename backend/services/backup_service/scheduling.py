"""Backup scheduling: start and stop, the APScheduler jobs, catch-up runs and overdue checks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.db.models import BackupJob, BackupJobStatus, BackupTarget
from backend.services.notification_events import backup_overdue_event
from backend.services.backup_service.common import logger
from backend.services.backup_service.base import BackupBase

# Schedules continue from the last backup run, across restarts and edits. A
# target whose next run passed while OmniSync was down runs this long after
# start; each further overdue target one CATCH_UP_STAGGER later, so they do
# not all start at once.
CATCH_UP_DELAY = timedelta(minutes=2)
CATCH_UP_STAGGER = timedelta(minutes=1)

# How long stop() may take at shutdown: main.py stops the backups before the
# engines (60 s), inside the container's 90 s grace period.
STOP_TIMEOUT = 20.0

# An enabled target is overdue when no backup completed for this many times
# its frequency; that is notified once (again after it recovered and fell
# behind again). Checked hourly, the first time after the catch-up runs.
OVERDUE_FACTOR = 2
OVERDUE_CHECK_JOB = "backup-overdue-check"
OVERDUE_CHECK_INTERVAL = timedelta(hours=1)
OVERDUE_FIRST_CHECK = timedelta(minutes=15)


def _aware(when: datetime) -> datetime:
    """A database timestamp (stored without zone, in UTC) as an aware datetime."""
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when


def next_backup_time(frequency_hours: int, last_run: datetime, now: datetime, catch_up_delay: timedelta) -> datetime:
    """When a target last run at ``last_run`` backs up next: one interval later, or soon if that has passed.

    Never more than one interval from now, even if ``last_run`` is in the
    future (the clock went back).
    """
    interval = timedelta(hours=frequency_hours)
    return min(max(_aware(last_run) + interval, now + catch_up_delay), now + interval)


def backup_is_overdue(target: BackupTarget, last_completed: datetime | None, now: datetime) -> bool:
    """Enabled, and no backup completed (or, never backed up, created) more than OVERDUE_FACTOR intervals ago."""
    if not target.enabled:
        return False
    since = last_completed if last_completed is not None else target.created_at
    if since is None:
        return False
    return now - _aware(since) > timedelta(hours=OVERDUE_FACTOR * target.frequency_hours)


class SchedulingMixin(BackupBase):
    """One interval job per enabled target, continuing from its last run across restarts."""

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        """Load enabled targets and schedule backup jobs, continuing from their last runs."""
        self._scheduler = AsyncIOScheduler()
        async with self._db() as session:
            stmt = (
                select(BackupTarget)
                .where(BackupTarget.enabled == True)  # noqa: E712
                .options(selectinload(BackupTarget.profile))
            )
            result = await session.execute(stmt)
            targets = result.scalars().all()
        last_runs = await self.last_backup_runs()

        overdue = 0
        for target in targets:
            if self.schedule_target(
                target, last_runs.get(target.id), catch_up_delay=CATCH_UP_DELAY + overdue * CATCH_UP_STAGGER,
            ):
                overdue += 1

        self._scheduler.add_job(
            self.check_overdue,
            trigger=IntervalTrigger(
                seconds=int(OVERDUE_CHECK_INTERVAL.total_seconds()),
                start_date=datetime.now(timezone.utc) + OVERDUE_FIRST_CHECK,
            ),
            id=OVERDUE_CHECK_JOB,
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info("BackupService started with %d target(s)", len(targets))

    async def stop(self, timeout: float = STOP_TIMEOUT) -> None:
        """Stop for shutdown, within ``timeout`` seconds.

        No new run starts; the backups and restores still running are
        cancelled (rclone is stopped) and their jobs recorded as stopped by
        the shutdown (stop_jobs). The scheduler does not wait for its jobs:
        those are the runs stop_jobs() just ended.
        """
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        try:
            await asyncio.wait_for(self.stop_jobs(timeout=max(timeout - 5, 1)), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Backup and restore runs did not stop within %.0f s", timeout)
        logger.info("BackupService stopped")

    # ── Scheduling ───────────────────────────────────────────────────

    def schedule_target(
        self, target: BackupTarget, last_run: datetime | None = None, *,
        catch_up_delay: timedelta = CATCH_UP_DELAY,
    ) -> bool:
        """Schedule a recurring backup job for a target.

        The first run is one interval after ``last_run`` (the start of its
        last backup run; a target never run counts from its creation), or
        ``catch_up_delay`` from now if that time has passed: a restart or an
        edit never pushes a backup further out. Returns whether the target
        was overdue (a catch-up run).
        """
        if self._scheduler is None:
            return False
        now = datetime.now(timezone.utc)
        anchor = last_run or target.created_at or now
        first = next_backup_time(target.frequency_hours, anchor, now, catch_up_delay)
        job_id = f"backup-{target.id}"
        self._scheduler.add_job(
            self.run_backup,
            trigger=IntervalTrigger(hours=target.frequency_hours, start_date=first),
            args=[target.id],
            id=job_id,
            replace_existing=True,
        )
        catch_up = first <= now + catch_up_delay
        logger.debug("Scheduled %s (every %dh, next %s%s)", job_id, target.frequency_hours,
                     first.isoformat(timespec="seconds"), ", catching up" if catch_up else "")
        return catch_up

    def unschedule_target(self, target_id: int) -> None:
        """Remove a scheduled backup job."""
        if self._scheduler is None:
            return
        job_id = f"backup-{target_id}"
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass  # Job may not exist

    async def reschedule_target(self, target: BackupTarget) -> None:
        """Unschedule then re-schedule with updated frequency, from the target's last run."""
        self.unschedule_target(target.id)
        if target.enabled:
            self.schedule_target(target, (await self.last_backup_runs([target.id])).get(target.id))

    async def last_backup_runs(self, target_ids: list[int] | None = None) -> dict[int, datetime]:
        """Target id -> when its last backup run (any outcome) started."""
        stmt = (
            select(BackupJob.target_id, func.max(BackupJob.started_at))
            .where(BackupJob.direction == "backup")
            .group_by(BackupJob.target_id)
        )
        if target_ids is not None:
            stmt = stmt.where(BackupJob.target_id.in_(target_ids))
        async with self._db() as session:
            rows = (await session.execute(stmt)).all()
        return {target_id: _aware(started) for target_id, started in rows if started is not None}

    @staticmethod
    async def last_completed_backups(session: AsyncSession, target_ids: list[int] | None = None) -> dict[int, datetime]:
        """Target id -> when its last completed backup finished."""
        stmt = (
            select(BackupJob.target_id, func.max(func.coalesce(BackupJob.finished_at, BackupJob.started_at)))
            .where(BackupJob.direction == "backup", BackupJob.status == BackupJobStatus.COMPLETED.value)
            .group_by(BackupJob.target_id)
        )
        if target_ids is not None:
            stmt = stmt.where(BackupJob.target_id.in_(target_ids))
        rows = (await session.execute(stmt)).all()
        return {target_id: _aware(when) for target_id, when in rows if when is not None}

    async def check_overdue(self) -> list[int]:
        """Notify once for each enabled target with no completed backup for OVERDUE_FACTOR intervals.

        Returns the ids of the overdue targets. A target that completes a
        backup again (or is disabled) is notified again if it falls behind
        later.
        """
        now = datetime.now(timezone.utc)
        async with self._db() as session:
            targets = (await session.execute(
                select(BackupTarget)
                .where(BackupTarget.enabled == True)  # noqa: E712
                .options(selectinload(BackupTarget.profile))
            )).scalars().all()
            completed = await self.last_completed_backups(session)

        overdue = [t for t in targets if backup_is_overdue(t, completed.get(t.id), now)]
        for target in overdue:
            if target.id in self._overdue_notified:
                continue
            self._overdue_notified.add(target.id)
            logger.warning("Backup target %d (%s) is overdue", target.id, target.name)
            try:
                await self._dispatcher.dispatch(backup_overdue_event(
                    target.name, target.frequency_hours, completed.get(target.id),
                    profile_name=target.profile.name, profile_slug=target.profile.slug,
                ))
            except Exception as exc:
                logger.warning("Could not report the overdue backup of target %d: %s", target.id, exc)
        self._overdue_notified &= {t.id for t in overdue}
        return [t.id for t in overdue]

    def get_next_run_time(self, target_id: int) -> datetime | None:
        """Return the next scheduled run time for a target, or None."""
        if self._scheduler is None:
            return None
        job = self._scheduler.get_job(f"backup-{target_id}")
        return job.next_run_time if job else None

    async def remote_names(self) -> set[str]:
        """Names of the rclone remotes configured now."""
        return {remote.name for remote in await self._rclone.list_remotes()}

    # ── Reload ───────────────────────────────────────────────────────

    async def reload_profile(self, profile_id: int) -> None:
        """Unschedule all targets for a profile, then reschedule enabled ones."""
        async with self._db() as session:
            stmt = select(BackupTarget).where(BackupTarget.profile_id == profile_id)
            result = await session.execute(stmt)
            targets = result.scalars().all()
        last_runs = await self.last_backup_runs([t.id for t in targets])

        for target in targets:
            self.unschedule_target(target.id)
            if target.enabled:
                self.schedule_target(target, last_runs.get(target.id))
