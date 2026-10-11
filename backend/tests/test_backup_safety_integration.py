"""Backups that go wrong: failed verification, damaged or missing snapshots, runs that collide or are stopped.

Real rclone on temp folders, like test_backup_features_integration.py
(whose Env and helpers this reuses): remotes of type `local` in a temp
rclone.conf and a SQLite database in a file (runs started in the
background write from their own task). Where a failure cannot be made
with real files (rclone failing to start, the database failing while a
run is recorded) only that one call is replaced.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.schemas import BackupMode, BackupTargetType, RestoreScope
from backend.db.models import Base, BackupJob, BackupJobStatus, BackupTarget
from backend.exceptions import RcloneAuthError, RcloneError, SyncBusyError
from backend.services.backup_service import BackupRunning, RestoreRefused, public_job_error
from backend.services.backup_service import common as backup_common
from backend.services.backup_service.browse import ARCHIVE_LIST_CACHE
from backend.services.backup_service.jobs import restore_error_code
from backend.services.history import INTERRUPTED
from backend.services.notification_events import NotificationEventType
from backend.services.sync_engine import remote_join
from backend.tests.test_backup_features_integration import PASSPHRASE, configure, events, load
from backend.tests.test_backup_restore_integration import Env, files_under, write

if shutil.which("rclone") is None and os.environ.get("OMNISYNC_REQUIRE_RCLONE") == "1":
    raise RuntimeError("OMNISYNC_REQUIRE_RCLONE=1 but rclone is not on PATH")
pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")

# A secret in the shape redact_secrets masks: it must never reach a job record.
SECRET = "password='fake-Pass_123'"
RUNNING = BackupJobStatus.RUNNING.value
WAIT = 120.0  # seconds: rclone is slow on a loaded test machine


@pytest.fixture
async def env(tmp_path: Path):
    db = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'omnisync.db'}")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    environment = Env(tmp_path, async_sessionmaker(db, expire_on_commit=False))
    yield environment
    await environment.service.stop_jobs(timeout=WAIT)
    await db.dispose()


async def finish(env: Env, job: BackupJob) -> BackupJob:
    """Wait for a run started in the background to end; returns its job record."""
    tasks = {task for task, job_id in env.service._job_tasks.items() if job_id == job.id}
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=WAIT)
        assert not pending, f"job {job.id} did not end"
    ended = await env.service.get_job(job.id)
    assert ended is not None
    return ended


def gate(env: Env, monkeypatch, method: str) -> tuple[asyncio.Event, asyncio.Event]:
    """Hold every call of the rclone method ``method`` until released: (started, release)."""
    started, release = asyncio.Event(), asyncio.Event()
    original: Callable[..., Awaitable[Any]] = getattr(env.service._rclone, method)

    async def gated(*args, **kwargs):
        started.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(env.service._rclone, method, gated)
    return started, release


async def jobs_of(env: Env, target_id: int) -> list[BackupJob]:
    async with env.factory() as session:
        return list((await session.execute(
            select(BackupJob).where(BackupJob.target_id == target_id).order_by(BackupJob.id)
        )).scalars())


def lock_free(env: Env) -> bool:
    return not env.engine.sync_lock.locked() and not env.service._running_targets


async def remote_archive_target(env: Env, *, passphrase: str | None = None) -> int:
    target_id = await env.add_target(
        f"backupremote:{env.backups}", mode=BackupMode.ARCHIVE.value, target_type=BackupTargetType.REMOTE.value,
    )
    await configure(env, target_id, passphrase=passphrase, verify=True)
    return target_id


def after_upload(env: Env, monkeypatch, damage: Callable[[str, str], Awaitable[None]]) -> None:
    """Run ``damage(local temp dir, archive name)`` right after each archive upload."""
    copy_files = env.service._rclone.copy_files

    async def copy_then_damage(source, dest, file_paths, *args, **kwargs):
        result = await copy_files(source, dest, file_paths, *args, **kwargs)
        await damage(source, file_paths[0])
        return result

    monkeypatch.setattr(env.service._rclone, "copy_files", copy_then_damage)


# ── Verification ─────────────────────────────────────────────────────


async def test_an_empty_folder_backs_up_and_verifies_trivially(env):
    """The first backup of an empty folder is allowed (nothing to lose), and is not a failed verification."""
    target_id = await env.add_target(str(env.backups))
    await configure(env, target_id, verify=True)
    job = await env.service.run_backup(target_id)
    assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
    assert (job.verify_status, job.verify_message) == ("verified", "Nothing to verify: the folder was empty.")
    assert not [e for e in events(env) if e.event_type == NotificationEventType.BACKUP_VERIFY_FAILED]


async def test_mirror_verification_reports_a_file_missing_from_the_backup(env, monkeypatch):
    """A file that never reached current/ fails the verification by name, and the user is notified."""
    target_id = await env.add_target(str(env.backups))
    await configure(env, target_id, verify=True)
    write(env.local, "keep.txt", "k")
    write(env.local, "sub dir/lost file.txt", "l")
    sync = env.service._rclone.sync_with_backup_dir

    async def sync_then_lose(source, dest, backup_dir, **kwargs):
        result = await sync(source, dest, backup_dir, **kwargs)
        (env.backups / "current" / "sub dir" / "lost file.txt").unlink()
        return result

    monkeypatch.setattr(env.service._rclone, "sync_with_backup_dir", sync_then_lose)
    job = await env.service.run_backup(target_id)
    assert job.status == BackupJobStatus.COMPLETED.value
    assert job.verify_status == "failed"
    message = job.verify_message or ""
    assert "1 file(s) are missing from the backup" in message and "differ from the folder" not in message
    assert "sub dir/lost file.txt" in message
    failed = [e for e in events(env) if e.event_type == NotificationEventType.BACKUP_VERIFY_FAILED]
    assert len(failed) == 1 and job.snapshot_id in failed[0].body


async def test_mirror_verification_fails_when_the_backup_is_gone(env, monkeypatch):
    """current/ removed right after the copy: the check cannot pass, and says so instead of 'verified'."""
    target_id = await env.add_target(str(env.backups))
    await configure(env, target_id, verify=True)
    write(env.local, "a.txt", "a")
    lsjson = env.service._rclone.lsjson

    async def list_then_remove(path, *args, **kwargs):
        entries = await lsjson(path, *args, **kwargs)
        if path.endswith("current"):
            shutil.rmtree(env.backups / "current")
        return entries

    monkeypatch.setattr(env.service._rclone, "lsjson", list_then_remove)
    job = await env.service.run_backup(target_id)
    assert job.status == BackupJobStatus.COMPLETED.value
    assert job.verify_status == "failed" and job.verify_message


async def test_mirror_verification_fails_when_the_backup_cannot_be_read(env, monkeypatch):
    """rclone check failing (current/ unreadable) is reported as a failed verification, not a pass."""
    target_id = await env.add_target(str(env.backups))
    await configure(env, target_id, verify=True)
    write(env.local, "sub/a.txt", "a")
    lsjson = env.service._rclone.lsjson
    current_sub = env.backups / "current" / "sub"

    async def list_then_lock(path, *args, **kwargs):
        entries = await lsjson(path, *args, **kwargs)
        if path.endswith("current"):
            current_sub.chmod(0)
        return entries

    monkeypatch.setattr(env.service._rclone, "lsjson", list_then_lock)
    try:
        job = await env.service.run_backup(target_id)
    finally:
        current_sub.chmod(0o700)
    assert job.status == BackupJobStatus.COMPLETED.value
    assert job.verify_status == "failed" and job.verify_message
    assert "match the folder" not in job.verify_message


async def test_mirror_verification_that_cannot_run_is_a_failure_without_secrets(env, monkeypatch):
    target_id = await env.add_target(str(env.backups))
    await configure(env, target_id, verify=True)
    write(env.local, "a.txt", "a")

    async def broken(*_args, **_kwargs):
        raise RcloneError(f"could not start: {SECRET}")

    monkeypatch.setattr(env.service._rclone, "verify_copy", broken)
    job = await env.service.run_backup(target_id)
    assert job.status == BackupJobStatus.COMPLETED.value  # the backup itself is there
    assert job.verify_status == "failed"
    assert (job.verify_message or "").startswith("The verification could not run")
    assert "fake-Pass" not in (job.verify_message or "")


async def test_archive_verification_catches_an_archive_missing_after_upload(env, monkeypatch):
    target_id = await remote_archive_target(env)
    write(env.local, "a.txt", "a" * 1000)

    async def remove(_temp_dir: str, name: str) -> None:
        (env.backups / name).unlink()

    after_upload(env, monkeypatch, remove)
    job = await env.service.run_backup(target_id)
    assert job.verify_status == "failed"
    assert job.verify_message == f"{job.snapshot_id} is not at the target after the upload."


async def test_archive_verification_catches_an_incomplete_upload(env, monkeypatch):
    target_id = await remote_archive_target(env)
    write(env.local, "a.txt", os.urandom(4000).hex())

    async def truncate(_temp_dir: str, name: str) -> None:
        stored = env.backups / name
        with open(stored, "r+b") as fh:
            fh.truncate(stored.stat().st_size // 2)

    after_upload(env, monkeypatch, truncate)
    job = await env.service.run_backup(target_id)
    assert job.verify_status == "failed"
    assert "the upload is incomplete" in (job.verify_message or "")
    assert f"{job.size_bytes:,}" in (job.verify_message or "")


async def test_encrypted_archive_verification_catches_other_content_of_the_same_size(env, monkeypatch):
    """Stored size alone proves little: the decrypted first block must be the archive written."""
    target_id = await remote_archive_target(env, passphrase=PASSPHRASE)
    write(env.local, "a.txt", "secret content")

    async def replace(temp_dir: str, name: str) -> None:
        impostor = Path(temp_dir).parent / "impostor"
        impostor.write_bytes(b"\0" * os.path.getsize(os.path.join(temp_dir, name)))
        target = await load(env, target_id)
        await env.service._rclone.copyto(str(impostor), remote_join(env.service.storage_root(target), name))

    after_upload(env, monkeypatch, replace)
    job = await env.service.run_backup(target_id)
    assert job.status == BackupJobStatus.COMPLETED.value
    assert job.verify_status == "failed"
    assert job.verify_message == f"{job.snapshot_id} at the target does not decrypt to the archive written."


async def test_archive_verification_that_cannot_run_is_a_failure_without_secrets(env, monkeypatch):
    target_id = await remote_archive_target(env)
    write(env.local, "a.txt", "a")

    async def broken(*_args, **_kwargs):
        raise RcloneError(f"listing failed: {SECRET}")

    monkeypatch.setattr(env.service._rclone, "lsjson_paths", broken)
    job = await env.service.run_backup(target_id)
    assert job.verify_status == "failed"
    assert (job.verify_message or "").startswith("The verification could not run")
    assert "fake-Pass" not in (job.verify_message or "")


@pytest.mark.parametrize("mode", [BackupMode.MIRROR.value, BackupMode.ARCHIVE.value])
async def test_an_emptied_folder_is_refused_and_the_backup_kept(env, mode):
    """An unmounted (empty) folder backed up would push every backed-up file out, and retention delete it."""
    target_id = await env.add_target(str(env.backups), mode=mode)
    write(env.local, "a.txt", "a")
    await env.backup(target_id)
    before = {str(p.relative_to(env.backups)): p.read_bytes() for p in env.backups.rglob("*") if p.is_file()}
    (env.local / "a.txt").unlink()
    job = await env.service.run_backup(target_id)
    assert (job.status, job.error_code) == (BackupJobStatus.FAILED.value, "backup_refused")
    assert "Nothing was backed up" in (job.error_message or "")
    after = {str(p.relative_to(env.backups)): p.read_bytes() for p in env.backups.rglob("*") if p.is_file()}
    assert after == before


# ── Starting runs: collisions, unknown targets ───────────────────────


async def test_two_backups_of_one_target_at_once_start_one_run(env):
    """Two clicks on "Back up now" at once: one run, the other refused, and the target free afterwards."""
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a")
    results = await asyncio.gather(env.service.start_backup(target_id), env.service.start_backup(target_id),
                                   return_exceptions=True)
    started = [r for r in results if isinstance(r, BackupJob)]
    refused = [r for r in results if isinstance(r, BaseException)]
    assert len(started) == 1 and len(refused) == 1, results
    assert isinstance(refused[0], BackupRunning)
    job = await finish(env, started[0])
    assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
    assert [j.id for j in await jobs_of(env, target_id)] == [job.id]
    assert lock_free(env)


async def test_a_backup_in_progress_refuses_a_second_start_and_a_restore(env, monkeypatch):
    """While a backup writes, neither another backup nor a restore of the same profile may start."""
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a1")
    snapshot = await env.backup(target_id)
    write(env.local, "a.txt", "a2")
    started, release = gate(env, monkeypatch, "sync_with_backup_dir")
    job = await env.service.start_backup(target_id)
    await asyncio.wait_for(started.wait(), WAIT)

    with pytest.raises(BackupRunning):
        await env.service.start_backup(target_id)
    with pytest.raises(SyncBusyError):
        await env.service.start_restore(target_id, snapshot, RestoreScope.LOCAL_ONLY)
    with pytest.raises(SyncBusyError):
        await env.service.start_restore_files(target_id, snapshot, ["a.txt"])
    assert len(await jobs_of(env, target_id)) == 2  # the first backup and the running one

    release.set()
    assert (await finish(env, job)).status == BackupJobStatus.COMPLETED.value
    assert (env.local / "a.txt").read_text() == "a2"  # no restore ran
    assert lock_free(env)


async def test_unknown_targets_record_nothing_and_hold_no_lock(env):
    await env.add_target(str(env.backups))
    with pytest.raises(ValueError):
        await env.service.start_backup(999)
    with pytest.raises(ValueError):
        await env.service.start_restore(999, "2026-01-01T00-00-00", RestoreScope.LOCAL_ONLY)
    with pytest.raises(ValueError):
        await env.service.restore_preview(999, "2026-01-01T00-00-00", RestoreScope.LOCAL_ONLY)
    async with env.factory() as session:
        assert (await session.execute(select(BackupJob))).first() is None
    assert lock_free(env)


async def test_a_target_deleted_while_its_start_is_processed_frees_the_lock(env, monkeypatch):
    """The lock is taken before the job is recorded: a failed record must give it back."""
    target_id = await env.add_target(str(env.backups))
    lookup = env.service._target_profile_id

    async def lookup_then_delete(tid: int) -> int:
        profile_id = await lookup(tid)
        async with env.factory() as session:
            target = await session.get(BackupTarget, tid)
            await session.delete(target)
            await session.commit()
        return profile_id

    monkeypatch.setattr(env.service, "_target_profile_id", lookup_then_delete)
    with pytest.raises(ValueError):
        await env.service.start_backup(target_id)
    assert lock_free(env)


async def test_a_target_deleted_during_its_run_frees_the_profile(env, monkeypatch, caplog):
    """Its job goes with it; the profile's lock and the target's running mark are given back regardless."""
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a")
    started, release = gate(env, monkeypatch, "sync_with_backup_dir")
    job = await env.service.start_backup(target_id)
    task = next(t for t, j in env.service._job_tasks.items() if j == job.id)
    await asyncio.wait_for(started.wait(), WAIT)
    async with env.factory() as session:
        target = await session.get(BackupTarget, target_id)
        await session.delete(target)
        await session.commit()
    release.set()
    await asyncio.wait({task}, timeout=WAIT)
    assert task.done()
    assert await env.service.get_job(job.id) is None
    assert lock_free(env) and not env.service._job_tasks
    assert any("ended with an error" in r.getMessage() for r in caplog.records)


# ── Stopping runs ────────────────────────────────────────────────────


async def test_a_cancelled_run_is_recorded_as_stopped_and_frees_the_profile(env, monkeypatch):
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a")
    started, _release = gate(env, monkeypatch, "sync_with_backup_dir")
    job = await env.service.start_backup(target_id)
    await asyncio.wait_for(started.wait(), WAIT)
    task = next(t for t, j in env.service._job_tasks.items() if j == job.id)
    task.cancel()
    ended = await finish(env, job)
    assert (ended.status, ended.error_code) == (BackupJobStatus.FAILED.value, "stopped")
    assert public_job_error(ended) == ("stopped", "Stopped before it finished.")
    assert lock_free(env)
    assert not (env.backups / "current").exists()  # stopped before rclone wrote anything


async def test_shutdown_records_a_run_even_when_the_run_could_not(env, monkeypatch, caplog):
    """The run's own record of the cancellation fails (database busy): stop_jobs() records it instead."""
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a")
    started, _release = gate(env, monkeypatch, "sync_with_backup_dir")
    job = await env.service.start_backup(target_id)
    await asyncio.wait_for(started.wait(), WAIT)
    end_job = env.service._end_job
    calls = 0

    async def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database is locked")
        return await end_job(*args, **kwargs)

    monkeypatch.setattr(env.service, "_end_job", fail_once)
    await env.service.stop_jobs(timeout=WAIT)
    ended = await env.service.get_job(job.id)
    assert ended is not None and (ended.status, ended.error_code) == (BackupJobStatus.FAILED.value, "shutdown")
    assert any("Could not record the stopped backup job" in r.getMessage() for r in caplog.records)
    assert lock_free(env)


