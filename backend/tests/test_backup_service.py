"""Tests for BackupService scheduling, execution, liveness, and retention."""

from __future__ import annotations

import asyncio

import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.schemas import BackupMode, BackupTargetType, RestoreScope
from backend.db.models import (
    BackupJob,
    BackupJobStatus,
    BackupTarget,
    Base,
    SyncProfile,
)
from backend.services.backup_service import BACKUP_FILTER, OVERDUE_CHECK_JOB, BackupService


@pytest_asyncio.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _now() -> datetime:
    return datetime.now(timezone.utc)


_profile_counter = 0


async def _make_profile_and_target(
    db_factory,
    *,
    backup_mode: str = BackupMode.MIRROR.value,
    target_type: str = BackupTargetType.LOCAL.value,
    target_path: str = "/backups/daily",
    enabled: bool = True,
) -> tuple[int, int, str]:
    """Create a profile + target, return (profile_id, target_id, profile_slug)."""
    global _profile_counter
    _profile_counter += 1
    slug = f"test-{_profile_counter}"
    now = _now()
    async with db_factory() as session:
        profile = SyncProfile(
            name=f"Test {_profile_counter}", slug=slug, local_dir="/sync/local",
            remote_dir="remote:data", created_at=now, updated_at=now,
        )
        session.add(profile)
        await session.flush()
        target = BackupTarget(
            profile_id=profile.id, name="Daily",
            target_path=target_path, target_type=target_type,
            backup_mode=backup_mode, enabled=enabled,
            retention_days=7, frequency_hours=24,
            created_at=now, updated_at=now,
        )
        session.add(target)
        await session.commit()
        return profile.id, target.id, profile.slug


def _make_service(db_factory, **overrides) -> BackupService:
    """Build a BackupService with mocked dependencies."""
    rclone = overrides.get("rclone", AsyncMock())
    dispatcher = overrides.get("dispatcher", AsyncMock())
    manager = overrides.get("manager", MagicMock())

    # Default mock engine with idle state
    if "manager" not in overrides:
        mock_state = MagicMock()
        mock_state.to_status_response.return_value = MagicMock(
            state=MagicMock(value="idle")
        )
        mock_engine = MagicMock()
        mock_engine.hold = AsyncMock()
        mock_engine._state = mock_state
        mock_engine.sync_lock = asyncio.Lock()
        manager.get_engine.return_value = mock_engine
    if isinstance(manager, MagicMock) and manager.sync_lock.side_effect is None:
        # The profile's sync lock is the one its (mock) engine holds.
        manager.sync_lock.side_effect = lambda _profile_id: manager.get_engine.return_value.sync_lock

    return BackupService(rclone, dispatcher, db_factory, manager)


# ── Scheduling ───────────────────────────────────────────────────────


class TestScheduling:
    @pytest.mark.asyncio
    async def test_start_schedules_enabled_targets(self, db_factory) -> None:
        await _make_profile_and_target(db_factory, enabled=True)
        await _make_profile_and_target(db_factory, enabled=False)

        service = _make_service(db_factory)
        await service.start()

        assert service._scheduler is not None
        jobs = [j for j in service._scheduler.get_jobs() if j.id != OVERDUE_CHECK_JOB]
        # Only the enabled target should be scheduled
        assert len(jobs) == 1
        assert jobs[0].id.startswith("backup-")

        await service.stop()

    @pytest.mark.asyncio
    async def test_stop_shuts_down_scheduler(self, db_factory) -> None:
        service = _make_service(db_factory)
        await service.start()
        assert service._scheduler is not None
        await service.stop()
        assert service._scheduler is None

    @pytest.mark.asyncio
    async def test_get_next_run_time(self, db_factory) -> None:
        _, target_id, _ = await _make_profile_and_target(db_factory)
        service = _make_service(db_factory)
        await service.start()

        nrt = service.get_next_run_time(target_id)
        assert nrt is not None
        # Should be in the future
        assert nrt >= _now() - timedelta(seconds=5)

        await service.stop()

    @pytest.mark.asyncio
    async def test_unschedule_target(self, db_factory) -> None:
        _, target_id, _ = await _make_profile_and_target(db_factory)
        service = _make_service(db_factory)
        await service.start()

        service.unschedule_target(target_id)
        assert service.get_next_run_time(target_id) is None

        await service.stop()


# ── Liveness ─────────────────────────────────────────────────────────


class TestLiveness:
    @pytest.mark.asyncio
    async def test_local_liveness_pass(self, db_factory, tmp_path) -> None:
        (tmp_path / "backups").mkdir()
        _, target_id, _ = await _make_profile_and_target(
            db_factory, target_path=str(tmp_path / "backups")
        )
        service = _make_service(db_factory)

        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            alive, error = await service.check_liveness(target)

        assert alive is True
        assert error is None
        assert os.listdir(tmp_path / "backups") == []  # the write test cleans up

    @pytest.mark.asyncio
    @pytest.mark.parametrize("first_run", [True, False])
    async def test_missing_local_folder_is_unreachable_and_not_created(self, db_factory, tmp_path, first_run) -> None:
        """An unmounted drive's mount point must not get a backup folder on the local disk."""
        missing = tmp_path / "nas" / "backups"
        _, target_id, _ = await _make_profile_and_target(db_factory, target_path=str(missing))
        service = _make_service(db_factory)

        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            alive, error = await service.check_liveness(target, first_run=first_run)

        assert alive is False
        assert error is not None and "does not exist or is not mounted" in error
        assert not (tmp_path / "nas").exists()

    @pytest.mark.asyncio
    async def test_local_liveness_fail_readonly(self, db_factory) -> None:
        _, target_id, _ = await _make_profile_and_target(
            db_factory, target_path="/proc/nonexistent-path-42"
        )
        service = _make_service(db_factory)

        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            alive, error = await service.check_liveness(target)

        assert alive is False
        assert error is not None

    @pytest.mark.asyncio
    async def test_remote_liveness_pass(self, db_factory) -> None:
        rclone = AsyncMock()
        _, target_id, _ = await _make_profile_and_target(
            db_factory, target_type=BackupTargetType.REMOTE.value,
            target_path="gdrive:backups",
        )
        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            target.remote_name = "gdrive"

        service = _make_service(db_factory, rclone=rclone)
        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            alive, error = await service.check_liveness(target)

        assert alive is True
        # The target folder itself is listed, not only the remote's root.
        first = rclone._run.await_args_list[0]
        assert first.args[0] == ["lsd"] and first.kwargs["positional"] == ["gdrive:backups"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("first_run", "alive"), [(True, True), (False, False)])
    async def test_remote_folder_missing(self, db_factory, first_run, alive) -> None:
        """Only a target that never completed a backup may lack its folder."""
        from backend.exceptions import RcloneError
        rclone = AsyncMock()
        rclone._run.side_effect = [RcloneError("rclone failed (exit 3): error listing: directory not found"),
                                   MagicMock(stdout=""), MagicMock(stdout="[]")]
        _, target_id, _ = await _make_profile_and_target(
            db_factory, target_type=BackupTargetType.REMOTE.value, target_path="gdrive:backups/docs",
        )
        service = _make_service(db_factory, rclone=rclone)
        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            ok, error = await service.check_liveness(target, first_run=first_run)

        assert ok is alive
        positionals = [c.kwargs["positional"] for c in rclone._run.await_args_list]
        assert positionals[0] == ["gdrive:backups/docs"]
        if first_run:
            assert positionals[1] == ["gdrive:"]  # the remote itself must answer
        else:
            assert len(positionals) == 1 and "was not found" in error

    @pytest.mark.asyncio
    async def test_remote_liveness_fail(self, db_factory) -> None:
        from backend.exceptions import RcloneError
        rclone = AsyncMock()
        rclone._run.side_effect = RcloneError("connection refused")

        _, target_id, _ = await _make_profile_and_target(
            db_factory, target_type=BackupTargetType.REMOTE.value,
            target_path="gdrive:backups",
        )
        service = _make_service(db_factory, rclone=rclone)

        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            target.remote_name = "gdrive"
            alive, error = await service.check_liveness(target)

        # rclone's text goes to the log, not to the job or the target.
        assert alive is False
        assert error == "The backup target could not be reached. The OmniSync log has the details."


