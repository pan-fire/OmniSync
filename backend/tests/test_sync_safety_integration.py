"""Data-safety tests that run the real rclone binary against temp folders.

The "remote" is an rclone remote of type `local`, so every sync, copy,
--backup-dir and --max-delete below is executed by rclone itself, the way
it runs against a cloud remote. Skipped when rclone is not installed.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.schemas import (
    ChangeCategory,
    DiffResponse,
    DiffSummary,
    FileAction,
    FileDiff,
    JobDirection,
    SelectiveSyncItem,
    SyncJobResponse,
    SyncState,
)
from backend.db.models import Base, FileChange, SyncError, SyncJob, SyncProfile
from backend.models.profile_config import ProfileConfig
from backend.exceptions import IntervalsNotResumableError, RcloneError
from backend.services.rclone import SENTINEL_FILE, TRASH_DIR, RcloneService
from backend.services.sync_engine import SyncEngine
from backend.tests.real_rclone import make_env
from backend.tests.real_rclone import write as write_bytes

if shutil.which("rclone") is None and os.environ.get("OMNISYNC_REQUIRE_RCLONE") == "1":
    raise RuntimeError("OMNISYNC_REQUIRE_RCLONE=1 but rclone is not on PATH")
pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")


def files_under(root: Path) -> dict[str, str]:
    """Relative path -> content for every file under root, trash included."""
    return {
        str(p.relative_to(root)): p.read_text()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


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

    def make_engine(local_dir: Path = local) -> SyncEngine:
        profile = ProfileConfig(
            profile_id=1, slug="docs", name="Docs",
            local_dir=str(local_dir), remote_dir=f"testremote:{remote}",
            max_retries=1,
        )
        return SyncEngine(profile, RcloneService(rclone_config_path=str(conf)), factory)

    yield make_engine, local, remote, factory
    await db.dispose()


def mark_both(local: Path, remote: Path) -> None:
    write(local, SENTINEL_FILE, "marker")
    write(remote, SENTINEL_FILE, "marker")


async def last_job(factory) -> SyncJob:
    async with factory() as session:
        return (await session.execute(select(SyncJob).order_by(SyncJob.id.desc()))).scalars().first()


async def job_errors(factory, job_id: int) -> list[str]:
    async with factory() as session:
        rows = (await session.execute(select(SyncError).where(SyncError.job_id == job_id))).scalars().all()
        return [r.message for r in rows]


# --- Refuse syncs that would wipe the other side ---


async def test_push_from_empty_local_is_refused(env):
    make_engine, local, remote, factory = env
    for i in range(3):
        write(remote, f"doc{i}.txt", "keep me")
    engine = make_engine()

    job_id = await engine.push()

    assert files_under(remote) == {f"doc{i}.txt": "keep me" for i in range(3)}
    job = await last_job(factory)
    assert job.id == job_id and job.status == "failed"
    assert any("empty" in m for m in await job_errors(factory, job_id))
    assert engine._state.state == SyncState.ERROR
    assert "empty" in (engine._state.last_error or "")


async def test_pull_from_empty_remote_is_refused(env):
    make_engine, local, remote, factory = env
    write(local, "thesis.docx", "years of work")
    engine = make_engine()

    await engine.pull()

    assert files_under(local) == {"thesis.docx": "years of work"}
    assert (await last_job(factory)).status == "failed"


@pytest.mark.parametrize(("direction", "empty_side"), [("push", "local folder"), ("pull", "remote folder")])
async def test_empty_side_refusal_names_the_empty_folder(env, direction, empty_side):
    make_engine, local, remote, factory = env
    write(remote if direction == "push" else local, "keep.txt", "keep me")
    engine = make_engine()

    await getattr(engine, direction)()

    assert f"the {empty_side} is empty" in (engine._state.last_error or "")
    assert files_under(remote if direction == "push" else local) == {"keep.txt": "keep me"}


async def test_push_from_a_missing_local_folder_is_refused(env, tmp_path):
    make_engine, local, remote, factory = env
    write(remote, "keep.txt", "keep me")
    missing = tmp_path / "unmounted"
    engine = make_engine(local_dir=missing)

    job_id = await engine.push()

    assert not missing.exists()
    assert files_under(remote) == {"keep.txt": "keep me"}
    assert (await last_job(factory)).status == "failed"
    assert any("missing or not mounted" in m for m in await job_errors(factory, job_id))


@pytest.mark.parametrize("direction", ["push", "pull"])
async def test_sync_of_an_unreadable_local_folder_is_refused(env, direction):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(local, "mine.txt", "local")
    write(remote, "theirs.txt", "remote")
    engine = make_engine()
    local.chmod(0)
    try:
        if os.access(local, os.R_OK):
            pytest.skip("running as root: permissions do not apply")
        job_id = await getattr(engine, direction)()
    finally:
        local.chmod(0o755)

    assert files_under(remote) == {SENTINEL_FILE: "marker", "theirs.txt": "remote"}
    assert files_under(local) == {SENTINEL_FILE: "marker", "mine.txt": "local"}
    assert any("cannot be read" in m for m in await job_errors(factory, job_id))


@pytest.mark.parametrize("marked_side", ["local", "remote"])
async def test_marker_on_one_side_only_blocks_both_directions(env, marked_side):
    make_engine, local, remote, factory = env
    write(local, "a.txt", "local")
    write(remote, "b.txt", "remote")
    write(local if marked_side == "local" else remote, SENTINEL_FILE, "marker")
    engine = make_engine()

    await engine.push()
    await engine.pull()

    assert "a.txt" in files_under(local) and "b.txt" not in files_under(local)
    assert "b.txt" in files_under(remote) and "a.txt" not in files_under(remote)
    assert SENTINEL_FILE in (engine._state.last_error or "")


async def test_first_sync_writes_the_marker_on_both_sides(env):
    make_engine, local, remote, factory = env
    write(local, "a.txt", "hello")
    engine = make_engine()

    await engine.push()

    assert (await last_job(factory)).status == "completed"
    assert (local / SENTINEL_FILE).exists() and (remote / SENTINEL_FILE).exists()
    assert (remote / "a.txt").read_text() == "hello"


# --- A missing local folder is never created ---


async def test_missing_local_dir_is_not_created_and_profile_pauses(env, tmp_path):
    make_engine, _, _, _ = env
    missing = tmp_path / "unmounted-drive"
    engine = make_engine(local_dir=missing)

    await engine.start()
    try:
        assert not missing.exists()
        assert engine._state.state == SyncState.ERROR
        assert engine._state.intervals_paused
        assert "not mounted" in (engine._state.last_error or "")
    finally:
        await engine.stop()


# --- Delete bound and recoverable backups ---


async def test_max_delete_stops_a_mass_deletion(env, monkeypatch):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(local, "kept.txt", "x")
    for i in range(6):
        write(remote, f"old{i}.txt", "precious")
    monkeypatch.setattr("backend.services.sync_engine.safety.DEFAULT_MAX_DELETE", 2)
    engine = make_engine()

    job_id = await engine.push()

    remaining = [f for f in files_under(remote) if f.startswith("old") and "/" not in f]
    assert len(remaining) >= 4, remaining  # at most 2 of 6 deleted
    assert (await last_job(factory)).status == "failed"
    assert any("would delete more than" in m for m in await job_errors(factory, job_id))
    # the ones rclone did delete are recoverable
    trashed = [f for f in files_under(remote) if f.startswith(TRASH_DIR)]
    assert len(trashed) == 6 - len(remaining)


@pytest.mark.parametrize("own", [["--max-delete", "10"], ["--max-delete=10"]])
async def test_a_profiles_own_max_delete_replaces_the_default(env, monkeypatch, own):
    """The profile's --max-delete is the limit, in either spelling: OmniSync
    must not add its default after it (rclone would take the last one)."""
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(local, "kept.txt", "x")
    for i in range(5):
        write(remote, f"old{i}.txt", "gone on purpose")
    monkeypatch.setattr("backend.services.sync_engine.safety.DEFAULT_MAX_DELETE", 2)
    engine = make_engine()
    engine._profile = replace(engine._profile, rclone_args=own)

    job_id = await engine.push()

    assert (await last_job(factory)).status == "completed", await job_errors(factory, job_id)
    assert not [f for f in files_under(remote) if f.startswith("old")]
    assert len([f for f in files_under(remote) if f.startswith(TRASH_DIR)]) == 5


async def test_other_profile_flags_keep_the_default_delete_limit(env, monkeypatch):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(local, "kept.txt", "x")
    for i in range(6):
        write(remote, f"old{i}.txt", "precious")
    monkeypatch.setattr("backend.services.sync_engine.safety.DEFAULT_MAX_DELETE", 2)
    engine = make_engine()
    engine._profile = replace(engine._profile, rclone_args=["--transfers", "2"])

    job_id = await engine.push()

    assert (await last_job(factory)).status == "failed"
    assert any("would delete more than" in m for m in await job_errors(factory, job_id))
    assert len([f for f in files_under(remote) if f.startswith("old")]) >= 4


async def test_retry_counts_deletions_of_the_failed_attempt(env, monkeypatch, tmp_path):
    """The delete limit is per sync: a retry may not delete up to the limit again."""
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(local, "kept.txt", "x")
    for i in range(8):
        write(remote, f"old{i}.txt", "precious")
    monkeypatch.setattr("backend.services.sync_engine.safety.DEFAULT_MAX_DELETE", 5)
    engine = make_engine()
    engine._profile = replace(engine.profile, max_retries=2)
    monkeypatch.setattr("backend.services.sync_engine.common.calculate_backoff_delay", lambda *_: 0)

    real_sync = engine._rclone.sync
    only_four = tmp_path / "only-four.filter"
    only_four.write_text("".join(f"- /old{i}.txt\n" for i in range(4, 8)))
    calls: list[int | None] = []

    async def flaky_sync(*args, **kwargs):
        calls.append(kwargs["max_delete"])
        if len(calls) == 1:
            # The first attempt deletes 4 files (within the limit of 5),
            # then fails for another reason, e.g. the connection dropped.
            await real_sync(*args, **{**kwargs, "exclude_filter_path": str(only_four)})
            raise RcloneError("connection reset by peer")
        return await real_sync(*args, **kwargs)

    monkeypatch.setattr(engine._rclone, "sync", flaky_sync)

    job_id = await engine.push()

    assert calls == [5, 1]
    remaining = [f for f in files_under(remote) if f.startswith("old") and "/" not in f]
    assert len(remaining) >= 3, remaining  # at most 5 of 8 deleted over both attempts
    assert (await last_job(factory)).status == "failed"
    assert any("would delete more than" in m for m in await job_errors(factory, job_id))


async def test_push_and_pull_skip_leftover_partial_files(env):
    """A killed run's <name>.<8 hex>.partial files are never carried over (nor deleted)."""
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(local, "a.txt", "a")
    write(local, "big.iso.0123abcd.partial", "half a download")
    write(remote, "b.txt.89abcdef.partial", "half an upload")
    engine = make_engine()

    await engine.push()
    assert (await last_job(factory)).status == "completed"
    assert files_under(remote) == {
        SENTINEL_FILE: "marker", "a.txt": "a", "b.txt.89abcdef.partial": "half an upload",
    }

    await engine.pull()
    assert (await last_job(factory)).status == "completed"
    assert files_under(local) == {
        SENTINEL_FILE: "marker", "a.txt": "a", "big.iso.0123abcd.partial": "half a download",
    }


