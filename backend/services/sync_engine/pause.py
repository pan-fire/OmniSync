"""The interval pause state machine of the sync engine (see SyncEngine)."""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import SyncProfile
from backend.exceptions import IntervalsNotResumableError
from backend.services.sync_engine.common import logger
from backend.services.sync_engine.reporting import ReportingMixin

# What a user pause (Pause / Pause all) says.
USER_PAUSE_REASON = "Paused by user"


async def store_pause_reason(
    db_session_factory: async_sessionmaker[AsyncSession], profile_id: int, reason: str | None,
) -> None:
    """Store (or, with None, clear) a pause with the profile, see SyncEngine.hold()."""
    async with db_session_factory() as session:
        profile = await session.get(SyncProfile, profile_id)
        if profile is not None:
            profile.pause_reason = reason
            await session.commit()


async def store_user_pause(
    db_session_factory: async_sessionmaker[AsyncSession], profile_id: int, paused: bool,
) -> None:
    """Store (or clear) the user's pause with the profile, see SyncEngine.pause_by_user()."""
    async with db_session_factory() as session:
        profile = await session.get(SyncProfile, profile_id)
        if profile is not None:
            profile.user_paused = paused
            await session.commit()


class PauseMixin(ReportingMixin):
    """Pausing and resuming automatic syncing: by the engine, the user, or a hold."""

    def _pause_if_needed(self, pending: int) -> None:
        """Pause scheduler if differences were found. Idempotent.

        Mirror profiles only: a mirror would overwrite one side with the
        other, while a two-way sync carries differences both ways.
        """
        if self._profile.two_way:
            return
        if pending > 0 and not self._state.intervals_paused:
            self._pause(reason=f"{pending} unresolved differences")
            # Schedule notification emission (can't await from sync context)
            if self._dispatcher is not None and self._loop is not None:
                from backend.services.notification_events import intervals_paused_event
                event = intervals_paused_event(
                    pending, profile_name=self._profile.name, profile_slug=self._profile.slug,
                )
                asyncio.run_coroutine_threadsafe(
                    self._dispatcher.dispatch(event),
                    self._loop,
                )

    def _apply_scheduler_pause(self) -> None:
        """Hold the scheduled run while automatic syncing is paused (by the engine or the user)."""
        if self._scheduler is None:
            return
        job = self._scheduler.get_job("periodic_pull")
        if job is None:
            return
        if self._state.auto_paused:
            job.pause()
        else:
            job.resume()

    def _pause(self, reason: str) -> None:
        """RUNNING -> PAUSED: hold the scheduled pull and watcher pushes. Idempotent."""
        if self._state.intervals_paused:
            return
        self._state.set_paused()
        self._apply_scheduler_pause()
        logger.info("Intervals paused for '%s': %s", self._profile.slug, reason)

    def _resume(self, reason: str) -> None:
        """PAUSED -> RUNNING (a user pause still holds, see pause_by_user)."""
        if not self._state.intervals_paused:
            return
        self._state.set_resumed()
        self._apply_scheduler_pause()
        logger.info("Intervals resumed for '%s': %s%s", self._profile.slug, reason,
                    " (still paused by the user)" if self._state.user_paused else "")

    async def pause_by_user(self) -> bool:
        """Pause automatic syncing for the user, stored with the profile; False if already paused so.

        Watcher pushes and scheduled runs wait until the user resumes
        (resume_intervals or resume_user_pause); a sync the user starts
        still runs, and a successful one does not lift this pause.
        """
        if self._state.user_paused:
            return False
        self._state.set_user_paused(True)
        self._apply_scheduler_pause()
        with self._debounce_lock:
            if self._debounce_timer is not None:
                # A push already armed by a change: remembered like any
                # change seen while paused (see resume_intervals).
                self._debounce_timer.cancel()
                self._debounce_timer = None
                self._paused_edits += 1
        await store_user_pause(self._db_session_factory, self.profile_id, True)
        logger.info("Profile '%s': automatic syncing paused by the user", self._profile.slug)
        return True

    async def resume_user_pause(self) -> str | None:
        """Lift the user's pause ("Resume all"); returns why syncing stays paused, or None.

        A pause the engine set (pending differences, a restore, a needed
        resync) stays: it needs its own resume. Without one, this resumes
        like resume_intervals (a mirror profile first compares local changes
        seen meanwhile; IntervalsNotResumableError if differences remain,
        the user pause then stays).
        """
        if not self._state.user_paused:
            return None
        if self._state.intervals_paused:
            await self._clear_user_pause()
            return self._state.last_error or "Automatic syncing is paused."
        await self.resume_intervals()
        return None

    async def _clear_user_pause(self) -> None:
        if not self._state.user_paused:
            return
        self._state.set_user_paused(False)
        self._apply_scheduler_pause()
        await store_user_pause(self._db_session_factory, self.profile_id, False)
        logger.info("Profile '%s': the user's pause was lifted", self._profile.slug)

    def pause_for(self, reason: str) -> None:
        """Pause intervals from outside the engine (in memory; see hold())."""
        self._state.last_error = reason
        self._pause(reason)

    async def hold(self, reason: str) -> None:
        """Pause automatic syncing, stored with the profile, e.g. for a one-sided restore.

        The next scheduled pull or watcher push would otherwise undo the
        restore (pull) or spread it to the other side unreviewed (push).
        Stored, the pause also holds after a restart and in the engine that
        replaces this one after a profile edit, until the user resumes or a
        sync of the whole profile succeeds.
        """
        self.pause_for(reason)
        self._held = True
        await store_pause_reason(self._db_session_factory, self.profile_id, reason)

    async def _load_hold(self) -> None:
        """At start: a pause stored with the profile (hold(), pause_by_user()) still holds."""
        try:
            async with self._db_session_factory() as session:
                profile = await session.get(SyncProfile, self.profile_id)
                reason = getattr(profile, "pause_reason", None) if profile is not None else None
                user_paused = getattr(profile, "user_paused", False) if profile is not None else False
        except Exception as exc:
            logger.warning("Profile '%s': could not read a stored pause: %s", self._profile.slug, exc)
            return
        if user_paused is True:
            logger.info("Profile '%s' stays paused by the user", self._profile.slug)
            self._state.set_user_paused(True)
        if isinstance(reason, str) and reason:
            logger.warning("Profile '%s' stays paused: %s", self._profile.slug, reason)
            self._held = True
            self.pause_for(reason)

    async def _release_hold(self) -> None:
        """After a resume: forget the stored pause, if any."""
        if self._held and not self._state.intervals_paused:
            self._held = False
            await store_pause_reason(self._db_session_factory, self.profile_id, None)

    async def resume_intervals(self) -> str:
        """Resume sync intervals (the engine's pause and the user's). Returns a detail message."""
        if not self._state.auto_paused:
            return "Intervals are not paused. No action needed."
        edits = self._paused_edits
        if self._profile.two_way:
            if self._state.resync_required:
                raise IntervalsNotResumableError(
                    0, "This profile needs a resync before it can sync again. Run Resync.",
                )
        elif self._state.pending_changes > 0:
            raise IntervalsNotResumableError(self._state.pending_changes)
        elif edits:
            # Mirror: the watcher pushed nothing while paused. Resuming as is
            # would let the next interval pull overwrite those local changes
            # (they would survive only in the trash), and pushing them blindly
            # could overwrite what changed on the remote meanwhile. So they
            # are compared first, like the startup check compares offline
            # changes: with any difference, resume is refused until the user
            # has pushed, pulled or synced per file.
            result = await self.check_diff()
            if result.error:
                raise IntervalsNotResumableError(0, (
                    "Cannot resume: files changed locally while syncing was paused, and comparing "
                    f"the folders failed: {result.error}"
                ))
            if self._state.pending_changes > 0:
                raise IntervalsNotResumableError(self._state.pending_changes, (
                    "Cannot resume: files changed locally while syncing was paused, and "
                    f"{self._state.pending_changes} difference(s) remain. Review the diff, then push, "
                    "pull or sync them per file."
                ))
        changed_meanwhile = self._paused_edits != edits
        self._paused_edits = 0
        await self._clear_user_pause()
        self._resume("resumed by user")
        await self._release_hold()
        if changed_meanwhile and not self._profile.two_way:
            # Saved while the folders were compared: pushed like any local change.
            self._arm_debounced_push()
        return "Intervals resumed successfully."