# ── Backup Execution ─────────────────────────────────────────────────


class TestBackupExecution:
    @pytest.mark.asyncio
    async def test_liveness_fail_records_skipped_job(self, db_factory) -> None:
        _, target_id, _ = await _make_profile_and_target(
            db_factory, target_path="/proc/nonexistent-42"
        )
        service = _make_service(db_factory)
        job = await service.run_backup(target_id)

        assert job.status == BackupJobStatus.SKIPPED.value
        assert job.error_message is not None
        service._dispatcher.dispatch.assert_called_once()  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_mirror_backup_success(self, db_factory, tmp_path) -> None:
        # Create source dir
        local_dir = tmp_path / "local"
        local_dir.mkdir()
        (local_dir / "file.txt").write_text("hello")

        target_path = str(tmp_path / "backup")
        _, target_id, slug = await _make_profile_and_target(
            db_factory, target_path=target_path, backup_mode=BackupMode.MIRROR.value,
        )
        # Update local_dir in profile
        async with db_factory() as session:
            stmt = select(SyncProfile).where(SyncProfile.slug == slug)
            profile = (await session.execute(stmt)).scalar_one()
            profile.local_dir = str(local_dir)
            await session.commit()

        (tmp_path / "backup").mkdir()
        rclone = AsyncMock()
        rclone.sync_with_backup_dir.return_value = MagicMock()
        rclone.list_top_level.return_value = []
        rclone.lsjson.return_value = [
            {"Path": "file.txt", "IsDir": False, "Size": 1000, "ModTime": "2026-01-02T03:04:05.123456789Z"},
            {"Path": "sub", "IsDir": True, "Size": -1},
            {"Path": "sub/b.txt", "IsDir": False, "Size": 24},
        ]
        manifests: list[dict] = []

        async def copyto(source, dest):
            import json
            with open(source, encoding="utf-8") as fh:
                manifests.append({"dest": dest, **json.load(fh)})

        rclone.copyto.side_effect = copyto

        service = _make_service(db_factory, rclone=rclone)
        job = await service.run_backup(target_id)

        assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
        assert job.size_bytes == 1024
        assert job.snapshot_id is not None
        rclone.sync_with_backup_dir.assert_called_once()
        # The manifest of the new snapshot lists every file with its size.
        assert manifests == [{
            "dest": f"{target_path}/manifests/{job.snapshot_id}.json", "format": 1,
            "snapshot_id": job.snapshot_id,
            # With its modification time (for browsing and restore previews), when listed.
            "files": [{"path": "file.txt", "size": 1000, "mtime": "2026-01-02T03:04:05.123456789Z"},
                      {"path": "sub/b.txt", "size": 24, "mtime": None}],
        }]
        service._dispatcher.dispatch.assert_called_once()  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_archive_backup_success_local(self, db_factory, tmp_path) -> None:
        local_dir = tmp_path / "local"
        local_dir.mkdir()
        (local_dir / "data.txt").write_text("content")

        target_path = str(tmp_path / "backup")
        (tmp_path / "backup").mkdir()
        _, target_id, slug = await _make_profile_and_target(
            db_factory, target_path=target_path,
            backup_mode=BackupMode.ARCHIVE.value,
            target_type=BackupTargetType.LOCAL.value,
        )
        async with db_factory() as session:
            stmt = select(SyncProfile).where(SyncProfile.slug == slug)
            profile = (await session.execute(stmt)).scalar_one()
            profile.local_dir = str(local_dir)
            await session.commit()

        service = _make_service(db_factory)
        job = await service.run_backup(target_id)

        assert job.status == BackupJobStatus.COMPLETED.value
        assert job.size_bytes is not None
        assert job.size_bytes > 0
        # Archive file should exist
        archives = [f for f in os.listdir(target_path) if f.endswith(".tar.gz")]
        assert len(archives) == 1

    @pytest.mark.asyncio
    async def test_backup_failure_records_failed_job(self, db_factory, tmp_path) -> None:
        target_path = str(tmp_path / "backup")
        (tmp_path / "backup").mkdir()
        (tmp_path / "local").mkdir()
        (tmp_path / "local" / "a.txt").write_text("a")
        _, target_id, slug = await _make_profile_and_target(
            db_factory, target_path=target_path, backup_mode=BackupMode.MIRROR.value,
        )
        await _set_local_dir(db_factory, slug, str(tmp_path / "local"))
        rclone = AsyncMock()
        rclone.list_top_level.return_value = []
        rclone.sync_with_backup_dir.side_effect = Exception("disk full")

        service = _make_service(db_factory, rclone=rclone)
        job = await service.run_backup(target_id)

        assert job.status == BackupJobStatus.FAILED.value
        assert job.error_message is not None and "disk full" in job.error_message

    @pytest.mark.asyncio
    async def test_concurrent_backup_raises(self, db_factory, tmp_path) -> None:
        target_path = str(tmp_path / "backup")
        _, target_id, _ = await _make_profile_and_target(
            db_factory, target_path=target_path,
        )
        service = _make_service(db_factory)
        # Simulate already running
        service._running_targets.add(target_id)

        with pytest.raises(RuntimeError, match="already running"):
            await service.run_backup(target_id)


# ── Retention ────────────────────────────────────────────────────────


class TestRetention:
    @pytest.mark.asyncio
    async def test_cleanup_deletes_expired_mirror_snapshots(self, db_factory) -> None:
        rclone = AsyncMock()
        # Simulate 3 version directories — 1 expired, 2 current
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H-%M-%S")
        recent_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")

        older_ts = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%dT%H-%M-%S")
        rclone.list_dirs.return_value = [old_ts, recent_ts]
        # older_ts: a backup that changed nothing, so it has no versions folder
        rclone.list_top_level.return_value = [f"{older_ts}.json", f"{old_ts}.json", f"{recent_ts}.json"]

        _, target_id, _ = await _make_profile_and_target(db_factory)
        service = _make_service(db_factory, rclone=rclone)

        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            target.retention_days = 7
            target.keep_last = 1
            deleted = await service.cleanup_old_snapshots(target)

        assert deleted == 2
        rclone.delete_path.assert_awaited_once_with(f"/backups/daily/versions/{old_ts}/")
        assert [c.kwargs["positional"] for c in rclone._run.await_args_list] == [
            [f"/backups/daily/manifests/{older_ts}.json"], [f"/backups/daily/manifests/{old_ts}.json"],
        ]

    @pytest.mark.asyncio
    async def test_cleanup_deletes_expired_archive_snapshots(self, db_factory) -> None:
        rclone = AsyncMock()
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H-%M-%S")
        recent_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")

        rclone.lsjson.return_value = [
            {"Path": f"backup-{old_ts}.tar.gz", "IsDir": False, "Size": 1000},
            {"Path": f"backup-{recent_ts}.tar.gz", "IsDir": False, "Size": 2000},
        ]

        _, target_id, _ = await _make_profile_and_target(
            db_factory, backup_mode=BackupMode.ARCHIVE.value,
            target_type=BackupTargetType.REMOTE.value,
            target_path="gdrive:backups",
        )
        service = _make_service(db_factory, rclone=rclone)

        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            target.retention_days = 7
            target.keep_last = 1
            deleted = await service.cleanup_old_snapshots(target)

        assert deleted == 1


# ── Property-based: Retention ────────────────────────────────────────


