"""The app's lifespan (backend/main.py): what a start sets up and a stop takes down.

The lifespan runs for real against a database, config and rclone config in a
temporary folder: migrations, job recovery, notification channels, engines,
schedulers and route wiring. Requests go through the app in-process
(ASGITransport does not run the lifespan itself; the tests drive it).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError

from backend import main
from backend.api.routes import backups, config, health, notifications
from backend.db import database
from backend.services.notification_channels.webpush import WebPushChannel
from backend.services.notification_dispatcher import NotificationDispatcher
from backend.tests.auth import AUTH_HEADERS

NOW = "2026-01-01 00:00:00.000000"


@pytest_asyncio.fixture(autouse=True)
async def _restore_globals() -> AsyncIterator[None]:
    """The lifespan replaces the database engine and sets log levels; put both back."""
    saved_db = database._engine, database._async_session_factory
    loggers = [logging.getLogger(), *(logging.getLogger(n) for n in list(logging.root.manager.loggerDict))]
    saved_levels = {lg: lg.level for lg in loggers}
    database._engine = None
    yield
    if database._engine is not None:
        await database._engine.dispose()
    database._engine, database._async_session_factory = saved_db
    for lg, level in saved_levels.items():
        lg.setLevel(level)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every file the lifespan touches lives in one temporary folder (the rclone config one level deeper)."""
    tmp_path = tmp_path / "data"
    monkeypatch.setenv("OMNISYNC_DB_PATH", str(tmp_path / "omnisync.db"))
    monkeypatch.setenv("OMNISYNC_CONFIG_PATH", str(tmp_path / "config.toml"))
    monkeypatch.setenv("OMNISYNC_RCLONE_CONFIG", str(tmp_path / "rclone" / "rclone.conf"))
    monkeypatch.setenv("OMNISYNC_BISYNC_DIR", str(tmp_path / "bisync"))
    monkeypatch.setenv("OMNISYNC_HOST_OS", "linux")
    # WebPushChannel's default key folder is fixed at import; point it here.
    monkeypatch.setattr(main, "WebPushChannel", lambda factory: WebPushChannel(factory, vapid_dir=tmp_path / "vapid"))
    return tmp_path


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test", headers=AUTH_HEADERS)


