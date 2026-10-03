"""Test fixtures for OmniSync backend tests."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from backend.tests.auth import AUTH_HEADERS, TEST_API_TOKEN, TEST_HOST

# Before the app is imported: the suite runs against the real token check and
# host guard (backend/security.py) with these credentials.
os.environ["OMNISYNC_API_TOKEN"] = TEST_API_TOKEN
os.environ["OMNISYNC_ALLOWED_HOSTS"] = TEST_HOST

import pytest
import pytest_asyncio
from hypothesis import HealthCheck, settings
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.schemas import GlobalConfigResponse
from backend.api.wiring import RouteServices, unwire_routes, wire_routes
from backend.db.models import Base
from backend.services.config import ConfigService
from backend.services.rclone import RcloneResult

# Property tests check behaviour, not speed: on a busy CI runner hypothesis'
# per-example deadline and "input generation is slow" check fail tests at
# random. Tests that set their own deadline keep it.
settings.register_profile(
    "omnisync", deadline=None, suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("omnisync")


@pytest.fixture(autouse=True)
def _fresh_auth_throttle():
    """Each test starts with no recorded authentication failures, so tests
    that send wrong tokens cannot get later ones answered with 429."""
    from backend import security

    security.reset_auth_throttle()
    yield
    security.reset_auth_throttle()


@pytest_asyncio.fixture
async def db_session():
    """Provide a fresh in-memory SQLite database session for each test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def test_db_factory():
    """Session factory of the in-memory database behind test_client.

    Seed rows with it, then read them back through the API.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def test_services(test_db_factory) -> RouteServices:
    """The services test_client wires into every router (mocks, except the database)."""
    from backend.models.sync_state import SyncStateManager
    from backend.services.backup_service import BackupService
    from backend.services.log_reader import LogReader
    from backend.services.notification_channels.webpush import WebPushChannel
    from backend.services.notification_dispatcher import NotificationDispatcher

    # Build a mock sync engine with sensible defaults
    mock_engine = AsyncMock()
    mock_engine._state = SyncStateManager()
    mock_engine._rclone = AsyncMock()
    mock_engine._rclone.check_installed = AsyncMock(return_value=True)
    mock_engine._rclone.check_remote = AsyncMock(return_value=True)
    mock_engine._profile = AsyncMock()
    mock_engine._profile.remote_dir = "remote:backup"
    mock_engine._profile.slug = "default"
    mock_engine._profile.name = "Default"
    mock_engine.get_status = AsyncMock(
        return_value=mock_engine._state.to_status_response()
    )

    # Build a mock manager that delegates to the single mock engine
    mock_manager = AsyncMock()
    mock_manager.engines = {"default": mock_engine}
    mock_manager.get_engine.return_value = mock_engine
    # The profile-scoped routes resolve slug -> profile id -> engine: the
    # mock engine runs profile 1, slug "default".
    mock_manager.get_engine_by_id = MagicMock(side_effect=lambda pid: mock_engine if pid == 1 else None)

    async def _get_by_slug(slug: str):
        from backend.exceptions import ProfileNotFoundError
        if slug != "default":
            raise ProfileNotFoundError(slug)
        return SimpleNamespace(id=1, slug="default", name="Default")

    mock_profile_service = MagicMock()
    mock_profile_service.get_by_slug = AsyncMock(side_effect=_get_by_slug)

    # Build a mock config service
    mock_config_service = MagicMock(spec_set=ConfigService)
    mock_config_service.read_global.return_value = GlobalConfigResponse()

    return RouteServices(
        manager=mock_manager,
        profile_service=mock_profile_service,
        rclone=mock_engine._rclone,
        config_service=mock_config_service,
        db_factory=test_db_factory,
        log_reader=LogReader(),
        # spec'd mocks: async methods become AsyncMocks, the others plain mocks
        dispatcher=MagicMock(spec=NotificationDispatcher),
        webpush_channel=MagicMock(spec=WebPushChannel),
        backup_service=MagicMock(spec=BackupService),
    )


@pytest_asyncio.fixture
async def test_client(test_services: RouteServices):
    """httpx AsyncClient connected to the FastAPI app, every router wired (see test_services)."""
    from backend.db.database import get_session
    from backend.main import app

    factory = test_services.db_factory
    assert factory is not None

    async def _override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    wire_routes(test_services)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=AUTH_HEADERS) as client:
        yield client

    app.dependency_overrides.clear()
    unwire_routes()


@pytest.fixture
def mock_rclone():
    """Patched RcloneService that returns configurable results without subprocess calls."""
    mock = AsyncMock()
    mock.check_installed = AsyncMock(return_value=True)
    mock.check_remote = AsyncMock(return_value=True)
    mock.list_remotes = AsyncMock(return_value=[])
    mock.sync = AsyncMock(
        return_value=RcloneResult(
            stdout="", stderr="", return_code=0, elapsed_seconds=0.1
        )
    )
    return mock


@pytest.fixture
def tmp_config(tmp_path):
    """Temporary TOML config file for config service tests."""
    config_file = tmp_path / "config.toml"
    return config_file
