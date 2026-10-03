"""Job records and notifications of the sync engine."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import insert

from backend.db.models import FileChange, SyncError, SyncJob
from backend.services.rclone import ChangeRecorder, FileChangeRecord
from backend.services.sync_engine.base import EngineBase
from backend.services.sync_engine.common import logger


class ReportingMixin(EngineBase):
    """Records jobs and their errors, and emits notification events."""

    async def _record_error(
        self, job_id: int, message: str, retry_count: int
    ) -> None:
        """Record a sync error in the database."""
        async with self._db_session_factory() as session:
            error = SyncError(
                job_id=job_id,
                message=message,
                retry_count=retry_count,
                created_at=datetime.now(timezone.utc),
            )
            session.add(error)
            await session.commit()

    async def _fail_job(self, job_id: int, recorder: ChangeRecorder | None = None) -> None:
        """Mark a job as failed in the database, with what it changed before failing."""
        await self._finish_job(job_id, "failed", recorder=recorder)

    async def _finish_job(
        self, job_id: int, status: str,
        recorder: ChangeRecorder | None = None,
        extra: list[FileChangeRecord] | None = None,
        errors: int | None = None,
        conflicts: int | None = None,
    ) -> None:
        """Close a job: status, finish time, and (when known) what it changed.

        ``files_changed`` counts every change rclone reported; FileChange
        rows are written for the first MAX_RECORDED_CHANGES of them.
        """
        rows = [*(recorder.rows if recorder is not None else []), *(extra or [])]
        async with self._db_session_factory() as session:
            job = await session.get(SyncJob, job_id)
            if job:
                job.finished_at = datetime.now(timezone.utc)
                job.status = status
                if recorder is not None or extra:
                    job.files_changed = (recorder.total if recorder is not None else 0) + len(extra or [])
                if errors is not None:
                    job.errors = errors
                if conflicts is not None:
                    job.conflicts = conflicts
                if rows:
                    await session.execute(insert(FileChange), [
                        {"job_id": job_id, "file_path": r.path, "action": r.action,
                         "size_bytes": r.size_bytes, "side": r.side}
                        for r in rows
                    ])
                await session.commit()
        if recorder is not None and recorder.truncated:
            logger.info("Job %d changed %d files; the first %d are recorded", job_id, recorder.total, len(rows))

    def _note_remote_auth(self, event_type: str, error: str) -> None:
        """Mark the profile's remote when a sync failed on refused credentials,
        and clear the mark when a sync completed."""
        try:
            from backend.services import notification_events as ne
            from backend.services import remote_auth

            remote = (self._profile.remote_dir or "").split(":", 1)[0]
            if not remote:
                return
            if event_type == "sync_completed":
                remote_auth.clear_auth_failed(remote)
            elif event_type == "auth_error" or (
                event_type in ("sync_failed", "startup_failure") and ne.classify_failure_text(error) == "auth"
            ):
                remote_auth.mark_auth_failed(remote)
        except Exception:
            logger.debug("Could not record the remote's sign-in state", exc_info=True)

    async def _emit_notification(self, event_type: str, **kwargs: object) -> None:
        """Dispatch a notification event if a dispatcher is available.

        In the background (the dispatcher's emit): the sync never waits for
        the notification channels. A failure given only as text is made
        specific here: an rclone authentication error becomes auth_error, a
        remote that could not be reached remote_unreachable.

        An auth error also marks the profile's remote (remote_auth), so the
        Remotes page offers to reconnect it; a completed sync clears that.
        """
        self._note_remote_auth(event_type, str(kwargs.get("error", "")))
        if self._dispatcher is None:
            return
        try:
            from backend.services import notification_events as ne
            profile = {"profile_name": self._profile.name, "profile_slug": self._profile.slug}
            error = str(kwargs.get("error", ""))
            if event_type in ("sync_failed", "startup_failure"):
                kind = ne.classify_failure_text(error)
                if kind == "auth":
                    event_type = "auth_error"
                elif kind == "network":
                    event_type = "remote_unreachable"
            factory_map = {
                "sync_completed": lambda: ne.sync_completed_event(
                    str(kwargs.get("direction", "")), int(str(kwargs.get("files", 0))),
                    conflicts=int(str(kwargs.get("conflicts", 0))), **profile,
                ),
                "sync_failed": lambda: ne.sync_failed_event(
                    str(kwargs.get("direction", "")), error,
                    attempts=int(str(kwargs["attempts"])) if kwargs.get("attempts") else None, **profile,
                ),
                "auth_error": lambda: ne.auth_error_event(error, **profile),
                "remote_unreachable": lambda: ne.remote_unreachable_event(
                    self._profile.remote_dir, error, **profile,
                ),
                "startup_failure": lambda: ne.startup_failure_event(
                    f"profile '{self._profile.name}'", error, **profile,
                ),
                "conflict_detected": lambda: ne.conflict_detected_event(
                    int(str(kwargs.get("count", 0))), kept_both=bool(kwargs.get("kept_both")), **profile,
                ),
                "resync_required": lambda: ne.resync_required_event(error, **profile),
                "engine_crash": lambda: ne.engine_crash_event(
                    str(kwargs.get("exc_type", "Error")), error, **profile,
                ),
            }
            factory = factory_map.get(event_type)
            if factory:
                self._dispatcher.emit(factory())
        except Exception as exc:
            logger.debug("Failed to emit notification '%s': %s", event_type, exc)

    def _emit_threadsafe(self, event_type: str, **kwargs: object) -> None:
        """_emit_notification from a thread or a callback (timer, scheduler listener)."""
        if self._dispatcher is None or self._loop is None or self._loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._emit_notification(event_type, **kwargs), self._loop)
        except Exception as exc:
            logger.debug("Failed to schedule notification '%s': %s", event_type, exc)

    def _on_job_error(self, event: object) -> None:
        """APScheduler listener: a scheduled sync raised instead of recording a failure."""
        exc = getattr(event, "exception", None)
        logger.error("Scheduled sync of '%s' crashed: %r", self._profile.slug, exc)
        self._emit_threadsafe("engine_crash", exc_type=type(exc).__name__, error=str(exc))