def _rows(db: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _seed_previous_run(db: Path, local_dir: Path) -> None:
    """A database as a crashed run left it: one enabled profile, one job still "running"."""
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO sync_profiles (id, slug, name, local_dir, remote_dir, debounce_seconds,"
            " pull_interval_minutes, rclone_filter, rclone_args, max_retries, enabled, created_at, updated_at)"
            " VALUES (1, 'docs', 'Docs', ?, 'gdrive:docs', 5, 5, '[]', '[]', 3, 1, ?, ?)",
            (str(local_dir), NOW, NOW),
        )
        conn.execute(
            "INSERT INTO sync_jobs (id, profile_id, direction, started_at, status, files_changed, conflicts, errors)"
            " VALUES (7, 1, 'push', ?, 'running', 0, 0, 0)",
            (NOW,),
        )
        conn.commit()
    finally:
        conn.close()


async def test_start_wires_the_app_and_stop_takes_it_down(data_dir: Path) -> None:
    """A clean start serves requests with real services; after the stop they get 503.

    Also: the job the last run left "running" ends as failed before any
    engine starts, the enabled profile gets an engine, the VAPID keys and
    the rclone config folder are created, and the start is recorded as a
    notification.
    """
    db = data_dir / "omnisync.db"
    await database.init_database(str(db))
    await database._engine.dispose()  # type: ignore[union-attr]
    database._engine = None
    # Missing on purpose: the engine starts paused instead of running rclone.
    _seed_previous_run(db, data_dir.parent / "not-mounted")

    async with main.lifespan(main.app), _client() as client:
        assert (data_dir / "rclone").is_dir()
        assert _rows(db, "SELECT status FROM sync_jobs WHERE id = 7") == [("failed",)]

        manager = health._manager
        assert manager is not None
        engine = manager.get_engine("docs")
        assert engine.profile.profile_id == 1
        assert backups._backup_service is not None
        backup_scheduler = backups._backup_service._scheduler
        engine_scheduler = engine._scheduler
        assert backup_scheduler is not None and backup_scheduler.running
        assert engine_scheduler is not None and engine_scheduler.running

        response = await client.get("/config")
        assert response.status_code == 200
        assert response.json()["log_level"] == "INFO"

        response = await client.get("/notifications/vapid-public-key")
        assert response.status_code == 200
        assert response.json()["public_key"] == (data_dir / "vapid" / "public_key.txt").read_text().strip()

        status = await client.get("/profiles/docs/sync/status")
        assert status.status_code == 200
        assert "not-mounted" in str(status.json())

    events = [row[0] for row in _rows(db, "SELECT event_type FROM notification_log")]
    assert "startup_success" in events
    assert (data_dir / "vapid" / "private_key.pem").is_file()

    # Stopped: no engines, no running scheduler, routes unwired.
    assert manager.engines == {}
    assert not backup_scheduler.running and not engine_scheduler.running
    assert config._config_service is None
    assert notifications._dispatcher is None
    async with _client() as client:
        response = await client.get("/config")
    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"


async def test_vapid_key_failure_is_reported_and_the_start_goes_on(data_dir: Path) -> None:
    """Web Push keys that cannot be created do not stop the app: the start is
    recorded as a failure of that one subsystem instead of a success."""
    data_dir.mkdir(parents=True)
    (data_dir / "vapid").write_text("a file where the key folder belongs")

    async with main.lifespan(main.app), _client() as client:
        assert (await client.get("/config")).status_code == 200

    rows = _rows(data_dir / "omnisync.db", "SELECT event_type, title FROM notification_log")
    assert rows == [("startup_failure", "Startup failure: Web Push (VAPID keys)")]


async def test_broken_config_file_does_not_stop_the_start(data_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A hand-edited config.toml that is not valid TOML: the start logs it and goes on."""
    data_dir.mkdir(parents=True)
    (data_dir / "config.toml").write_text("log_level = \n")

    async with main.lifespan(main.app), _client() as client:
        assert (await client.get("/config")).status_code == 500

    assert "Could not apply the configured log level" in caplog.text


async def test_unusable_database_stops_the_start_before_anything_is_wired(data_dir: Path) -> None:
    """The database is the first step: if it cannot be opened the app does not
    start, and no route is handed half-initialized services."""
    (data_dir / "omnisync.db").mkdir(parents=True)  # a folder where the database file belongs

    with pytest.raises(OperationalError, match="unable to open database file"):
        async with main.lifespan(main.app):
            pytest.fail("the app must not start without its database")

    assert config._config_service is None and health._manager is None
    assert not (data_dir / "vapid").exists()


async def test_failing_engine_start_fails_the_start(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An error starting the engines propagates (uvicorn then exits) instead of
    serving an app with no engines; the routes are never wired."""
    async def broken_start_all(self) -> None:
        raise RuntimeError("engine start failed")

    monkeypatch.setattr(main.SyncEngineManager, "start_all", broken_start_all)

    with pytest.raises(RuntimeError, match="engine start failed"):
        async with main.lifespan(main.app):
            pytest.fail("the app must not start without its engines")

    assert config._config_service is None and backups._backup_service is None


async def test_failing_startup_notification_does_not_stop_the_start(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Announcing the start is best effort: a dispatcher error there must not
    take down an otherwise healthy app."""
    async def broken_dispatch(self, event, only_channel=None):
        raise RuntimeError("notification log unavailable")

    monkeypatch.setattr(NotificationDispatcher, "dispatch", broken_dispatch)

    async with main.lifespan(main.app), _client() as client:
        assert (await client.get("/config")).status_code == 200

    assert _rows(data_dir / "omnisync.db", "SELECT COUNT(*) FROM notification_log") == [(0,)]
