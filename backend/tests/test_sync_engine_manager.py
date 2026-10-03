"""SyncEngineManager: which engines start_all() starts, and stop_all().

Real SyncEngine objects with start/stop patched out (no file watcher,
scheduler or rclone), as in the env fixture of test_profiles.py; a real
in-memory database holds the profiles.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from backend.db.models import SyncProfile
from backend.exceptions import ProfileNotFoundError
from backend.services.sync_engine import SyncEngine
from backend.services.sync_engine_manager import SyncEngineManager


@pytest.fixture
def calls(monkeypatch) -> dict[str, list[SyncEngine]]:
    seen: dict[str, list[SyncEngine]] = {"started": [], "stopped": []}

    async def fake_start(self):
        seen["started"].append(self)

    async def fake_stop(self):
        seen["stopped"].append(self)

    monkeypatch.setattr(SyncEngine, "start", fake_start)
    monkeypatch.setattr(SyncEngine, "stop", fake_stop)
    return seen


async def _add_profiles(factory, *profiles: tuple[str, bool]) -> dict[str, int]:
    """Insert (slug, enabled) profiles; return slug -> id."""
    now = datetime.now(timezone.utc)
    async with factory() as session:
        rows = [
            SyncProfile(
                slug=slug, name=slug.title(), local_dir=f"/sync/{slug}", remote_dir=f"gdrive:{slug}",
                enabled=enabled, created_at=now, updated_at=now,
            )
            for slug, enabled in profiles
        ]
        session.add_all(rows)
        await session.commit()
        return {r.slug: r.id for r in rows}


async def test_start_all_starts_only_enabled_profiles(test_db_factory, calls) -> None:
    ids = await _add_profiles(test_db_factory, ("docs", True), ("old", False), ("photos", True))
    rclone, dispatcher = AsyncMock(), AsyncMock()
    manager = SyncEngineManager(rclone, dispatcher, test_db_factory)

    await manager.start_all()

    assert set(manager.engines) == {"docs", "photos"}
    assert set(manager.engines_by_id) == {ids["docs"], ids["photos"]}
    assert sorted(e.profile.slug for e in calls["started"]) == ["docs", "photos"]
    assert calls["stopped"] == []
    for slug in ("docs", "photos"):
        engine = manager.get_engine(slug)
        assert engine.profile.profile_id == ids[slug]
        assert engine.profile.local_dir == f"/sync/{slug}"
        # Every engine shares the manager's rclone service and dispatcher.
        assert engine._rclone is rclone and engine._dispatcher is dispatcher
    with pytest.raises(ProfileNotFoundError):
        manager.get_engine("old")
    assert manager.get_engine_by_id(ids["old"]) is None


async def test_start_all_without_enabled_profiles_starts_nothing(test_db_factory, calls) -> None:
    await _add_profiles(test_db_factory, ("a", False), ("b", False))
    manager = SyncEngineManager(AsyncMock(), AsyncMock(), test_db_factory)

    await manager.start_all()

    assert manager.engines == {}
    assert calls["started"] == []


async def test_start_all_with_no_profiles_starts_nothing(test_db_factory, calls) -> None:
    manager = SyncEngineManager(AsyncMock(), AsyncMock(), test_db_factory)
    await manager.start_all()
    assert manager.engines == {} and calls["started"] == []


async def test_stop_all_stops_every_engine(test_db_factory, calls) -> None:
    await _add_profiles(test_db_factory, ("a", True), ("b", True), ("c", True))
    manager = SyncEngineManager(AsyncMock(), AsyncMock(), test_db_factory)
    await manager.start_all()
    running = list(manager.engines_by_id.values())
    assert len(running) == 3

    await manager.stop_all()

    assert sorted(map(id, calls["stopped"])) == sorted(map(id, running))
    assert manager.engines == {}
    with pytest.raises(ProfileNotFoundError):
        manager.get_engine("a")


async def test_stop_all_continues_past_an_engine_that_fails_to_stop(test_db_factory, calls, monkeypatch) -> None:
    await _add_profiles(test_db_factory, ("a", True), ("b", True), ("c", True))
    manager = SyncEngineManager(AsyncMock(), AsyncMock(), test_db_factory)
    await manager.start_all()
    running = list(manager.engines_by_id.values())
    attempted: list[SyncEngine] = []

    async def stop(self):
        attempted.append(self)
        if self is running[0]:
            raise RuntimeError("watcher hung")

    monkeypatch.setattr(SyncEngine, "stop", stop)

    await manager.stop_all()

    assert sorted(map(id, attempted)) == sorted(map(id, running))
    assert manager.engines == {}


async def test_stop_all_with_no_engines(test_db_factory, calls) -> None:
    manager = SyncEngineManager(AsyncMock(), AsyncMock(), test_db_factory)
    await manager.stop_all()
    assert calls["stopped"] == [] and manager.engines == {}