@given(
    retention=st.integers(min_value=1, max_value=365),
    ages=st.lists(st.integers(min_value=0, max_value=400), min_size=0, max_size=10),
)
@settings(max_examples=50)
def test_retention_correctness(retention: int, ages: list[int]) -> None:
    """Snapshots older than retention_days should be deleted, others kept."""
    cutoff = _now() - timedelta(days=retention)
    expected_deleted = sum(1 for age in ages if (_now() - timedelta(days=age)) < cutoff)
    expected_kept = len(ages) - expected_deleted
    # Just verify the logic — not the async service
    deleted = 0
    kept = 0
    for age in ages:
        snap_time = _now() - timedelta(days=age)
        if snap_time < cutoff:
            deleted += 1
        else:
            kept += 1
    assert deleted == expected_deleted
    assert kept == expected_kept


# ── Snapshot listing ─────────────────────────────────────────────────


class TestSnapshotListing:
    @pytest.mark.asyncio
    async def test_list_mirror_snapshots(self, db_factory) -> None:
        rclone = AsyncMock()
        rclone.list_dirs.return_value = ["2025-06-01T12-00-00", "2025-05-30T08-00-00", "some-random-dir"]
        rclone.list_top_level.return_value = [
            "2025-06-02T09-00-00.json", "2025-06-01T12-00-00.json", "notes.txt",
        ]

        _, target_id, _ = await _make_profile_and_target(db_factory)
        service = _make_service(db_factory, rclone=rclone)

        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            snapshots = await service.list_snapshots(target)

        # Newest first; every manifest is a full snapshot (also one whose
        # backup changed nothing and so has no versions folder), a version
        # folder without a manifest is a legacy one.
        assert [(s.snapshot_id, s.kind, s.latest) for s in snapshots] == [
            ("2025-06-02T09-00-00", "full", True),
            ("2025-06-01T12-00-00", "full", False),
            ("2025-05-30T08-00-00", "legacy", False),
        ]

    @pytest.mark.asyncio
    async def test_pre_manifest_target_lists_its_latest_backup(self, db_factory) -> None:
        rclone = AsyncMock()
        rclone.list_dirs.return_value = ["2025-05-30T08-00-00"]
        rclone.list_top_level.side_effect = lambda path: [] if path.endswith("manifests") else ["a.txt"]

        _, target_id, _ = await _make_profile_and_target(db_factory)
        async with db_factory() as session:
            session.add(BackupJob(
                target_id=target_id, started_at=_now(), finished_at=_now(), direction="backup",
                status=BackupJobStatus.COMPLETED.value, snapshot_id="2025-06-01T12-00-00",
            ))
            await session.commit()
        service = _make_service(db_factory, rclone=rclone)

        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            snapshots = await service.list_snapshots(target)

        assert [(s.snapshot_id, s.kind, s.latest) for s in snapshots] == [
            ("current", "full", True),
            ("2025-05-30T08-00-00", "legacy", False),
        ]
        assert snapshots[0].created_at == datetime(2025, 6, 1, 12, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_list_archive_snapshots(self, db_factory) -> None:
        rclone = AsyncMock()
        rclone.lsjson.return_value = [
            {"Path": "backup-2025-06-01T12-00-00.tar.gz", "IsDir": False, "Size": 5000},
            {"Path": "other-file.txt", "IsDir": False},
            {"Path": "backup-2025-05-30T08-00-00.tar.gz", "IsDir": False, "Size": 3000},
        ]

        _, target_id, _ = await _make_profile_and_target(
            db_factory, backup_mode=BackupMode.ARCHIVE.value,
        )
        service = _make_service(db_factory, rclone=rclone)

        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            snapshots = await service.list_snapshots(target)

        assert len(snapshots) == 2
        assert snapshots[0].size_bytes == 5000


# ── Restore ──────────────────────────────────────────────────────────


def _mirror_rclone(snapshot: str = "2025-06-01T12-00-00") -> AsyncMock:
    """An rclone mock holding one mirror snapshot with one file."""
    rclone = AsyncMock()
    rclone.list_dirs.return_value = [snapshot]
    rclone.lsjson.return_value = [{"Path": "a.txt", "IsDir": False}]
    return rclone


async def _set_local_dir(db_factory, slug: str, local_dir: str) -> None:
    async with db_factory() as session:
        profile = (await session.execute(select(SyncProfile).where(SyncProfile.slug == slug))).scalar_one()
        profile.local_dir = local_dir
        await session.commit()


class TestRestore:
    @pytest.mark.asyncio
    async def test_restore_holds_the_sync_lock(self, db_factory, tmp_path) -> None:
        """No push or pull may run while a restore rewrites the folder."""
        target_path = str(tmp_path / "backup")
        _, target_id, slug = await _make_profile_and_target(
            db_factory, target_path=target_path,
        )
        await _set_local_dir(db_factory, slug, str(tmp_path))
        lock = asyncio.Lock()
        held_during_restore: list[bool] = []

        async def fake_restore(*_args, **_kwargs):
            held_during_restore.append(lock.locked())

        rclone = _mirror_rclone()
        rclone.copy_files.side_effect = fake_restore
        manager = MagicMock()
        mock_engine = MagicMock()
        mock_engine.hold = AsyncMock()
        mock_engine.sync_lock = lock
        manager.get_engine.return_value = mock_engine

        service = _make_service(db_factory, rclone=rclone, manager=manager)
        job = await service.restore(target_id, "2025-06-01T12-00-00", RestoreScope.BOTH)

        assert held_during_restore and all(held_during_restore)
        assert not lock.locked()
        assert job.status == BackupJobStatus.COMPLETED.value
        assert job.direction == "restore"

    @pytest.mark.asyncio
    async def test_restore_resumes_engine_on_failure(self, db_factory, tmp_path) -> None:
        target_path = str(tmp_path / "backup")
        _, target_id, slug = await _make_profile_and_target(
            db_factory, target_path=target_path,
        )
        await _set_local_dir(db_factory, slug, str(tmp_path))
        rclone = _mirror_rclone()
        rclone.copy_files.side_effect = Exception("restore error")

        manager = MagicMock()
        mock_engine = MagicMock()
        mock_engine.hold = AsyncMock()
        mock_engine.sync_lock = asyncio.Lock()
        manager.get_engine.return_value = mock_engine

        service = _make_service(db_factory, rclone=rclone, manager=manager)
        job = await service.restore(target_id, "2025-06-01T12-00-00", RestoreScope.LOCAL_ONLY)

        # The lock is released even though the restore failed
        assert not mock_engine.sync_lock.locked()
        assert job.status == BackupJobStatus.FAILED.value
        assert job.error_message is not None and "restore error" in job.error_message

    @pytest.mark.asyncio
    async def test_restore_records_job(self, db_factory, tmp_path) -> None:
        target_path = str(tmp_path / "backup")
        _, target_id, _ = await _make_profile_and_target(
            db_factory, target_path=target_path,
        )
        rclone = AsyncMock()
        service = _make_service(db_factory, rclone=rclone)

        await service.restore(target_id, "2025-06-01T12-00-00", RestoreScope.LOCAL_ONLY)

        async with db_factory() as session:
            jobs = (await session.execute(select(BackupJob))).scalars().all()
            assert len(jobs) == 1
            assert jobs[0].direction == "restore"
            assert jobs[0].snapshot_id == "2025-06-01T12-00-00"


class TestLegacyPurge:
    """Pre-rename '.gsync-*' cleanup.

    The invariant under test is that the pre-restore safety backups survive:
    they are rclone --backup-dir destinations holding the only copy of files
    a restore overwrote, so they must be reported and never deleted.
    """

    @staticmethod
    def _seed(root) -> None:
        """Lay out a target as it looks after a pre-rename restore."""
        (root / ".gsync-pre-restore" / "Docs").mkdir(parents=True)
        (root / ".gsync-pre-restore" / "Docs" / "thesis.txt").write_text("only copy")
        (root / ".gsync-pre-restore-remote").mkdir()
        (root / ".gsync-liveness-check").write_text("ok")
        (root / ".omnisync-pre-restore").mkdir()
        (root / ".omnisync-liveness-check").write_text("ok")

    async def _purge(self, db_factory, target_path, **kw):
        _, target_id, _ = await _make_profile_and_target(
            db_factory, target_path=str(target_path), **kw
        )
        service = _make_service(db_factory, **{k: v for k, v in kw.items() if k == "rclone"})
        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            return service, await service.purge_legacy_markers(target)

    def test_marker_and_preserved_lists_are_disjoint(self) -> None:
        from backend.services.backup_service import (
            LEGACY_MARKER_FILES,
            LEGACY_PRESERVED_DIRS,
        )

        assert not set(LEGACY_MARKER_FILES) & set(LEGACY_PRESERVED_DIRS)

    @pytest.mark.asyncio
    async def test_removes_marker_and_preserves_pre_restore(
        self, db_factory, tmp_path
    ) -> None:
        root = tmp_path / "backups"
        root.mkdir()
        self._seed(root)

        _, removed = await self._purge(db_factory, root)

        assert removed == 1
        assert not (root / ".gsync-liveness-check").exists()
        # The whole point: the user's overwritten files are still there.
        assert (root / ".gsync-pre-restore" / "Docs" / "thesis.txt").read_text() == "only copy"
        assert (root / ".gsync-pre-restore-remote").is_dir()

    @pytest.mark.asyncio
    async def test_current_markers_untouched(self, db_factory, tmp_path) -> None:
        root = tmp_path / "backups"
        root.mkdir()
        self._seed(root)

        await self._purge(db_factory, root)

        assert (root / ".omnisync-pre-restore").is_dir()
        assert (root / ".omnisync-liveness-check").exists()

    @pytest.mark.asyncio
    async def test_marker_as_directory_is_not_removed(self, db_factory, tmp_path) -> None:
        root = tmp_path / "backups"
        (root / ".gsync-liveness-check" / "inner").mkdir(parents=True)
        (root / ".gsync-liveness-check" / "inner" / "f").write_text("x")

        _, removed = await self._purge(db_factory, root)

        assert removed == 0
        assert (root / ".gsync-liveness-check" / "inner" / "f").exists()

    @pytest.mark.asyncio
    async def test_marker_as_symlink_is_not_followed(self, db_factory, tmp_path) -> None:
        root = tmp_path / "backups"
        (root / "secret").mkdir(parents=True)
        (root / "secret" / "data").write_text("x")
        os.symlink(root / "secret", root / ".gsync-liveness-check")

        _, removed = await self._purge(db_factory, root)

        assert removed == 0
        assert (root / "secret" / "data").exists()
        assert os.path.lexists(root / ".gsync-liveness-check")

    @pytest.mark.asyncio
    async def test_runs_once_per_target(self, db_factory, tmp_path) -> None:
        root = tmp_path / "backups"
        root.mkdir()
        self._seed(root)

        _, target_id, _ = await _make_profile_and_target(
            db_factory, target_path=str(root)
        )
        service = _make_service(db_factory)
        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            assert await service.purge_legacy_markers(target) == 1
            (root / ".gsync-liveness-check").write_text("ok")  # reappears
            assert await service.purge_legacy_markers(target) == 0
            assert (root / ".gsync-liveness-check").exists()  # not rescanned

    @pytest.mark.asyncio
    async def test_degenerate_roots_refused(self, db_factory) -> None:
        for bad in ("/", "."):
            _, removed = await self._purge(db_factory, bad)
            assert removed == 0

    @pytest.mark.asyncio
    async def test_root_inside_pre_restore_refused(self, db_factory, tmp_path) -> None:
        root = tmp_path / "backups"
        inner = root / ".gsync-pre-restore"
        inner.mkdir(parents=True)
        (inner / ".gsync-liveness-check").write_text("ok")

        _, removed = await self._purge(db_factory, inner)

        assert removed == 0
        assert (inner / ".gsync-liveness-check").exists()

    @pytest.mark.asyncio
    async def test_remote_target_never_deletes(self, db_factory) -> None:
        rclone = AsyncMock()
        rclone._run.return_value = MagicMock(
            stdout='[{"Path": ".gsync-pre-restore", "IsDir": true},'
                   ' {"Path": ".gsync-liveness-check", "IsDir": false}]'
        )
        _, target_id, _ = await _make_profile_and_target(
            db_factory, target_type=BackupTargetType.REMOTE.value,
            target_path="gdrive:backups",
        )
        service = _make_service(db_factory, rclone=rclone)
        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            removed = await service.purge_legacy_markers(target)

        assert removed == 0
        rclone.delete_path.assert_not_awaited()
        subcommands = [c.args[0][0] for c in rclone._run.await_args_list if c.args]
        assert "deletefile" not in subcommands
        assert "purge" not in subcommands

    @pytest.mark.asyncio
    async def test_liveness_survives_a_failing_purge(self, db_factory, tmp_path) -> None:
        root = tmp_path / "backups"
        root.mkdir()
        _, target_id, _ = await _make_profile_and_target(
            db_factory, target_path=str(root)
        )
        service = _make_service(db_factory)
        service.purge_legacy_markers = AsyncMock(side_effect=RuntimeError("boom"))

        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            alive, error = await service.check_liveness(target)

        assert alive is True
        assert error is None


# ── rclone argument safety ───────────────────────────────────────────


class TestRclonePositionalPaths:
    """Every path the service hands to RcloneService._run goes after '--'.

    _run puts ``positional`` after the terminator, so a target path can
    never be read as an rclone flag, whatever it contains.
    """

    @pytest.mark.asyncio
    async def test_remote_target_paths_are_positional(self, db_factory) -> None:
        rclone = AsyncMock()
        rclone._run.return_value = MagicMock(stdout="[]")
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H-%M-%S")
        new_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        rclone.lsjson.return_value = [{"Path": f"backup-{old_ts}.tar.gz", "IsDir": False, "Size": 1},
                                      {"Path": f"backup-{new_ts}.tar.gz", "IsDir": False, "Size": 1}]
        _, target_id, _ = await _make_profile_and_target(
            db_factory, backup_mode=BackupMode.ARCHIVE.value,
            target_type=BackupTargetType.REMOTE.value, target_path="gdrive:backups",
        )
        service = _make_service(db_factory, rclone=rclone)
        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            target.remote_name = "gdrive"
            target.keep_last = 1
            alive, _ = await service.check_liveness(target)  # lsd + legacy lsjson
            assert alive is True
            assert await service.cleanup_old_snapshots(target) == 1  # deletefile

        calls = rclone._run.await_args_list
        assert [c.args[0][0] for c in calls] == ["lsd", "lsjson", "deletefile"]
        assert [c.kwargs["positional"] for c in calls] == [
            ["gdrive:backups"], ["gdrive:backups"], [f"gdrive:backups/backup-{old_ts}.tar.gz"],
        ]
        for call in calls:
            assert not any("gdrive" in arg for arg in call.args[0]), call

    @pytest.mark.asyncio
    async def test_run_puts_positional_after_terminator(self, monkeypatch) -> None:
        """The real _run with use_config_args=False, as the backup calls use it."""
        from backend.services.rclone import RcloneService

        seen: list[tuple[str, ...]] = []

        class Proc:
            returncode = 0

            async def communicate(self):
                return b"", b""

        async def fake_exec(*cmd, **_kwargs):
            seen.append(cmd)
            return Proc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        await RcloneService(rclone_config_path="/nonexistent/rclone.conf")._run(
            ["deletefile"], use_config_args=False, positional=["-x:evil"],
        )
        (cmd,) = seen
        assert cmd[-2:] == ("--", "-x:evil")


# ── Snapshot ids, mirror paths and retention on real folders (5.3) ───


SNAPSHOT_FORMAT = "%Y-%m-%dT%H-%M-%S"  # the format _execute_mirror/_execute_archive name snapshots with

snapshot_times = st.datetimes(
    min_value=datetime(2000, 1, 1), max_value=datetime(2200, 12, 31), timezones=st.just(timezone.utc),
).map(lambda d: d.replace(microsecond=0))


@given(when=snapshot_times)
@settings(max_examples=200)
def test_snapshot_id_timestamp_roundtrip(when: datetime) -> None:
    """A snapshot id parses back to the second it was taken, also inside an archive name."""
    snapshot_id = when.strftime(SNAPSHOT_FORMAT)
    assert BackupService._parse_timestamp(snapshot_id) == when
    archive = f"backup-{snapshot_id}.tar.gz"
    assert BackupService._parse_timestamp(archive.removeprefix("backup-").removesuffix(".tar.gz")) == when
    # Ids sort like their times (retention and listing rely on it).
    later = (when + timedelta(seconds=1)).strftime(SNAPSHOT_FORMAT)
    assert later > snapshot_id


@pytest.mark.parametrize("text", [
    "", "current", "2025-06-01T12:00:00", "2025-06-01 12-00-00", "2025-13-01T12-00-00",
    "2025-06-01T12-00-00.json", "backup-2025-06-01T12-00-00.tar.gz", "../2025-06-01T12-00-00",
])
def test_non_snapshot_names_do_not_parse(text: str) -> None:
    assert BackupService._parse_timestamp(text) is None


class TestMirrorBackupPaths:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("target_type", "target_path", "current", "versions"), [
        (BackupTargetType.LOCAL.value, None, "{root}/current/", "{root}/versions/{snap}/"),
        (BackupTargetType.REMOTE.value, "nas:backups/docs/", "nas:backups/docs/current/",
         "nas:backups/docs/versions/{snap}/"),
        (BackupTargetType.REMOTE.value, "nas:", "nas:current/", "nas:versions/{snap}/"),
    ])
    async def test_mirror_backup_syncs_into_current_with_versions_backup_dir(
        self, db_factory, tmp_path, target_type, target_path, current, versions,
    ) -> None:
        local_dir = tmp_path / "local"
        local_dir.mkdir()
        (local_dir / "a.txt").write_text("a")
        root = target_path or str(tmp_path / "backup")
        if target_path is None:
            (tmp_path / "backup").mkdir()
        _, target_id, slug = await _make_profile_and_target(
            db_factory, target_path=root, target_type=target_type, backup_mode=BackupMode.MIRROR.value,
        )
        await _set_local_dir(db_factory, slug, str(local_dir))
        rclone = AsyncMock()
        rclone.list_top_level.return_value = []
        rclone.lsjson.return_value = [{"Path": "a.txt", "IsDir": False, "Size": 1}]

        service = _make_service(db_factory, rclone=rclone)
        before = _now().replace(microsecond=0)
        job = await service.run_backup(target_id)

        assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
        snap = job.snapshot_id
        taken = BackupService._parse_timestamp(snap)
        assert taken is not None and before <= taken <= _now()
        rclone.sync_with_backup_dir.assert_awaited_once_with(
            str(local_dir), current.format(root=root), versions.format(root=root, snap=snap),
            rclone_filter=BACKUP_FILTER, rclone_args=[],
        )
        # The manifest of that snapshot sits next to current/ and versions/.
        manifest_dest = rclone.copyto.await_args.args[1]
        assert manifest_dest == current.format(root=root).removesuffix("current/") + f"manifests/{snap}.json"