async def test_shutdown_never_fails_when_nothing_can_be_recorded(env, monkeypatch, caplog):
    """The job stays "running" and is marked interrupted at the next start; the shutdown goes on."""
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a")
    started, _release = gate(env, monkeypatch, "sync_with_backup_dir")
    job = await env.service.start_backup(target_id)
    await asyncio.wait_for(started.wait(), WAIT)

    async def always_fail(*_args, **_kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(env.service, "_end_job", always_fail)
    await env.service.stop_jobs(timeout=WAIT)
    ended = await env.service.get_job(job.id)
    assert ended is not None and ended.status == RUNNING
    assert any(f"Could not record the stopped job {job.id}" in r.getMessage() for r in caplog.records)
    assert lock_free(env)


async def test_a_crash_is_recorded_and_reported_even_when_reporting_fails(env, monkeypatch, caplog):
    """An unexpected exception ends the job as "crashed" with a redacted message; a failing notifier changes nothing."""
    target_id = await env.add_target(str(env.backups))

    async def crash(*_args, **_kwargs):
        raise KeyError(f"surprise {SECRET}")

    monkeypatch.setattr(env.service, "check_liveness", crash)
    env.service._dispatcher.dispatch.side_effect = RuntimeError("SMTP down")
    job = await env.service.run_backup(target_id)
    assert (job.status, job.error_code) == (BackupJobStatus.FAILED.value, "crashed")
    assert "fake-Pass" not in (job.error_message or "")
    assert public_job_error(job)[0] == "crashed"
    assert any("Could not report the crash" in r.getMessage() for r in caplog.records)
    assert lock_free(env)


async def test_a_crash_that_cannot_be_recorded_is_logged(env, monkeypatch, caplog):
    target_id = await env.add_target(str(env.backups))

    async def crash(*_args, **_kwargs):
        raise KeyError("surprise")

    async def always_fail(*_args, **_kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(env.service, "check_liveness", crash)
    monkeypatch.setattr(env.service, "_end_job", always_fail)
    with caplog.at_level(logging.ERROR, logger="backend"):
        job = await env.service.run_backup(target_id)
    assert job.status == RUNNING  # left for recover_interrupted_jobs at the next start
    assert any("Could not record the failed backup job" in r.getMessage() for r in caplog.records)
    assert lock_free(env)


# ── Restores started in the background ───────────────────────────────


async def test_a_restore_started_in_the_background_restores_the_snapshot(env):
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a1")
    snapshot = await env.backup(target_id)
    write(env.local, "a.txt", "changed")
    job = await env.service.start_restore(target_id, snapshot, RestoreScope.LOCAL_ONLY)
    assert job.status == RUNNING and job.direction == "restore"
    ended = await finish(env, job)
    assert ended.status == BackupJobStatus.COMPLETED.value, ended.error_message
    assert files_under(env.local) == {"a.txt": "a1"}
    assert lock_free(env)


async def test_a_restore_of_a_missing_snapshot_fails_its_job(env):
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a1")
    await env.backup(target_id)
    job = await finish(env, await env.service.start_restore(target_id, "2001-01-01T00-00-00", RestoreScope.BOTH))
    assert (job.status, job.error_code) == (BackupJobStatus.FAILED.value, "snapshot_not_found")
    assert files_under(env.local) == {"a.txt": "a1"}
    assert lock_free(env)


# ── Browsing and restoring chosen files ──────────────────────────────


ODD_NAMES = {
    "-rf.txt": "dash",
    "--help": "double dash",
    "with space.txt": "space",
    "Ünïcödé/файл 日本.txt": "unicode",
    "n" * 200 + ".txt": "long",
}


@pytest.mark.parametrize("mode", [BackupMode.MIRROR.value, BackupMode.ARCHIVE.value])
async def test_odd_file_names_are_browsed_and_restored_exactly(env, mode):
    """Names that look like rclone flags, or hold spaces, non-Latin letters or 200 characters, survive."""
    target_id = await env.add_target(str(env.backups), mode=mode)
    for name, content in ODD_NAMES.items():
        write(env.local, name, content)
    snapshot = await env.backup(target_id)
    target = await load(env, target_id)
    assert {f.path for f in await env.service.snapshot_files(target, snapshot)} >= set(ODD_NAMES)

    for name in ODD_NAMES:
        write(env.local, name, "overwritten")
    job = await env.service.restore_files(target_id, snapshot, list(ODD_NAMES))
    assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
    assert files_under(env.local) == ODD_NAMES


@pytest.mark.parametrize("mode", [BackupMode.MIRROR.value, BackupMode.ARCHIVE.value])
@pytest.mark.parametrize("path", ["../outside.txt", "../../etc/passwd", "/a.txt/..", "sub/../../outside.txt"])
async def test_paths_that_climb_out_select_nothing(env, tmp_path: Path, mode, path):
    """A path leaving the snapshot selects no file: nothing is written, inside or outside the folder."""
    target_id = await env.add_target(str(env.backups), mode=mode)
    write(env.local, "a.txt", "a")
    write(tmp_path, "outside.txt", "outside")
    snapshot = await env.backup(target_id)
    with pytest.raises(ValueError, match="None of the selected paths"):
        await env.service.restore_files(target_id, snapshot, [path])
    assert (tmp_path / "outside.txt").read_text() == "outside"
    assert files_under(env.local) == {"a.txt": "a"}


async def test_a_damaged_archive_is_refused_not_misread(env):
    target_id = await env.add_target(str(env.backups), mode=BackupMode.ARCHIVE.value)
    write(env.local, "a.txt", "a")
    snapshot = await env.backup(target_id)
    (env.backups / snapshot).write_bytes(b"this is not a tar.gz")
    target = await load(env, target_id)
    with pytest.raises(RestoreRefused, match="may be damaged"):
        await env.service.snapshot_files(target, snapshot)
    with pytest.raises(RestoreRefused):
        await env.service.restore_files(target_id, snapshot, ["a.txt"])


async def test_a_missing_or_unreadable_remote_archive(env):
    """Missing, or encrypted with another passphrase (the names do not decrypt): "not found", never an empty list."""
    target_id = await remote_archive_target(env, passphrase=PASSPHRASE)
    write(env.local, "a.txt", "a")
    snapshot = await env.backup(target_id)
    target = await load(env, target_id)
    with pytest.raises(ValueError, match="not found"):
        await env.service.snapshot_files(target, "backup-2001-01-01T00-00-00.tar.gz")
    with pytest.raises(ValueError, match="Invalid snapshot id"):
        await env.service.snapshot_files(target, "../backup-2001-01-01T00-00-00.tar.gz")
    target.encryption_password = await env.service.obscure_passphrase("another passphrase")
    with pytest.raises(ValueError, match="not found"):
        await env.service.snapshot_files(target, snapshot)


async def test_an_unreadable_remote_archive_is_an_rclone_error_not_a_missing_snapshot(env):
    """A permission problem must not look like "no such snapshot" (which would invite deleting it)."""
    target_id = await remote_archive_target(env)
    write(env.local, "a.txt", "a")
    snapshot = await env.backup(target_id)
    stored = env.backups / snapshot
    stored.chmod(0)
    try:
        with pytest.raises(RcloneError):
            await env.service.snapshot_files(await load(env, target_id), snapshot)
    finally:
        stored.chmod(0o600)


async def test_archive_lists_are_cached_for_the_newest_snapshots_only(env):
    """Browsing many archives keeps memory bounded: the oldest listed one is read again from the target."""
    target_id = await env.add_target(str(env.backups), mode=BackupMode.ARCHIVE.value)
    write(env.local, "a.txt", "a")
    first = await env.backup(target_id)
    names = [first] + [f"backup-2020-01-01T00-00-{i:02d}.tar.gz" for i in range(ARCHIVE_LIST_CACHE)]
    for name in names[1:]:
        shutil.copy(env.backups / first, env.backups / name)
    target = await load(env, target_id)
    for name in names:
        assert [f.path for f in await env.service.snapshot_files(target, name)] == ["a.txt"]
    assert len(env.service._archive_lists) == ARCHIVE_LIST_CACHE
    # the newest still come from memory, the first was dropped and is read again
    (env.backups / names[-1]).unlink()
    assert [f.path for f in await env.service.snapshot_files(target, names[-1])] == ["a.txt"]
    (env.backups / first).unlink()
    with pytest.raises(ValueError, match="not found"):
        await env.service.snapshot_files(target, first)


async def test_mirror_snapshot_ids_are_checked(env):
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a")
    await env.backup(target_id)
    target = await load(env, target_id)
    for bad in ("latest", "../current", "2026-13-45T99-99-99"):
        with pytest.raises(ValueError, match="Invalid snapshot id"):
            await env.service.snapshot_files(target, bad)
    with pytest.raises(ValueError, match="not found"):
        await env.service.snapshot_files(target, "2001-01-01T00-00-00")


async def test_manifest_times_that_are_missing_or_garbled_are_unknown_not_fatal(env):
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a")
    await env.backup(target_id)
    target = await load(env, target_id)
    snapshot = "2020-01-01T00-00-00"
    await env.service._write_manifest(env.service.storage_root(target), snapshot, [
        {"path": "none.txt", "size": 1},
        {"path": "number.txt", "size": 1, "mtime": 1700000000},
        {"path": "garbled.txt", "size": 1, "mtime": "yesterday-ish"},
        {"path": "good.txt", "size": 1, "mtime": "2020-01-01T00:00:00Z"},
    ])
    files = {f.path: f.mtime for f in await env.service.snapshot_files(target, snapshot)}
    assert files == {"none.txt": None, "number.txt": None, "garbled.txt": None,
                     "good.txt": datetime(2020, 1, 1, tzinfo=timezone.utc)}


async def test_restore_files_into_a_missing_folder_is_refused(env, tmp_path: Path):
    """A missing (unmounted) local folder or a target folder without parent: nothing written, nothing created."""
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a")
    snapshot = await env.backup(target_id)

    nowhere = tmp_path / "no" / "such" / "dir"
    job = await env.service.restore_files(target_id, snapshot, ["a.txt"], str(nowhere))
    assert (job.status, job.error_code) == (BackupJobStatus.FAILED.value, "restore_refused")
    assert public_job_error(job) == ("restore_refused", job.error_message)
    assert not (tmp_path / "no").exists()

    shutil.rmtree(env.local)
    job = await env.service.restore_files(target_id, snapshot, ["a.txt"])
    assert (job.status, job.error_code) == (BackupJobStatus.FAILED.value, "restore_refused")
    assert "missing or not mounted" in (job.error_message or "")
    assert not env.local.exists()
    env.engine.hold.assert_not_awaited()  # syncing was not paused for a restore that did not happen
    failed = [e for e in events(env) if e.event_type == NotificationEventType.BACKUP_RESTORE_FAILED]
    assert len(failed) == 2


async def test_restore_files_waits_for_the_sync_lock_then_gives_up(env, monkeypatch):
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a")
    snapshot = await env.backup(target_id)
    write(env.local, "a.txt", "newer")
    monkeypatch.setattr(backup_common, "LOCK_TIMEOUT", 0.05)
    async with env.engine.sync_lock:  # a sync of the profile runs
        job = await env.service.restore_files(target_id, snapshot, ["a.txt"])
    assert (job.status, job.error_code) == (BackupJobStatus.FAILED.value, "sync_busy")
    assert (env.local / "a.txt").read_text() == "newer"
    assert lock_free(env)


async def test_restore_preview_of_missing_folders(env):
    """A missing local folder is refused (it is never created); a missing remote folder gets every file."""
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a")
    write(env.local, "b.txt", "b")
    snapshot = await env.backup(target_id)
    shutil.rmtree(env.remote)
    preview = await env.service.restore_preview(target_id, snapshot, RestoreScope.REMOTE_ONLY)
    (side,) = preview.sides
    assert (side.side, side.added, side.replaced, side.removed, side.unchanged) == ("remote", 2, 0, 0, 0)
    shutil.rmtree(env.local)
    with pytest.raises(RestoreRefused, match="missing or not mounted"):
        await env.service.restore_preview(target_id, snapshot, RestoreScope.BOTH)
    assert not env.local.exists() and not env.remote.exists()  # a preview changes nothing


async def test_restore_preview_of_an_unreadable_folder_is_an_error_not_an_empty_folder(env):
    """Counting every file as "added" for a folder that could not be read would hide what a restore replaces."""
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a")
    snapshot = await env.backup(target_id)
    write(env.remote, "sub/b.txt", "b")
    (env.remote / "sub").chmod(0)
    try:
        with pytest.raises(RcloneError):
            await env.service.restore_preview(target_id, snapshot, RestoreScope.REMOTE_ONLY)
    finally:
        (env.remote / "sub").chmod(0o700)


# ── The job's error as clients see it ────────────────────────────────


def job(status: str, code: str | None = None, message: str | None = None, direction: str = "backup") -> BackupJob:
    return BackupJob(status=status, error_code=code, error_message=message, direction=direction)


@pytest.mark.parametrize(("record", "expected"), [
    (job("running"), (None, None)),
    (job("completed"), (None, None)),
    (job("failed", None, INTERRUPTED), ("interrupted", INTERRUPTED)),
    (job("skipped", None, f"unreachable {SECRET}"), ("target_unreachable", None)),
    (job("failed", None, "rclone: disk full"), ("backup_failed", "The backup failed.")),
    (job("failed", None, "rclone: disk full", "restore"), ("restore_failed", "The restore failed.")),
    (job("failed", "from_a_newer_version", "rclone: disk full"), ("from_a_newer_version", "The run failed.")),
    (job("failed", "backup_refused", None), ("backup_refused", "The run failed.")),
    (job("failed", "auth_failed", f"token expired {SECRET}"), ("auth_failed", "Signing in to the remote failed")),
])
def test_clients_get_a_code_and_a_fixed_message(record, expected):
    """Old jobs without a code and codes from a newer version still answer something safe and stable."""
    code, message = public_job_error(record)
    assert code == expected[0]
    if expected[1] is None:
        assert message is None or "fake-Pass" not in message
    else:
        assert message is not None and message.startswith(expected[1])
        assert "disk full" not in message and "fake-Pass" not in message


@pytest.mark.parametrize(("exc", "code"), [
    (RestoreRefused("no"), "restore_refused"),
    (ValueError("no snapshot"), "snapshot_not_found"),
    (RcloneAuthError("token expired"), "auth_failed"),
    (RcloneError("disk full"), "restore_failed"),
    (OSError("disk full"), "restore_failed"),
])
def test_restore_failures_map_to_codes(exc, code):
    assert restore_error_code(exc) == code
