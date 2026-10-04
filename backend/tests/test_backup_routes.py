"""Tests for backup API routes."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.tests.auth import AUTH_HEADERS
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.routes import backups
from backend.api.schemas import BackupMode, BackupTargetType, SnapshotResponse
from backend.db.database import get_session
from backend.db.models import BackupJob, BackupJobStatus, BackupTarget, Base, SyncProfile
from backend.exceptions import SyncBusyError
from backend.services.backup_service import BackupRunning
from backend.main import app

_counter = 0


def _unique_slug() -> str:
    global _counter
    _counter += 1
    return f"backup-route-test-{_counter}"


async def _create_profile(session, slug: str | None = None) -> SyncProfile:
    """Insert a SyncProfile and return it."""
    s = slug or _unique_slug()
    now = datetime.now(timezone.utc)
    profile = SyncProfile(
        slug=s,
        name=f"Test {s}",
        local_dir="/sync/local",
        remote_dir="remote:backup",
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


async def _create_target(
    session, profile_id: int, name: str = "My backup",
) -> BackupTarget:
    """Insert a BackupTarget and return it."""
    now = datetime.now(timezone.utc)
    target = BackupTarget(
        profile_id=profile_id,
        name=name,
        target_path="/backups/dest",
        target_type=BackupTargetType.LOCAL.value,
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
async def backup_client():
    """AsyncClient with mocked BackupService and in-memory DB."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session

    mock_service = MagicMock(spec_set=[
        "schedule_target", "unschedule_target", "reschedule_target",
        "get_next_run_time", "start_backup", "list_snapshots", "start_restore", "remote_names",
    ])
    mock_service.get_next_run_time = MagicMock(return_value=None)
    mock_service.schedule_target = MagicMock()
    mock_service.unschedule_target = MagicMock()
    mock_service.reschedule_target = AsyncMock()
    mock_service.start_backup = AsyncMock()
    mock_service.list_snapshots = AsyncMock(return_value=[])
    mock_service.start_restore = AsyncMock()
    mock_service.remote_names = AsyncMock(return_value={"gdrive", "b2", "box", "my_nas-2", "remote"})

    backups.set_backup_service(mock_service)
    backups.set_db_factory(factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=AUTH_HEADERS) as client:
        yield client, mock_service, factory

    app.dependency_overrides.clear()
    backups.set_backup_service(None)
    backups.set_db_factory(None)
    await engine.dispose()