async def test_overwritten_and_deleted_files_are_kept_in_trash(env):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(local, "report.txt", "v2")
    write(remote, "report.txt", "v1")
    write(remote, "removed-locally.txt", "still wanted?")
    engine = make_engine()

    await engine.push()

    assert (await last_job(factory)).status == "completed"
    after = files_under(remote)
    assert after["report.txt"] == "v2"
    assert "removed-locally.txt" not in after
    trash = {k.split("/", 2)[2]: v for k, v in after.items() if k.startswith(TRASH_DIR + "/")}
    assert trash == {"report.txt": "v1", "removed-locally.txt": "still wanted?"}


async def test_trash_is_never_synced_to_the_other_side(env):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(local, "a.txt", "1")
    # a recent trash folder (older ones are pruned by trash retention)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    write(remote, f"{TRASH_DIR}/{stamp}/secret-old.txt", "old")
    write(remote, "a.txt", "1")
    engine = make_engine()

    await engine.pull()
    await engine.push()

    assert not (local / TRASH_DIR).exists()
    assert (remote / TRASH_DIR / stamp / "secret-old.txt").exists()


# --- Per-file actions touch exactly the chosen file ---


def cache_diff(engine: SyncEngine, *paths: str, category=ChangeCategory.LOCAL_ONLY) -> None:
    files = [FileDiff(path=p, category=category, is_conflict=category == ChangeCategory.MODIFIED_BOTH) for p in paths]
    engine._state.cache_diff(DiffResponse(files=files, summary=DiffSummary(total=len(files))))


