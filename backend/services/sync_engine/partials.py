"""Removing rclone's leftover in-progress files after a successful run."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from backend.db.models import SyncJob
from backend.exceptions import RcloneError
from backend.services.sync_engine.common import logger
from backend.services.sync_engine.reporting import ReportingMixin


class PartialsMixin(ReportingMixin):
    """_remove_partial_leftovers(): the clean-up after an interrupted transfer.

    rclone writes a file as ``<name>.<8 hex>.partial`` and renames it when
    the transfer is done. A run that is killed (a crash, a container stop
    past its grace period) leaves those files behind. Every sync ignores
    them (PARTIAL_FILTER), so nothing else ever removes them.

    After a successful mirror or two-way run, when the profile's run before
    it did not complete (failed, stopped or killed), they are deleted from
    both of the profile's folders: only files named like rclone's partial
    files, last modified before this run started, outside the trash folder
    (RcloneService.remove_partials). A failure here is logged and never
    fails the run.
    """

    async def _interrupted_before(self, job_id: int) -> datetime | None:
        """When job ``job_id`` started, if the profile's job before it did not
        complete; None when it did (or there is none)."""
        async with self._db_session_factory() as session:
            previous = (await session.execute(
                select(SyncJob.status)
                .where(SyncJob.profile_id == self.profile_id, SyncJob.id < job_id)
                .order_by(SyncJob.id.desc()).limit(1)
            )).scalar_one_or_none()
            job = await session.get(SyncJob, job_id)
        if previous is None or previous == "completed" or job is None:
            return None
        started = job.started_at
        return started if started.tzinfo is not None else started.replace(tzinfo=timezone.utc)

    async def _remove_partial_leftovers(self, job_id: int) -> int:
        """Delete leftover partial files older than this run (see the class); how many went."""
        started = await self._interrupted_before(job_id)
        if started is None:
            return 0
        config = self._profile
        removed: dict[str, int] = {}
        for side, root in (("local", config.local_dir), ("remote", config.remote_dir)):
            try:
                removed[side] = await self._rclone.remove_partials(root, older_than=started)
            except RcloneError as exc:
                logger.warning("Profile '%s' (job %d): could not remove the leftover partial files in the %s "
                               "folder: %s", config.slug, job_id, side, exc)
        total = sum(removed.values())
        logger.info(
            "Profile '%s' (job %d): removed %d leftover partial file(s) of an interrupted transfer "
            "(local %d, remote %d)", config.slug, job_id, total, removed.get("local", 0), removed.get("remote", 0),
            extra={"fields": {"job_id": job_id, "partials_removed": total,
                              "local": removed.get("local", 0), "remote": removed.get("remote", 0)}},
        )
        return total
