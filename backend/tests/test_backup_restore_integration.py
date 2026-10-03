"""Backup and restore tests that run the real rclone binary.

Same pattern as test_sync_safety_integration.py: remotes of type `local` in
a temp rclone.conf and a real in-memory SQLite database. Skipped when rclone
is not installed.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tarfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.schemas import BackupMode, BackupTargetType, RestoreScope
from backend.db.models import Base, BackupJobStatus, BackupTarget, SyncProfile
from backend.services.backup_service import archive as backup_module
from backend.services.backup_service import PRE_RESTORE_DIR, BackupService, backup_paths_overlap
from backend.services.rclone import SENTINEL_FILE, TRASH_DIR, RcloneService

if shutil.which("rclone") is None and os.environ.get("OMNISYNC_REQUIRE_RCLONE") == "1":
    raise RuntimeError("OMNISYNC_REQUIRE_RCLONE=1 but rclone is not on PATH")
pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")


def files_under(root: Path, skip_trash: bool = True) -> dict[str, str]:
    return {
        str(p.relative_to(root)): p.read_text()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not (skip_trash and str(p.relative_to(root)).startswith(TRASH_DIR))
    }


def safety_copies(root: Path) -> list[dict[str, str]]:
    """Contents of each pre-restore folder under root, oldest first."""
    base = root / TRASH_DIR / PRE_RESTORE_DIR
    if not base.is_dir():
        return []
    return [files_under(d, skip_trash=False) for d in sorted(base.iterdir())]


def write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class Env:
    def __init__(self, tmp_path: Path, factory) -> None:
        self.local = tmp_path / "local"
        self.remote = tmp_path / "remote"
        self.backups = tmp_path / "backups"
        for d in (self.local, self.remote, self.backups):
            d.mkdir()
        conf = tmp_path / "rclone.conf"
        conf.write_text("[testremote]\ntype = local\n\n[backupremote]\ntype = local\n")
        self.factory = factory
        self.engine = MagicMock()
        self.engine.sync_lock = asyncio.Lock()
        self.engine.hold = AsyncMock()
        manager = MagicMock()
        manager.get_engine.return_value = self.engine
        manager.sync_lock.return_value = self.engine.sync_lock
        self.service = BackupService(RcloneService(rclone_config_path=str(conf)), AsyncMock(), factory, manager)

    async def add_target(
        self, target_path: str, mode: str = BackupMode.MIRROR.value,
        target_type: str = BackupTargetType.LOCAL.value, profile_id: int = 1,
    ) -> int:
        now = datetime.now(timezone.utc)
        async with self.factory() as session:
            if await session.get(SyncProfile, profile_id) is None:
                session.add(SyncProfile(
                    id=profile_id, name="Docs", slug="docs", local_dir=str(self.local),
                    remote_dir=f"testremote:{self.remote}", created_at=now, updated_at=now,
                ))
                await session.flush()
            target = BackupTarget(
                profile_id=profile_id, name=f"t{now.timestamp()}", target_path=target_path,
                target_type=target_type, backup_mode=mode, retention_days=30,
                frequency_hours=24, created_at=now, updated_at=now,
            )
            session.add(target)
            await session.commit()
            return target.id

    async def backup(self, target_id: int) -> str:
        job = await self.service.run_backup(target_id)
        assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
        await asyncio.sleep(1.1)  # snapshot names have one-second resolution
        return job.snapshot_id


@pytest.fixture
async def env(tmp_path: Path):
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield Env(tmp_path, async_sessionmaker(db, expire_on_commit=False))
    await db.dispose()


async def three_mirror_backups(env: Env, target_id: int) -> tuple[str, str, str]:
    """T1: a1 b1 c1 / T2: a2, b deleted, d2 added / T3: c3."""
    write(env.local, "a.txt", "a1")
    write(env.local, "b.txt", "b1")
    write(env.local, "sub/c.txt", "c1")
    t1 = await env.backup(target_id)
    write(env.local, "a.txt", "a2")
    (env.local / "b.txt").unlink()
    write(env.local, "d.txt", "d2")
    t2 = await env.backup(target_id)
    write(env.local, "sub/c.txt", "c3")
    t3 = await env.backup(target_id)
    return t1, t2, t3


# The tree right after each backup of three_mirror_backups
AFTER_T1 = {"a.txt": "a1", "b.txt": "b1", "sub/c.txt": "c1"}
AFTER_T2 = {"a.txt": "a2", "sub/c.txt": "c1", "d.txt": "d2"}
AFTER_T3 = {"a.txt": "a2", "sub/c.txt": "c3", "d.txt": "d2"}


def target_ref(env: Env):
    return type("T", (), {"id": 0, "backup_mode": "mirror", "target_path": str(env.backups), "encryption_password": None})()


async def test_mirror_restore_gives_the_tree_right_after_that_backup(env):
    target_id = await env.add_target(str(env.backups))
    t1, t2, t3 = await three_mirror_backups(env, target_id)
    # live folder after the last backup
    write(env.local, "a.txt", "live edit")
    (env.local / "sub" / "c.txt").unlink()
    write(env.local, "e.txt", "created after every backup")

    job = await env.service.restore(target_id, t2, RestoreScope.LOCAL_ONLY)

    assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
    assert files_under(env.local) == AFTER_T2
    # nothing is deleted: the replaced file and the file t2 did not have
    # are in one safety folder
    assert safety_copies(env.local) == [{"a.txt": "live edit", "e.txt": "created after every backup"}]
    # a one-sided restore holds syncing, from before it starts
    assert [c.args[0].split(" only.")[0] for c in env.engine.hold.await_args_list] == [
        f"Snapshot {t2} is being restored to the local folder", f"Snapshot {t2} was restored to the local folder",
    ]


async def test_every_snapshot_restores_exactly_including_the_latest(env):
    target_id = await env.add_target(str(env.backups))
    t1, t2, t3 = await three_mirror_backups(env, target_id)

    snapshots = await env.service.list_snapshots(target_ref(env))
    assert [(s.snapshot_id, s.kind, s.latest) for s in snapshots] == [
        (t3, "full", True), (t2, "full", False), (t1, "full", False),
    ]
    for snapshot, expected in ((t1, AFTER_T1), (t3, AFTER_T3), (t2, AFTER_T2), (t3, AFTER_T3)):
        job = await env.service.restore(target_id, snapshot, RestoreScope.LOCAL_ONLY)
        assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
        assert files_under(env.local) == expected, snapshot


async def test_a_backup_that_only_added_files_is_a_snapshot_too(env):
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a")
    t1 = await env.backup(target_id)
    write(env.local, "b.txt", "b")
    t2 = await env.backup(target_id)  # replaces nothing: rclone writes no versions/t2
    assert not (env.backups / "versions" / t2).exists()

    assert [s.snapshot_id for s in await env.service.list_snapshots(target_ref(env))] == [t2, t1]
    await env.service.restore(target_id, t1, RestoreScope.LOCAL_ONLY)
    assert files_under(env.local) == {"a.txt": "a"}
    await env.service.restore(target_id, t2, RestoreScope.LOCAL_ONLY)
    assert files_under(env.local) == {"a.txt": "a", "b.txt": "b"}


async def test_restore_leaves_the_sync_marker_and_trash_alone(env):
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a")
    t1 = await env.backup(target_id)
    write(env.remote, SENTINEL_FILE, "remote marker")
    write(env.remote, f"{TRASH_DIR}/old/x.txt", "trashed earlier")
    write(env.remote, "extra.txt", "only on the remote")

    job = await env.service.restore(target_id, t1, RestoreScope.REMOTE_ONLY)

    assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
    assert files_under(env.remote) == {"a.txt": "a", SENTINEL_FILE: "remote marker"}
    assert (env.remote / TRASH_DIR / "old" / "x.txt").read_text() == "trashed earlier"
    assert safety_copies(env.remote) == [{"extra.txt": "only on the remote"}]


async def test_a_damaged_backup_fails_the_restore_without_touching_anything(env):
    target_id = await env.add_target(str(env.backups))
    _, t2, _ = await three_mirror_backups(env, target_id)
    (env.backups / "current" / "d.txt").unlink()
    write(env.local, "live.txt", "live")
    before = files_under(env.local)

    job = await env.service.restore(target_id, t2, RestoreScope.LOCAL_ONLY)

    assert job.status == BackupJobStatus.FAILED.value
    assert "missing from the backup" in job.error_message
    assert files_under(env.local) == before and safety_copies(env.local) == []


async def test_pre_manifest_targets_keep_working(env):
    """Backups written before manifests: legacy versions and the latest backup (current)."""
    target_id = await env.add_target(str(env.backups))
    _, t2, t3 = await three_mirror_backups(env, target_id)
    shutil.rmtree(env.backups / "manifests")

    snapshots = await env.service.list_snapshots(
        type("T", (), {"id": target_id, "backup_mode": "mirror", "target_path": str(env.backups), "encryption_password": None})())
    assert [(s.snapshot_id, s.kind, s.latest) for s in snapshots] == [
        ("current", "full", True), (t3, "legacy", False), (t2, "legacy", False),
    ]

    # a legacy version brings back the files as they were before that backup
    # (= after t1) over current/, and removes nothing
    write(env.local, "e.txt", "created after every backup")
    job = await env.service.restore(target_id, t2, RestoreScope.LOCAL_ONLY)
    assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
    assert files_under(env.local) == {
        "a.txt": "a1", "b.txt": "b1", "sub/c.txt": "c1", "d.txt": "d2", "e.txt": "created after every backup",
    }

    # the latest backup is restored exactly
    job = await env.service.restore(target_id, "current", RestoreScope.LOCAL_ONLY)
    assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
    assert files_under(env.local) == AFTER_T3
    assert {"b.txt": "b1", "e.txt": "created after every backup"}.items() <= safety_copies(env.local)[-1].items()


async def test_pre_restore_safety_folders_are_never_overwritten(env):
    target_id = await env.add_target(str(env.backups))
    _, t2, _ = await three_mirror_backups(env, target_id)

    write(env.local, "a.txt", "first live version")
    await env.service.restore(target_id, t2, RestoreScope.LOCAL_ONLY)
    write(env.local, "a.txt", "second live version")
    await env.service.restore(target_id, t2, RestoreScope.LOCAL_ONLY)

    copies = safety_copies(env.local)
    assert len(copies) == 2
    assert {c.get("a.txt") for c in copies} == {"first live version", "second live version"}


async def test_cross_remote_restore_keeps_the_safety_copy_on_the_destination_remote(env):
    # backups on one remote, the profile's remote folder on another
    target_id = await env.add_target(f"backupremote:{env.backups}", target_type=BackupTargetType.REMOTE.value)
    t1, _, _ = await three_mirror_backups(env, target_id)
    write(env.remote, "a.txt", "remote live")

    job = await env.service.restore(target_id, t1, RestoreScope.REMOTE_ONLY)

    assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
    assert files_under(env.remote) == AFTER_T1
    assert safety_copies(env.remote) == [{"a.txt": "remote live"}]
    assert not (env.backups / ".omnisync-pre-restore").exists()


async def test_restore_to_both_sides(env):
    target_id = await env.add_target(str(env.backups))
    t1, _, _ = await three_mirror_backups(env, target_id)

    job = await env.service.restore(target_id, t1, RestoreScope.BOTH)

    assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
    assert files_under(env.remote) == AFTER_T1
    assert files_under(env.local) == AFTER_T1
    env.engine.hold.assert_not_called()


@pytest.mark.parametrize("snapshot", ["../../etc", "2025-06-01T12-00-00", "latest"])
async def test_unknown_or_malformed_snapshots_are_refused(env, snapshot):
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a")
    await env.backup(target_id)
    before = files_under(env.local)

    job = await env.service.restore(target_id, snapshot, RestoreScope.LOCAL_ONLY)

    assert job.status == BackupJobStatus.FAILED.value
    assert files_under(env.local) == before


async def test_archive_restore_restores_the_archive_and_keeps_replaced_files(env):
    target_id = await env.add_target(str(env.backups), mode=BackupMode.ARCHIVE.value)
    write(env.local, "a.txt", "archived")
    write(env.local, SENTINEL_FILE, "marker")
    snapshot = await env.backup(target_id)
    write(env.local, "a.txt", "changed later")
    write(env.local, "new.txt", "created later")

    job = await env.service.restore(target_id, snapshot, RestoreScope.LOCAL_ONLY)

    assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
    assert files_under(env.local) == {"a.txt": "archived", SENTINEL_FILE: "marker"}
    assert safety_copies(env.local) == [{"a.txt": "changed later", "new.txt": "created later"}]


async def test_archive_work_runs_off_the_event_loop(env, monkeypatch):
    threads: list[str] = []
    real_create, real_extract = backup_module._create_archive, backup_module._extract_archive

    def create(*args):
        threads.append(threading.current_thread().name)
        return real_create(*args)

    def extract(*args):
        threads.append(threading.current_thread().name)
        return real_extract(*args)

    monkeypatch.setattr(backup_module, "_create_archive", create)
    monkeypatch.setattr(backup_module, "_extract_archive", extract)
    target_id = await env.add_target(str(env.backups), mode=BackupMode.ARCHIVE.value)
    write(env.local, "a.txt", "a")
    snapshot = await env.backup(target_id)
    await env.service.restore(target_id, snapshot, RestoreScope.LOCAL_ONLY)

    assert len(threads) == 2 and threading.main_thread().name not in threads


# --- Empty or unmounted source (the sync engine's rails, applied to backups) ---


def versions_of(env: Env) -> list[str]:
    base = env.backups / "versions"
    return sorted(p.name for p in base.iterdir()) if base.is_dir() else []


def failed_notifications(env: Env) -> list:
    return [c.args[0] for c in env.service._dispatcher.dispatch.await_args_list
            if "fail" in str(c.args[0]).lower()]


async def refused(env: Env, target_id: int, reason: str) -> None:
    job = await env.service.run_backup(target_id)
    assert job.status == BackupJobStatus.FAILED.value
    assert reason in job.error_message
    assert failed_notifications(env), "the refusal must notify"


@pytest.mark.parametrize("how", ["emptied", "missing"])
async def test_mirror_backup_of_an_empty_or_missing_folder_is_refused(env, how):
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a")
    write(env.local, "sub/b.txt", "b")
    await env.backup(target_id)
    backed_up = files_under(env.backups / "current")
    shutil.rmtree(env.local)
    if how == "emptied":
        env.local.mkdir()  # an unmounted mount point looks like this

    await refused(env, target_id, "is empty while the backup holds" if how == "emptied" else "missing or not mounted")

    assert files_under(env.backups / "current") == backed_up
    assert versions_of(env) == []


async def test_mirror_backup_refused_when_the_folder_lost_its_sync_marker(env):
    target_id = await env.add_target(str(env.backups))
    write(env.local, SENTINEL_FILE, "marker")
    write(env.local, "a.txt", "a")
    await env.backup(target_id)
    (env.local / SENTINEL_FILE).unlink()
    write(env.local, "other.txt", "a different folder")

    await refused(env, target_id, f"sync marker {SENTINEL_FILE} is in the backup")

    assert files_under(env.backups / "current") == {SENTINEL_FILE: "marker", "a.txt": "a"}
    assert versions_of(env) == []


async def test_first_backup_of_an_empty_folder_runs(env):
    target_id = await env.add_target(str(env.backups))
    job = await env.service.run_backup(target_id)  # nothing backed up yet: nothing to lose
    assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
    assert json.loads((env.backups / "manifests" / f"{job.snapshot_id}.json").read_text())["files"] == []


async def test_archive_backup_of_an_empty_folder_is_refused_once_archives_exist(env):
    target_id = await env.add_target(str(env.backups), mode=BackupMode.ARCHIVE.value)
    write(env.local, "a.txt", "a")
    await env.backup(target_id)
    archives = sorted(p.name for p in env.backups.iterdir())
    (env.local / "a.txt").unlink()

    await refused(env, target_id, "is empty while the target holds 1 archive(s)")

    assert sorted(p.name for p in env.backups.iterdir()) == archives


# --- Liveness: skip, never create ---


async def test_missing_local_target_folder_is_skipped_not_created(env):
    target_id = await env.add_target(str(env.backups / "nas"))
    write(env.local, "a.txt", "a")

    job = await env.service.run_backup(target_id)

    assert job.status == BackupJobStatus.SKIPPED.value
    assert "does not exist or is not mounted" in job.error_message
    assert not (env.backups / "nas").exists()
    env.service._dispatcher.dispatch.assert_awaited_once()


async def test_remote_target_folder_is_checked_itself(env):
    target_id = await env.add_target(f"backupremote:{env.backups}/docs", target_type=BackupTargetType.REMOTE.value)
    write(env.local, "a.txt", "a")
    await env.backup(target_id)  # a new target: the first run creates its folder
    shutil.rmtree(env.backups / "docs")

    job = await env.service.run_backup(target_id)

    assert job.status == BackupJobStatus.SKIPPED.value
    assert "was not found" in job.error_message
    assert not (env.backups / "docs").exists()


# --- Targets that share a path ---


async def test_targets_sharing_a_path_are_refused(env):
    first = await env.add_target(str(env.backups / "shared"))
    second = await env.add_target(str(env.backups / "shared" / "nested"))
    write(env.local, "a.txt", "a")

    job = await env.service.run_backup(second)

    assert job.status == BackupJobStatus.FAILED.value and "overlaps backup target" in job.error_message
    assert not (env.backups / "shared").exists()
    assert (await env.service.run_backup(first)).status == BackupJobStatus.FAILED.value


async def test_target_inside_the_synced_folder_is_refused(env):
    target_id = await env.add_target(str(env.local / "backups"))
    write(env.local, "a.txt", "a")

    job = await env.service.run_backup(target_id)

    assert job.status == BackupJobStatus.FAILED.value and "local folder" in job.error_message
    assert not (env.local / "backups").exists()


@pytest.mark.parametrize(("a", "b", "overlap"), [
    ("/data/backup", "/data/backup", True),
    ("/data/backup", "/data/backup/", True),
    ("/data/backup", "/data/backup/daily", True),
    ("/data/backup/daily", "/data", True),
    ("/data/backup", "/data/backup-2", False),
    ("/data/a/../backup", "/data/backup", True),
    ("gdrive:backups", "gdrive:backups/x", True),
    ("gdrive:", "gdrive:backups", True),
    ("gdrive:backups", "gdrive:backups2", False),
    ("gdrive:backups", "dropbox:backups", False),
    ("gdrive:backups", "/backups", False),
])
def test_backup_paths_overlap(a, b, overlap):
    assert backup_paths_overlap(a, b) is overlap
    assert backup_paths_overlap(b, a) is overlap


async def test_backups_leave_out_the_trash_and_partial_files(env):
    """Neither mode backs up .omnisync-trash or rclone's in-progress <name>.<8 hex>.partial files."""
    write(env.local, "a.txt", "a")
    write(env.local, f"{TRASH_DIR}/old/x.txt", "trashed")
    write(env.local, "sub/b.txt.0123abcd.partial", "being transferred")
    write(env.local, "notes.partial", "a user's own file")
    (env.backups / "mirror").mkdir()
    mirror = await env.add_target(str(env.backups / "mirror"))
    await env.backup(mirror)
    assert files_under(env.backups / "mirror" / "current", skip_trash=False) == {
        "a.txt": "a", "notes.partial": "a user's own file",
    }

    (env.backups / "archive").mkdir()
    archive = await env.add_target(str(env.backups / "archive"), mode=BackupMode.ARCHIVE.value)
    snapshot = await env.backup(archive)
    with tarfile.open(env.backups / "archive" / snapshot) as tar:
        assert sorted(m.name for m in tar.getmembers() if m.isfile()) == ["./a.txt", "./notes.partial"]