async def test_per_file_push_leaves_same_named_files_elsewhere_alone(env):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(local, "notes.txt", "new top-level notes")
    write(local, "sub/notes.txt", "local sub")
    write(remote, "sub/notes.txt", "remote sub")
    write(local, "report[1].txt", "bracketed")
    write(remote, "report1.txt", "different file")
    engine = make_engine()
    cache_diff(engine, "notes.txt", "report[1].txt")

    result = await engine.selective_sync([
        SelectiveSyncItem(path="notes.txt", action=FileAction.PUSH),
        SelectiveSyncItem(path="report[1].txt", action=FileAction.PUSH),
    ])

    assert result.failed == 0
    after = files_under(remote)
    assert after["notes.txt"] == "new top-level notes"
    assert after["sub/notes.txt"] == "remote sub"
    assert after["report[1].txt"] == "bracketed"
    assert after["report1.txt"] == "different file"


async def test_keep_both_keeps_both_versions_on_both_sides(env):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(local, "plan.md", "local edit")
    write(remote, "plan.md", "remote edit")
    engine = make_engine()
    cache_diff(engine, "plan.md", category=ChangeCategory.MODIFIED_BOTH)

    result = await engine.selective_sync([SelectiveSyncItem(path="plan.md", action=FileAction.KEEP_BOTH)])

    assert result.failed == 0
    for side in (local, remote):
        files = files_under(side)
        assert files["plan.md"] == "local edit"
        conflict = [v for k, v in files.items() if k.startswith("plan") and "conflict" in k]
        assert conflict == ["remote edit"], (side, files)


