"""Encrypted targets, verification, snapshot browsing, single-file restores and restore previews.

Real rclone on temp folders, like test_backup_restore_integration.py (whose
Env this reuses): remotes of type `local` in a temp rclone.conf and an
in-memory SQLite database. Skipped when rclone is not installed.
"""

from __future__ import annotations

import asyncio

import logging
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.routes import backups as backup_routes
from backend.api.schemas import BackupMode, BackupTargetType, RestoreScope
from backend.db.models import Base, BackupJob, BackupJobStatus, BackupTarget
from backend.main import app
from backend.services.backup_service import archive as backup_module
from backend.services.backup_service import (
    SnapshotFile,
    browse_snapshot,
    crypt_root,
    path_selector,
)
from backend.services.notification_events import NotificationEventType
from backend.services.rclone import SENTINEL_FILE, process_env, redact_secrets
from backend.tests.auth import AUTH_HEADERS
from backend.tests.test_backup_restore_integration import Env, files_under, safety_copies, write

if shutil.which("rclone") is None and os.environ.get("OMNISYNC_REQUIRE_RCLONE") == "1":
    raise RuntimeError("OMNISYNC_REQUIRE_RCLONE=1 but rclone is not on PATH")
needs_rclone = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")

PASSPHRASE = "correct horse battery staple"


@pytest.fixture
async def env(tmp_path: Path):
    # A file, not :memory: (one connection shared by every session): API
    # tests poll a run that writes in the background.
    db = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'omnisync.db'}")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield Env(tmp_path, async_sessionmaker(db, expire_on_commit=False))
    await db.dispose()


async def configure(env: Env, target_id: int, *, passphrase: str | None = None, verify: bool = False) -> None:
    async with env.factory() as session:
        target = await session.get(BackupTarget, target_id)
        assert target is not None
        if passphrase:
            target.encryption_password = await env.service.obscure_passphrase(passphrase)
        target.verify_after_backup = verify
        await session.commit()


async def load(env: Env, target_id: int) -> BackupTarget:
    async with env.factory() as session:
        target = await session.get(BackupTarget, target_id)
        assert target is not None
        return target


async def last_job(env: Env, target_id: int) -> BackupJob:
    async with env.factory() as session:
        return (await session.execute(
            select(BackupJob).where(BackupJob.target_id == target_id).order_by(BackupJob.id.desc()).limit(1)
        )).scalar_one()


def raw_tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def events(env: Env) -> list:
    return [c.args[0] for c in env.service._dispatcher.dispatch.await_args_list]


# ── Encryption ───────────────────────────────────────────────────────


def test_crypt_root_is_a_remote_defined_only_in_the_environment():
    assert crypt_root(5, "gdrive:Back,ups/it's", "OBSC") == "omnisync_backup_crypt_5:"
    assert crypt_root(15, "/other", "OTHER") == "omnisync_backup_crypt_15:"
    cmd = ["rclone", "--config", "x.conf", "lsjson", "--", "omnisync_backup_crypt_5:current"]
    proc_env = process_env(cmd)
    assert proc_env is not None
    assert {k: v for k, v in proc_env.items() if k.startswith("RCLONE_CONFIG_")} == {
        # the path as it is: no quoting needed for commas, colons and quotes
        "RCLONE_CONFIG_OMNISYNC_BACKUP_CRYPT_5_TYPE": "crypt",
        "RCLONE_CONFIG_OMNISYNC_BACKUP_CRYPT_5_REMOTE": "gdrive:Back,ups/it's",
        "RCLONE_CONFIG_OMNISYNC_BACKUP_CRYPT_5_PASSWORD": "OBSC",
    }
    # the rest of the environment is kept
    assert {k: v for k, v in proc_env.items() if not k.startswith("RCLONE_CONFIG_OMNISYNC")} == {
        k: v for k, v in os.environ.items() if not k.startswith("RCLONE_CONFIG_OMNISYNC")
    }
    # flags too (--backup-dir=...), and only the remotes named
    both = process_env(["move", "--backup-dir=omnisync_backup_crypt_15:v", "--", "/a", "omnisync_backup_crypt_5:b"])
    assert both is not None and both["RCLONE_CONFIG_OMNISYNC_BACKUP_CRYPT_15_PASSWORD"] == "OTHER"
    assert "RCLONE_CONFIG_OMNISYNC_BACKUP_CRYPT_15_PASSWORD" not in proc_env
    # other processes inherit the environment unchanged
    assert process_env(["rclone", "lsjson", "--", "xomnisync_backup_crypt_5:a", "omnisync_backup_crypt_9:"]) is None
    assert process_env(["rclone", "sync", "--", "/a", "gdrive:b"]) is None


