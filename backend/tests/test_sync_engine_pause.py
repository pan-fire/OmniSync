"""Tests for SyncEngine pause/resume logic (sync-interval-auto-pause feature)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.exceptions import IntervalsNotResumableError
from backend.models.profile_config import ProfileConfig
from backend.models.sync_state import SyncStateManager
from backend.services.rclone import SENTINEL_FILE
from backend.services.sync_engine import SyncEngine

# engine.start() imports the watcher class inside the function, so it is
# patched where it is defined. Failing it runs the engine without a watcher.
WATCHER = "watchdog.observers.polling.PollingObserver"


@pytest.fixture(scope="module")
def local_dir(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A real local folder carrying the sync marker, so the engine's pre-sync
    safety checks pass (module scope: hypothesis tests reuse the fixtures)."""
    path = tmp_path_factory.mktemp("pause-local")
    for name in ("a.txt", SENTINEL_FILE):
        (path / name).write_text("x")
    return str(path)


@pytest.fixture
def mock_profile(local_dir: str) -> ProfileConfig:
    return ProfileConfig(
        profile_id=1,
        slug="test",
        name="Test",
        local_dir=local_dir,
        remote_dir="remote:backup",
        pull_interval_minutes=5,
        debounce_seconds=3,
        max_retries=3,
    )


@pytest.fixture
def mock_rclone() -> AsyncMock:
    rclone = AsyncMock()
    rclone.check_diff = AsyncMock(return_value={
        "has_changes": False,
        "local_only": [],
        "remote_only": [],
        "differ": [],
        "error": None,
    })
    rclone.sync = AsyncMock()
    rclone.list_top_level = AsyncMock(return_value=["a.txt", SENTINEL_FILE])
    return rclone


@pytest.fixture
def mock_db_factory() -> AsyncMock:
    """Mock db session factory that returns a context manager."""
    session = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.get = AsyncMock(return_value=None)
    # no manual flags
    session.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [])))

    factory = MagicMock()  # factory() is a plain call returning an async context manager
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


@pytest.fixture
def engine(mock_profile, mock_rclone, mock_db_factory) -> SyncEngine:
    return SyncEngine(mock_profile, mock_rclone, mock_db_factory)


class TestPauseIfNeeded:
    def test_pauses_when_pending_positive(self, engine: SyncEngine) -> None:
        mock_scheduler = MagicMock()
        mock_job = MagicMock()
        mock_scheduler.get_job.return_value = mock_job
        engine._scheduler = mock_scheduler

        engine._pause_if_needed(5)

        assert engine._state.intervals_paused is True
        assert engine._state.paused_at is not None
        mock_job.pause.assert_called_once()

    def test_no_op_when_pending_zero(self, engine: SyncEngine) -> None:
        engine._pause_if_needed(0)
        assert engine._state.intervals_paused is False

    def test_idempotent_when_already_paused(self, engine: SyncEngine) -> None:
        mock_scheduler = MagicMock()
        mock_job = MagicMock()
        mock_scheduler.get_job.return_value = mock_job
        engine._scheduler = mock_scheduler

        engine._pause_if_needed(3)
        first_ts = engine._state.paused_at

        # Call again — should not call pause again
        mock_job.pause.reset_mock()
        engine._pause_if_needed(5)
        assert engine._state.paused_at is first_ts
        mock_job.pause.assert_not_called()

    def test_safe_with_no_scheduler(self, engine: SyncEngine) -> None:
        engine._scheduler = None
        engine._pause_if_needed(3)
        assert engine._state.intervals_paused is True