def _real_rclone(tmp: str):
    from backend.services.rclone import RcloneService

    conf = os.path.join(tmp, "rclone.conf")
    with open(conf, "w") as fh:
        fh.write("")
    return RcloneService(rclone_config_path=conf)


def _transient_target(root: str, mode: str, retention_days: int) -> BackupTarget:
    # keep_last=1: these test age-based retention, not the "always keep" minimum.
    return BackupTarget(
        id=1, profile_id=1, name="t", target_path=root, target_type=BackupTargetType.LOCAL.value,
        backup_mode=mode, retention_days=retention_days, frequency_hours=24, keep_last=1,
    )


needs_rclone = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")


@needs_rclone
@given(
    retention=st.integers(min_value=1, max_value=365),
    ages=st.lists(st.integers(min_value=0, max_value=400), max_size=6, unique=True),
)
@settings(max_examples=25)
async def test_archive_retention_deletes_exactly_the_expired_files(retention: int, ages: list[int]) -> None:
    """cleanup_old_snapshots on a real folder: archives older than retention_days go, except the newest."""
    now = _now()
    # An hour inside each day, so no snapshot sits on the cutoff itself.
    stamps = {age: (now - timedelta(days=age) + timedelta(hours=1)).strftime(SNAPSHOT_FORMAT) for age in ages}
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "target")
        os.mkdir(root)
        for stamp in stamps.values():
            with open(os.path.join(root, f"backup-{stamp}.tar.gz"), "wb") as fh:
                fh.write(b"x")
        # Not snapshots: never touched by retention.
        for other in ("notes.txt", "backup-latest.tar.gz"):
            with open(os.path.join(root, other), "w") as fh:
                fh.write("keep")

        service = BackupService(_real_rclone(tmp), AsyncMock(), None, MagicMock())  # type: ignore[arg-type]
        deleted = await service.cleanup_old_snapshots(_transient_target(root, BackupMode.ARCHIVE.value, retention))

        # The newest archive always stays (keep_last=1), however old.
        kept_ages = {age for age in ages if age <= retention} | ({min(ages)} if ages else set())
        expected_kept = {f"backup-{stamps[age]}.tar.gz" for age in kept_ages}
        assert deleted == len(ages) - len(kept_ages)
        assert set(os.listdir(root)) == expected_kept | {"notes.txt", "backup-latest.tar.gz"}