def test_passwords_never_reach_logs_or_messages():
    line = "rclone lsjson -- :crypt,remote='/b/it''s',password='abc-DEF_12':current"
    assert redact_secrets(line) == "rclone lsjson -- :crypt,remote='/b/it''s',password=***:current"


@needs_rclone
async def test_encrypted_mirror_round_trip(env, caplog):
    """Nothing readable at the target; snapshots, exact restores and retention work through crypt."""
    caplog.set_level(logging.DEBUG, logger="backend")
    backups = env.backups / "it's, encrypted"  # quotes and commas in the path
    backups.mkdir()
    target_id = await env.add_target(str(backups))
    await configure(env, target_id, passphrase=PASSPHRASE, verify=True)

    write(env.local, "a.txt", "secret-a1")
    write(env.local, "sub/c.txt", "secret-c1")
    t1 = await env.backup(target_id)
    write(env.local, "a.txt", "secret-a2")
    (env.local / "sub" / "c.txt").unlink()
    t2 = await env.backup(target_id)

    raw = raw_tree(backups)
    assert raw, "the backup wrote nothing"
    names = " ".join(raw)
    assert not any(word in names for word in ("current", "versions", "manifests", "a.txt", "sub", t1, t2))
    assert not any(b"secret" in content for content in raw.values())

    target = await load(env, target_id)
    assert [s.snapshot_id for s in await env.service.list_snapshots(target)] == [t2, t1]
    job = await last_job(env, target_id)
    assert job.verify_status == "verified", job.verify_message
    assert "cryptcheck" in (job.verify_message or "")

    job = await env.service.restore(target_id, t1, RestoreScope.LOCAL_ONLY)
    assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
    assert files_under(env.local) == {"a.txt": "secret-a1", "sub/c.txt": "secret-c1"}
    job = await env.service.restore(target_id, t2, RestoreScope.LOCAL_ONLY)
    assert files_under(env.local) == {"a.txt": "secret-a2"}

    # retention deletes the old snapshot through crypt
    target.retention_days, target.keep_last = 1, 1
    old = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H-%M-%S")
    root = env.service.storage_root(target)
    await env.service._write_manifest(root, old, [{"path": "a.txt", "size": 9}])
    assert [s.snapshot_id for s in await env.service.list_snapshots(target)] == [t2, t1, old]
    assert await env.service.cleanup_old_snapshots(target) == 1
    assert [s.snapshot_id for s in await env.service.list_snapshots(target)] == [t2, t1]

    # the obscured passphrase never shows up in OmniSync's log
    logged = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("backend"))
    assert target.encryption_password and target.encryption_password not in logged
    assert PASSPHRASE not in logged
    assert f"omnisync_backup_crypt_{target_id}:" in logged


