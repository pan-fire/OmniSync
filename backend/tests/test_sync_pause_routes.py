"""Integration tests for sync pause/resume API routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.api.routes import sync as sync_module
from backend.api.schemas import SyncDirection
from backend.exceptions import IntervalsNotResumableError


def _default_engine():
    """The mock engine the test_client fixture runs for profile "default"."""
    return next(iter(sync_module._manager.engines.values()))


def _running_launch(job_id: int) -> MagicMock:
    """What engine.launch() returns for a run that is changing files."""
    launch = MagicMock(job_id=job_id)
    launch.wait_started = AsyncMock(return_value=False)
    return launch


@pytest.mark.asyncio
class TestStartSyncPauseGuard:
    async def test_returns_409_when_paused_no_force(self, test_client) -> None:
        """POST /profiles/{slug}/sync/start returns 409 when intervals are paused."""
        engine = _default_engine()
        assert engine is not None

        engine._state.set_paused()
        engine._state.pending_changes = 3

        resp = await test_client.post(
            "/profiles/default/sync/start",
            json={"direction": "push"},
        )
        assert resp.status_code == 409
        body = resp.json()
        assert "paused" in body["detail"].lower()
        assert "3" in body["detail"]

    async def test_allows_with_force(self, test_client) -> None:
        """POST /profiles/{slug}/sync/start with force=true bypasses pause guard."""
        engine = _default_engine()
        assert engine is not None

        engine._state.set_paused()
        engine._state.pending_changes = 3
        engine.launch = AsyncMock(return_value=_running_launch(1))
        engine.get_status = AsyncMock(
            return_value=engine._state.to_status_response()
        )

        resp = await test_client.post(
            "/profiles/default/sync/start",
            json={"direction": "push", "force": True},
        )
        assert resp.status_code == 202
        assert resp.json()["job_id"] == 1
        engine.launch.assert_called_once_with(SyncDirection.PUSH, resync=False)

    async def test_normal_start_when_not_paused(self, test_client) -> None:
        """POST /profiles/{slug}/sync/start works normally when not paused."""
        engine = _default_engine()
        assert engine is not None

        engine._state.intervals_paused = False
        engine.launch = AsyncMock(return_value=_running_launch(1))
        engine.get_status = AsyncMock(
            return_value=engine._state.to_status_response()
        )

        resp = await test_client.post(
            "/profiles/default/sync/start",
            json={"direction": "push"},
        )
        assert resp.status_code == 202


@pytest.mark.asyncio
class TestResumeIntervals:
    async def test_returns_409_when_pending(self, test_client) -> None:
        """POST /profiles/{slug}/sync/resume-intervals returns 409 when pending > 0."""
        engine = _default_engine()
        assert engine is not None

        engine.resume_intervals = AsyncMock(
            side_effect=IntervalsNotResumableError(3)
        )

        resp = await test_client.post("/profiles/default/sync/resume-intervals")
        assert resp.status_code == 409
        assert "3" in resp.json()["detail"]

    async def test_returns_200_when_resumed(self, test_client) -> None:
        """POST /profiles/{slug}/sync/resume-intervals returns 200 when pending == 0."""
        engine = _default_engine()
        assert engine is not None

        engine.resume_intervals = AsyncMock(
            return_value="Intervals resumed successfully."
        )

        resp = await test_client.post("/profiles/default/sync/resume-intervals")
        assert resp.status_code == 200
        assert resp.json()["detail"] == "Intervals resumed successfully."

    async def test_returns_200_noop_when_not_paused(self, test_client) -> None:
        """POST /profiles/{slug}/sync/resume-intervals returns 200 no-op when not paused."""
        engine = _default_engine()
        assert engine is not None

        engine.resume_intervals = AsyncMock(
            return_value="Intervals are not paused. No action needed."
        )

        resp = await test_client.post("/profiles/default/sync/resume-intervals")
        assert resp.status_code == 200
        assert "not paused" in resp.json()["detail"].lower()


@pytest.mark.asyncio
class TestStatusIncludesPauseFields:
    async def test_status_includes_pause_fields(self, test_client) -> None:
        """GET /profiles/{slug}/sync/status includes intervals_paused and paused_at."""
        engine = _default_engine()
        assert engine is not None

        engine._state.set_paused()
        engine.get_status = AsyncMock(
            return_value=engine._state.to_status_response()
        )

        resp = await test_client.get("/profiles/default/sync/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["intervals_paused"] is True
        assert body["paused_at"] is not None


@pytest.mark.asyncio
class TestStopRoutes:
    """Stop ends the running rclone, not the engine's watcher and scheduler."""

    @pytest.mark.parametrize("running", [True, False])
    async def test_stop_route_stops_only_the_running_sync(self, test_client, running: bool) -> None:
        engine = _default_engine()
        assert engine is not None
        engine.stop_current_sync = AsyncMock(return_value=running)
        engine.stop = AsyncMock()

        resp = await test_client.post("/profiles/default/sync/stop")

        assert resp.status_code == 200
        assert resp.json()["message"] == ("Sync stopped" if running else "No sync was running")
        engine.stop_current_sync.assert_awaited_once()
        engine.stop.assert_not_called()

    async def test_profile_stop_stops_only_the_running_sync(self, test_client) -> None:
        from unittest.mock import MagicMock

        from backend.api.routes import profiles

        engine = _default_engine()
        assert engine is not None
        engine.stop_current_sync = AsyncMock(return_value=True)
        engine.stop = AsyncMock()
        # The route resolves slug -> profile id -> engine
        manager = MagicMock()
        manager.get_engine_by_id.return_value = engine
        profile_service = MagicMock()
        profile_service.get_by_slug = AsyncMock(return_value=MagicMock(id=7))
        profiles.set_manager(manager)
        profiles.set_profile_service(profile_service)
        try:
            resp = await test_client.post("/profiles/default/sync/stop")
        finally:
            profiles.set_manager(None)
            profiles.set_profile_service(None)
        manager.get_engine_by_id.assert_called_once_with(7)

        assert resp.status_code == 200 and resp.json()["message"] == "Sync stopped"
        engine.stop_current_sync.assert_awaited_once()
        engine.stop.assert_not_called()