# --- Job history is saved, selective jobs are readable ---


async def test_multi_thread_transfers_are_in_the_job_history(tmp_path, monkeypatch):
    """rclone copies a large file in several streams to or from most cloud
    backends, and logs that as "Multi-thread Copied (...)". A `combine`
    remote over the folder gets the same treatment from rclone, and a low
    --multi-thread-cutoff makes a 2 MiB file large enough."""
    env, db = await make_env(tmp_path, monkeypatch)
    try:
        with open(env.conf, "a") as fh:
            fh.write(f"\n[streams]\ntype = combine\nupstreams = root={env.remote}\n")
        engine = env.engine(remote_dir="streams:root", rclone_args=["--multi-thread-cutoff", "1M"])
        local = env.local
        write_bytes(local, "video.bin", os.urandom(2 << 20))

        first = await env.completed(await engine.push())
        write_bytes(local, "video.bin", os.urandom(2 << 20))
        second = await env.completed(await engine.push())

        assert (first.files_changed, second.files_changed) == (1, 1)
        async with env.factory() as session:
            rows = (await session.execute(select(FileChange).order_by(FileChange.id))).scalars().all()
        assert [(r.job_id, r.file_path, r.action) for r in rows] == [
            (first.id, "video.bin", "created"), (second.id, "video.bin", "modified"),
        ]
        assert (env.remote / "video.bin").read_bytes() == (local / "video.bin").read_bytes()
    finally:
        await db.dispose()