@needs_rclone
@pytest.mark.parametrize("mode", [BackupMode.MIRROR.value, BackupMode.ARCHIVE.value])
async def test_the_passphrase_is_never_on_a_command_line(env, monkeypatch, mode):
    """Every process spawned for an encrypted target: neither form of the passphrase in its argv.

    A command line is readable by every local user (ps, /proc/<pid>/cmdline);
    the crypt remote is passed through the process's environment instead.
    """
    spawned: list[tuple[list[str], dict[str, str] | None]] = []

    class RecordingPopen(subprocess.Popen):
        def __init__(self, args, *a, **kw):
            spawned.append(([str(args)] if isinstance(args, (str, bytes)) else [str(x) for x in args], kw.get("env")))
            super().__init__(args, *a, **kw)

    # asyncio's subprocesses are started through subprocess.Popen too
    monkeypatch.setattr(subprocess, "Popen", RecordingPopen)
    backups = env.backups / "it's, a:b"  # quotes, a comma and a colon in the path
    backups.mkdir()
    target_id = await env.add_target(str(backups), mode=mode)
    await configure(env, target_id, passphrase=PASSPHRASE, verify=True)
    write(env.local, "a.txt", "secret-a1")
    write(env.local, "sub/c.txt", "secret-c1")
    t1 = await env.backup(target_id)
    write(env.local, "a.txt", "secret-a2")
    await env.backup(target_id)
    assert (await last_job(env, target_id)).verify_status == "verified"

    target = await load(env, target_id)
    assert {f.path for f in await env.service.snapshot_files(target, t1)} >= {"a.txt", "sub/c.txt"}
    await env.service.restore_preview(target_id, t1, RestoreScope.LOCAL_ONLY)
    job = await env.service.restore_files(target_id, t1, ["a.txt"])
    assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
    job = await env.service.restore(target_id, t1, RestoreScope.LOCAL_ONLY)
    assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
    assert files_under(env.local) == {"a.txt": "secret-a1", "sub/c.txt": "secret-c1"}
    assert not any(b"secret" in content for content in raw_tree(backups).values())

    obscured = target.encryption_password
    assert obscured
    remote = f"omnisync_backup_crypt_{target_id}:"
    rclone_runs = [(argv, proc_env) for argv, proc_env in spawned if "rclone" in argv[0]]
    crypt_runs = [(argv, proc_env) for argv, proc_env in rclone_runs if any(remote in a for a in argv)]
    assert crypt_runs, "nothing went through the crypt remote"
    for argv, _proc_env in spawned:
        assert not any(obscured in a or PASSPHRASE in a for a in argv), argv
    variable = f"RCLONE_CONFIG_OMNISYNC_BACKUP_CRYPT_{target_id}_PASSWORD"
    for argv, proc_env in crypt_runs:
        assert proc_env is not None and proc_env[variable] == obscured, argv
    # the processes that do not use the target do not get its passphrase
    assert all(proc_env is None or variable not in proc_env for argv, proc_env in rclone_runs
               if not any(remote in a for a in argv))


@needs_rclone
async def test_encrypted_archive_round_trip_on_a_remote(env):
    target_id = await env.add_target(
        f"backupremote:{env.backups}", mode=BackupMode.ARCHIVE.value, target_type=BackupTargetType.REMOTE.value,
    )
    await configure(env, target_id, passphrase=PASSPHRASE, verify=True)
    write(env.local, "a.txt", "secret-a1")
    write(env.local, "sub/c.txt", "secret-c1")
    snapshot = await env.backup(target_id)

    raw = raw_tree(env.backups)
    assert len(raw) == 1 and "backup-" not in next(iter(raw))
    assert b"secret" not in next(iter(raw.values()))
    job = await last_job(env, target_id)
    assert job.verify_status == "verified" and "decryption tested" in (job.verify_message or "")

    target = await load(env, target_id)
    assert [s.snapshot_id for s in await env.service.list_snapshots(target)] == [snapshot]
    write(env.local, "a.txt", "changed")
    job = await env.service.restore(target_id, snapshot, RestoreScope.LOCAL_ONLY)
    assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
    assert files_under(env.local) == {"a.txt": "secret-a1", "sub/c.txt": "secret-c1"}


@needs_rclone
async def test_encrypted_local_archive_retention(env):
    target_id = await env.add_target(str(env.backups), mode=BackupMode.ARCHIVE.value)
    await configure(env, target_id, passphrase=PASSPHRASE)
    write(env.local, "a.txt", "a")
    await env.backup(target_id)
    newest = await env.backup(target_id)
    target = await load(env, target_id)
    target.keep_last, target.retention_days = 1, 1

    async def all_old(_target):
        snaps = await original(_target)
        for s in snaps[1:]:
            s.created_at -= timedelta(days=5)
        return snaps

    original = env.service._list_archive_snapshots
    env.service._list_archive_snapshots = all_old  # type: ignore[method-assign]
    assert await env.service.cleanup_old_snapshots(target) == 1
    env.service._list_archive_snapshots = original  # type: ignore[method-assign]
    assert [s.snapshot_id for s in await env.service.list_snapshots(target)] == [newest]
    assert len(raw_tree(env.backups)) == 1