class TestCreateTarget:
    @pytest.mark.asyncio
    async def test_create_target_201(self, backup_client):
        client, svc, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            slug = profile.slug

        resp = await client.post(f"/profiles/{slug}/backups", json={
            "name": "Daily backup",
            "target_path": "/backups/daily",
            "target_type": "local",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Daily backup"
        assert data["target_path"] == "/backups/daily"
        assert data["target_type"] == "local"
        assert data["backup_mode"] == "mirror"
        assert data["retention_days"] == 7
        assert data["enabled"] is True
        svc.schedule_target.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_target_unknown_profile_404(self, backup_client):
        client, *_ = backup_client
        resp = await client.post("/profiles/no-such-profile/backups", json={
            "name": "X",
            "target_path": "/x",
            "target_type": "local",
        })
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_create_target_path_same_as_local_dir_400(self, backup_client):
        client, _, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            slug = profile.slug

        resp = await client.post(f"/profiles/{slug}/backups", json={
            "name": "bad",
            "target_path": "/sync/local",
            "target_type": "local",
        })
        assert resp.status_code == 400
        assert "local_dir" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_target_path_same_as_remote_dir_400(self, backup_client):
        client, _, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            slug = profile.slug

        resp = await client.post(f"/profiles/{slug}/backups", json={
            "name": "bad",
            "target_path": "remote:backup",
            "target_type": "remote",
        })
        assert resp.status_code == 400
        assert "remote_dir" in resp.json()["detail"]


class TestListTargets:
    @pytest.mark.asyncio
    async def test_list_targets_empty(self, backup_client):
        client, _, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            slug = profile.slug

        resp = await client.get(f"/profiles/{slug}/backups")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_list_targets_returns_targets(self, backup_client):
        client, _, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            slug = profile.slug
            await _create_target(session, profile.id, "First")
            await _create_target(session, profile.id, "Second")

        resp = await client.get(f"/profiles/{slug}/backups")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = {t["name"] for t in data}
        assert names == {"First", "Second"}


class TestGetTarget:
    @pytest.mark.asyncio
    async def test_get_target_200(self, backup_client):
        client, _, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            slug = profile.slug
            target = await _create_target(session, profile.id)
            target_id = target.id

        resp = await client.get(f"/profiles/{slug}/backups/{target_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "My backup"

    @pytest.mark.asyncio
    async def test_get_target_wrong_profile_404(self, backup_client):
        client, _, factory = backup_client
        async with factory() as session:
            p1 = await _create_profile(session)
            p2 = await _create_profile(session)
            target = await _create_target(session, p1.id)
            target_id = target.id

        resp = await client.get(f"/profiles/{p2.slug}/backups/{target_id}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_target_nonexistent_404(self, backup_client):
        client, _, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            slug = profile.slug

        resp = await client.get(f"/profiles/{slug}/backups/9999")
        assert resp.status_code == 404


class TestUpdateTarget:
    @pytest.mark.asyncio
    async def test_update_target_200(self, backup_client):
        client, svc, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            slug = profile.slug
            target = await _create_target(session, profile.id)
            target_id = target.id

        resp = await client.put(f"/profiles/{slug}/backups/{target_id}", json={
            "name": "Renamed",
            "frequency_hours": 12,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Renamed"
        assert data["frequency_hours"] == 12
        # Frequency changed, should reschedule
        svc.reschedule_target.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_target_path_overlap_400(self, backup_client):
        client, _, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            slug = profile.slug
            target = await _create_target(session, profile.id)
            target_id = target.id

        resp = await client.put(f"/profiles/{slug}/backups/{target_id}", json={
            "target_path": "/sync/local",
        })
        assert resp.status_code == 400


class TestDeleteTarget:
    @pytest.mark.asyncio
    async def test_delete_without_confirm_400(self, backup_client):
        client, _, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            slug = profile.slug
            target = await _create_target(session, profile.id)
            target_id = target.id

        resp = await client.delete(f"/profiles/{slug}/backups/{target_id}")
        assert resp.status_code == 400
        assert "confirm" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_delete_with_confirm_204(self, backup_client):
        client, svc, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            slug = profile.slug
            target = await _create_target(session, profile.id)
            target_id = target.id

        resp = await client.delete(f"/profiles/{slug}/backups/{target_id}?confirm=true")
        assert resp.status_code == 204
        svc.unschedule_target.assert_called_once_with(target_id)

        # Verify actually deleted
        async with factory() as session:
            result = await session.execute(
                select(BackupTarget).where(BackupTarget.id == target_id)
            )
            assert result.scalar_one_or_none() is None


def _running_job(job_id: int, target_id: int, direction: str = "backup", snapshot_id: str | None = None) -> BackupJob:
    return BackupJob(id=job_id, target_id=target_id, started_at=datetime.now(timezone.utc),
                     status=BackupJobStatus.RUNNING.value, direction=direction, snapshot_id=snapshot_id)


async def _profile_and_target(factory) -> tuple[str, int]:
    async with factory() as session:
        profile = await _create_profile(session)
        target = await _create_target(session, profile.id)
        return profile.slug, target.id


class TestRunBackup:
    @pytest.mark.asyncio
    async def test_run_backup_answers_202_with_the_running_job(self, backup_client):
        client, svc, factory = backup_client
        slug, target_id = await _profile_and_target(factory)
        svc.start_backup.return_value = _running_job(1, target_id)

        resp = await client.post(f"/profiles/{slug}/backups/{target_id}/run")
        assert resp.status_code == 202
        assert (resp.json()["id"], resp.json()["status"], resp.json()["error_code"]) == (1, "running", None)
        svc.start_backup.assert_awaited_once_with(target_id)

    @pytest.mark.asyncio
    async def test_run_backup_already_running_409(self, backup_client):
        client, svc, factory = backup_client
        slug, target_id = await _profile_and_target(factory)
        svc.start_backup.side_effect = BackupRunning("Backup already running")

        resp = await client.post(f"/profiles/{slug}/backups/{target_id}/run")
        assert (resp.status_code, resp.json()["code"]) == (409, "backup_running")

    @pytest.mark.asyncio
    async def test_run_backup_while_the_profile_is_busy_409(self, backup_client):
        client, svc, factory = backup_client
        slug, target_id = await _profile_and_target(factory)
        svc.start_backup.side_effect = SyncBusyError("A sync of this profile is running.")

        resp = await client.post(f"/profiles/{slug}/backups/{target_id}/run")
        assert (resp.status_code, resp.json()["code"]) == (409, "sync_busy")


class TestBackupJob:
    @pytest.mark.asyncio
    async def test_a_failed_job_has_a_code_and_a_fixed_message_never_stderr(self, backup_client):
        client, _, factory = backup_client
        slug, target_id = await _profile_and_target(factory)
        stderr = "ERROR : docs/a.txt: Failed to copy: googleapi: Error 403: token=ya29.secret quota"
        async with factory() as session:
            job = BackupJob(target_id=target_id, started_at=datetime.now(timezone.utc),
                            finished_at=datetime.now(timezone.utc), status=BackupJobStatus.FAILED.value,
                            direction="backup", error_code="backup_failed", error_message=stderr)
            legacy = BackupJob(target_id=target_id, started_at=datetime.now(timezone.utc),
                               status=BackupJobStatus.FAILED.value, direction="restore", error_message=stderr)
            session.add_all([job, legacy])
            await session.commit()
            ids = job.id, legacy.id

        for job_id, code in zip(ids, ("backup_failed", "restore_failed"), strict=True):
            resp = await client.get(f"/profiles/{slug}/backups/{target_id}/jobs/{job_id}")
            assert resp.status_code == 200
            body = resp.json()
            assert body["error_code"] == code
            assert body["error_message"].endswith("The OmniSync log has the details.")
            for fragment in ("googleapi", "403", "ya29", "docs/a.txt", "Failed to copy"):
                assert fragment not in resp.text

    @pytest.mark.asyncio
    async def test_own_refusals_are_shown_as_they_are(self, backup_client):
        client, _, factory = backup_client
        slug, target_id = await _profile_and_target(factory)
        refusal = "Refusing to back up: the local folder '/data/docs' is empty while the backup holds 3 item(s)."
        async with factory() as session:
            job = BackupJob(target_id=target_id, started_at=datetime.now(timezone.utc),
                            status=BackupJobStatus.FAILED.value, direction="backup",
                            error_code="backup_refused", error_message=refusal)
            session.add(job)
            await session.commit()
            job_id = job.id
        body = (await client.get(f"/profiles/{slug}/backups/{target_id}/jobs/{job_id}")).json()
        assert (body["error_code"], body["error_message"]) == ("backup_refused", refusal)

    @pytest.mark.asyncio
    async def test_a_job_of_another_target_is_not_found(self, backup_client):
        client, _, factory = backup_client
        slug, target_id = await _profile_and_target(factory)
        _, other_target = await _profile_and_target(factory)
        async with factory() as session:
            job = BackupJob(target_id=other_target, started_at=datetime.now(timezone.utc),
                            status=BackupJobStatus.RUNNING.value, direction="backup")
            session.add(job)
            await session.commit()
            job_id = job.id
        resp = await client.get(f"/profiles/{slug}/backups/{target_id}/jobs/{job_id}")
        assert (resp.status_code, resp.json()["code"]) == (404, "backup_job_not_found")


class TestSnapshots:
    @pytest.mark.asyncio
    async def test_list_snapshots_200(self, backup_client):
        client, svc, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            slug = profile.slug
            target = await _create_target(session, profile.id)
            target_id = target.id

        svc.list_snapshots.return_value = [
            SnapshotResponse(
                snapshot_id="2025-01-01T00-00-00",
                created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                size_bytes=512,
                status="available",
                latest=True,
            ),
            SnapshotResponse(
                snapshot_id="2024-12-31T00-00-00",
                created_at=datetime(2024, 12, 31, tzinfo=timezone.utc),
                status="available",
            ),
        ]

        resp = await client.get(f"/profiles/{slug}/backups/{target_id}/snapshots")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["snapshot_id"] == "2025-01-01T00-00-00"
        assert (data[0]["latest"], data[1]["latest"]) == (True, False)
        assert "kind" not in data[0]


class TestRestore:
    @pytest.mark.asyncio
    async def test_restore_202(self, backup_client):
        client, svc, factory = backup_client
        slug, target_id = await _profile_and_target(factory)
        svc.start_restore.return_value = _running_job(2, target_id, "restore", "2025-01-01T00-00-00")

        resp = await client.post(f"/profiles/{slug}/backups/{target_id}/restore", json={
            "snapshot_id": "2025-01-01T00-00-00",
            "restore_scope": "local_only",
        })
        assert resp.status_code == 202
        assert (resp.json()["direction"], resp.json()["status"]) == ("restore", "running")
        assert svc.start_restore.await_args.args[1] == "2025-01-01T00-00-00"

    @pytest.mark.asyncio
    async def test_restore_while_the_profile_is_busy_is_a_409_not_a_500(self, backup_client):
        client, svc, factory = backup_client
        slug, target_id = await _profile_and_target(factory)
        svc.start_restore.side_effect = SyncBusyError("A sync of this profile is running.")

        resp = await client.post(f"/profiles/{slug}/backups/{target_id}/restore", json={
            "snapshot_id": "2025-01-01T00-00-00", "restore_scope": "both",
        })
        assert (resp.status_code, resp.json()["code"]) == (409, "sync_busy")


class TestTargetValidation:
    """target_path / remote_name are checked before they are stored.

    They go to rclone and the filesystem as stored, so a relative local
    path, a remote path without '<remote>:', a leading '-' or a control
    character is refused on create and on update.
    """

    @staticmethod
    async def _slug(factory) -> str:
        async with factory() as session:
            return (await _create_profile(session)).slug

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", [
        {"target_type": "local", "target_path": "backups/daily"},
        {"target_type": "local", "target_path": "-backups"},
        {"target_type": "local", "target_path": "/backups/a\nb"},
        {"target_type": "local", "target_path": "/backups/a\x00b"},
        {"target_type": "remote", "target_path": "/mnt/backups"},
        {"target_type": "remote", "target_path": "Backups"},
        {"target_type": "remote", "target_path": "-gdrive:Backups"},
        {"target_type": "remote", "target_path": "--config=/tmp/x:Backups"},
        {"target_type": "custom_remote", "target_path": "box:Backups", "remote_name": "gdrive"},
        {"target_type": "custom_remote", "target_path": "gdrive:Backups", "remote_name": "-gdrive"},
        {"target_type": "custom_remote", "target_path": "gdrive:Backups", "remote_name": "g drive"},
    ])
    async def test_create_refuses_invalid_target(self, backup_client, body):
        client, svc, factory = backup_client
        slug = await self._slug(factory)
        resp = await client.post(f"/profiles/{slug}/backups", json={"name": "bad", **body})
        assert resp.status_code == 422, resp.text
        svc.schedule_target.assert_not_called()
        async with factory() as session:
            assert (await session.execute(select(BackupTarget))).scalars().all() == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", [
        {"target_type": "local", "target_path": "/mnt/backups"},
        {"target_type": "remote", "target_path": "gdrive:Backups/docs"},
        {"target_type": "remote", "target_path": "my_nas-2:"},
        {"target_type": "custom_remote", "target_path": "b2:bucket/docs", "remote_name": "b2"},
        {"target_type": "custom_remote", "target_path": "b2:bucket/docs", "remote_name": ""},
    ])
    async def test_create_accepts_valid_target(self, backup_client, body):
        client, _, factory = backup_client
        slug = await self._slug(factory)
        resp = await client.post(f"/profiles/{slug}/backups", json={"name": "ok", **body})
        assert resp.status_code == 201, resp.text
        assert resp.json()["target_path"] == body["target_path"]

    @pytest.mark.asyncio
    async def test_update_checks_path_against_stored_type(self, backup_client):
        client, _, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            target_id = (await _create_target(session, profile.id)).id  # local, /backups/dest
        url = f"/profiles/{profile.slug}/backups/{target_id}"

        assert (await client.put(url, json={"target_path": "gdrive:Backups"})).status_code == 422
        assert (await client.put(url, json={"target_type": "remote"})).status_code == 422
        assert (await client.put(url, json={"target_path": "-rf"})).status_code == 422
        assert (await client.put(url, json={"remote_name": "bad name"})).status_code == 422
        resp = await client.put(url, json={"target_type": "remote", "target_path": "gdrive:Backups"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["target_type"] == "remote"
        resp = await client.put(url, json={"target_type": "custom_remote", "remote_name": "box"})
        assert resp.status_code == 422
        assert "box" in resp.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", [
        {"target_type": "remote", "target_path": "onedrive:Backups"},
        {"target_type": "custom_remote", "target_path": "onedrive:Backups", "remote_name": "onedrive"},
        {"target_type": "custom_remote", "target_path": "onedrive:Backups", "remote_name": ""},
    ])
    async def test_create_refuses_remote_that_is_not_configured(self, backup_client, body):
        client, svc, factory = backup_client
        slug = await self._slug(factory)
        resp = await client.post(f"/profiles/{slug}/backups", json={"name": "gone", **body})
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == "Remote 'onedrive' is not configured"
        svc.schedule_target.assert_not_called()
        async with factory() as session:
            assert (await session.execute(select(BackupTarget))).scalars().all() == []

    @pytest.mark.asyncio
    async def test_local_target_does_not_list_remotes(self, backup_client):
        client, svc, factory = backup_client
        slug = await self._slug(factory)
        resp = await client.post(
            f"/profiles/{slug}/backups",
            json={"name": "nas", "target_type": "local", "target_path": "/mnt/backups"},
        )
        assert resp.status_code == 201, resp.text
        svc.remote_names.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_refuses_remote_that_is_not_configured(self, backup_client):
        client, svc, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            target_id = (await _create_target(session, profile.id)).id
        url = f"/profiles/{profile.slug}/backups/{target_id}"

        resp = await client.put(url, json={"target_type": "custom_remote", "target_path": "gone:Backups"})
        assert resp.status_code == 422
        assert resp.json()["detail"] == "Remote 'gone' is not configured"
        # Changes that leave the path alone do not ask rclone.
        svc.remote_names.reset_mock()
        assert (await client.put(url, json={"name": "renamed"})).status_code == 200
        svc.remote_names.assert_not_called()

    @pytest.mark.asyncio
    async def test_remote_list_failure_is_503(self, backup_client):
        client, svc, factory = backup_client
        svc.remote_names.side_effect = RuntimeError("rclone config unreadable")
        slug = await self._slug(factory)
        resp = await client.post(
            f"/profiles/{slug}/backups",
            json={"name": "x", "target_type": "remote", "target_path": "gdrive:Backups"},
        )
        assert resp.status_code == 503
        assert "rclone config unreadable" not in resp.text

    @pytest.mark.asyncio
    async def test_update_of_other_fields_skips_path_checks(self, backup_client):
        """A stored target from before the checks can still be renamed or disabled."""
        client, _, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            target = await _create_target(session, profile.id)
            target.target_path = "relative/legacy"
            await session.commit()
            target_id = target.id
        resp = await client.put(f"/profiles/{profile.slug}/backups/{target_id}", json={"enabled": False})
        assert resp.status_code == 200, resp.text


class TestTargetOverlap:
    """Two backup targets may not share a folder, on create or on update."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/backups/dest", "/backups/dest/", "/backups/dest/inner", "/backups"])
    async def test_create_refuses_overlapping_target(self, backup_client, path):
        client, svc, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            await _create_target(session, profile.id, "Existing")  # /backups/dest
        resp = await client.post(f"/profiles/{profile.slug}/backups", json={
            "name": "Second", "target_path": path, "target_type": "local",
        })
        assert resp.status_code == 400
        assert "overlaps backup target 'Existing'" in resp.json()["detail"]
        svc.schedule_target.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_refuses_overlap_with_other_profiles_target(self, backup_client):
        client, _, factory = backup_client
        async with factory() as session:
            p1 = await _create_profile(session)
            p2 = await _create_profile(session)
            await _create_target(session, p1.id, "Other profile's")
        resp = await client.post(f"/profiles/{p2.slug}/backups", json={
            "name": "Mine", "target_path": "/backups/dest", "target_type": "local",
        })
        assert resp.status_code == 400
        assert "Other profile's" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_allows_sibling_and_other_remote(self, backup_client):
        client, _, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            await _create_target(session, profile.id)  # /backups/dest
        for body in ({"target_type": "local", "target_path": "/backups/dest2"},
                     {"target_type": "remote", "target_path": "gdrive:backups/dest"}):
            resp = await client.post(f"/profiles/{profile.slug}/backups", json={"name": "n", **body})
            assert resp.status_code == 201, resp.text

    @pytest.mark.asyncio
    async def test_update_refuses_overlapping_target(self, backup_client):
        client, _, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            await _create_target(session, profile.id, "Existing")  # /backups/dest
            second = await _create_target(session, profile.id, "Second")
            second.target_path = "/elsewhere"
            await session.commit()
            second_id = second.id
        url = f"/profiles/{profile.slug}/backups/{second_id}"
        resp = await client.put(url, json={"target_path": "/backups/dest/sub"})
        assert resp.status_code == 400
        assert "overlaps backup target 'Existing'" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_update_keeping_own_path_is_not_an_overlap(self, backup_client):
        """The edit form sends the unchanged path back: a target never overlaps itself."""
        client, _, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            target_id = (await _create_target(session, profile.id)).id
        resp = await client.put(f"/profiles/{profile.slug}/backups/{target_id}", json={
            "name": "Renamed", "target_path": "/backups/dest", "target_type": "local", "remote_name": None,
        })
        assert resp.status_code == 200, resp.text


class TestKeepLastAndOverdue:
    @pytest.mark.asyncio
    async def test_keep_last_defaults_to_3_and_can_be_changed(self, backup_client):
        client, _, factory = backup_client
        async with factory() as session:
            slug = (await _create_profile(session)).slug

        resp = await client.post(f"/profiles/{slug}/backups", json={
            "name": "Daily", "target_path": "/backups/keep", "target_type": "local",
        })
        assert resp.status_code == 201
        assert resp.json()["keep_last"] == 3
        target_id = resp.json()["id"]

        resp = await client.put(f"/profiles/{slug}/backups/{target_id}", json={"keep_last": 10})
        assert resp.status_code == 200 and resp.json()["keep_last"] == 10
        resp = await client.put(f"/profiles/{slug}/backups/{target_id}", json={"keep_last": 0})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_overdue_after_two_intervals_without_a_completed_backup(self, backup_client):
        from datetime import timedelta

        from backend.db.models import BackupJob

        client, _, factory = backup_client
        now = datetime.now(timezone.utc)
        async with factory() as session:
            profile = await _create_profile(session)
            slug = profile.slug
            target = await _create_target(session, profile.id)
            target_id = target.id
            target.created_at = now - timedelta(days=10)
            session.add(BackupJob(target_id=target_id, started_at=now - timedelta(hours=50),
                                  finished_at=now - timedelta(hours=50), status="completed", direction="backup"))
            # A recent failure does not make it current.
            session.add(BackupJob(target_id=target_id, started_at=now - timedelta(hours=1),
                                  finished_at=now - timedelta(hours=1), status="failed", direction="backup"))
            await session.commit()

        resp = await client.get(f"/profiles/{slug}/backups/{target_id}")
        assert resp.json()["overdue"] is True
        resp = await client.get(f"/profiles/{slug}/backups")
        assert [t["overdue"] for t in resp.json()] == [True]

        async with factory() as session:
            session.add(BackupJob(target_id=target_id, started_at=now, finished_at=now,
                                  status="completed", direction="backup"))
            await session.commit()
        resp = await client.get(f"/profiles/{slug}/backups/{target_id}")
        assert resp.json()["overdue"] is False

    @pytest.mark.asyncio
    async def test_disabled_or_new_targets_are_not_overdue(self, backup_client):
        client, _, factory = backup_client
        async with factory() as session:
            profile = await _create_profile(session)
            slug = profile.slug
            target = await _create_target(session, profile.id)
            target_id = target.id

        resp = await client.get(f"/profiles/{slug}/backups/{target_id}")
        assert resp.json()["overdue"] is False  # created just now

        resp = await client.put(f"/profiles/{slug}/backups/{target_id}", json={"enabled": False})
        assert resp.json()["overdue"] is False
