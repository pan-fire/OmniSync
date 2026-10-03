"""Tests for extended remotes API endpoints (/about, /test, /dependencies, DELETE with deps)."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.tests.auth import AUTH_HEADERS
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.routes import remotes
from backend.api.schemas import BackupMode, BackupTargetType, RemoteResponse
from backend.db.database import get_session
from backend.db.models import BackupTarget, Base, Remote, SyncProfile
from backend.exceptions import RcloneError
from backend.main import app
from backend.services.rclone import RcloneService

_counter = 0


def _unique_slug() -> str:
    global _counter
    _counter += 1
    return f"remote-test-{_counter}"


async def _create_profile(session, slug: str | None = None, remote_dir: str = "gdrive:sync") -> SyncProfile:
    s = slug or _unique_slug()
    now = datetime.now(timezone.utc)
    profile = SyncProfile(
        slug=s,
        name=f"Test {s}",
        local_dir="/sync/local",
        remote_dir=remote_dir,
        debounce_seconds=5,
        pull_interval_minutes=5,
        rclone_filter="[]",
        rclone_args="[]",
        max_retries=3,
        enabled=True,
        created_at=now,
        updated_at=now,
    )
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return profile


async def _create_backup_target(session, profile_id: int, remote_name: str | None = None) -> BackupTarget:
    now = datetime.now(timezone.utc)
    target = BackupTarget(
        profile_id=profile_id,
        name="Backup target",
        target_path="/backups/dest" if not remote_name else f"{remote_name}:backups",
        target_type=BackupTargetType.LOCAL.value if not remote_name else BackupTargetType.REMOTE.value,
        remote_name=remote_name,
        retention_days=7,
        frequency_hours=24,
        backup_mode=BackupMode.MIRROR.value,
        enabled=True,
        created_at=now,
        updated_at=now,
    )
    session.add(target)
    await session.commit()
    await session.refresh(target)
    return target


@pytest_asyncio.fixture
async def remote_client():
    """AsyncClient with mocked rclone and in-memory DB for remotes endpoints."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session

    mock_rclone = MagicMock()
    mock_rclone.about = AsyncMock()
    mock_rclone._run = AsyncMock()
    mock_rclone.delete_remote = AsyncMock()
    mock_rclone.list_remotes = AsyncMock(return_value=[])

    remotes.set_rclone_service(mock_rclone)
    remotes.set_db_factory(factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=AUTH_HEADERS) as client:
        yield client, mock_rclone, factory

    app.dependency_overrides.clear()
    remotes.set_rclone_service(None)
    remotes.set_db_factory(None)
    await engine.dispose()


class TestRemoteAbout:
    @pytest.mark.asyncio
    async def test_about_returns_storage_info(self, remote_client):
        client, mock_rclone, _ = remote_client
        mock_rclone.about.return_value = {
            "total": 15_000_000_000,
            "used": 5_000_000_000,
            "free": 10_000_000_000,
            "trashed": 100_000,
        }

        resp = await client.get("/remotes/gdrive/about")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_bytes"] == 15_000_000_000
        assert data["used_bytes"] == 5_000_000_000
        assert data["supported"] is True

    @pytest.mark.asyncio
    async def test_about_unsupported_returns_supported_false(self, remote_client):
        client, mock_rclone, _ = remote_client
        mock_rclone.about.side_effect = RcloneError("about: unsupported by backend")

        resp = await client.get("/remotes/local/about")
        assert resp.status_code == 200
        data = resp.json()
        assert data["supported"] is False

    @pytest.mark.asyncio
    async def test_about_unsupported_as_rclone_says_it(self, remote_client):
        """rclone 1.75's own wording, as RcloneService.about raises it."""
        client, mock_rclone, _ = remote_client
        mock_rclone.about.side_effect = RcloneError(
            "rclone failed (exit 1): Failed to about: Memory root '' doesn't support about"
        )

        resp = await client.get("/remotes/mem/about")
        assert resp.status_code == 200
        assert resp.json() == {
            "total_bytes": None, "used_bytes": None, "free_bytes": None, "trashed_bytes": None,
            "supported": False,
        }

    @pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")
    @pytest.mark.asyncio
    async def test_about_unsupported_with_real_rclone(self, remote_client, tmp_path):
        client, _, _ = remote_client
        conf = tmp_path / "rclone.conf"
        conf.write_text("[mem]\ntype = memory\n")
        remotes.set_rclone_service(RcloneService(rclone_config_path=str(conf)))

        resp = await client.get("/remotes/mem/about")
        assert resp.status_code == 200
        assert resp.json()["supported"] is False

    @pytest.mark.asyncio
    async def test_about_unreachable_503(self, remote_client):
        client, mock_rclone, _ = remote_client
        mock_rclone.about.side_effect = RcloneError("connection timeout")

        resp = await client.get("/remotes/gdrive/about")
        assert resp.status_code == 503