@needs_rclone
@pytest.mark.asyncio
async def test_mirror_retention_on_real_folders(tmp_path) -> None:
    """Expired versions/ folders and manifests are deleted; current/ and newer snapshots stay."""
    now = _now()

    def stamp(days: float) -> str:
        return (now - timedelta(days=days)).strftime(SNAPSHOT_FORMAT)

    old, older_manifest_only, recent, newest = stamp(30), stamp(40), stamp(3), stamp(0.5)
    root = tmp_path / "target"
    for rel in ("current/a.txt", f"versions/{old}/a.txt", f"versions/{recent}/sub/b.txt",
                "versions/not-a-snapshot/c.txt"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x")
    (root / "manifests").mkdir()
    for snap in (older_manifest_only, old, recent, newest):
        (root / "manifests" / f"{snap}.json").write_text("{}")

    service = BackupService(_real_rclone(str(tmp_path)), AsyncMock(), None, MagicMock())  # type: ignore[arg-type]
    deleted = await service.cleanup_old_snapshots(_transient_target(str(root), BackupMode.MIRROR.value, 7))

    assert deleted == 2  # old (folder + manifest) and older_manifest_only (manifest only)
    assert sorted(p.name for p in (root / "versions").iterdir()) == sorted([recent, "not-a-snapshot"])
    assert sorted(p.name for p in (root / "manifests").iterdir()) == sorted([f"{recent}.json", f"{newest}.json"])
    assert (root / "current" / "a.txt").read_text() == "x"
    assert (root / "versions" / recent / "sub" / "b.txt").exists()


# ── After a restore (6.2) ────────────────────────────────────────────
# restore() does not call reload_profile(): backup schedules are not
# touched by a restore. What it does afterwards is pause the profile's
# engine when only one side was restored, while still holding its lock.


def _engine_manager(lock: asyncio.Lock | None = None) -> tuple[MagicMock, MagicMock]:
    engine = MagicMock()
    engine.sync_lock = lock or asyncio.Lock()
    manager = MagicMock()
    manager.get_engine.return_value = engine
    # Like SyncEngineManager.sync_lock: the profile's lock is the one its engine holds.
    manager.sync_lock.side_effect = lambda _profile_id: engine.sync_lock
    return manager, engine


class TestAfterRestore:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("scope", "side"), [
        (RestoreScope.LOCAL_ONLY, "local folder"), (RestoreScope.REMOTE_ONLY, "remote folder"),
    ])
    async def test_one_sided_restore_holds_the_engine_before_and_after(self, db_factory, tmp_path, scope, side) -> None:
        _, target_id, slug = await _make_profile_and_target(db_factory, target_path=str(tmp_path / "backup"))
        await _set_local_dir(db_factory, slug, str(tmp_path))
        manager, engine = _engine_manager()
        order: list[str] = []
        rclone = _mirror_rclone()
        rclone.copy_files.side_effect = lambda *a, **k: order.append("restore")
        engine.hold = AsyncMock(side_effect=lambda reason: order.append(f"hold locked={engine.sync_lock.locked()}"))

        service = _make_service(db_factory, rclone=rclone, manager=manager)
        job = await service.restore(target_id, "2025-06-01T12-00-00", scope)

        assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
        # Held before anything changes (queued syncs then skip), updated once done.
        assert order == ["hold locked=True", "restore", "hold locked=True"]
        manager.get_engine.assert_called_with(slug)
        (reason,), _ = engine.hold.call_args
        assert "2025-06-01T12-00-00 was restored" in reason and f"to the {side} only" in reason
        assert not engine.sync_lock.locked()
        event = service._dispatcher.dispatch.await_args.args[0]  # type: ignore[union-attr]
        assert (event.event_type.value, event.profile_slug) == ("backup_restore_completed", slug)

    @pytest.mark.asyncio
    async def test_failed_restore_stays_held(self, db_factory, tmp_path) -> None:
        """The folder may be partly restored: the hold set before the restore stays."""
        _, target_id, slug = await _make_profile_and_target(db_factory, target_path=str(tmp_path / "backup"))
        await _set_local_dir(db_factory, slug, str(tmp_path))
        manager, engine = _engine_manager()
        engine.hold = AsyncMock()
        rclone = _mirror_rclone()
        rclone.copy_files.side_effect = RuntimeError("disk full")

        service = _make_service(db_factory, rclone=rclone, manager=manager)
        job = await service.restore(target_id, "2025-06-01T12-00-00", RestoreScope.LOCAL_ONLY)

        assert job.status == BackupJobStatus.FAILED.value
        (reason,), _ = engine.hold.call_args
        assert engine.hold.await_count == 1 and "is being restored" in reason
        event = service._dispatcher.dispatch.await_args.args[0]  # type: ignore[union-attr]
        assert event.event_type.value == "backup_restore_failed"

    @pytest.mark.asyncio
    async def test_restore_of_a_profile_without_engine(self, db_factory, tmp_path) -> None:
        """A disabled (not running) profile restores under its shared sync lock; the hold is stored."""
        from backend.exceptions import ProfileNotFoundError

        profile_id, target_id, slug = await _make_profile_and_target(db_factory, target_path=str(tmp_path / "backup"))
        await _set_local_dir(db_factory, slug, str(tmp_path))
        manager = MagicMock()
        manager.get_engine.side_effect = ProfileNotFoundError(slug)
        shared_lock = asyncio.Lock()
        manager.sync_lock.side_effect = lambda _profile_id: shared_lock

        service = _make_service(db_factory, rclone=_mirror_rclone(), manager=manager)
        job = await service.restore(target_id, "2025-06-01T12-00-00", RestoreScope.LOCAL_ONLY)

        assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
        manager.sync_lock.assert_called_with(profile_id)
        assert not shared_lock.locked()


