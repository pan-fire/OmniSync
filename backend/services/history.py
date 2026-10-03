"""Housekeeping of the job history: interrupted jobs and old entries.

Jobs are recorded as "running" when they start and closed when they end.
A crash or SIGKILL leaves them "running" for ever; recover_interrupted_jobs
closes them as failed at the next start, before any engine runs.

The history (sync jobs with their file changes and errors, backup jobs)
otherwise grows without end. prune_history removes entries older than the
global history_days setting (0 keeps everything), but always keeps the
newest HISTORY_KEEP_JOBS jobs of each profile and backup target, running
jobs, jobs that still have an unresolved conflict, and each target's newest
completed backup (the backup schedule counts from it). Notification log
entries older than history_days go too (the log is also capped at its
newest MAX_LOG_ROWS rows by the dispatcher). HistoryMaintenance
runs it at startup and once a day, and VACUUMs the database when much of
it is free space.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from sqlalchemy import CursorResult, Select, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from backend.db.models import BackupJob, BackupJobStatus, Conflict, NotificationLog, SyncError, SyncJob

logger = logging.getLogger(__name__)

INTERRUPTED = "Interrupted: OmniSync stopped while it ran."

# Newest jobs kept per profile (sync) and per target (backup), however old.
HISTORY_KEEP_JOBS = 100

# Rows deleted per transaction, so syncs recording their jobs never wait long.
DELETE_BATCH = 500

# VACUUM when at least this share of the database file is free pages.
VACUUM_FREE_RATIO = 0.25

CLEANUP_INTERVAL = timedelta(days=1)


@dataclass
class PruneResult:
    sync_jobs: int = 0
    backup_jobs: int = 0
    notifications: int = 0
    vacuumed: bool = False


async def recover_interrupted_jobs(db: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    """Close the jobs a previous run left "running" as failed.

    Call before any engine or backup starts: then nothing is running yet.
    Returns how many (sync jobs, backup jobs) were closed.
    """
    now = datetime.now(timezone.utc)
    async with db() as session:
        stuck = (await session.execute(
            select(SyncJob.id).where(SyncJob.status == "running")
        )).scalars().all()
        if stuck:
            session.add_all(
                SyncError(job_id=job_id, message=INTERRUPTED, retry_count=0, created_at=now) for job_id in stuck
            )
            await session.execute(
                update(SyncJob).where(SyncJob.id.in_(stuck))
                .values(status="failed", finished_at=now, errors=SyncJob.errors + 1)
            )
        result = cast("CursorResult[Any]", await session.execute(
            update(BackupJob).where(BackupJob.status == BackupJobStatus.RUNNING.value)
            .values(status=BackupJobStatus.FAILED.value, finished_at=now, error_message=INTERRUPTED)
        ))
        backups = result.rowcount or 0
        await session.commit()
    if stuck or backups:
        logger.warning("Marked %d sync job(s) and %d backup job(s) left running by the last run as failed",
                       len(stuck), backups)
    return len(stuck), backups


def _beyond_newest(column, partition, order, keep: int):
    """Ids of the rows that are not among the ``keep`` newest of their partition."""
    ranked = select(
        column.label("id"),
        func.row_number().over(partition_by=partition, order_by=order).label("rank"),
    ).subquery()
    return select(ranked.c.id).where(ranked.c.rank > keep)


def _old_sync_jobs(cutoff: datetime, keep: int) -> Select:
    unresolved = select(Conflict.job_id).where(Conflict.resolved.is_(False), Conflict.job_id.is_not(None))
    return select(SyncJob.id).where(
        SyncJob.started_at < cutoff,
        SyncJob.status != "running",
        SyncJob.id.in_(_beyond_newest(
            SyncJob.id, SyncJob.profile_id, (SyncJob.started_at.desc(), SyncJob.id.desc()), keep,
        )),
        SyncJob.id.not_in(unresolved),
    )


def _old_backup_jobs(cutoff: datetime, keep: int) -> Select:
    completed = BackupJob.direction == "backup", BackupJob.status == BackupJobStatus.COMPLETED.value
    ranked_completed = select(
        BackupJob.id.label("id"),
        func.row_number().over(
            partition_by=BackupJob.target_id, order_by=(BackupJob.started_at.desc(), BackupJob.id.desc()),
        ).label("rank"),
    ).where(*completed).subquery()
    newest_completed = select(ranked_completed.c.id).where(ranked_completed.c.rank == 1)
    return select(BackupJob.id).where(
        BackupJob.started_at < cutoff,
        BackupJob.status != BackupJobStatus.RUNNING.value,
        BackupJob.id.in_(_beyond_newest(
            BackupJob.id, BackupJob.target_id, (BackupJob.started_at.desc(), BackupJob.id.desc()), keep,
        )),
        BackupJob.id.not_in(newest_completed),
    )


async def _delete_in_batches(db: async_sessionmaker[AsyncSession], query: Select, model) -> int:
    async with db() as session:
        ids = list((await session.execute(query)).scalars().all())
    for start in range(0, len(ids), DELETE_BATCH):
        async with db() as session:
            # Foreign keys cascade: a job takes its file changes, errors and
            # (resolved) conflicts with it.
            await session.execute(delete(model).where(model.id.in_(ids[start:start + DELETE_BATCH])))
            await session.commit()
    return len(ids)


async def prune_history(
    db: async_sessionmaker[AsyncSession],
    history_days: int,
    keep_jobs: int = HISTORY_KEEP_JOBS,
    now: datetime | None = None,
) -> PruneResult:
    """Delete sync and backup jobs and notification log entries older than ``history_days``.

    See the module docstring for what is kept regardless.
    """
    if history_days <= 0:
        return PruneResult()
    # Job times are stored as naive UTC.
    cutoff = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None) \
        - timedelta(days=history_days)
    result = PruneResult(
        sync_jobs=await _delete_in_batches(db, _old_sync_jobs(cutoff, keep_jobs), SyncJob),
        backup_jobs=await _delete_in_batches(db, _old_backup_jobs(cutoff, keep_jobs), BackupJob),
        notifications=await _delete_in_batches(
            db, select(NotificationLog.id).where(NotificationLog.timestamp < cutoff), NotificationLog,
        ),
    )
    if result.sync_jobs or result.backup_jobs or result.notifications:
        logger.info("History cleanup: removed %d sync job(s), %d backup job(s) and %d notification(s) "
                    "older than %d days",
                    result.sync_jobs, result.backup_jobs, result.notifications, history_days)
    return result


async def vacuum_if_fragmented(engine: AsyncEngine, free_ratio: float = VACUUM_FREE_RATIO) -> bool:
    """VACUUM the database when at least ``free_ratio`` of its pages are free.

    Deleted rows leave free pages that SQLite reuses but never returns to
    the file system; VACUUM rewrites the file without them.
    """
    async with engine.connect() as conn:
        pages = (await conn.execute(text("PRAGMA page_count"))).scalar() or 0
        free = (await conn.execute(text("PRAGMA freelist_count"))).scalar() or 0
    if pages == 0 or free / pages < free_ratio:
        return False
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("VACUUM"))
    logger.info("Compacted the database: %d of %d pages were free", free, pages)
    return True


class HistoryMaintenance:
    """Runs the history cleanup at startup and then once a day."""

    def __init__(
        self,
        db: async_sessionmaker[AsyncSession],
        history_days: Callable[[], int],
        interval: timedelta = CLEANUP_INTERVAL,
    ) -> None:
        self._db = db
        self._history_days = history_days
        self._interval = interval
        self._task: asyncio.Task[None] | None = None

    async def run_once(self) -> PruneResult:
        try:
            days = self._history_days()
        except Exception as exc:
            logger.warning("History cleanup skipped: could not read history_days: %s", exc)
            return PruneResult()
        result = await prune_history(self._db, days)
        engine = self._db.kw.get("bind")
        if (result.sync_jobs or result.backup_jobs) and isinstance(engine, AsyncEngine):
            result.vacuumed = await vacuum_if_fragmented(engine)
        return result

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("History cleanup failed: %s", exc)
            await asyncio.sleep(self._interval.total_seconds())

    def start(self) -> None:
        """Clean up now (in the background) and every interval from now on."""
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="history-maintenance")

    async def stop(self, timeout: float = 5.0) -> None:
        """Cancel the cleanup, waiting at most ``timeout`` seconds (shutdown is bounded).

        A VACUUM or delete running in the database driver's thread cannot be
        interrupted; it is left to finish while the shutdown goes on.
        """
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=timeout)
            if not done:
                logger.warning("History cleanup did not stop within %.0f s", timeout)
            elif not task.cancelled() and task.exception() is not None:
                logger.warning("History cleanup ended with an error: %s", task.exception())
