"""The state every part of the sync engine shares.

SyncEngine (engine.py) is assembled from mixins, one per concern, each in
its own module. All instance state lives here, set in EngineBase.__init__,
so it is defined in one place; the mixins only read and change it.

Every mixin derives from ReportingMixin (reporting.py: job records and
notifications), which derives from EngineBase. For the type checker only,
EngineBase also declares the methods that one mixin calls on another (see
the TYPE_CHECKING block); at runtime they come from the mixins through
SyncEngine's method resolution order.
"""

from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Literal, overload

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from watchdog.observers.polling import PollingObserver

    from backend.services.notification_dispatcher import NotificationDispatcher

from backend.api.schemas import (
    ConflictResolution,
    DiffSummary,
    FileDiff,
    SelectiveSyncResponse,
    SyncCheckResponse,
    SyncDirection,
    TwoWayPreview,
)
from backend.services.rclone import MAX_RECORDED_CHANGES, FileChangeRecord, RcloneService
from backend.models.sync_state import SyncStateManager
from backend.models.profile_config import ProfileConfig
from backend.services.sync_engine.lock import SyncLock

if TYPE_CHECKING:
    from backend.services.sync_engine.runs import SyncLaunch, T


class EngineBase:
    """Instance state of a SyncEngine; see SyncEngine for the behaviour."""

    # FileChange rows stored per job (every change is still counted).
    max_recorded_changes = MAX_RECORDED_CHANGES

    def __init__(
        self,
        profile: ProfileConfig,
        rclone: RcloneService,
        db_session_factory: async_sessionmaker[AsyncSession],
        dispatcher: NotificationDispatcher | None = None,
        sync_lock: asyncio.Lock | None = None,
    ) -> None:
        self._profile = profile
        self._rclone = rclone
        self._db_session_factory = db_session_factory
        self._dispatcher: NotificationDispatcher | None = dispatcher
        self._state = SyncStateManager()
        self._observer: PollingObserver | None = None
        self._scheduler: AsyncIOScheduler | None = None
        self._debounce_timer: threading.Timer | None = None
        self._debounce_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None  # stored at start()
        # One rclone operation per profile at a time: watcher pushes, scheduled
        # pulls, API syncs, per-file actions, backups and restores all take it.
        # The manager passes the profile's lock so a replacement engine (after
        # a rename or config change) waits for its predecessor's last run.
        self._sync_lock = sync_lock if sync_lock is not None else SyncLock()
        # The push, pull or per-file operation running under the lock, so
        # stop_current_sync() can cancel it (which SIGTERMs rclone).
        self._current_task: asyncio.Task | None = None
        self._stop_reason: str | None = None
        # The sync launch() runs in the background, while it runs.
        self._launch: SyncLaunch | None = None
        # Results of recent per-file runs by job id (launch_selective()).
        self._selective_results: OrderedDict[int, SelectiveSyncResponse] = OrderedDict()
        # Paths the user chose to skip, for the life of the cached diff.
        self._skipped: set[str] = set()
        # Watcher events are held while start() makes the startup decision.
        self._startup_pending = False
        self._changed_during_startup = False
        self._last_trash_prune: dict[str, float] = {}
        # The pause is stored with the profile (hold()), until resumed.
        self._held = False
        # Local changes the watcher saw while paused (not pushed then).
        self._paused_edits = 0
        # The running operation was started by the watcher or the scheduler.
        self._automatic_run = False
        # Automatic runs held back by the sync window: "push", "pull", "two_way".
        self._deferred: set[str] = set()
        self._state.sync_window = profile.sync_window

    @property
    def profile_id(self) -> int:
        """The profile this engine syncs. Unlike the slug, it never changes."""
        return self._profile.profile_id

    @property
    def profile(self) -> ProfileConfig:
        """The configuration this engine was started with."""
        return self._profile

    @property
    def sync_lock(self) -> asyncio.Lock:
        """Held while any rclone operation on this profile runs."""
        return self._sync_lock

    if TYPE_CHECKING:
        # Provided by the mixins (see the module docstring).
        # runs.py
        def _job_recorded(self, job_id: int) -> None: ...
        def _changing_files(self) -> None: ...
        @overload
        async def _exclusive(self, operation: Callable[[], Awaitable[T]], automatic: Literal[False] = False,
                             defer_as: None = None) -> T: ...
        @overload
        async def _exclusive(self, operation: Callable[[], Awaitable[T]], automatic: bool,
                             defer_as: str | None = None) -> T | None: ...
        async def _exclusive(self, operation: Callable[[], Awaitable[T]], automatic: bool = False,
                             defer_as: str | None = None) -> T | None: ...
        def _stop_retrying(self) -> bool: ...
        def _consume_stop(self) -> str | None: ...
        def _check_not_busy(self) -> None: ...
        def _start_launch(self, body: Callable[[], Awaitable[int | None]], job_id: int | None = None) -> SyncLaunch: ...
        async def _run_current(self, operation: Callable[[], Awaitable[T]]) -> T: ...
        # pause.py
        def _pause_if_needed(self, pending: int) -> None: ...
        def _pause(self, reason: str) -> None: ...
        def _resume(self, reason: str) -> None: ...
        async def _release_hold(self) -> None: ...
        # safety.py
        async def _preflight(self, direction: SyncDirection) -> tuple[str | None, bool]: ...
        async def _write_sentinels(self) -> None: ...
        def _backup_dir(self, dest: str) -> str: ...
        def _max_delete(self) -> int | None: ...
        @property
        def _transfer_args(self) -> list[str]: ...
        @property
        def _copy_args(self) -> list[str]: ...
        @property
        def max_delete(self) -> int | None: ...
        async def _preflight_two_way(self, resync: bool) -> tuple[str | None, bool]: ...
        # diff.py
        async def check_diff(self, timeout: float | None = None) -> SyncCheckResponse: ...
        @staticmethod
        def _parse_rclone_modtime(modtime_str: str) -> datetime: ...
        @staticmethod
        def _summarize(files: list[FileDiff]) -> DiffSummary: ...
        def _pending_count(self, files: list[FileDiff]) -> int: ...
        async def get_manual_flags(self) -> list[str]: ...
        def _remove_resolved_from_diff(self, resolved_paths: list[str]) -> None: ...
        # selective.py
        async def _keep_both(self, path: str) -> list[FileChangeRecord]: ...
        # conflicts.py
        async def _record_conflicts(self, files: list[FileDiff]) -> None: ...
        async def _close_conflicts(self, resolved: dict[str, ConflictResolution]) -> None: ...
        async def _record_two_way_conflicts(self, job_id: int, conflicts: dict[str, dict[str, str]]) -> int: ...
        # mirror.py
        async def _run_sync(self, direction: SyncDirection) -> int: ...
        # two_way.py
        async def _run_two_way(self, explicit_resync: bool) -> int: ...
        async def _two_way_preview(self) -> TwoWayPreview: ...
        # trash.py
        async def _maybe_prune_trash(self, side: str) -> None: ...
        # engine.py
        def _arm_debounced_push(self) -> None: ...