@needs_rclone
async def test_a_wrong_passphrase_sees_no_snapshots(env):
    target_id = await env.add_target(str(env.backups))
    await configure(env, target_id, passphrase=PASSPHRASE)
    write(env.local, "a.txt", "a")
    await env.backup(target_id)
    target = await load(env, target_id)
    target.encryption_password = await env.service.obscure_passphrase("another passphrase")
    assert await env.service.list_snapshots(target) == []
    assert await env.service.location_has_data(target.target_path)


# ── Verification ─────────────────────────────────────────────────────


@needs_rclone
@pytest.mark.parametrize("encrypted", [False, True])
async def test_mirror_verification_catches_a_corrupted_backup(env, encrypted):
    target_id = await env.add_target(str(env.backups))
    await configure(env, target_id, passphrase=PASSPHRASE if encrypted else None, verify=True)
    write(env.local, "a.txt", "aaaa")
    write(env.local, "b.txt", "bbbb")
    await env.backup(target_id)
    assert (await last_job(env, target_id)).verify_status == "verified"

    rclone = env.service._rclone
    sync = rclone.sync_with_backup_dir

    async def sync_then_corrupt(source, dest, backup_dir, **kwargs):
        result = await sync(source, dest, backup_dir, **kwargs)
        # same size, other content, in the backup's copy of b.txt
        target = await load(env, target_id)
        tmp = env.backups.parent / "corrupt"
        tmp.mkdir(exist_ok=True)
        (tmp / "b.txt").write_text("XXXX")
        dest = f"{env.service.storage_root(target)}current/b.txt" if encrypted else f"{env.backups}/current/b.txt"
        await rclone.copyto(str(tmp / "b.txt"), dest)
        return result

    rclone.sync_with_backup_dir = sync_then_corrupt
    write(env.local, "a.txt", "aaa2")
    job = await env.service.run_backup(target_id)
    assert job.status == BackupJobStatus.COMPLETED.value
    assert job.verify_status == "failed"
    assert "1 file(s) differ" in (job.verify_message or "") and "b.txt" in (job.verify_message or "")
    failed = [e for e in events(env) if e.event_type == NotificationEventType.BACKUP_VERIFY_FAILED]
    assert len(failed) == 1 and job.snapshot_id in failed[0].body