async def test_selective_jobs_are_saved_and_readable(env):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(local, "a.txt", "1")
    engine = make_engine()
    cache_diff(engine, "a.txt")

    await engine.selective_sync([SelectiveSyncItem(path="a.txt", action=FileAction.PUSH)])

    job = await last_job(factory)
    assert job.direction == "selective" and job.status == "completed"
    response = SyncJobResponse(id=job.id, direction=JobDirection(job.direction), started_at=job.started_at,
                               status=job.status)
    assert response.direction == JobDirection.SELECTIVE


async def test_failed_per_file_action_marks_the_job_failed(env):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    engine = make_engine()
    cache_diff(engine, "does-not-exist.txt")

    result = await engine.selective_sync([SelectiveSyncItem(path="does-not-exist.txt", action=FileAction.PULL)])

    assert result.failed == 1
    assert (await last_job(factory)).status == "failed"


# --- One operation per profile at a time ---


async def test_syncs_of_one_profile_never_overlap(env):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(local, "a.txt", "1")
    engine = make_engine()

    await engine.sync_lock.acquire()  # e.g. a backup is running
    push = asyncio.create_task(engine.push())
    await asyncio.sleep(0.3)
    assert not push.done()
    engine.sync_lock.release()
    await asyncio.wait_for(push, timeout=30)
    assert (await last_job(factory)).status == "completed"


# --- automatic syncs and pauses ---


async def add_profile(factory) -> None:
    now = datetime.now(timezone.utc)
    async with factory() as session:
        session.add(SyncProfile(
            id=1, slug="docs", name="Docs", local_dir="/x", remote_dir="testremote:/x",
            created_at=now, updated_at=now,
        ))
        await session.commit()


async def stored_pause(factory) -> str | None:
    async with factory() as session:
        return (await session.get(SyncProfile, 1)).pause_reason


async def test_automatic_syncs_queued_behind_a_one_sided_restore_are_skipped(env):
    """A watcher push or a scheduled pull waiting for the lock must not spread or undo a restore."""
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    for side in (local, remote):
        write(side, "a.txt", "v1")
        write(side, "b.txt", "v1")
    engine = make_engine()

    # A local-only restore holds the sync lock while syncs queue up behind it.
    await engine.sync_lock.acquire()
    queued = [asyncio.create_task(engine._auto_sync()), asyncio.create_task(engine._scheduled_sync())]
    await asyncio.sleep(0.1)
    engine.pause_for("Snapshot X was restored to the local folder only.")
    write(local, "a.txt", "restored")
    (local / "b.txt").unlink()
    engine.sync_lock.release()

    assert await asyncio.gather(*queued) == [None, None]
    assert await last_job(factory) is None
    assert files_under(remote) == {SENTINEL_FILE: "marker", "a.txt": "v1", "b.txt": "v1"}
    assert files_under(local) == {SENTINEL_FILE: "marker", "a.txt": "restored"}
    # What the user starts still runs.
    await engine.push()
    assert (await last_job(factory)).status == "completed"
    assert files_under(remote)["a.txt"] == "restored"


