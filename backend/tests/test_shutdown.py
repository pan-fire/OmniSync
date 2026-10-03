"""Shutdown: engines stop together, within the container's stop timeout."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.services.rclone import BISYNC_STOP_GRACE
from backend.services.sync_engine_manager import SyncEngineManager

ROOT = Path(__file__).resolve().parents[2]


class SlowEngine:
    """Stands in for a SyncEngine whose running sync takes a while to stop."""

    def __init__(self, slug: str, delay: float, fail: bool = False) -> None:
        self.profile = SimpleNamespace(slug=slug)
        self.delay = delay
        self.fail = fail
        self.stopped = False

    async def stop(self) -> None:
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("observer would not stop")
        self.stopped = True


async def test_engines_are_stopped_concurrently(caplog) -> None:
    manager = SyncEngineManager(AsyncMock(), AsyncMock(), AsyncMock())
    engines = [SlowEngine(f"p{i}", 0.3) for i in range(5)] + [SlowEngine("broken", 0.1, fail=True)]
    manager._engines = {i: engine for i, engine in enumerate(engines)}  # type: ignore[assignment]

    start = time.monotonic()
    await manager.stop_all()
    elapsed = time.monotonic() - start

    assert elapsed < 1.0  # one engine's wait, not the sum of all (1.6 s)
    assert all(e.stopped for e in engines[:5])
    assert manager.engines_by_id == {}
    assert "Error stopping engine 'broken'" in caplog.text


def test_compose_gives_the_backend_time_to_stop_a_two_way_sync() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()
    backend = compose.split("\n  frontend:")[0]
    match = re.search(r"^    stop_grace_period: (\d+)s$", backend, re.MULTILINE)
    assert match, "the backend service needs a stop_grace_period"
    assert int(match.group(1)) >= BISYNC_STOP_GRACE + 30
