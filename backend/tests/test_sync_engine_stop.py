"""SyncEngine.stop() is bounded by one deadline below Docker's stop grace period."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.models.profile_config import ProfileConfig
from backend.services.rclone import BISYNC_STOP_GRACE
from backend.services.sync_engine import SyncEngine
from backend.services.sync_engine.common import ENGINE_STOPPED
from backend.services.sync_engine.runs import SyncLaunch

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


@pytest.fixture
def engine(tmp_path) -> SyncEngine:
    profile = ProfileConfig(profile_id=1, slug="stop", name="Stop", local_dir=str(tmp_path),
                            remote_dir="remote:stop", pull_interval_minutes=5, debounce_seconds=3, max_retries=3)
    return SyncEngine(profile, AsyncMock(), MagicMock())


async def _stubborn(release: asyncio.Event) -> None:
    """An operation that ignores cancellation until released (a hung rclone)."""
    while not release.is_set():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            continue


async def _finish(release: asyncio.Event, *tasks: asyncio.Task) -> None:
    release.set()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_stop_waits_at_most_stop_timeout_in_total(engine: SyncEngine) -> None:
    """A hung operation, a hung launch and a slow watcher share one deadline."""
    release = asyncio.Event()
    current = asyncio.ensure_future(_stubborn(release))
    launch = SyncLaunch()
    launch.task = asyncio.ensure_future(_stubborn(release))  # type: ignore[assignment]
    engine._current_task = current
    engine._launch = launch
    joins: list[float] = []
    observer = MagicMock()
    observer.join.side_effect = lambda timeout: (joins.append(timeout), time.sleep(timeout))
    engine._observer = observer
    engine.stop_timeout = 0.6
    try:
        started = time.monotonic()
        # Without the shared deadline this would wait 60 s + 5 s + 60 s.
        await asyncio.wait_for(engine.stop(), timeout=10)
        elapsed = time.monotonic() - started
    finally:
        await _finish(release, current, launch.task)
    assert elapsed < 0.6 + 0.4
    assert observer.stop.called and len(joins) == 1 and joins[0] <= 0.6


async def test_a_cooperative_stop_still_records_engine_stopped(engine: SyncEngine) -> None:
    recorded: list[str | None] = []

    async def operation() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            # What a run does: absorb the stop and record its job.
            recorded.append(engine._consume_stop())

    engine._current_task = asyncio.ensure_future(operation())
    await asyncio.sleep(0)
    started = time.monotonic()
    await asyncio.wait_for(engine.stop(), timeout=10)
    assert recorded == [ENGINE_STOPPED]
    assert time.monotonic() - started < 1


def test_default_bound_fits_the_compose_grace_period() -> None:
    match = re.search(r"stop_grace_period:\s*(\d+)s", COMPOSE.read_text())
    assert match, "docker-compose.yml sets no stop_grace_period"
    grace = float(match.group(1))
    # Margin for the rest of the shutdown (other services, notifications).
    assert SyncEngine.stop_timeout + 20 <= grace
    # A stopped two-way sync gets its graceful shutdown inside the bound.
    assert BISYNC_STOP_GRACE + 10 <= SyncEngine.stop_timeout