class TestRemoteTest:
    @pytest.mark.asyncio
    async def test_test_success(self, remote_client):
        client, mock_rclone, _ = remote_client
        mock_rclone._run.return_value = MagicMock(stdout="", stderr="")

        resp = await client.post("/remotes/gdrive/test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["latency_ms"] is not None

    @pytest.mark.asyncio
    async def test_success_records_last_verified(self, remote_client):
        client, mock_rclone, factory = remote_client
        mock_rclone._run.return_value = MagicMock(stdout="", stderr="")
        mock_rclone.list_remotes.return_value = [RemoteResponse(name="gdrive", type="drive")]

        before = datetime.now(timezone.utc)
        assert (await client.post("/remotes/gdrive/test")).json()["success"] is True
        async with factory() as session:
            row = (await session.execute(select(Remote))).scalar_one()
        assert (row.name, row.type) == ("gdrive", "drive")
        assert row.last_verified.replace(tzinfo=timezone.utc) >= before.replace(microsecond=0)

        # A second test updates the same row; the list shows the time.
        assert (await client.post("/remotes/gdrive/test")).json()["success"] is True
        mock_rclone.list_remotes.return_value = [RemoteResponse(name="gdrive", type="drive")]
        listed = (await client.get("/remotes")).json()
        assert listed[0]["name"] == "gdrive" and listed[0]["last_verified"] is not None
        async with factory() as session:
            assert len((await session.execute(select(Remote))).scalars().all()) == 1

    @pytest.mark.asyncio
    async def test_failure_does_not_record_last_verified(self, remote_client):
        client, mock_rclone, factory = remote_client
        mock_rclone._run.side_effect = RcloneError("dial tcp: timeout")

        assert (await client.post("/remotes/gdrive/test")).json()["success"] is False
        async with factory() as session:
            assert (await session.execute(select(Remote))).scalars().all() == []

    @pytest.mark.asyncio
    async def test_test_failure(self, remote_client, caplog):
        client, mock_rclone, _ = remote_client
        mock_rclone._run.side_effect = RcloneError("rclone stderr: dial tcp: timeout")

        resp = await client.post("/remotes/gdrive/test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        # The rclone error goes to the log, not to the client.
        assert "stderr" not in data["error"]
        assert data["error"].startswith("Connection test failed.")
        assert "rclone stderr: dial tcp: timeout" in caplog.text


class TestRemoteDependencies:
    @pytest.mark.asyncio
    async def test_no_dependencies(self, remote_client):
        client, _, _ = remote_client
        resp = await client.get("/remotes/gdrive/dependencies")
        assert resp.status_code == 200
        data = resp.json()
        assert data["profiles"] == []
        assert data["backup_targets"] == []

    @pytest.mark.asyncio
    async def test_profile_dependency(self, remote_client):
        client, _, factory = remote_client
        async with factory() as session:
            await _create_profile(session, remote_dir="gdrive:sync/data")

        resp = await client.get("/remotes/gdrive/dependencies")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["profiles"]) == 1
        assert data["profiles"][0]["name"].startswith("Test ")

    @pytest.mark.asyncio
    async def test_backup_target_dependency(self, remote_client):
        client, _, factory = remote_client
        async with factory() as session:
            profile = await _create_profile(session, remote_dir="onedrive:docs")
            await _create_backup_target(session, profile.id, remote_name="gdrive")

        resp = await client.get("/remotes/gdrive/dependencies")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["backup_targets"]) == 1
        assert data["backup_targets"][0]["target_name"] == "Backup target"


    @pytest.mark.asyncio
    async def test_remote_type_target_found_by_path(self, remote_client):
        """A 'remote' target names its remote only in target_path (remote_name is None)."""
        client, _, factory = remote_client
        async with factory() as session:
            profile = await _create_profile(session, remote_dir="onedrive:docs")
            target = await _create_backup_target(session, profile.id, remote_name="gdrive")
            target.remote_name = None
            await session.commit()

        data = (await client.get("/remotes/gdrive/dependencies")).json()
        assert [t["target_name"] for t in data["backup_targets"]] == ["Backup target"]
        assert data["backup_targets"][0]["profile_slug"] == profile.slug

    @pytest.mark.asyncio
    async def test_similar_names_and_local_paths_are_not_dependencies(self, remote_client):
        """'g_drive' is not 'gXdrive' (LIKE wildcard), 'gdrive2:' is not 'gdrive:', a local target never counts."""
        client, _, factory = remote_client
        async with factory() as session:
            profile = await _create_profile(session, remote_dir="gXdrive:sync")
            target = await _create_backup_target(session, profile.id, remote_name="gdrive2")
            target.remote_name = None
            local = await _create_backup_target(session, profile.id)
            local.target_path = "g_drive:relative/legacy"
            await session.commit()

        for name in ("g_drive", "gdrive"):
            data = (await client.get(f"/remotes/{name}/dependencies")).json()
            assert data == {"profiles": [], "backup_targets": []}, name


class TestDeleteRemoteWithDeps:
    @pytest.mark.asyncio
    async def test_delete_remote_used_by_remote_target_409(self, remote_client):
        client, mock_rclone, factory = remote_client
        async with factory() as session:
            profile = await _create_profile(session, remote_dir="onedrive:docs")
            target = await _create_backup_target(session, profile.id, remote_name="gdrive")
            target.remote_name = None
            await session.commit()

        resp = await client.delete("/remotes/gdrive")
        assert resp.status_code == 409
        mock_rclone.delete_remote.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_with_deps_409(self, remote_client):
        client, mock_rclone, factory = remote_client
        async with factory() as session:
            await _create_profile(session, remote_dir="gdrive:sync/data")

        resp = await client.delete("/remotes/gdrive")
        assert resp.status_code == 409
        assert "dependencies" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_delete_with_deps_force_200(self, remote_client):
        client, mock_rclone, factory = remote_client
        async with factory() as session:
            await _create_profile(session, remote_dir="gdrive:sync/data")

        resp = await client.delete("/remotes/gdrive?force=true")
        assert resp.status_code == 200
        mock_rclone.delete_remote.assert_called_once_with("gdrive")

    @pytest.mark.asyncio
    async def test_delete_no_deps_200(self, remote_client):
        client, mock_rclone, _ = remote_client
        resp = await client.delete("/remotes/myremote")
        assert resp.status_code == 200
        mock_rclone.delete_remote.assert_called_once_with("myremote")