class TestReloadProfile:
    @pytest.mark.asyncio
    async def test_reload_profile_reschedules_the_profiles_targets(self, db_factory) -> None:
        profile_id, kept_id, _ = await _make_profile_and_target(db_factory, target_path="/backups/a")
        _, other_profile_target, _ = await _make_profile_and_target(db_factory, target_path="/backups/other")
        now = _now()
        async with db_factory() as session:
            disabled = BackupTarget(
                profile_id=profile_id, name="Weekly", target_path="/backups/b", target_type="local",
                backup_mode="mirror", enabled=True, retention_days=7, frequency_hours=24,
                created_at=now, updated_at=now,
            )
            session.add(disabled)
            await session.commit()
            disabled_id = disabled.id

        service = _make_service(db_factory)
        await service.start()
        try:
            assert {j.id for j in service._scheduler.get_jobs()} - {OVERDUE_CHECK_JOB} == {  # type: ignore[union-attr]
                f"backup-{kept_id}", f"backup-{disabled_id}", f"backup-{other_profile_target}",
            }
            async with db_factory() as session:
                (await session.get(BackupTarget, kept_id)).frequency_hours = 6
                (await session.get(BackupTarget, disabled_id)).enabled = False
                await session.commit()

            await service.reload_profile(profile_id)

            jobs = {j.id: j for j in service._scheduler.get_jobs()}  # type: ignore[union-attr]
            assert set(jobs) - {OVERDUE_CHECK_JOB} == {f"backup-{kept_id}", f"backup-{other_profile_target}"}
            assert jobs[f"backup-{kept_id}"].trigger.interval == timedelta(hours=6)
            assert jobs[f"backup-{other_profile_target}"].trigger.interval == timedelta(hours=24)
        finally:
            await service.stop()


# ── Schedules continue across restarts and edits ────────────────────


async def _add_job(db_factory, target_id: int, started_at: datetime, status: str = "completed") -> None:
    async with db_factory() as session:
        session.add(BackupJob(
            target_id=target_id, started_at=started_at, finished_at=started_at + timedelta(minutes=1),
            status=status, direction="backup",
        ))
        await session.commit()


async def _set_created(db_factory, target_id: int, created_at: datetime) -> None:
    async with db_factory() as session:
        (await session.get(BackupTarget, target_id)).created_at = created_at
        await session.commit()


def _close(actual: datetime | None, expected: datetime, slack: timedelta = timedelta(seconds=30)) -> bool:
    return actual is not None and abs(actual - expected) <= slack