async def test_automatic_run_is_not_retried_once_paused(env, monkeypatch):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    write(local, "a.txt", "new")
    engine = make_engine()
    engine._profile = replace(engine.profile, max_retries=3)
    monkeypatch.setattr("backend.services.sync_engine.common.calculate_backoff_delay", lambda *_: 0)
    calls = 0

    async def failing_sync(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        engine.pause_for("a restore is waiting")  # paused while the run waits to retry
        raise RcloneError("connection reset by peer")

    monkeypatch.setattr(engine._rclone, "sync", failing_sync)
    await engine._auto_sync()

    assert calls == 1
    assert (await last_job(factory)).status == "failed"


async def test_a_held_pause_survives_a_new_engine_until_resumed(env):
    """hold() (one-sided restore) is stored: a restart or a profile edit does not lift it."""
    make_engine, local, remote, factory = env
    await add_profile(factory)
    mark_both(local, remote)
    for side in (local, remote):
        write(side, "a.txt", "same")
    first = make_engine()
    await first.hold("Snapshot X was restored to the local folder only.")
    assert await stored_pause(factory) == "Snapshot X was restored to the local folder only."

    engine = make_engine()  # e.g. after a restart, or the profile was edited
    await engine.start()
    try:
        assert engine._state.intervals_paused
        assert engine._state.last_error == "Snapshot X was restored to the local folder only."
        assert await engine._scheduled_sync() is None
        assert await last_job(factory) is None

        await engine.resume_intervals()
        assert await stored_pause(factory) is None
    finally:
        await engine.stop()

    engine = make_engine()
    await engine.start()
    try:
        assert not engine._state.intervals_paused
    finally:
        await engine.stop()


async def test_a_successful_sync_clears_a_held_pause(env):
    make_engine, local, remote, factory = env
    await add_profile(factory)
    mark_both(local, remote)
    write(local, "a.txt", "restored")
    engine = make_engine()
    await engine.hold("Snapshot X was restored to the local folder only.")

    await engine.push()  # the user reviewed it and spreads the restore

    assert (await last_job(factory)).status == "completed"
    assert not engine._state.intervals_paused
    assert await stored_pause(factory) is None


async def test_mirror_edits_made_while_paused_are_not_reverted_after_resume(env):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    for side in (local, remote):
        write(side, "notes.txt", "v1")
    engine = make_engine()
    await engine.start()
    try:
        engine.pause_for("paused for a review")
        write(local, "notes.txt", "v2 - edited while paused")
        engine._on_file_change(SimpleNamespace(src_path=str(local / "notes.txt"), event_type="modified"))

        # Resuming would let the next interval pull overwrite the edit: refused.
        with pytest.raises(IntervalsNotResumableError, match="changed locally while syncing was paused"):
            await engine.resume_intervals()
        assert engine._state.intervals_paused and engine._state.pending_changes == 1
        assert await engine._scheduled_sync() is None
        assert files_under(local)["notes.txt"] == "v2 - edited while paused"

        # The user pushes it: the profile resumes, nothing was lost.
        await engine.push()
        assert files_under(remote)["notes.txt"] == "v2 - edited while paused"
        assert not engine._state.intervals_paused
    finally:
        await engine.stop()


async def test_mirror_resume_after_paused_edits_that_left_no_difference(env):
    make_engine, local, remote, factory = env
    mark_both(local, remote)
    for side in (local, remote):
        write(side, "notes.txt", "v1")
    engine = make_engine()
    await engine.start()
    try:
        engine.pause_for("paused for a review")
        write(local, "notes.txt", "v1")  # saved, but unchanged
        engine._on_file_change(SimpleNamespace(src_path=str(local / "notes.txt"), event_type="modified"))

        assert "resumed" in await engine.resume_intervals()
        assert not engine._state.intervals_paused
    finally:
        await engine.stop()


# --- Long transfers are never killed by a wall-clock limit ---


@pytest.mark.parametrize(("method", "args"), [
    ("sync_with_backup_dir", ("/src", "r:dst", "r:versions/1")),
    ("copy_files", ("/src", "r:dst", ["a.txt"])),
    ("copyto", ("/src/a.txt", "r:dst/a.txt")),
    ("delete_path", ("r:versions/old",)),
    ("get_dir_size", ("r:dst",)),
])
async def test_transfers_and_tree_walks_have_no_wall_clock_limit(method, args, monkeypatch):
    from backend.services.rclone import RcloneResult

    seen: list[dict] = []

    async def fake_run(self, cmd_args, **kwargs):
        seen.append(kwargs)
        return RcloneResult(stdout='{"bytes": 0}', stderr="", return_code=0, elapsed_seconds=0)

    monkeypatch.setattr(RcloneService, "_run", fake_run)
    await getattr(RcloneService(rclone_config_path="/tmp/x.conf"), method)(*args)
    assert seen and seen[0].get("no_timeout") is True