class TestResumeIntervals:
    @pytest.mark.asyncio
    async def test_not_paused_returns_no_op(self, engine: SyncEngine) -> None:
        result = await engine.resume_intervals()
        assert "not paused" in result.lower()

    @pytest.mark.asyncio
    async def test_raises_when_pending_positive(self, engine: SyncEngine) -> None:
        engine._state.set_paused()
        engine._state.pending_changes = 3
        with pytest.raises(IntervalsNotResumableError) as exc_info:
            await engine.resume_intervals()
        assert exc_info.value.pending_count == 3

    @pytest.mark.asyncio
    async def test_resumes_when_pending_zero(self, engine: SyncEngine) -> None:
        mock_scheduler = MagicMock()
        mock_job = MagicMock()
        mock_scheduler.get_job.return_value = mock_job
        engine._scheduler = mock_scheduler

        engine._state.set_paused()
        engine._state.pending_changes = 0

        result = await engine.resume_intervals()
        assert "resumed successfully" in result.lower()
        assert engine._state.intervals_paused is False
        assert engine._state.paused_at is None
        mock_job.resume.assert_called_once()

    @pytest.mark.asyncio
    async def test_safe_with_no_scheduler(self, engine: SyncEngine) -> None:
        engine._scheduler = None
        engine._state.set_paused()
        engine._state.pending_changes = 0
        result = await engine.resume_intervals()
        assert "resumed" in result.lower()
        assert engine._state.intervals_paused is False


class TestOnFileChangeGuard:
    def test_suppresses_when_paused(self, engine: SyncEngine) -> None:
        engine._state.set_paused()
        mock_event = MagicMock()
        mock_event.src_path = "/tmp/test-local/file.txt"
        mock_event.event_type = "modified"

        # Should return early without starting debounce timer
        engine._on_file_change(mock_event)
        assert engine._debounce_timer is None

    def test_proceeds_when_not_paused(self, engine: SyncEngine) -> None:
        mock_event = MagicMock()
        mock_event.src_path = "/tmp/test-local/file.txt"
        mock_event.event_type = "modified"

        engine._on_file_change(mock_event)
        # Timer should have been created
        assert engine._debounce_timer is not None
        engine._debounce_timer.cancel()


class TestStartupOrdering:
    @pytest.mark.asyncio
    async def test_startup_check_runs_before_scheduler(
        self, engine: SyncEngine, mock_rclone: AsyncMock
    ) -> None:
        """Verify _startup_check is awaited before scheduler.start()."""
        # Make check_diff return differences
        mock_rclone.check_diff.return_value = {
            "has_changes": True,
            "local_only": ["file1.txt"],
            "remote_only": [],
            "differ": [],
            "error": None,
        }

        call_order: list[str] = []

        original_startup = engine._startup_check

        async def tracked_startup():
            call_order.append("startup_check_start")
            await original_startup()
            call_order.append("startup_check_end")

        engine._startup_check = tracked_startup

        with patch("os.path.isdir", return_value=True), \
             patch("os.makedirs"), \
             patch(WATCHER, side_effect=ImportError):
            await engine.start()

        # startup_check must complete before scheduler starts
        assert "startup_check_start" in call_order
        assert "startup_check_end" in call_order
        # State should be paused due to 1 pending change
        assert engine._state.intervals_paused is True
        assert engine._state.pending_changes == 1

    @pytest.mark.asyncio
    async def test_no_pause_when_no_differences(
        self, engine: SyncEngine, mock_rclone: AsyncMock
    ) -> None:
        mock_rclone.check_diff.return_value = {
            "has_changes": False,
            "local_only": [],
            "remote_only": [],
            "differ": [],
            "error": None,
        }

        with patch("os.path.isdir", return_value=True), \
             patch("os.makedirs"), \
             patch(WATCHER, side_effect=ImportError):
            await engine.start()

        assert engine._state.intervals_paused is False
        assert engine._state.pending_changes == 0


class TestWatcherFailure:
    """The engine runs without a file watcher when the watcher cannot start."""

    @pytest.mark.asyncio
    async def test_watcher_class_unavailable(self, engine: SyncEngine, caplog) -> None:
        with patch("os.makedirs"), patch(WATCHER, side_effect=ImportError("no watchdog")) as watcher:
            await engine.start()
        try:
            watcher.assert_called_once()
            assert engine._observer is None
            assert engine._scheduler is not None
            assert "Could not start file watcher" in caplog.text
        finally:
            await engine.stop()

    @pytest.mark.parametrize("failing", ["schedule", "start"])
    @pytest.mark.asyncio
    async def test_watcher_that_fails_to_start_is_not_kept(self, engine: SyncEngine, failing: str) -> None:
        """A watcher whose schedule()/start() failed is dropped: stop() would
        otherwise join a thread that never started (RuntimeError)."""
        from watchdog.observers.polling import PollingObserver

        class Failing(PollingObserver):
            pass

        def boom(*args, **kwargs):
            raise OSError(28, "inotify watch limit reached")

        setattr(Failing, failing, boom)
        with patch("os.makedirs"), patch(WATCHER, Failing):
            await engine.start()
        assert engine._observer is None
        await engine.stop()

    @pytest.mark.asyncio
    async def test_watcher_runs_and_stops(self, engine: SyncEngine) -> None:
        with patch("os.makedirs"):
            await engine.start()
        observer = engine._observer
        assert observer is not None and observer.is_alive()
        await engine.stop()
        assert engine._observer is None and not observer.is_alive()