@needs_rclone
async def test_archive_verification_reads_the_local_copy_back(env, monkeypatch):
    target_id = await env.add_target(str(env.backups), mode=BackupMode.ARCHIVE.value)
    await configure(env, target_id, verify=True)
    write(env.local, "a.txt", "a" * 5000)
    await env.backup(target_id)
    job = await last_job(env, target_id)
    assert job.verify_status == "verified" and "read back in full" in (job.verify_message or "")

    install = backup_module._install_archive

    def truncating_install(source, target_dir, filename):
        install(source, target_dir, filename)
        path = os.path.join(target_dir, filename)
        with open(path, "r+b") as fh:
            fh.truncate(os.path.getsize(path) // 2)

    monkeypatch.setattr(backup_module, "_install_archive", truncating_install)
    job = await env.service.run_backup(target_id)
    assert job.status == BackupJobStatus.COMPLETED.value
    assert job.verify_status == "failed" and "cannot be read back" in (job.verify_message or "")


@needs_rclone
async def test_no_verification_unless_asked(env):
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a")
    await env.backup(target_id)
    assert (await last_job(env, target_id)).verify_status is None


# ── Browsing ─────────────────────────────────────────────────────────


def test_browse_snapshot_lists_one_folder_or_searches():
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    files = [SnapshotFile(p, s, when) for p, s in (
        ("b.txt", 1), ("A.txt", 2), ("docs/x.md", 10), ("docs/deep/y.md", 20), ("zeta/z.bin", 5),
    )]
    top = browse_snapshot(files)
    assert [(e.name, e.is_dir, e.size, e.file_count) for e in top] == [
        ("docs", True, 30, 2), ("zeta", True, 5, 1), ("A.txt", False, 2, None), ("b.txt", False, 1, None),
    ]
    assert [e.path for e in browse_snapshot(files, "docs/")] == ["docs/deep", "docs/x.md"]
    assert [e.path for e in browse_snapshot(files, search="MD")] == ["docs/deep/y.md", "docs/x.md"]
    assert [e.path for e in browse_snapshot(files, "docs", search="y")] == ["docs/deep/y.md"]
    assert browse_snapshot(files, "nope") == []


def test_path_selector_takes_files_and_whole_folders():
    select = path_selector(["docs", "b.txt"])
    assert [p for p in ("b.txt", "bb.txt", "docs/x", "docs/a/b", "docsx/y", "a/b.txt") if select(p)] == [
        "b.txt", "docs/x", "docs/a/b",
    ]


@needs_rclone
@pytest.mark.parametrize(("mode", "encrypted"), [
    (BackupMode.MIRROR.value, False), (BackupMode.ARCHIVE.value, False), (BackupMode.ARCHIVE.value, True),
])
async def test_snapshot_files_of_every_kind(env, mode, encrypted):
    target_id = await env.add_target(str(env.backups), mode=mode)
    await configure(env, target_id, passphrase=PASSPHRASE if encrypted else None)
    write(env.local, "a.txt", "a1")
    write(env.local, "sub/c.txt", "c111")
    write(env.local, SENTINEL_FILE, "marker")
    t1 = await env.backup(target_id)
    write(env.local, "d.txt", "d")
    await env.backup(target_id)
    target = await load(env, target_id)

    files = await env.service.snapshot_files(target, t1)
    assert {f.path: f.size for f in files if f.path != SENTINEL_FILE} == {"a.txt": 2, "sub/c.txt": 4}
    assert all(f.mtime is not None for f in files)
    with pytest.raises(ValueError):
        await env.service.snapshot_files(target, "2001-01-01T00-00-00" if mode == "mirror"
                                         else "backup-2001-01-01T00-00-00.tar.gz")


@needs_rclone
async def test_archive_lists_are_streamed_once_and_cached(env, monkeypatch):
    target_id = await env.add_target(
        f"backupremote:{env.backups}", mode=BackupMode.ARCHIVE.value, target_type=BackupTargetType.REMOTE.value,
    )
    write(env.local, "a.txt", "a")
    snapshot = await env.backup(target_id)
    target = await load(env, target_id)
    reads = []
    stream = backup_module._read_archive_stream
    monkeypatch.setattr(backup_module, "_read_archive_stream", lambda src, fn: reads.append(src) or stream(src, fn))
    for _ in range(3):
        assert [f.path for f in await env.service.snapshot_files(target, snapshot)] == ["a.txt"]
    assert len(reads) == 1 and reads[0][-3:-1] == ["cat", "--"]  # streamed with rclone cat, not downloaded


# ── Single-file restores ─────────────────────────────────────────────


@needs_rclone
@pytest.mark.parametrize(("mode", "encrypted"), [
    (BackupMode.MIRROR.value, False), (BackupMode.MIRROR.value, True),
    (BackupMode.ARCHIVE.value, False), (BackupMode.ARCHIVE.value, True),
])
async def test_restore_selected_files_changes_only_them(env, mode, encrypted):
    target_id = await env.add_target(str(env.backups), mode=mode)
    await configure(env, target_id, passphrase=PASSPHRASE if encrypted else None)
    write(env.local, "a.txt", "a1")
    write(env.local, "keep.txt", "k1")
    write(env.local, "docs/x.md", "x1")
    write(env.local, "docs/deep/y.md", "y1")
    t1 = await env.backup(target_id)
    write(env.local, "a.txt", "a2")
    write(env.local, "keep.txt", "k2")
    shutil.rmtree(env.local / "docs")
    write(env.local, "new.txt", "n2")
    await env.backup(target_id)

    job = await env.service.restore_files(target_id, t1, ["a.txt", "docs"])

    assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
    assert files_under(env.local) == {
        "a.txt": "a1", "keep.txt": "k2", "new.txt": "n2", "docs/x.md": "x1", "docs/deep/y.md": "y1",
    }
    assert safety_copies(env.local) == [{"a.txt": "a2"}]
    # Into a mirror profile's folder: paused first, so the next pull cannot remove the restored files.
    assert [c.args[0] for c in env.engine.hold.await_args_list] == [
        f"3 file(s) of snapshot {t1} are being restored to the local folder. Automatic syncing is paused so "
        "the next pull does not remove them; review the diff, then push or resume.",
        f"3 file(s) of snapshot {t1} were restored to the local folder. Automatic syncing is paused so "
        "the next pull does not remove them; review the diff, then push or resume.",
    ]
    completed = [e for e in events(env) if e.event_type == NotificationEventType.BACKUP_RESTORE_COMPLETED]
    assert "3 selected file(s)" in completed[-1].body


@needs_rclone
@pytest.mark.parametrize("mode", [BackupMode.MIRROR.value, BackupMode.ARCHIVE.value])
async def test_restore_selected_files_into_another_folder(env, tmp_path, mode):
    target_id = await env.add_target(str(env.backups), mode=mode)
    write(env.local, "a.txt", "a1")
    write(env.local, "docs/x.md", "x1")
    t1 = await env.backup(target_id)
    write(env.local, "a.txt", "a2")
    elsewhere = tmp_path / "restored" / "here"
    (tmp_path / "restored").mkdir()

    job = await env.service.restore_files(target_id, t1, ["docs/x.md", "a.txt"], str(elsewhere))

    assert job.status == BackupJobStatus.COMPLETED.value, job.error_message
    assert files_under(elsewhere) == {"a.txt": "a1", "docs/x.md": "x1"}
    assert files_under(env.local) == {"a.txt": "a2", "docs/x.md": "x1"}  # untouched


@needs_rclone
async def test_restore_selected_files_refuses_an_empty_selection(env):
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a1")
    t1 = await env.backup(target_id)
    with pytest.raises(ValueError, match="None of the selected paths"):
        await env.service.restore_files(target_id, t1, ["missing.txt"])
    with pytest.raises(ValueError):
        await env.service.restore_files(target_id, "2001-01-01T00-00-00", ["a.txt"])


# ── Restore preview ──────────────────────────────────────────────────


@needs_rclone
@pytest.mark.parametrize(("mode", "encrypted"), [
    (BackupMode.MIRROR.value, False), (BackupMode.MIRROR.value, True), (BackupMode.ARCHIVE.value, False),
])
async def test_restore_preview_counts_what_the_restore_then_does(env, mode, encrypted):
    target_id = await env.add_target(str(env.backups), mode=mode)
    await configure(env, target_id, passphrase=PASSPHRASE if encrypted else None)
    write(env.local, "same.txt", "same")
    write(env.local, "changed.txt", "old")
    write(env.local, "gone.txt", "gone")
    write(env.local, SENTINEL_FILE, "marker")
    t1 = await env.backup(target_id)
    write(env.local, "changed.txt", "new content")
    (env.local / "gone.txt").unlink()
    write(env.local, "extra1.txt", "e")
    write(env.local, "sub/extra2.txt", "e")
    before = files_under(env.local)

    preview = await env.service.restore_preview(target_id, t1, RestoreScope.LOCAL_ONLY)

    assert files_under(env.local) == before  # a preview changes nothing
    [local] = preview.sides
    assert (local.side, local.added, local.replaced, local.removed, local.unchanged) == ("local", 1, 1, 2, 1)
    assert (local.added_examples, local.replaced_examples, local.removed_examples) == (
        ["gone.txt"], ["changed.txt"], ["extra1.txt", "sub/extra2.txt"],
    )

    await env.service.restore(target_id, t1, RestoreScope.LOCAL_ONLY)
    assert safety_copies(env.local) == [{"changed.txt": "new content", "extra1.txt": "e", "sub/extra2.txt": "e"}]
    again = await env.service.restore_preview(target_id, t1, RestoreScope.LOCAL_ONLY)
    assert (again.sides[0].added, again.sides[0].replaced, again.sides[0].removed) == (0, 0, 0)


@needs_rclone
async def test_restore_preview_of_both_sides(env):
    target_id = await env.add_target(str(env.backups))
    write(env.local, "a.txt", "a1")
    await env.backup(target_id)
    write(env.local, "a.txt", "a2")
    write(env.local, "b.txt", "b2")
    t2 = await env.backup(target_id)
    write(env.remote, "a.txt", "a1")

    preview = await env.service.restore_preview(target_id, t2, RestoreScope.BOTH)
    assert [(s.side, s.added, s.replaced, s.removed, s.unchanged) for s in preview.sides] == [
        ("local", 0, 0, 0, 2), ("remote", 1, 1, 0, 0),
    ]


# ── API ──────────────────────────────────────────────────────────────


@pytest.fixture
async def api(env, monkeypatch):
    monkeypatch.delenv("OMNISYNC_BROWSE_ROOTS", raising=False)
    backup_routes.set_backup_service(env.service)
    backup_routes.set_db_factory(env.factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH_HEADERS) as client:
        yield client
    await env.service.stop_jobs()
    backup_routes.set_backup_service(None)
    backup_routes.set_db_factory(None)


async def follow(api: AsyncClient, resp, target_id: int) -> dict:
    """A run the API started (202, running): poll GET .../jobs/{id} until it ends, and return it."""
    assert resp.status_code == 202, resp.text
    job = resp.json()
    for _ in range(2400):  # 2 minutes: rclone is slow on a loaded test machine
        if job["status"] != "running":
            return job
        await asyncio.sleep(0.05)
        job = (await api.get(f"/profiles/docs/backups/{target_id}/jobs/{job['id']}")).json()
    raise AssertionError(f"job {job} did not end")


async def create_profile(env: Env) -> None:
    await env.add_target(str(env.backups.parent / "unused"))  # creates profile 'docs'
    async with env.factory() as session:
        for t in (await session.execute(select(BackupTarget))).scalars():
            await session.delete(t)
        await session.commit()


@needs_rclone
async def test_api_encrypts_and_never_returns_the_passphrase(env, api):
    await create_profile(env)
    resp = await api.post("/profiles/docs/backups", json={
        "name": "Vault", "target_path": str(env.backups), "target_type": "local",
        "encryption_passphrase": PASSPHRASE,
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["encrypted"] is True and body["verify_after_backup"] is True
    assert PASSPHRASE not in resp.text and "password" not in resp.text
    target = await load(env, body["id"])
    assert target.encryption_password and target.encryption_password != PASSPHRASE

    listed = await api.get("/profiles/docs/backups")
    assert target.encryption_password not in listed.text and PASSPHRASE not in listed.text

    short = await api.post("/profiles/docs/backups", json={
        "name": "Weak", "target_path": str(env.backups.parent / "weak"), "target_type": "local",
        "encryption_passphrase": "short",
    })
    assert short.status_code == 422


@needs_rclone
async def test_api_refuses_a_passphrase_change_while_backups_exist(env, api):
    await create_profile(env)
    resp = await api.post("/profiles/docs/backups", json={
        "name": "Vault", "target_path": str(env.backups), "target_type": "local",
        "encryption_passphrase": PASSPHRASE,
    })
    target_id = resp.json()["id"]

    # still empty: changing it is fine
    assert (await api.put(f"/profiles/docs/backups/{target_id}",
                          json={"encryption_passphrase": "another passphrase"})).status_code == 200
    write(env.local, "a.txt", "a")
    assert (await follow(api, await api.post(f"/profiles/docs/backups/{target_id}/run"), target_id))["status"] == "completed"

    for change in ({"encryption_passphrase": "a third passphrase"}, {"encryption_passphrase": None},
                   {"encryption_passphrase": ""}):
        resp = await api.put(f"/profiles/docs/backups/{target_id}", json=change)
        assert resp.status_code == 409, change
        assert "unreadable" in resp.json()["detail"]
    # other edits still work, and a new empty location may take a new passphrase
    assert (await api.put(f"/profiles/docs/backups/{target_id}", json={"name": "Renamed"})).status_code == 200
    fresh = env.backups.parent / "fresh"
    fresh.mkdir()
    resp = await api.put(f"/profiles/docs/backups/{target_id}",
                         json={"target_path": str(fresh), "encryption_passphrase": None})
    assert resp.status_code == 200 and resp.json()["encrypted"] is False


@needs_rclone
async def test_api_browse_preview_and_restore_files(env, api, tmp_path, monkeypatch):
    await create_profile(env)
    target_id = (await api.post("/profiles/docs/backups", json={
        "name": "Mirror", "target_path": str(env.backups), "target_type": "local",
    })).json()["id"]
    for i in range(5):
        write(env.local, f"docs/f{i}.txt", str(i))
    write(env.local, "top.txt", "top")
    job = await follow(api, await api.post(f"/profiles/docs/backups/{target_id}/run"), target_id)
    assert job["verify_status"] == "verified", job
    snapshot = job["snapshot_id"]
    target = (await api.get(f"/profiles/docs/backups/{target_id}")).json()
    assert target["last_verify_status"] == "verified" and "6 file(s)" in target["last_verify_message"]

    files = f"/profiles/docs/backups/{target_id}/snapshots/{snapshot}/files"
    top = (await api.get(files)).json()
    assert [(e["name"], e["is_dir"], e["file_count"]) for e in top["entries"]] == [
        ("docs", True, 5), ("top.txt", False, None),
    ]
    page = (await api.get(files, params={"path": "docs", "offset": 1, "limit": 2})).json()
    assert (page["total"], [e["name"] for e in page["entries"]], page["snapshot_files"]) == (5, ["f1.txt", "f2.txt"], 6)
    assert (await api.get(files, params={"search": "F3"})).json()["entries"][0]["path"] == "docs/f3.txt"
    assert (await api.get(f"/profiles/docs/backups/{target_id}/snapshots/2001-01-01T00-00-00/files")).status_code == 404

    write(env.local, "top.txt", "changed")
    preview = await api.post(f"/profiles/docs/backups/{target_id}/restore/preview",
                             json={"snapshot_id": snapshot, "restore_scope": "local_only"})
    assert preview.status_code == 200
    assert preview.json()["sides"][0]["replaced"] == 1

    restore = f"/profiles/docs/backups/{target_id}/restore-files"
    resp = await api.post(restore, json={"snapshot_id": snapshot, "paths": ["top.txt"]})
    assert (await follow(api, resp, target_id))["status"] == "completed"
    assert (env.local / "top.txt").read_text() == "top"

    bad = [
        {"snapshot_id": snapshot, "paths": ["../etc/passwd"]},
        {"snapshot_id": snapshot, "paths": []},
        {"snapshot_id": snapshot, "paths": ["top.txt"], "target_dir": "relative/dir"},
        {"snapshot_id": snapshot, "paths": ["top.txt"], "target_dir": str(env.backups / "inside")},
    ]
    for body in bad:
        assert (await api.post(restore, json=body)).status_code == 422, body
    assert (await api.post(restore, json={"snapshot_id": snapshot, "paths": ["nope.txt"]})).status_code == 404

    monkeypatch.setenv("OMNISYNC_BROWSE_ROOTS", str(tmp_path / "allowed"))
    resp = await api.post(restore, json={"snapshot_id": snapshot, "paths": ["top.txt"],
                                         "target_dir": str(tmp_path / "elsewhere")})
    assert resp.status_code == 422
    (tmp_path / "allowed").mkdir()
    resp = await api.post(restore, json={"snapshot_id": snapshot, "paths": ["docs"],
                                         "target_dir": str(tmp_path / "allowed" / "out")})
    assert (await follow(api, resp, target_id))["status"] == "completed", resp.text
    assert len(files_under(tmp_path / "allowed" / "out")) == 5