class TestScheduleFromLastRun:
    @pytest.mark.asyncio
    async def test_restart_continues_from_the_last_run(self, db_factory) -> None:
        """Backed up 23h ago, every 24h: the next run is in 1h, not 24h after the restart."""
        _, target_id, _ = await _make_profile_and_target(db_factory)
        await _set_created(db_factory, target_id, _now() - timedelta(days=30))
        await _add_job(db_factory, target_id, _now() - timedelta(hours=23))
        service = _make_service(db_factory)
        await service.start()
        try:
            assert _close(service.get_next_run_time(target_id), _now() + timedelta(hours=1))
        finally:
            await service.stop()

    @pytest.mark.asyncio
    async def test_a_failed_last_run_counts_as_the_last_run(self, db_factory) -> None:
        _, target_id, _ = await _make_profile_and_target(db_factory)
        await _add_job(db_factory, target_id, _now() - timedelta(hours=30), "completed")
        await _add_job(db_factory, target_id, _now() - timedelta(hours=4), "failed")
        service = _make_service(db_factory)
        await service.start()
        try:
            assert _close(service.get_next_run_time(target_id), _now() + timedelta(hours=20))
        finally:
            await service.stop()

    @pytest.mark.asyncio
    async def test_overdue_targets_catch_up_soon_and_staggered(self, db_factory) -> None:
        from backend.services.backup_service import CATCH_UP_DELAY, CATCH_UP_STAGGER

        ids = []
        for _ in range(3):
            _, target_id, _ = await _make_profile_and_target(db_factory)
            await _add_job(db_factory, target_id, _now() - timedelta(days=3))
            ids.append(target_id)
        service = _make_service(db_factory)
        await service.start()
        try:
            runs = sorted(service.get_next_run_time(i) for i in ids)
            for n, run in enumerate(runs):
                assert _close(run, _now() + CATCH_UP_DELAY + n * CATCH_UP_STAGGER, timedelta(seconds=10))
        finally:
            await service.stop()

    @pytest.mark.asyncio
    async def test_never_run_target_counts_from_its_creation(self, db_factory) -> None:
        _, target_id, _ = await _make_profile_and_target(db_factory)
        created = _now() - timedelta(hours=5)
        await _set_created(db_factory, target_id, created)
        service = _make_service(db_factory)
        await service.start()
        try:
            assert _close(service.get_next_run_time(target_id), created + timedelta(hours=24))
        finally:
            await service.stop()

    @pytest.mark.asyncio
    async def test_editing_the_frequency_reschedules_from_the_last_run(self, db_factory) -> None:
        _, target_id, _ = await _make_profile_and_target(db_factory)
        await _add_job(db_factory, target_id, _now() - timedelta(hours=6))
        service = _make_service(db_factory)
        await service.start()
        try:
            async with db_factory() as session:
                target = await session.get(BackupTarget, target_id)
                target.frequency_hours = 12
                await service.reschedule_target(target)
            assert _close(service.get_next_run_time(target_id), _now() + timedelta(hours=6))
        finally:
            await service.stop()


# ── Overdue alert ────────────────────────────────────────────────────


class TestOverdue:
    def test_backup_is_overdue(self) -> None:
        from backend.services.backup_service import backup_is_overdue

        now = _now()
        target = BackupTarget(enabled=True, frequency_hours=24, created_at=now - timedelta(days=10))
        assert backup_is_overdue(target, now - timedelta(hours=49), now)
        assert not backup_is_overdue(target, now - timedelta(hours=47), now)
        assert backup_is_overdue(target, None, now)  # never completed, created 10 days ago
        target.created_at = now - timedelta(hours=30)
        assert not backup_is_overdue(target, None, now)
        target.enabled = False
        assert not backup_is_overdue(target, now - timedelta(days=30), now)

    @pytest.mark.asyncio
    async def test_overdue_target_is_notified_once(self, db_factory) -> None:
        from backend.services.notification_events import NotificationEventType

        _, target_id, _ = await _make_profile_and_target(db_factory)
        _, disabled_id, _ = await _make_profile_and_target(db_factory, enabled=False)
        _, fresh_id, _ = await _make_profile_and_target(db_factory)
        for tid in (target_id, disabled_id):
            await _set_created(db_factory, tid, _now() - timedelta(days=30))
            await _add_job(db_factory, tid, _now() - timedelta(days=3))
        await _add_job(db_factory, target_id, _now() - timedelta(hours=1), "failed")
        await _add_job(db_factory, fresh_id, _now() - timedelta(hours=10))
        service = _make_service(db_factory)
        dispatch = service._dispatcher.dispatch  # type: ignore[union-attr]

        assert await service.check_overdue() == [target_id]
        assert dispatch.await_count == 1
        event = dispatch.await_args.args[0]
        assert event.event_type == NotificationEventType.BACKUP_OVERDUE
        assert "every 24h" in event.body and "Daily" in event.title

        assert await service.check_overdue() == [target_id]
        assert dispatch.await_count == 1  # once

        # It recovers, then falls behind again: notified again.
        await _add_job(db_factory, target_id, _now())
        assert await service.check_overdue() == []
        async with db_factory() as session:
            (await session.get(BackupTarget, target_id)).frequency_hours = 1
            await session.commit()
        async with db_factory() as session:
            for job in (await session.execute(select(BackupJob).where(BackupJob.target_id == target_id))).scalars():
                job.finished_at = _now() - timedelta(hours=3)
            await session.commit()
        assert await service.check_overdue() == [target_id]
        assert dispatch.await_count == 2


# ── The profile's sync lock ──────────────────────────────────────────


class TestProfileLock:
    @pytest.mark.asyncio
    async def test_backup_of_a_profile_without_engine_takes_the_managers_lock(
        self, db_factory, tmp_path, monkeypatch,
    ) -> None:
        """An engine started mid-backup (profile enabled) gets the lock the backup holds."""
        from backend.services.backup_service import common as backup_common
        from backend.services.sync_engine_manager import SyncEngineManager

        monkeypatch.setattr(backup_common, "LOCK_TIMEOUT", 0.05)
        (tmp_path / "local").mkdir()
        (tmp_path / "local" / "a.txt").write_text("a")
        (tmp_path / "backup").mkdir()
        profile_id, target_id, slug = await _make_profile_and_target(
            db_factory, target_path=str(tmp_path / "backup"), backup_mode=BackupMode.ARCHIVE.value,
        )
        await _set_local_dir(db_factory, slug, str(tmp_path / "local"))
        manager = SyncEngineManager(AsyncMock(), AsyncMock(), db_factory)
        assert manager.sync_lock(profile_id) is manager.sync_lock(profile_id)
        service = BackupService(AsyncMock(), AsyncMock(), db_factory, manager)
        assert service.profile_lock(profile_id) is manager.sync_lock(profile_id)

        lock = manager.sync_lock(profile_id)
        await lock.acquire()  # e.g. the engine just started syncing
        try:
            job = await service.run_backup(target_id)
        finally:
            lock.release()
        assert job.status == BackupJobStatus.FAILED.value
        assert "Timed out waiting" in (job.error_message or "")
        assert not [f for f in os.listdir(tmp_path / "backup") if f.endswith(".tar.gz")]


# ── Archives: written atomically, without trash or partial files ─────


async def _archive_target(db_factory, tmp_path) -> tuple[int, str]:
    local = tmp_path / "local"
    local.mkdir(exist_ok=True)
    (local / "a.txt").write_text("a")
    target = tmp_path / "backup"
    target.mkdir(exist_ok=True)
    _, target_id, slug = await _make_profile_and_target(
        db_factory, target_path=str(target), backup_mode=BackupMode.ARCHIVE.value,
    )
    await _set_local_dir(db_factory, slug, str(local))
    return target_id, str(target)