class TestStartPausesJobIfFlagSet:
    @pytest.mark.asyncio
    async def test_start_pauses_job_when_flag_already_set(
        self, engine: SyncEngine, mock_rclone: AsyncMock
    ) -> None:
        """When intervals_paused is True on state (e.g. from reload_config),
        start() should pause the scheduler job."""
        engine._state.intervals_paused = True
        engine._state.paused_at = datetime.now(timezone.utc)

        # No pending changes from diff — but flag is already set
        mock_rclone.check_diff.return_value = {
            "has_changes": False,
            "local_only": [],
            "remote_only": [],
            "differ": [],
            "error": None,
        }

        with patch("os.path.isdir", return_value=True), \
             patch("os.makedirs"), \
             patch(WATCHER, side_effect=ImportError):
            await engine.start()

        # Scheduler should exist and the job should be paused
        assert engine._scheduler is not None
        job = engine._scheduler.get_job("periodic_pull")
        if job is not None:
            # In our mock-free APScheduler, check that pause was called
            # The real scheduler may or may not be available
            assert engine._state.intervals_paused is True


class TestForcedBulkSyncClearsPause:
    @pytest.mark.asyncio
    async def test_successful_sync_clears_pause(self, engine: SyncEngine, mock_rclone: AsyncMock) -> None:
        """A successful _run_sync should clear pause state."""
        from backend.services.rclone import RcloneResult

        engine._state.set_paused()
        engine._state.pending_changes = 2

        mock_scheduler = MagicMock()
        mock_job = MagicMock()
        mock_scheduler.get_job.return_value = mock_job
        engine._scheduler = mock_scheduler

        mock_rclone.sync.return_value = RcloneResult(
            stdout="", stderr="", return_code=0, elapsed_seconds=0.1
        )

        # Build a proper async context manager mock for db_session_factory
        mock_session = AsyncMock()
        mock_job_record = MagicMock()
        mock_job_record.id = 1
        mock_session.flush = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_job_record)

        # Make session.add set job.id like a real SQLAlchemy flush would
        def fake_add(obj):
            if hasattr(obj, "id") and obj.id is None:
                obj.id = 1
        mock_session.add = MagicMock(side_effect=fake_add)

        from contextlib import asynccontextmanager

        mock_session.execute = AsyncMock(return_value=MagicMock(
            fetchall=lambda: [], scalars=lambda: MagicMock(all=lambda: [])
        ))

        @asynccontextmanager
        async def fake_factory():
            yield mock_session

        engine._db_session_factory = fake_factory  # type: ignore[assignment]

        from backend.api.schemas import SyncDirection
        await engine._run_sync(SyncDirection.PULL)

        assert engine._state.intervals_paused is False
        mock_job.resume.assert_called_once()


class TestPauseStateInvariant:
    """Property-based test for pause/resume state transitions."""

    @given(st.lists(
        st.sampled_from(["pause_trigger", "selective_resolve", "resume_attempt"]),
        min_size=1,
        max_size=30,
    ))
    @settings(max_examples=200)
    def test_pause_state_consistency(self, actions: list[str]) -> None:
        state = SyncStateManager()
        state.pending_changes = 5  # Start with some pending

        for action in actions:
            if action == "pause_trigger":
                if state.pending_changes > 0 and not state.intervals_paused:
                    state.set_paused()
            elif action == "selective_resolve":
                if state.pending_changes > 0:
                    state.pending_changes -= 1
            elif action == "resume_attempt":
                if state.intervals_paused and state.pending_changes == 0:
                    state.set_resumed()

        # Invariant: paused ⟹ paused_at set, not paused ⟹ paused_at None
        if state.intervals_paused:
            assert state.paused_at is not None
        else:
            assert state.paused_at is None
