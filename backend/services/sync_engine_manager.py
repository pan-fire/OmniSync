"""Registry that manages one SyncEngine per enabled profile.

Engines are keyed by the profile's id, which never changes. The slug does
change when a profile is renamed, so it is only ever used to find the
current engine, never to identify one.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import SyncProfile
from backend.exceptions import ProfileNotFoundError
from backend.models.profile_config import ProfileConfig
from backend.services.notification_dispatcher import NotificationDispatcher
from backend.services.rclone import RcloneService
from backend.services.sync_engine import SyncEngine, SyncLock

logger = logging.getLogger(__name__)

# Called with every engine the manager starts, e.g. to add scheduler jobs.
EngineHook = Callable[[SyncEngine], Awaitable[None] | None]


class SyncEngineManager:
    """Thin registry: one SyncEngine per enabled sync profile."""

    def __init__(
        self,
        rclone: RcloneService,
        dispatcher: NotificationDispatcher,
        db_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._rclone = rclone
        self._dispatcher = dispatcher
        self._db_session_factory = db_session_factory
        self._engines: dict[int, SyncEngine] = {}
        # Start and stop one engine at a time, so two requests for the same
        # profile cannot both start an engine.
        self._lock = asyncio.Lock()
        # One sync lock per profile, shared by each engine that profile gets:
        # an rclone run the previous engine started (before a rename or a
        # disable/enable) still holds it, so the new engine waits for it.
        self._sync_locks: dict[int, asyncio.Lock] = {}
        self._on_start: list[EngineHook] = []

    def on_engine_started(self, hook: EngineHook) -> None:
        """Run ``hook`` for every engine started from now on."""
        self._on_start.append(hook)

    async def start_all(self) -> None:
        """Create and start an engine for every enabled profile."""
        async with self._db_session_factory() as session:
            result = await session.execute(
                select(SyncProfile).where(SyncProfile.enabled == True)  # noqa: E712
            )
            profiles = result.scalars().all()

        for p in profiles:
            cfg = ProfileConfig.from_orm(p)
            await self.create_engine(cfg)

        logger.info("SyncEngineManager started %d engine(s)", len(self._engines))

    async def stop_all(self) -> None:
        """Stop every running engine, all at once.

        A stopping two-way sync may take up to BISYNC_STOP_GRACE to shut
        down cleanly; stopping the engines one after another would add
        those waits up and outlast the container's stop timeout.
        """
        async with self._lock:
            engines = list(self._engines.values())
            results = await asyncio.gather(*(engine.stop() for engine in engines), return_exceptions=True)
            for engine, result in zip(engines, results, strict=True):
                if isinstance(result, Exception):
                    logger.warning("Error stopping engine '%s': %s", engine.profile.slug, result)
            self._engines.clear()
        logger.info("SyncEngineManager stopped all engines")

    async def create_engine(self, profile: ProfileConfig) -> SyncEngine:
        """Make sure an engine runs for ``profile`` with exactly this config.

        Idempotent: if the profile's engine already runs with the same
        config it is returned as is; if its config differs (renamed, new
        folders, ...) the old engine is stopped and a new one started.
        """
        async with self._lock:
            current = self._engines.get(profile.profile_id)
            if current is not None:
                if current.profile == profile:
                    return current
                await self._stop(profile.profile_id)
            engine = SyncEngine(
                profile, self._rclone, self._db_session_factory, self._dispatcher,
                sync_lock=self.sync_lock(profile.profile_id),
            )
            await engine.start()
            self._engines[profile.profile_id] = engine
            logger.info("Engine started for profile '%s' (id %d)", profile.slug, profile.profile_id)

        for hook in self._on_start:
            try:
                result = hook(engine)
                if result is not None:
                    await result
            except Exception as exc:
                logger.warning("Engine start hook failed for '%s': %s", profile.slug, exc)
        return engine

    async def remove_engine(self, profile_id: int, deleted: bool = False) -> None:
        """Stop and deregister the engine of a profile, if one runs.

        ``deleted`` also forgets the profile's sync lock.
        """
        async with self._lock:
            await self._stop(profile_id)
            if deleted:
                self._sync_locks.pop(profile_id, None)

    async def _stop(self, profile_id: int) -> None:
        engine = self._engines.pop(profile_id, None)
        if engine is not None:
            await engine.stop()
            logger.info("Engine stopped for profile '%s' (id %d)", engine.profile.slug, profile_id)

    def sync_lock(self, profile_id: int) -> asyncio.Lock:
        """The profile's sync lock: the one its engine holds, whether or not it runs now.

        Backups and restores take it too, so an engine started while they
        run (e.g. the profile is enabled) waits for them.
        """
        return self._sync_locks.setdefault(profile_id, SyncLock())

    def get_engine_by_id(self, profile_id: int) -> SyncEngine | None:
        """The running engine of a profile, or None."""
        return self._engines.get(profile_id)

    def get_engine(self, slug: str) -> SyncEngine:
        """The running engine whose profile currently has ``slug``.

        Raises ProfileNotFoundError if no running engine has that slug.
        """
        for engine in self._engines.values():
            if engine.profile.slug == slug:
                return engine
        raise ProfileNotFoundError(slug)

    def get_all_statuses(self) -> dict[str, object]:
        """Return slug → status mapping for all running engines."""
        return {slug: engine._state.to_status_response() for slug, engine in self.engines.items()}

    @property
    def engines(self) -> dict[str, SyncEngine]:
        """Running engines by their profile's current slug."""
        return {engine.profile.slug: engine for engine in self._engines.values()}

    @property
    def engines_by_id(self) -> dict[int, SyncEngine]:
        """Running engines by profile id."""
        return dict(self._engines)