class TestArchiveWrites:
    @pytest.mark.asyncio
    async def test_a_full_disk_leaves_no_truncated_snapshot(self, db_factory, tmp_path, monkeypatch) -> None:
        import errno
        import shutil as shutil_module

        target_id, target = await _archive_target(db_factory, tmp_path)
        real_copy2 = shutil_module.copy2

        def full_disk(src, dst, *args, **kwargs):
            with open(dst, "wb") as fh:
                fh.write(b"\x1f\x8b truncated")
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(shutil_module, "copy2", full_disk)
        service = _make_service(db_factory)
        job = await service.run_backup(target_id)
        assert job.status == BackupJobStatus.FAILED.value
        assert "No space left" in (job.error_message or "")
        assert os.listdir(target) == []

        # A partial file an interrupted run left behind is removed by the next one.
        stale = os.path.join(target, ".omnisync-partial-backup-2020-01-01T00-00-00.tar.gz")
        with open(stale, "wb") as fh:
            fh.write(b"half")
        monkeypatch.setattr(shutil_module, "copy2", real_copy2)
        job = await service.run_backup(target_id)
        assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
        assert os.listdir(target) == [job.snapshot_id]

    @pytest.mark.asyncio
    async def test_archive_leaves_out_trash_and_partial_files(self, db_factory, tmp_path) -> None:
        import tarfile

        target_id, target = await _archive_target(db_factory, tmp_path)
        local = tmp_path / "local"
        (local / ".omnisync-trash" / "old").mkdir(parents=True)
        (local / ".omnisync-trash" / "old" / "x.txt").write_text("trashed")
        (local / "sub").mkdir()
        (local / "sub" / "b.txt").write_text("b")
        (local / "sub" / "b.txt.0123abcd.partial").write_text("in progress")
        (local / "notes.partial").write_text("a user's file")
        (local / "sub" / ".omnisync-trash").mkdir()  # only the top-level trash is OmniSync's
        (local / "sub" / ".omnisync-trash" / "y.txt").write_text("y")

        service = _make_service(db_factory)
        job = await service.run_backup(target_id)
        assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
        with tarfile.open(os.path.join(target, job.snapshot_id)) as tar:
            names = sorted(n.removeprefix("./") for n in tar.getnames() if n != ".")
        assert names == ["a.txt", "notes.partial", "sub", "sub/.omnisync-trash", "sub/.omnisync-trash/y.txt",
                         "sub/b.txt"]

    @pytest.mark.asyncio
    async def test_mirror_backup_filters_trash_and_partial_files(self, db_factory, tmp_path) -> None:
        from backend.services.backup_service import BACKUP_FILTER
        from backend.services.rclone import TRASH_FILTER

        (tmp_path / "local").mkdir()
        (tmp_path / "local" / "a.txt").write_text("a")
        (tmp_path / "backup").mkdir()
        _, target_id, slug = await _make_profile_and_target(db_factory, target_path=str(tmp_path / "backup"))
        await _set_local_dir(db_factory, slug, str(tmp_path / "local"))
        rclone = AsyncMock()
        rclone.list_top_level.return_value = []
        rclone.list_dirs.return_value = []
        rclone.lsjson.return_value = []
        service = _make_service(db_factory, rclone=rclone)
        job = await service.run_backup(target_id)
        assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
        assert rclone.sync_with_backup_dir.await_args.kwargs["rclone_filter"] == BACKUP_FILTER
        assert TRASH_FILTER in BACKUP_FILTER


# ── Snapshot names never go backwards ────────────────────────────────


def _real_rclone_in(tmp_path):
    """The real rclone, which lists the archives of a local target."""
    import shutil

    from backend.services.rclone import RcloneService

    if shutil.which("rclone") is None and os.environ.get("OMNISYNC_REQUIRE_RCLONE") != "1":
        pytest.skip("rclone not installed")

    conf = tmp_path / "rclone.conf"
    conf.write_text("")
    return RcloneService(rclone_config_path=str(conf))


class TestSnapshotOrder:
    @pytest.mark.asyncio
    async def test_clock_behind_the_newest_snapshot_refuses_the_backup(self, db_factory, tmp_path) -> None:
        target_id, target = await _archive_target(db_factory, tmp_path)
        future = (_now() + timedelta(days=1)).strftime("%Y-%m-%dT%H-%M-%S")
        open(os.path.join(target, f"backup-{future}.tar.gz"), "wb").close()

        service = _make_service(db_factory, rclone=_real_rclone_in(tmp_path))
        job = await service.run_backup(target_id)
        assert job.status == BackupJobStatus.FAILED.value
        assert "clock" in (job.error_message or "")
        assert os.listdir(target) == [f"backup-{future}.tar.gz"]

    @pytest.mark.asyncio
    async def test_a_snapshot_in_the_same_second_waits_for_the_next(self, db_factory, tmp_path) -> None:
        target_id, target = await _archive_target(db_factory, tmp_path)
        newest = _now().strftime("%Y-%m-%dT%H-%M-%S")
        open(os.path.join(target, f"backup-{newest}.tar.gz"), "wb").close()

        service = _make_service(db_factory, rclone=_real_rclone_in(tmp_path))
        job = await service.run_backup(target_id)
        assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
        assert job.snapshot_id > f"backup-{newest}.tar.gz"

    @pytest.mark.asyncio
    async def test_mirror_versions_count_as_snapshots(self, db_factory, tmp_path) -> None:
        (tmp_path / "local").mkdir()
        (tmp_path / "local" / "a.txt").write_text("a")
        (tmp_path / "backup").mkdir()
        _, target_id, slug = await _make_profile_and_target(db_factory, target_path=str(tmp_path / "backup"))
        await _set_local_dir(db_factory, slug, str(tmp_path / "local"))
        rclone = AsyncMock()
        rclone.list_top_level.return_value = []
        rclone.list_dirs.return_value = [(_now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H-%M-%S")]
        service = _make_service(db_factory, rclone=rclone)
        job = await service.run_backup(target_id)
        assert job.status == BackupJobStatus.FAILED.value
        assert "clock" in (job.error_message or "")
        rclone.sync_with_backup_dir.assert_not_awaited()


# ── Retention keeps the newest snapshots ─────────────────────────────


def _stamp(days_ago: float) -> str:
    return (_now() - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H-%M-%S")


class TestRetentionKeepsNewest:
    @pytest.mark.asyncio
    async def test_archives_keep_last_regardless_of_age(self, db_factory) -> None:
        stamps = [_stamp(d) for d in (50, 40, 30, 20, 10)]  # all older than retention
        rclone = AsyncMock()
        rclone.lsjson.return_value = [{"Path": f"backup-{s}.tar.gz", "IsDir": False, "Size": 1} for s in stamps]
        _, target_id, _ = await _make_profile_and_target(
            db_factory, backup_mode=BackupMode.ARCHIVE.value,
            target_type=BackupTargetType.REMOTE.value, target_path="gdrive:backups",
        )
        service = _make_service(db_factory, rclone=rclone)
        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            assert target.keep_last == 3  # the default
            target.retention_days = 7
            assert await service.cleanup_old_snapshots(target) == 2
        deleted = sorted(c.kwargs["positional"][0] for c in rclone._run.await_args_list)
        assert deleted == [f"gdrive:backups/backup-{s}.tar.gz" for s in stamps[:2]]

    @pytest.mark.asyncio
    async def test_mirror_never_deletes_the_newest_completed_snapshot(self, db_factory) -> None:
        """Old manifests only, then version folders of failed runs: the newest manifest stays."""
        m1, m2, m3 = _stamp(40), _stamp(30), _stamp(20)
        failed = [_stamp(15), _stamp(12)]  # versions without manifest
        rclone = AsyncMock()
        rclone.list_dirs.return_value = [m1, m2, m3, *failed]
        rclone.list_top_level.return_value = [f"{m}.json" for m in (m1, m2, m3)]
        _, target_id, _ = await _make_profile_and_target(db_factory)
        service = _make_service(db_factory, rclone=rclone)
        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            target.retention_days = 7
            target.keep_last = 1
            assert await service.cleanup_old_snapshots(target) == 2
        assert [c.args[0] for c in rclone.delete_path.await_args_list] == [
            f"/backups/daily/versions/{m}/" for m in (m1, m2)
        ]

    @pytest.mark.asyncio
    async def test_mirror_keeps_last_three_by_default(self, db_factory) -> None:
        stamps = [_stamp(d) for d in (50, 40, 30, 20, 10)]
        rclone = AsyncMock()
        rclone.list_dirs.return_value = stamps
        rclone.list_top_level.return_value = [f"{s}.json" for s in stamps]
        _, target_id, _ = await _make_profile_and_target(db_factory)
        service = _make_service(db_factory, rclone=rclone)
        async with db_factory() as session:
            target = await session.get(BackupTarget, target_id)
            target.retention_days = 7
            assert await service.cleanup_old_snapshots(target) == 2
        assert [c.args[0] for c in rclone.delete_path.await_args_list] == [
            f"/backups/daily/versions/{s}/" for s in stamps[:2]
        ]
