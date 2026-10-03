"""Engine hardening tests that run the real rclone binary against temp folders.

Same pattern as test_sync_safety_integration.py: the "remote" is an rclone
remote of type `local` in a temp rclone.conf, and the DB is a real
in-memory SQLite database. Skipped when rclone is not installed.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.schemas import FileAction, SelectiveSyncItem, SyncState
from backend.db.models import Base, FileChange, SyncError, SyncJob
from backend.exceptions import IntervalsNotResumableError, NoCachedDiffError, RcloneError
from backend.models.profile_config import ProfileConfig
from backend.services import sync_engine as sync_engine_module
from backend.services.rclone import SENTINEL_FILE, TRASH_DIR, RcloneService
from backend.services.sync_engine import SyncEngine

if shutil.which("rclone") is None and os.environ.get("OMNISYNC_REQUIRE_RCLONE") == "1":
    raise RuntimeError("OMNISYNC_REQUIRE_RCLONE=1 but rclone is not on PATH")
pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")

IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def files_under(root: Path) -> dict[str, str]:
    """Relative path -> content for every file under root, trash included."""
    return {
        str(p.relative_to(root)): p.read_text()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def write(root: Path, rel: str, content: str, mtime: float | None = None) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def mark_both(local: Path, remote: Path) -> None:
    write(local, SENTINEL_FILE, "marker")
    write(remote, SENTINEL_FILE, "marker")


@pytest.fixture
async def env(tmp_path: Path):
    """A profile whose remote is the real folder tmp/remote, plus a real DB."""
    local = tmp_path / "local"
    remote = tmp_path / "remote"
    local.mkdir()
    remote.mkdir()
    conf = tmp_path / "rclone.conf"
    conf.write_text("[testremote]\ntype = local\n")

    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db, expire_on_commit=False)

    def make_engine(local_dir: Path = local, remote_dir: str | None = None) -> SyncEngine:
        profile = ProfileConfig(
            profile_id=1, slug="docs", name="Docs",
            local_dir=str(local_dir), remote_dir=remote_dir or f"testremote:{remote}",
            max_retries=1,
        )
        return SyncEngine(profile, RcloneService(rclone_config_path=str(conf)), factory)

    yield make_engine, local, remote, factory
    await db.dispose()


# --- DS-15: strict parsing of rclone check ---


async def test_check_keeps_spaces_in_names_and_classifies(env):
    make_engine, local, remote, _ = env
    write(local, " lead.txt ", "l")
    write(remote, "remote only.txt", "r")
    write(local, "same.txt", "s")
    write(remote, "same.txt", "s")
    write(local, "differs.txt", "local")
    write(remote, "differs.txt", "remote!")
    engine = make_engine()

    result = await engine._rclone.check_diff(str(local), f"testremote:{remote}")

    assert result["error"] is None
    assert result["local_only"] == [" lead.txt "]
    assert result["remote_only"] == ["remote only.txt"]
    assert result["differ"] == ["differs.txt"]


@pytest.mark.skipif(IS_ROOT, reason="root can read chmod 000 files")
async def test_check_error_records_are_not_in_sync(env):
    make_engine, local, remote, _ = env
    write(local, "unreadable.txt", "abc")
    write(remote, "unreadable.txt", "abd")
    (local / "unreadable.txt").chmod(0)
    engine = make_engine()
    try:
        result = await engine._rclone.check_diff(str(local), f"testremote:{remote}")
    finally:
        (local / "unreadable.txt").chmod(0o644)

    assert result["error"] is not None and "unreadable.txt" in result["error"]
    assert not result["has_changes"]


async def test_check_of_unconfigured_remote_is_an_error(env):
    make_engine, local, remote, _ = env
    write(local, "a.txt", "a")
    engine = make_engine()

    # rclone exits 1 with empty output here: that must not read as "in sync"
    result = await engine._rclone.check_diff(str(local), "notconfigured:somewhere")

    assert result["error"] is not None and "exit 1" in result["error"]


@pytest.mark.skipif(IS_ROOT, reason="root can read chmod 000 folders")
async def test_check_with_unreadable_folder_is_an_error(env):
    make_engine, local, remote, _ = env
    write(local, "locked/f.txt", "x")
    (local / "locked").chmod(0)
    engine = make_engine()
    try:
        result = await engine._rclone.check_diff(str(local), f"testremote:{remote}")
    finally:
        (local / "locked").chmod(0o755)

    assert result["error"] is not None


# --- helpers for engine tests ---


async def last_job(factory) -> SyncJob:
    async with factory() as session:
        return (await session.execute(select(SyncJob).order_by(SyncJob.id.desc()))).scalars().first()


async def job_changes(factory, job_id: int) -> list[tuple[str, str, int | None]]:
    async with factory() as session:
        rows = (await session.execute(
            select(FileChange).where(FileChange.job_id == job_id).order_by(FileChange.file_path)
        )).scalars().all()
        return [(r.file_path, r.action, r.size_bytes) for r in rows]


async def job_errors(factory, job_id: int) -> list[str]:
    async with factory() as session:
        rows = (await session.execute(select(SyncError).where(SyncError.job_id == job_id))).scalars().all()
        return [r.message for r in rows]


class Event:
    def __init__(self, path: str) -> None:
        self.src_path = path
        self.event_type = "modified"


# --- DS-3: the startup check fails closed ---


async def test_failed_startup_check_pauses_intervals(env):
    make_engine, local, remote, _ = env
    write(local, "a.txt", "a")
    engine = make_engine(remote_dir="notconfigured:somewhere")

    await engine.start()
    try:
        assert engine._state.intervals_paused
        assert "Startup check could not compare" in (engine._state.last_error or "")
        job = engine._scheduler.get_job("periodic_pull")
        assert job is not None and job.next_run_time is None  # paused
    finally:
        await engine.stop()


async def test_startup_check_timeout_pauses_intervals(env, monkeypatch):
    make_engine, local, remote, _ = env
    write(local, "a.txt", "a")
    engine = make_engine()

    async def hanging_check(*args, **kwargs):
        raise TimeoutError("check took too long")

    monkeypatch.setattr(engine._rclone, "check_diff", hanging_check)
    await engine.start()
    try:
        assert engine._state.intervals_paused
        assert "too long" in (engine._state.last_error or "")
    finally:
        await engine.stop()


async def test_startup_check_hitting_its_time_limit_pauses_intervals(env, monkeypatch):
    make_engine, local, remote, _ = env
    mark_both(local, remote)
    write(local, "a.txt", "a")
    write(remote, "a.txt", "a")
    monkeypatch.setattr("backend.services.sync_engine.safety.STARTUP_CHECK_TIMEOUT", 0.001)
    engine = make_engine()

    await engine.start()
    try:
        assert engine._state.intervals_paused
        assert "timed out" in (engine._state.last_error or "")
    finally:
        await engine.stop()


@pytest.mark.parametrize("in_sync", [True, False])
async def test_save_during_startup_check_waits_for_the_decision(env, monkeypatch, in_sync):
    make_engine, local, remote, _ = env
    mark_both(local, remote)
    write(local, "a.txt", "same")
    write(remote, "a.txt", "same" if in_sync else "remote edit")
    engine = make_engine()
    real_check = engine._startup_check
    armed_during_check: list[bool] = []

    async def check_with_a_save():
        engine._on_file_change(Event(str(local / "a.txt")))  # the user saves mid-check
        armed_during_check.append(engine._debounce_timer is not None)
        await real_check()

    monkeypatch.setattr(engine, "_startup_check", check_with_a_save)
    await engine.start()
    try:
        assert armed_during_check == [False]  # no push before the decision
        if in_sync:
            assert not engine._state.intervals_paused
            assert engine._debounce_timer is not None  # pushed once it was safe
        else:
            assert engine._state.intervals_paused
            assert engine._debounce_timer is None
    finally:
        await engine.stop()


# --- DS-11: what each sync did is recorded ---


async def test_push_records_created_modified_and_deleted_files(env):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(local, "new.txt", "brand new")
    write(local, "sub/mod.txt", "v2 local")
    write(remote, "sub/mod.txt", "v1", mtime=1_600_000_000)
    write(remote, "gone.txt", "deleted locally")
    write(local, " spaced .txt", "s")
    engine = make_engine()

    job_id = await engine.push()

    job = await last_job(factory)
    assert job.id == job_id and job.status == "completed"
    assert job.files_changed == 4
    assert await job_changes(factory, job_id) == [
        (" spaced .txt", "created", 1),
        ("gone.txt", "deleted", None),
        ("new.txt", "created", 9),
        ("sub/mod.txt", "modified", 8),
    ]


async def test_pull_records_changes_on_the_local_side(env):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(remote, "from-remote.txt", "r")
    write(local, "old-local.txt", "l")
    engine = make_engine()

    job_id = await engine.pull()

    assert (await last_job(factory)).files_changed == 2
    assert await job_changes(factory, job_id) == [
        ("from-remote.txt", "created", 1), ("old-local.txt", "deleted", None),
    ]


async def test_jobs_of_real_syncs_are_read_back_through_the_api(env):
    """test-strategy R2: what a real push and pull wrote, as GET /jobs serves it."""
    from httpx import ASGITransport, AsyncClient

    from backend.db.database import get_session
    from backend.db.models import SyncProfile
    from backend.main import app
    from backend.tests.auth import AUTH_HEADERS

    make_engine, local, remote, factory = env
    now = datetime.now(timezone.utc)
    async with factory() as session:
        session.add(SyncProfile(id=1, slug="docs", name="Docs", local_dir=str(local),
                                remote_dir=f"testremote:{remote}", created_at=now, updated_at=now))
        await session.commit()
    mark_both(local, remote)
    write(local, "new.txt", "brand new")
    write(remote, "from-remote.txt", "r")
    engine = make_engine()
    push_id = await engine.push()  # uploads new.txt, deletes from-remote.txt on the remote
    write(remote, "later.txt", "later")
    pull_id = await engine.pull()

    async def _session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH_HEADERS) as client:
            jobs = (await client.get("/jobs", params={"profile": "docs"})).json()
            push_files = (await client.get(f"/jobs/{push_id}/files")).json()
            pull_files = (await client.get(f"/jobs/{pull_id}/files")).json()
    finally:
        app.dependency_overrides.clear()

    assert [(j["id"], j["direction"], j["status"], j["files_changed"], j["profile_slug"]) for j in jobs] == [
        (pull_id, "pull", "completed", 1, "docs"),
        (push_id, "push", "completed", 2, "docs"),
    ]
    assert all(j["finished_at"] is not None and j["errors"] == 0 for j in jobs)
    assert sorted((f["file_path"], f["action"], f["side"]) for f in push_files) == [
        ("from-remote.txt", "deleted", "remote"), ("new.txt", "created", "remote"),
    ]
    assert [(f["file_path"], f["action"], f["side"], f["size_bytes"]) for f in pull_files] == [
        ("later.txt", "created", "local", 5),
    ]


async def test_huge_syncs_store_a_capped_number_of_rows_but_count_all(env):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    for i in range(30):
        write(local, f"f{i:02}.txt", "x")
    engine = make_engine()
    engine.max_recorded_changes = 10

    job_id = await engine.push()

    assert (await last_job(factory)).files_changed == 30
    assert len(await job_changes(factory, job_id)) == 10


async def test_failed_sync_records_what_it_did_before_failing(env, monkeypatch):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(local, "kept.txt", "x")
    for i in range(6):
        write(remote, f"old{i}.txt", "precious")
    monkeypatch.setattr("backend.services.sync_engine.safety.DEFAULT_MAX_DELETE", 2)
    engine = make_engine()

    job_id = await engine.push()

    job = await last_job(factory)
    assert job.status == "failed"
    trashed = [f for f in files_under(remote) if f.startswith(TRASH_DIR)]
    deleted = [c for c in await job_changes(factory, job_id) if c[1] == "deleted"]
    assert len(deleted) == len(trashed) and job.files_changed >= len(trashed)


# --- DS-12: failed listings fail the diff; actions re-check first ---


async def test_diff_fails_when_a_listing_fails_and_drops_the_old_diff(env, monkeypatch):
    make_engine, local, remote, _ = env
    mark_both(local, remote)
    write(local, "a.txt", "a")
    engine = make_engine()
    assert (await engine.enhanced_diff()).error is None
    assert engine._state.cached_diff is not None
    real_lsjson = engine._rclone.lsjson

    async def failing_remote_listing(path, rclone_filter=None):
        if path.startswith("testremote:"):
            raise RcloneError("rclone failed (exit 3): error listing: directory not found")
        return await real_lsjson(path, rclone_filter=rclone_filter)

    monkeypatch.setattr(engine._rclone, "lsjson", failing_remote_listing)
    diff = await engine.enhanced_diff()

    assert diff.error is not None and "Could not list" in diff.error
    assert diff.files == []
    assert engine._state.cached_diff is None
    with pytest.raises(NoCachedDiffError):
        await engine.selective_sync([SelectiveSyncItem(path="a.txt", action=FileAction.PUSH)])


async def test_push_refuses_to_overwrite_a_file_that_appeared_since_the_diff(env):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(local, "a.txt", "local")
    engine = make_engine()
    await engine.enhanced_diff()
    write(remote, "a.txt", "someone uploaded this after the diff")

    result = await engine.selective_sync([SelectiveSyncItem(path="a.txt", action=FileAction.PUSH)])

    assert result.failed == 1 and "changed since the diff" in result.errors[0].error
    assert (remote / "a.txt").read_text() == "someone uploaded this after the diff"
    assert (await last_job(factory)).status == "failed"


async def test_push_refuses_when_the_remote_version_changed_since_the_diff(env):
    make_engine, local, remote, _ = env
    mark_both(local, remote)
    write(local, "doc.txt", "local edit", mtime=1_700_000_000)
    write(remote, "doc.txt", "old", mtime=1_600_000_000)
    engine = make_engine()
    diff = await engine.enhanced_diff()
    assert [f.path for f in diff.files] == ["doc.txt"]
    write(remote, "doc.txt", "a newer remote edit")

    result = await engine.selective_sync([SelectiveSyncItem(path="doc.txt", action=FileAction.PUSH)])

    assert result.failed == 1
    assert (remote / "doc.txt").read_text() == "a newer remote edit"


async def test_pull_of_a_file_deleted_since_the_diff_fails(env):
    make_engine, local, remote, _ = env
    mark_both(local, remote)
    write(remote, "b.txt", "remote")
    engine = make_engine()
    await engine.enhanced_diff()
    (remote / "b.txt").unlink()

    result = await engine.selective_sync([SelectiveSyncItem(path="b.txt", action=FileAction.PULL)])

    assert result.failed == 1 and "does not exist" in result.errors[0].error
    assert not (local / "b.txt").exists()


async def test_unchanged_file_is_pushed_and_the_replaced_version_kept(env):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(local, "doc.txt", "local edit", mtime=1_700_000_000)
    write(remote, "doc.txt", "old", mtime=1_600_000_000)
    engine = make_engine()
    await engine.enhanced_diff()

    result = await engine.selective_sync([SelectiveSyncItem(path="doc.txt", action=FileAction.PUSH)])

    assert result.failed == 0
    after = files_under(remote)
    assert after["doc.txt"] == "local edit"
    assert [v for k, v in after.items() if k.startswith(TRASH_DIR + "/")] == ["old"]
    job = await last_job(factory)
    assert job.files_changed == 1
    assert await job_changes(factory, job.id) == [("doc.txt", "modified", 10)]


# --- DS-13: stop stops rclone; the pause state machine ---


async def test_stop_terminates_rclone_and_keeps_the_engine_running(env, tmp_path):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(remote, "a.txt", "a")
    write(local, "a.txt", "a")
    (local / "big.bin").write_bytes(os.urandom(4 * 1024 * 1024))
    profile = ProfileConfig(
        profile_id=1, slug="docs", name="Docs", local_dir=str(local),
        remote_dir=f"testremote:{remote}", max_retries=1, rclone_args=["--bwlimit", "100k"],
    )
    engine = SyncEngine(profile, RcloneService(rclone_config_path=str(tmp_path / "rclone.conf")), factory)
    await engine.start()
    try:
        watcher, scheduler = engine._observer, engine._scheduler
        push = asyncio.create_task(engine.push())
        for _ in range(100):  # until rclone is transferring
            await asyncio.sleep(0.1)
            if engine._state.state == SyncState.PUSHING and any(remote.glob("big.bin*")):
                break
        assert engine.is_running_operation

        loop = asyncio.get_running_loop()
        started = loop.time()
        assert await engine.stop_current_sync() is True
        job_id = await asyncio.wait_for(push, timeout=20)
        assert loop.time() - started < 15  # 4 MB at 100 KiB/s would take 40 s

        job = await last_job(factory)
        assert job.id == job_id and job.status == "failed"
        assert await job_errors(factory, job_id) == [sync_engine_module.STOPPED_BY_USER]
        assert engine._state.state == SyncState.IDLE
        assert engine._state.last_error == sync_engine_module.STOPPED_BY_USER
        big = remote / "big.bin"
        assert not big.exists() or big.stat().st_size < 4 * 1024 * 1024
        # the engine keeps working: watcher and scheduler untouched
        assert engine._observer is watcher and engine._scheduler is scheduler
        assert engine._scheduler is not None and engine._scheduler.running
        assert await engine.stop_current_sync() is False
    finally:
        await engine.stop()


async def test_stopping_the_engine_ends_a_running_sync_cleanly(env, tmp_path):
    """Profile reload/disable/shutdown: rclone ends, the job is recorded, callers get the job id."""
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    (local / "big.bin").write_bytes(os.urandom(4 * 1024 * 1024))
    profile = ProfileConfig(
        profile_id=1, slug="docs", name="Docs", local_dir=str(local),
        remote_dir=f"testremote:{remote}", max_retries=1, rclone_args=["--bwlimit", "100k"],
    )
    engine = SyncEngine(profile, RcloneService(rclone_config_path=str(tmp_path / "rclone.conf")), factory)
    push = asyncio.create_task(engine.push())
    for _ in range(100):
        await asyncio.sleep(0.1)
        if engine._state.state == SyncState.PUSHING and any(remote.glob("big.bin*")):
            break

    await engine.stop()
    job_id = await asyncio.wait_for(push, timeout=20)  # no CancelledError for the caller

    job = await last_job(factory)
    assert job.id == job_id and job.status == "failed"
    assert await job_errors(factory, job_id) == [sync_engine_module.ENGINE_STOPPED]


async def test_skip_survives_a_new_diff_and_manual_flags_never_block_resume(env):
    make_engine, local, remote, _ = env
    mark_both(local, remote)
    write(local, "skipped.txt", "s")
    write(local, "flagged.txt", "f")
    engine = make_engine()

    await engine.enhanced_diff()
    assert engine._state.intervals_paused and engine._state.pending_changes == 2
    await engine.selective_sync([
        SelectiveSyncItem(path="skipped.txt", action=FileAction.SKIP),
        SelectiveSyncItem(path="flagged.txt", action=FileAction.MANUAL),
    ])
    assert engine._state.pending_changes == 0

    diff = await engine.enhanced_diff()  # e.g. after a page reload
    assert [f.path for f in diff.files] == ["flagged.txt"] and diff.files[0].manual_flag
    assert engine._state.pending_changes == 0
    assert "resumed" in (await engine.resume_intervals()).lower()


async def test_forced_sync_keeps_intervals_paused_while_a_conflict_is_unresolved(env):
    make_engine, local, remote, _ = env
    mark_both(local, remote)
    write(local, "new.txt", "n")
    write(local, "both.txt", "local edit", mtime=1_700_000_000)
    write(remote, "both.txt", "remote edit", mtime=1_700_000_000)  # same mtime: a conflict
    engine = make_engine()
    diff = await engine.enhanced_diff()
    assert {f.path: f.is_conflict for f in diff.files} == {"new.txt": False, "both.txt": True}
    assert engine._state.intervals_paused

    await engine.push()  # forced bulk sync

    assert (remote / "new.txt").exists()
    assert (remote / "both.txt").read_text() == "remote edit"  # conflict left alone
    assert engine._state.intervals_paused
    assert [f.path for f in engine._state.cached_diff.files] == ["both.txt"]
    assert engine._state.pending_changes == 1
    with pytest.raises(IntervalsNotResumableError):
        await engine.resume_intervals()

    await engine.selective_sync([SelectiveSyncItem(path="both.txt", action=FileAction.KEEP_BOTH)])
    assert engine._state.pending_changes == 0
    await engine.push()  # nothing unresolved any more: intervals resume
    assert not engine._state.intervals_paused


# --- Trash retention ---


def stamp(days_ago: float) -> str:
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return when.strftime(sync_engine_module.TRASH_STAMP_FORMAT)


def fill_trash(root: Path, old: str, recent: str) -> None:
    write(root, f"{TRASH_DIR}/{old}/x.txt", "old")
    write(root, f"{TRASH_DIR}/{recent}/y.txt", "recent")
    write(root, f"{TRASH_DIR}/pre-restore/{old}/z.txt", "restore safety copy")
    write(root, f"{TRASH_DIR}/2020-01-01/mine.txt", "not a sync timestamp")
    write(root, f"{TRASH_DIR}/loose.txt", "loose file")


def assert_only_old_removed(root: Path, old: str, recent: str) -> None:
    trash = root / TRASH_DIR
    assert not (trash / old).exists()
    assert (trash / recent / "y.txt").read_text() == "recent"
    assert (trash / "pre-restore" / old / "z.txt").exists()
    assert (trash / "2020-01-01" / "mine.txt").exists()
    assert (trash / "loose.txt").exists()


async def test_remote_trash_older_than_retention_is_purged_after_a_push(env):
    make_engine, local, remote, _ = env
    mark_both(local, remote)
    write(local, "a.txt", "1")
    old, recent = stamp(31), stamp(29)
    fill_trash(remote, old, recent)
    engine = make_engine()

    await engine.push()

    assert_only_old_removed(remote, old, recent)


async def test_local_trash_is_pruned_after_a_pull_without_following_symlinks(env, tmp_path, monkeypatch):
    make_engine, local, remote, _ = env
    mark_both(local, remote)
    write(remote, "a.txt", "1")
    monkeypatch.setattr("backend.services.sync_engine.trash.TRASH_RETENTION_DAYS", 7)
    old, recent = stamp(8), stamp(6)
    fill_trash(local, old, recent)
    outside = tmp_path / "elsewhere"
    write(outside, "precious.txt", "never delete")
    linked = stamp(100)
    (local / TRASH_DIR / linked).symlink_to(outside, target_is_directory=True)
    engine = make_engine()

    await engine.pull()

    assert_only_old_removed(local, old, recent)
    assert (outside / "precious.txt").read_text() == "never delete"
    assert (local / TRASH_DIR / linked).is_symlink()


async def test_trash_is_pruned_at_most_hourly_and_never_after_a_failed_sync(env):
    make_engine, local, remote, _ = env
    mark_both(local, remote)
    write(local, "a.txt", "1")
    engine = make_engine()
    await engine.push()

    old = stamp(40)
    write(remote, f"{TRASH_DIR}/{old}/x.txt", "old")
    await engine.push()  # within the hour: not pruned again
    assert (remote / TRASH_DIR / old).exists()

    engine._last_trash_prune.clear()
    (local / SENTINEL_FILE).unlink()  # the next push is refused
    await engine.push()
    assert (remote / TRASH_DIR / old).exists()

    assert await engine.prune_trash("remote") == [old]
    assert not (remote / TRASH_DIR / old).exists()


@pytest.mark.parametrize("days", [0, -1])
async def test_trash_days_zero_keeps_the_trash_forever(env, monkeypatch, days):
    """OMNISYNC_TRASH_DAYS=0 means never prune, not "prune everything"."""
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(local, "a.txt", "1")
    monkeypatch.setattr("backend.services.sync_engine.trash.TRASH_RETENTION_DAYS", days)
    ancient, today = stamp(3650), stamp(0)
    fill_trash(remote, ancient, today)
    fill_trash(local, ancient, today)
    engine = make_engine()

    await engine.push()

    assert (await last_job(factory)).status == "completed"
    for root in (local, remote):
        assert (root / TRASH_DIR / ancient / "x.txt").exists()
        assert (root / TRASH_DIR / today / "y.txt").exists()
    assert await engine.prune_trash("remote") == []
    assert await engine.prune_trash("local") == []
    assert (remote / TRASH_DIR / ancient).exists() and (local / TRASH_DIR / ancient).exists()
