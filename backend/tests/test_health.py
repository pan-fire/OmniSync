"""Tests for /health (liveness, local checks only) and /health/remotes."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import OperationalError

from backend.api.routes import health


@pytest.fixture
def rclone_present(monkeypatch):
    monkeypatch.setattr(health.shutil, "which", lambda name: f"/usr/bin/{name}")


@pytest.fixture
def rclone_missing(monkeypatch):
    monkeypatch.setattr(health.shutil, "which", lambda name: None)


async def test_health_returns_200(test_client, rclone_present):
    """GET /health returns 200 with expected HealthResponse schema."""
    response = await test_client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert "status" in data
    assert "rclone_installed" in data
    assert "remote_accessible" not in data
    assert "uptime_seconds" in data
    assert "database_ok" in data


async def test_health_database_ok(test_client, rclone_present):
    """GET /health reports database_ok as True with in-memory DB."""
    response = await test_client.get("/health")
    data = response.json()
    assert data["database_ok"] is True
    assert data["status"] == "ok"


async def test_rclone_is_detected_without_any_profile(test_client, rclone_present):
    # rclone_installed used to come from the first profile's engine.
    health.set_manager(None)
    data = (await test_client.get("/health")).json()
    assert data["rclone_installed"] is True
    assert data["status"] == "ok"


async def test_real_path_lookup_finds_rclone(test_client, monkeypatch, tmp_path):
    fake = tmp_path / "rclone"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    health.set_manager(None)
    assert (await test_client.get("/health")).json()["rclone_installed"] is True


async def test_missing_rclone_is_degraded_503(test_client, rclone_missing):
    response = await test_client.get("/health")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "degraded"
    assert data["rclone_installed"] is False
    assert data["database_ok"] is True


async def test_database_failure_is_degraded_503(test_client, rclone_present):
    from backend.db.database import get_session
    from backend.main import app

    class BrokenSession:
        async def execute(self, *args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("disk I/O error"))

    async def broken():
        yield BrokenSession()

    app.dependency_overrides[get_session] = broken
    response = await test_client.get("/health")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "degraded"
    assert data["database_ok"] is False


async def test_health_makes_no_rclone_or_provider_calls(test_client, rclone_present):
    engine = health._manager.engines["default"]
    response = await test_client.get("/health")
    assert response.status_code == 200
    engine._rclone.check_installed.assert_not_awaited()
    engine._rclone.check_remote.assert_not_awaited()


async def test_health_remotes_checks_each_profile_remote(test_client):
    engine = health._manager.engines["default"]
    engine._rclone.check_remote = AsyncMock(return_value=True)
    response = await test_client.get("/health/remotes")
    assert response.status_code == 200
    assert response.json() == {"remotes": [
        {"remote": "remote", "accessible": True, "profiles": ["default"], "auth_error": False},
    ]}
    engine._rclone.check_remote.assert_awaited_once_with("remote")


async def test_health_remotes_reports_failure_without_error_text(test_client):
    engine = health._manager.engines["default"]
    engine._rclone.check_remote = AsyncMock(side_effect=RuntimeError("stderr: token /secret/path"))
    response = await test_client.get("/health/remotes")
    assert response.json()["remotes"][0]["accessible"] is False
    assert "secret" not in response.text


async def test_health_remotes_needs_token(test_client):
    from httpx import ASGITransport, AsyncClient

    from backend.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/health/remotes")).status_code == 401
