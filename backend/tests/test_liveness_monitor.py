"""Tests for LivenessMonitor."""

from __future__ import annotations

import asyncio
import inspect

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base
from backend.services.liveness_monitor import LIVENESS_JOB, LivenessMonitor, start_liveness_monitor
from backend.services.notification_dispatcher import NotificationDispatcher
from backend.services.notification_events import NotificationEventType


@pytest_asyncio.fixture
async def liveness_deps(tmp_path):
    """Provide a LivenessMonitor with mocked engine and dispatcher."""
    engine_db = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine_db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine_db, expire_on_commit=False)

    # Mock sync engine
    mock_engine = MagicMock()
    mock_scheduler = MagicMock()
    mock_scheduler.running = True
    mock_engine._scheduler = mock_scheduler

    # Mock dispatcher
    mock_dispatcher = AsyncMock(spec=NotificationDispatcher)
    mock_dispatcher.dispatch = AsyncMock(return_value=[])

    monitor = LivenessMonitor(lambda: [("profile 'docs'", mock_engine._scheduler)], mock_dispatcher, factory)
    yield monitor, mock_engine, mock_dispatcher, factory
    await engine_db.dispose()


@pytest.mark.asyncio
class TestLivenessMonitor:

    async def test_all_healthy_no_notification(self, liveness_deps) -> None:
        monitor, engine, dispatcher, _ = liveness_deps
        with patch("shutil.which", return_value="/usr/bin/rclone"):
            await monitor.check()
        dispatcher.dispatch.assert_not_called()

    async def test_scheduler_stopped_emits_degraded(self, liveness_deps) -> None:
        monitor, engine, dispatcher, _ = liveness_deps
        engine._scheduler.running = False

        with patch("shutil.which", return_value="/usr/bin/rclone"):
            await monitor.check()

        assert dispatcher.dispatch.call_count >= 1
        event = dispatcher.dispatch.call_args_list[0][0][0]
        assert event.event_type == NotificationEventType.SUBSYSTEM_DEGRADED
        assert "scheduler" in event.title.lower()

    async def test_transition_only_notification(self, liveness_deps) -> None:
        """Property P11: Notification fires only on ok→degraded transition."""
        monitor, engine, dispatcher, _ = liveness_deps
        engine._scheduler.running = False

        with patch("shutil.which", return_value="/usr/bin/rclone"):
            await monitor.check()
            call_count_1 = dispatcher.dispatch.call_count

            # Second check with same state — should NOT emit again
            await monitor.check()
            call_count_2 = dispatcher.dispatch.call_count

        assert call_count_1 == call_count_2  # No new notification

    async def test_rclone_missing_emits_unavailable(self, liveness_deps) -> None:
        monitor, engine, dispatcher, _ = liveness_deps

        with patch("shutil.which", return_value=None):
            await monitor.check()

        # Find the rclone_unavailable event
        calls = dispatcher.dispatch.call_args_list
        rclone_events = [c for c in calls if c[0][0].event_type == NotificationEventType.RCLONE_UNAVAILABLE]
        assert len(rclone_events) == 1

    async def test_db_failure_emits_degraded(self, liveness_deps) -> None:
        monitor, engine, dispatcher, _ = liveness_deps

        # Replace the session factory to simulate DB failure
        async def failing_factory():
            raise RuntimeError("DB down")

        # Use a context manager mock that raises
        mock_factory = MagicMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(side_effect=RuntimeError("DB down"))
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = mock_cm
        monitor._db_session_factory = mock_factory

        with patch("shutil.which", return_value="/usr/bin/rclone"):
            await monitor.check()

        calls = dispatcher.dispatch.call_args_list
        db_events = [
            c for c in calls
            if c[0][0].event_type == NotificationEventType.SUBSYSTEM_DEGRADED
            and "database" in c[0][0].title.lower()
        ]
        assert len(db_events) == 1

    async def test_recovery_allows_new_alert(self, liveness_deps) -> None:
        """After recovery and new degradation, a fresh alert fires."""
        monitor, engine, dispatcher, _ = liveness_deps

        with patch("shutil.which", return_value="/usr/bin/rclone"):
            # Break scheduler
            engine._scheduler.running = False
            await monitor.check()
            count_after_break = dispatcher.dispatch.call_count

            # Recover
            engine._scheduler.running = True
            await monitor.check()

            # Break again
            engine._scheduler.running = False
            await monitor.check()
            count_after_second_break = dispatcher.dispatch.call_count

        # Should have fired twice (once per transition)
        assert count_after_second_break > count_after_break


@pytest.mark.asyncio
async def test_no_scheduler_at_all_is_healthy(liveness_deps) -> None:
    """With no profile there are no engine schedulers: nothing to report."""
    _, _, dispatcher, factory = liveness_deps
    monitor = LivenessMonitor(lambda: [], dispatcher, factory)
    with patch("shutil.which", return_value="/usr/bin/rclone"):
        await monitor.check()
    dispatcher.dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_a_stopped_scheduler_is_named(liveness_deps) -> None:
    _, _, dispatcher, factory = liveness_deps
    running, stopped = MagicMock(running=True), MagicMock(running=False)
    monitor = LivenessMonitor(lambda: [("profile 'a'", running), ("backups", stopped), ("profile 'b'", None)],
                              dispatcher, factory)
    with patch("shutil.which", return_value="/usr/bin/rclone"):
        await monitor.check()
    event = dispatcher.dispatch.call_args_list[0][0][0]
    assert event.event_type == NotificationEventType.SUBSYSTEM_DEGRADED
    assert "backups" in event.body and "profile 'b'" in event.body and "profile 'a'" not in event.body


@pytest.mark.asyncio
async def test_runs_once_per_interval_on_its_own_scheduler_and_stops() -> None:
    """One job, never overlapping: a check slower than the interval does not pile up runs."""
    calls: list[float] = []
    running = 0
    overlapped = False

    class SlowMonitor:
        async def check(self) -> None:
            nonlocal running, overlapped
            running += 1
            overlapped = overlapped or running > 1
            calls.append(asyncio.get_running_loop().time())
            await asyncio.sleep(0.25)  # longer than the interval
            running -= 1

    scheduler = start_liveness_monitor(SlowMonitor(), interval=0.1)  # type: ignore[arg-type]
    try:
        assert [job.id for job in scheduler.get_jobs()] == [LIVENESS_JOB]
        job = scheduler.get_job(LIVENESS_JOB)
        assert job.max_instances == 1 and job.coalesce is True
        await asyncio.sleep(1.0)
    finally:
        scheduler.shutdown(wait=False)
    await asyncio.sleep(0.3)  # a run submitted just before the shutdown may still start
    assert 2 <= len(calls) <= 7 and not overlapped
    count = len(calls)
    await asyncio.sleep(0.5)
    assert len(calls) == count  # stopped with the scheduler
    assert not scheduler.running


def test_main_runs_the_monitor_on_an_app_scheduler_not_per_engine() -> None:
    """main.py owns the monitor's scheduler: not tied to engines, and stopped at shutdown."""
    import backend.main as main

    source = inspect.getsource(main.lifespan)
    assert "start_liveness_monitor(" in source and "liveness_scheduler.shutdown(" in source
    assert "on_engine_started" not in source
    assert source.index("start_liveness_monitor(") > source.index("backup_service.start()")
    assert source.index("liveness_scheduler.shutdown(") < source.index("manager.stop_all()")
