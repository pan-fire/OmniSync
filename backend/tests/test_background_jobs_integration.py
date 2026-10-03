"""Backups, restores and per-file actions as background jobs, run by the real rclone binary.

Covers: the starts the API uses (job recorded running, lock taken or the
start refused while the profile is busy), crashes and shutdown ending the
job record, a restore whose lock wait times out, the pause a single-file
restore sets so a queued pull cannot remove the restored files, the
profile's bandwidth limit and tuning flags on every transfer (argv), and
the sync lock's waiting count (SyncLock).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.schemas import (
    ChangeCategory,
    ConflictResolution,
    DiffResponse,
    DiffSummary,
    FileAction,
    FileDiff,
    JobStatus,
    RestoreScope,
    SelectiveSyncItem,
    SyncDirection,
)
from backend.db.models import Base, BackupJob, BackupTarget, Conflict, SyncError, SyncJob, SyncProfile
from backend.exceptions import SyncBusyError
from backend.models.profile_config import ProfileConfig
from backend.services.backup_service import BackupService, common
from backend.services.notification_events import NotificationEventType
from backend.services.rclone import SENTINEL_FILE, TRASH_DIR, RcloneService
from backend.services.sync_engine import SyncEngine, SyncLock, is_busy, transfer_tuning_args
from backend.services.sync_engine.selective import SELECTIVE_CRASHED

pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")

PROFILE_ARGS = ["--transfers", "2", "--exclude", "*.tmp", "--update", "--checkers=3", "--max-delete", "5"]


def files_under(root: Path, trash: bool = False) -> dict[str, str]:
    return {
        str(p.relative_to(root)): p.read_text()
        for p in sorted(root.rglob("*"))
        if p.is_file() and (trash or not str(p.relative_to(root)).startswith(TRASH_DIR))
    }


def write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class Env:
    def __init__(self, tmp: Path, factory: async_sessionmaker, *, bwlimit: str | None, args: list[str]) -> None:
        self.local, self.remote, self.backups = tmp / "local", tmp / "remote", tmp / "backups"
        for d in (self.local, self.remote, self.backups):
            d.mkdir()
        conf = tmp / "rclone.conf"
        conf.write_text("[testremote]\ntype = local\n")
        self.factory = factory
        self.rclone = RcloneService(rclone_config_path=str(conf))
        self.lock = SyncLock()
        self.config = ProfileConfig(
            profile_id=1, slug="docs", name="Docs", local_dir=str(self.local),
            remote_dir=f"testremote:{self.remote}", max_retries=1, bwlimit=bwlimit, rclone_args=args,
        )
        self.engine = SyncEngine(self.config, self.rclone, factory, sync_lock=self.lock)
        self.dispatcher = MagicMock()
        self.dispatcher.dispatch = AsyncMock(return_value=[])
        manager = MagicMock()
        manager.get_engine.return_value = self.engine
        manager.sync_lock.side_effect = lambda _pid: self.lock
        self.service = BackupService(self.rclone, self.dispatcher, factory, manager)
        self.bwlimit, self.args = bwlimit, args

    async def add_rows(self) -> int:
        now = datetime.now(timezone.utc)
        async with self.factory() as session:
            session.add(SyncProfile(
                id=1, slug="docs", name="Docs", local_dir=str(self.local), remote_dir=f"testremote:{self.remote}",
                rclone_args=json.dumps(self.args), bwlimit=self.bwlimit, created_at=now, updated_at=now,
            ))
            target = BackupTarget(profile_id=1, name="Daily", target_path=str(self.backups), target_type="local",
                                  backup_mode="mirror", created_at=now, updated_at=now)
            session.add(target)
            await session.commit()
            return target.id

    def events(self) -> list:
        return [c.args[0] for c in self.dispatcher.dispatch.await_args_list]


async def _env(tmp_path: Path, *, bwlimit: str | None = None, args: list[str] | None = None):
    # A file, not :memory: (one connection shared by every session): the
    # background run and the polling test write and read at the same time.
    db = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'omnisync.db'}")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    env = Env(tmp_path, async_sessionmaker(db, expire_on_commit=False), bwlimit=bwlimit, args=args or [])
    return env, db


@pytest.fixture
async def env(tmp_path: Path):
    env, db = await _env(tmp_path)
    yield env
    await env.service.stop_jobs()
    await db.dispose()


@pytest.fixture
async def limited(tmp_path: Path):
    """A profile with a bandwidth limit and rclone flags of every kind."""
    env, db = await _env(tmp_path, bwlimit="10M", args=PROFILE_ARGS)
    yield env
    await env.service.stop_jobs()
    await db.dispose()


async def wait_job(env: Env, job_id: int) -> BackupJob:
    for _ in range(1200):
        job = await env.service.get_job(job_id)
        assert job is not None
        if job.status != "running" and job_id not in env.service._job_tasks.values():
            await asyncio.sleep(0)  # the run's done callback gives the lock back
            return job
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {job_id} did not end")


def mark_both(env: Env) -> None:
    write(env.local, SENTINEL_FILE, "marker")
    write(env.remote, SENTINEL_FILE, "marker")


# --- background starts ---


async def test_a_backup_started_for_the_api_runs_in_the_background(env):
    target_id = await env.add_rows()
    write(env.local, "a.txt", "a")

    job = await env.service.start_backup(target_id)

    assert job.status == "running" and job.finished_at is None
    assert env.lock.locked() and is_busy(env.lock)  # taken before the answer
    done = await wait_job(env, job.id)
    assert (done.status, done.error_code) == ("completed", None)
    assert files_under(env.backups / "current") == {"a.txt": "a"}
    assert not env.lock.locked() and target_id not in env.service._running_targets


async def test_starts_are_refused_while_the_profile_is_busy_or_wanted(env):
    target_id = await env.add_rows()
    write(env.local, "a.txt", "a")

    await env.lock.acquire()  # e.g. a sync runs
    with pytest.raises(SyncBusyError):
        await env.service.start_backup(target_id)
    waiter = asyncio.create_task(env.lock.acquire())  # e.g. a scheduled pull waits for its turn
    await asyncio.sleep(0)
    assert env.lock.waiting == 1
    env.lock.release()
    # Free for a moment, but the waiting pull has the next turn: still refused, not queued.
    assert not env.lock.locked() and is_busy(env.lock)
    with pytest.raises(SyncBusyError):
        await env.service.start_backup(target_id)
    with pytest.raises(SyncBusyError):
        await env.service.start_restore(target_id, "current", RestoreScope.LOCAL_ONLY)
    await waiter
    env.lock.release()
    assert env.lock.waiting == 0 and not is_busy(env.lock)

    async with env.factory() as session:
        assert (await session.execute(select(BackupJob))).scalars().all() == []  # nothing recorded


async def test_launch_refuses_while_an_operation_waits_for_the_lock(env):
    """SyncEngine.launch() and launch_selective() see waiters through SyncLock, not asyncio internals."""
    mark_both(env)
    cache_diff(env.engine, "a.txt")
    await env.lock.acquire()
    waiter = asyncio.create_task(env.lock.acquire())
    await asyncio.sleep(0)
    env.lock.release()
    assert not env.lock.locked() and env.lock.waiting == 1

    with pytest.raises(SyncBusyError):
        await env.engine.launch(SyncDirection.PUSH)
    with pytest.raises(SyncBusyError):
        await env.engine.launch_selective([SelectiveSyncItem(path="a.txt", action=FileAction.PUSH)])
    await waiter
    env.lock.release()
    launch = await env.engine.launch(SyncDirection.PUSH)  # free and wanted by nobody: taken
    assert launch.task is not None
    await asyncio.wait_for(launch.task, timeout=60)


async def test_a_crash_in_the_background_ends_the_job_and_is_logged(env, caplog):
    target_id = await env.add_rows()
    env.service._backup = AsyncMock(side_effect=RuntimeError("ERROR : secret stderr line"))

    with caplog.at_level(logging.ERROR):
        job = await env.service.start_backup(target_id)
        done = await wait_job(env, job.id)

    assert (done.status, done.error_code) == ("failed", "crashed")
    assert any(r.exc_info for r in caplog.records if "failed unexpectedly" in r.getMessage())
    assert [e.event_type for e in env.events()] == [NotificationEventType.ENGINE_CRASH]
    await asyncio.sleep(0)
    assert not env.lock.locked()


async def test_shutdown_cancels_and_records_running_jobs(env):
    target_id = await env.add_rows()
    started = asyncio.Event()

    async def endless(*_args, **_kwargs) -> None:
        started.set()
        await asyncio.sleep(3600)

    env.service._backup = endless  # type: ignore[method-assign]
    env.service._restore = endless  # type: ignore[method-assign]
    await env.service.start()
    backup = await env.service.start_backup(target_id)
    await started.wait()
    with pytest.raises(SyncBusyError):  # a second start of the profile is refused, not queued
        await env.service.start_restore(target_id, "current", RestoreScope.LOCAL_ONLY)

    await asyncio.wait_for(env.service.stop(timeout=10), timeout=15)

    done = await env.service.get_job(backup.id)
    assert done is not None and (done.status, done.error_code) == ("failed", "shutdown")
    assert done.finished_at is not None
    assert not env.lock.locked()


async def test_restore_lock_timeout_fails_the_job_and_notifies(env, monkeypatch):
    """A restore that waited too long for the lock records it; it is no unlogged exception."""
    target_id = await env.add_rows()
    write(env.local, "a.txt", "a")
    await env.service.run_backup(target_id)
    monkeypatch.setattr(common, "LOCK_TIMEOUT", 0.1)

    await env.lock.acquire()
    try:
        job = await env.service.restore(target_id, "current", RestoreScope.LOCAL_ONLY)
    finally:
        env.lock.release()

    assert (job.status, job.error_code) == ("failed", "sync_busy")
    assert env.events()[-1].event_type == NotificationEventType.BACKUP_RESTORE_FAILED


# --- the pause of a single-file restore ---


async def test_a_pull_queued_behind_a_file_restore_keeps_the_restored_files(env):
    """Mirror profile: restoring a lost file must not be undone by the scheduled pull waiting for the lock."""
    target_id = await env.add_rows()
    mark_both(env)
    for side in (env.local, env.remote):
        write(side, "lost.txt", "precious")
        write(side, "other.txt", "o")
    assert (await env.service.run_backup(target_id)).status == "completed"
    snapshot = (await env.service.list_snapshots(await _target(env, target_id)))[0].snapshot_id
    for side in (env.local, env.remote):
        (side / "lost.txt").unlink()  # gone on both sides; only the backup has it

    job = await env.service.start_restore_files(target_id, snapshot, ["lost.txt"])
    pull = asyncio.create_task(env.engine._scheduled_sync())  # queued on the sync lock
    await asyncio.sleep(0.05)
    assert not pull.done()

    assert (await wait_job(env, job.id)).status == "completed"
    assert await asyncio.wait_for(pull, timeout=60) is None  # skipped: syncing is paused
    assert files_under(env.local)["lost.txt"] == "precious"
    assert env.engine._state.auto_paused
    async with env.factory() as session:
        assert "restored to the local folder" in ((await session.get(SyncProfile, 1)).pause_reason or "")
        assert (await session.execute(select(SyncJob))).scalars().all() == []  # the pull never ran


async def _target(env: Env, target_id: int) -> BackupTarget:
    async with env.factory() as session:
        target = await session.get(BackupTarget, target_id)
        assert target is not None
        return target


# --- per-file actions in the background ---


def cache_diff(engine: SyncEngine, *paths: str, category=ChangeCategory.LOCAL_ONLY) -> None:
    files = [FileDiff(path=p, category=category, is_conflict=category == ChangeCategory.MODIFIED_BOTH)
             for p in paths]
    engine._state.cache_diff(DiffResponse(files=files, summary=DiffSummary(total=len(files))))


async def test_per_file_actions_run_in_the_background(env):
    await env.add_rows()
    mark_both(env)
    write(env.local, "a.txt", "a")
    cache_diff(env.engine, "a.txt", "gone.txt")

    running = await env.engine.launch_selective([
        SelectiveSyncItem(path="a.txt", action=FileAction.PUSH),
        SelectiveSyncItem(path="gone.txt", action=FileAction.PUSH),
    ])

    assert running.status == JobStatus.RUNNING and env.lock.locked()
    with pytest.raises(SyncBusyError):
        await env.engine.launch_selective([SelectiveSyncItem(path="a.txt", action=FileAction.PUSH)])
    launch = env.engine._launch
    assert launch is not None and launch.task is not None
    await asyncio.wait_for(launch.task, timeout=60)

    result = env.engine.selective_result(running.job_id)
    assert result is not None and (result.status, result.succeeded, result.failed) == (JobStatus.FAILED, 1, 1)
    assert files_under(env.remote)["a.txt"] == "a"
    assert not env.lock.locked()


async def test_a_crash_of_per_file_actions_fails_their_job(env):
    await env.add_rows()
    cache_diff(env.engine, "a.txt")
    env.engine._selective_sync = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    running = await env.engine.launch_selective([SelectiveSyncItem(path="a.txt", action=FileAction.PUSH)])
    assert env.engine._launch is not None and env.engine._launch.task is not None
    await asyncio.wait_for(env.engine._launch.task, timeout=10)

    result = env.engine.selective_result(running.job_id)
    assert result is not None and result.status == JobStatus.FAILED
    assert result.errors[0].error == SELECTIVE_CRASHED
    async with env.factory() as session:
        job = await session.get(SyncJob, running.job_id)
        assert job is not None and job.status == "failed"
        errors = (await session.execute(select(SyncError.message))).scalars().all()
    assert errors == [SELECTIVE_CRASHED]


# --- the bandwidth limit and tuning flags on every transfer (argv) ---


@pytest.fixture
def argv(monkeypatch):
    """Every rclone command line, as rclone ran it."""
    seen: list[list[str]] = []
    real = asyncio.create_subprocess_exec

    async def recording(*cmd, **kwargs):
        seen.append([str(c) for c in cmd])
        return await real(*cmd, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recording)
    return seen


def commands(seen: list[list[str]], subcommand: str) -> list[list[str]]:
    return [cmd for cmd in seen if subcommand in cmd[3:5]]


def assert_tuned(cmd: list[str]) -> None:
    flags = cmd[:cmd.index("--")] if "--" in cmd else cmd
    assert "--bwlimit" in flags and flags[flags.index("--bwlimit") + 1] == "10M", cmd
    assert "--transfers" in flags and flags[flags.index("--transfers") + 1] == "2", cmd
    assert "--checkers=3" in flags, cmd
    # Filters, --update and --max-delete never reach copies of chosen files, backups or restores.
    for flag in ("--exclude", "--update", "--max-delete"):
        assert flag not in flags, cmd


def test_transfer_tuning_args_keeps_only_tuning_flags():
    assert transfer_tuning_args(PROFILE_ARGS, "1M:off") == ["--transfers", "2", "--checkers=3", "--bwlimit", "1M:off"]
    assert transfer_tuning_args([], None) == []


async def test_per_file_push_pull_and_keep_both_use_the_bandwidth_limit(limited, argv):
    env = limited
    await env.add_rows()
    mark_both(env)
    write(env.local, "up.txt", "u")
    write(env.remote, "down.txt", "d")
    write(env.local, "both.txt", "local")
    write(env.remote, "both.txt", "remote!")
    cache_diff_all = [
        FileDiff(path="up.txt", category=ChangeCategory.LOCAL_ONLY),
        FileDiff(path="down.txt", category=ChangeCategory.REMOTE_ONLY),
        FileDiff(path="both.txt", category=ChangeCategory.MODIFIED_BOTH, is_conflict=True),
    ]
    env.engine._state.cache_diff(DiffResponse(files=cache_diff_all, summary=DiffSummary(total=3)))

    result = await env.engine.selective_sync([
        SelectiveSyncItem(path="up.txt", action=FileAction.PUSH),
        SelectiveSyncItem(path="down.txt", action=FileAction.PULL),
        SelectiveSyncItem(path="both.txt", action=FileAction.KEEP_BOTH),
    ])

    assert result.failed == 0, result.errors  # rclone refuses --exclude next to --files-from-raw
    copies = commands(argv, "copy")
    copytos = commands(argv, "copyto")
    assert len(copies) == 2 and len(copytos) == 3
    for cmd in copies + copytos:
        assert_tuned(cmd)


async def test_conflict_resolution_copies_use_the_bandwidth_limit(limited, argv):
    env = limited
    await env.add_rows()
    mark_both(env)
    write(env.local, "c.txt", "local version")
    write(env.remote, "c.txt", "remote version")
    env.engine._state.last_sync = datetime.now(timezone.utc) - timedelta(hours=1)
    diff = await env.engine.enhanced_diff()
    assert [f.path for f in diff.files if f.is_conflict] == ["c.txt"], diff
    async with env.factory() as session:
        (conflict_id,) = (await session.execute(select(Conflict.id))).scalars().all()
    argv.clear()

    await env.engine.resolve_conflict(conflict_id, ConflictResolution.KEEP_LOCAL)

    copies = commands(argv, "copy")
    assert len(copies) == 1
    assert_tuned(copies[0])
    assert files_under(env.remote)["c.txt"] == "local version"


async def test_backups_and_restores_use_the_profile_bandwidth_limit(limited, argv):
    env = limited
    target_id = await env.add_rows()
    write(env.local, "a.txt", "a1")
    write(env.local, "skip.tmp", "a backup ignores the profile's filters")

    assert (await env.service.run_backup(target_id)).status == "completed"
    syncs = commands(argv, "sync")
    assert len(syncs) == 1
    assert_tuned(syncs[0])
    assert "skip.tmp" in files_under(env.backups / "current")

    write(env.local, "a.txt", "a2")
    argv.clear()
    job = await env.service.restore(target_id, "current", RestoreScope.LOCAL_ONLY)
    assert job.status == "completed", job.error_message
    copies = commands(argv, "copy")
    assert copies
    for cmd in copies:
        assert_tuned(cmd)
    assert files_under(env.local)["a.txt"] == "a1"


async def test_job_errors_never_carry_rclone_output(env, monkeypatch):
    """A failed backup stores redacted details; clients get a code and a fixed message."""
    from backend.api.routes.backups import _job_response
    from backend.exceptions import RcloneError

    target_id = await env.add_rows()
    write(env.local, "a.txt", "a")
    stderr = "ERROR : a.txt: Failed to copy to :sftp,host=nas,pass=hunter2:/b: googleapi: Error 403: ya29 quota"
    monkeypatch.setattr(env.rclone, "sync_with_backup_dir", AsyncMock(side_effect=RcloneError(stderr)))

    job = await env.service.run_backup(target_id)

    assert (job.status, job.error_code) == ("failed", "backup_failed")
    assert "hunter2" not in (job.error_message or "") and "pass=***" in (job.error_message or "")  # redacted
    public = _job_response(job).model_dump_json()
    for fragment in ("googleapi", "403", "ya29", "Failed to copy", "a.txt", "sftp", "pass="):
        assert fragment not in public
    assert "The OmniSync log has the details." in public
