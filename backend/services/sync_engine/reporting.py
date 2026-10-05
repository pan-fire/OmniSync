"""Job records and notifications of the sync engine."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import insert

from backend.api.schemas import SyncWarning, SyncWarningCode
from backend.db.models import FileChange, SyncError, SyncJob
from backend.services.rclone import ChangeRecorder, FileChangeRecord, redact_secrets
from backend.services.sync_engine.base import EngineBase
from backend.services.sync_engine.common import filter_escape, logger
from backend.services.sync_engine.names import (
    LocalNames,
    describe,
    is_utf8,
    name_warnings,
    only_listed,
    scan_local,
)


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

    # --- Local names rclone cannot sync as they are (see names.py) ---

    async def _local_names(self, listed: set[str] | None = None) -> LocalNames:
        """The profile's local folder scanned for such names.

        Narrowed to what the profile's filters let a sync see: ``listed``
        (the paths rclone listed with them), else, only when the profile
        filters and a name was found, a listing made here. If that listing
        fails, everything found is kept (a warning too many, never one too
        few).
        """
        from backend.services.sync_engine.two_way import filter_flags  # two_way imports this module

        config = self._profile
        found = await asyncio.to_thread(scan_local, config.local_dir)
        if not (found.collisions or found.not_utf8):
            return found
        if listed is None:
            flags = filter_flags(config.rclone_args)
            if not (config.rclone_filter or flags):
                return found
            try:
                entries = await self._rclone.lsjson(config.local_dir, rclone_filter=config.rclone_filter,
                                                    rclone_args=flags)
            except Exception as exc:
                logger.warning("Profile '%s': could not list the local folder with the profile's filters "
                               "to check its file names: %s", config.slug, redact_secrets(str(exc)))
                return found
            listed = {e["Path"] for e in entries if isinstance(e, dict) and "Path" in e}
        return only_listed(found, listed)

    async def _shadowing_links(self, links: list[str]) -> list[str]:
        """The local links whose path names a file or folder in the remote folder.

        One listing of exactly those paths. A link whose name cannot be put
        in a filter rule (not UTF-8, a line break) is left out; if the
        listing fails, every link is reported (better a warning too many).
        """
        nameable = [p for p in links if is_utf8(p) and "\n" not in p and "\r" not in p]
        if not nameable:
            return []
        rules = [rule for p in nameable for rule in (f"+ /{filter_escape(p)}", f"+ /{filter_escape(p)}/")]
        try:
            items = await self._rclone.existing_items(self._profile.remote_dir, rules)
        except Exception as exc:
            logger.warning("Profile '%s': could not check the remote folder for names of local symbolic "
                           "links: %s", self._profile.slug, redact_secrets(str(exc)))
            return nameable
        return [p for p in nameable if p in items or f"{p}/" in items]

    async def _name_warnings(self, link_code: SyncWarningCode,
                             listed: set[str] | None = None) -> tuple[list[SyncWarning], list[str]]:
        """The warnings about local names for a preview, diff or run, and the local links.

        ``link_code``: how links named like a remote item are reported (what
        the run does with that item). Never raises: a failed check is
        logged and gives no warnings, as the sync itself does not depend on it.
        """
        try:
            found = await self._local_names(listed)
            shadowing = await self._shadowing_links(found.symlinks)
        except Exception:
            logger.warning("Profile '%s': could not check the local file names", self._profile.slug, exc_info=True)
            return [], []
        return name_warnings(found, shadowing, link_code), found.symlinks

    def _log_warnings(self, job_id: int, warnings: list[SyncWarning]) -> None:
        for line in describe(warnings):
            logger.warning("Profile '%s' (job %d): %s", self._profile.slug, job_id, line)

    async def _fail_job(self, job_id: int, recorder: ChangeRecorder | None = None) -> None:
        """Mark a job as failed in the database, with what it changed before failing."""
        await self._finish_job(job_id, "failed", recorder=recorder)

    async def _finish_job(
        self, job_id: int, status: str,
        recorder: ChangeRecorder | None = None,
        extra: list[FileChangeRecord] | None = None,
        errors: int | None = None,
        conflicts: int | None = None,
        warnings: list[SyncWarning] | None = None,
    ) -> None:
        """Close a job: status, finish time, and (when known) what it changed.

        ``files_changed`` counts every change rclone reported; FileChange
        rows are written for the first MAX_RECORDED_CHANGES of them.
        ``warnings`` are stored with the job (and logged).
        """
        if warnings:
            self._log_warnings(job_id, warnings)
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
                if warnings:
                    job.warnings = json.dumps([w.model_dump(mode="json") for w in warnings])
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
                    conflicts=int(str(kwargs.get("conflicts", 0))),
                    warnings=describe(found) if isinstance(found := kwargs.get("warnings"), list) else None,
                    **profile,
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
