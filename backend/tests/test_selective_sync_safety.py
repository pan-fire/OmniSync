"""Per-file actions never act on a stale or vanished file, and fail safe.

SyncEngine.selective_sync / launch_selective against the real rclone binary:
the "remote" is an rclone remote of type `local` in a temp rclone.conf, the
database a real, migrated SQLite file (as in
test_conflict_resolution_integration.py). Covered here: the refusals before
anything runs, files that changed or vanished since the diff, an unmounted
local folder, rclone failing, and a stop or crash part-way. Skipped when
rclone is not installed.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.api.schemas import FileAction, JobStatus, SelectiveSyncItem
from backend.db import database
from backend.db.database import init_database
from backend.db.models import ManualFlag, SyncError, SyncJob, SyncProfile
from backend.exceptions import NoCachedDiffError
from backend.models.profile_config import ProfileConfig
from backend.services.rclone import SENTINEL_FILE, TRASH_DIR, RcloneService
from backend.services.sync_engine import SyncEngine
from backend.services.sync_engine.common import ENGINE_STOPPED, STOPPED_BY_USER
from backend.services.sync_engine.selective import (
    COPY_FAILED,
    KEEP_BOTH_FAILED,
    SELECTIVE_CRASHED,
    SELECTIVE_RESULTS_KEPT,
)

if shutil.which("rclone") is None and os.environ.get("OMNISYNC_REQUIRE_RCLONE") == "1":
    raise RuntimeError("OMNISYNC_REQUIRE_RCLONE=1 but rclone is not on PATH")
pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")


def files_under(root: Path) -> dict[str, str]:
    """Relative path -> content for every file under root, trash included."""
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


def user_files(root: Path) -> dict[str, str]:
    """Files under root outside the trash, without the sync marker."""
    return {k: v for k, v in files_under(root).items()
            if k != SENTINEL_FILE and not k.startswith(TRASH_DIR + "/")}


def write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def items(action: FileAction, *paths: str) -> list[SelectiveSyncItem]:
    return [SelectiveSyncItem(path=p, action=action) for p in paths]


@dataclass
class Env:
    engine: SyncEngine
    local: Path
    remote: Path
    factory: object

    async def diff(self) -> None:
        result = await self.engine.enhanced_diff()
        assert result.error is None, result.error

    async def job(self, job_id: int) -> SyncJob:
        async with self.factory() as session:
            return await session.get(SyncJob, job_id)

    async def jobs(self) -> list[SyncJob]:
        async with self.factory() as session:
            return list((await session.execute(select(SyncJob).order_by(SyncJob.id))).scalars().all())

    async def errors(self, job_id: int) -> list[str]:
        async with self.factory() as session:
            rows = (await session.execute(select(SyncError).where(SyncError.job_id == job_id))).scalars().all()
            return [r.message for r in rows]


@pytest_asyncio.fixture
async def env(tmp_path: Path):
    local = tmp_path / "local"
    remote = tmp_path / "remote"
    local.mkdir()
    remote.mkdir()
    write(local, SENTINEL_FILE, "marker")
    write(remote, SENTINEL_FILE, "marker")
    conf = tmp_path / "rclone.conf"
    conf.write_text("[testremote]\ntype = local\n")

    saved = database._engine, database._async_session_factory
    database._engine = None
    await init_database(str(tmp_path / "selective.db"))
    factory = database._async_session_factory
    now = datetime.now(timezone.utc)
    async with factory() as session:
        profile = SyncProfile(
            slug="docs", name="Docs", local_dir=str(local), remote_dir=f"testremote:{remote}",
            debounce_seconds=5, pull_interval_minutes=5, rclone_filter="[]", rclone_args="[]",
            max_retries=1, enabled=True, created_at=now, updated_at=now,
        )
        session.add(profile)
        await session.commit()
        profile_id = profile.id

    config = ProfileConfig(profile_id=profile_id, slug="docs", name="Docs", local_dir=str(local),
                           remote_dir=f"testremote:{remote}", max_retries=1)
    engine = SyncEngine(config, RcloneService(rclone_config_path=str(conf)), factory)
    yield Env(engine, local, remote, factory)

    await database._engine.dispose()
    database._engine, database._async_session_factory = saved


# --- refused before anything runs ---


async def test_launching_without_a_diff_is_refused_and_leaves_the_lock_free(env):
    """Acting on files nobody reviewed is refused; no job, and the profile is not left busy."""
    write(env.local, "a.md", "a")

    with pytest.raises(NoCachedDiffError):
        await env.engine.launch_selective(items(FileAction.PUSH, "a.md"))

    assert await env.jobs() == []
    assert not env.engine.sync_lock.locked()
    assert user_files(env.remote) == {}


async def test_a_failed_job_record_releases_the_lock(env, monkeypatch):
    """The database refusing the job row must not leave the profile locked for good."""
    write(env.local, "a.md", "a")
    await env.diff()

    async def no_job():
        raise RuntimeError("database is locked")
    monkeypatch.setattr(env.engine, "_create_selective_job", no_job)

    with pytest.raises(RuntimeError, match="database is locked"):
        await env.engine.launch_selective(items(FileAction.PUSH, "a.md"))

    assert not env.engine.sync_lock.locked()
    assert user_files(env.remote) == {}


async def test_only_the_most_recent_results_are_kept(env):
    """Results kept for polling are bounded; the oldest is dropped first."""
    paths = [f"f{i:02}.md" for i in range(SELECTIVE_RESULTS_KEPT + 1)]
    for p in paths:
        write(env.local, p, p)
    await env.diff()

    job_ids = [(await env.engine.selective_sync(items(FileAction.SKIP, p))).job_id for p in paths]

    assert env.engine.selective_result(job_ids[0]) is None
    assert all(env.engine.selective_result(j) is not None for j in job_ids[1:])


# --- stale or vanished files ---


async def test_an_unmounted_local_folder_fails_every_copy_but_not_skip_or_manual(env):
    """A missing local folder is never created or filled; the remote is untouched."""
    for p in ("push.md", "both.md"):
        write(env.local, p, "local")
    write(env.local, "skip.md", "x")
    write(env.local, "manual.md", "x")
    write(env.remote, "pull.md", "remote")
    write(env.remote, "both.md", "remote")
    await env.diff()
    remote_before = files_under(env.remote)
    env.local.rename(env.local.with_name("unmounted"))

    result = await env.engine.selective_sync([
        *items(FileAction.PUSH, "push.md"), *items(FileAction.PULL, "pull.md"),
        *items(FileAction.KEEP_BOTH, "both.md"), *items(FileAction.SKIP, "skip.md"),
        *items(FileAction.MANUAL, "manual.md"),
    ])

    assert (result.status, result.succeeded, result.failed) == (JobStatus.FAILED, 2, 3)
    assert {e.path for e in result.errors} == {"push.md", "pull.md", "both.md"}
    assert all("missing or not mounted" in e.error for e in result.errors)
    assert not env.local.exists()
    assert files_under(env.remote) == remote_before
    assert (await env.job(result.job_id)).status == "failed"


async def test_a_push_over_a_remote_file_that_changed_since_the_diff_is_refused(env):
    """The remote edit made after the diff was never reviewed: it is not overwritten."""
    write(env.local, "plan.md", "local")
    write(env.remote, "plan.md", "remote")
    await env.diff()
    write(env.remote, "plan.md", "remote, edited after the diff")

    result = await env.engine.selective_sync(items(FileAction.PUSH, "plan.md"))

    assert (result.succeeded, result.failed) == (0, 1)
    assert "remote file changed since the diff" in result.errors[0].error
    assert (env.remote / "plan.md").read_text() == "remote, edited after the diff"
    assert not (env.remote / TRASH_DIR).exists()


async def test_a_push_whose_remote_counterpart_vanished_is_refused(env):
    """The diff saw a remote file to replace; that it is gone now is a change too."""
    write(env.local, "plan.md", "local")
    write(env.remote, "plan.md", "remote")
    await env.diff()
    (env.remote / "plan.md").unlink()

    result = await env.engine.selective_sync(items(FileAction.PUSH, "plan.md"))

    assert result.failed == 1
    assert "changed since the diff" in result.errors[0].error
    assert user_files(env.remote) == {}


async def test_a_pull_onto_a_local_file_created_since_the_diff_is_refused(env):
    """The diff saw no local file; one created since would be overwritten unseen."""
    write(env.remote, "new.md", "remote")
    await env.diff()
    write(env.local, "new.md", "created locally after the diff")

    result = await env.engine.selective_sync(items(FileAction.PULL, "new.md"))

    assert result.failed == 1
    assert "local file changed since the diff" in result.errors[0].error
    assert (env.local / "new.md").read_text() == "created locally after the diff"


async def test_keep_both_of_a_file_gone_on_one_side_is_refused(env):
    """Keeping both versions needs both; nothing is copied when one is gone."""
    for side in (env.local, env.remote):
        write(side, "a.md", f"{side.name} a")
        write(side, "b.md", f"{side.name} b")
    await env.diff()
    (env.local / "a.md").unlink()
    (env.remote / "b.md").unlink()

    result = await env.engine.selective_sync(items(FileAction.KEEP_BOTH, "a.md", "b.md"))

    assert result.failed == 2
    assert {e.path: e.error.split(".")[0] for e in result.errors} == {
        "a.md": "The local file no longer exists", "b.md": "The remote file no longer exists",
    }
    assert user_files(env.local) == {"b.md": "local b"}
    assert user_files(env.remote) == {"a.md": "remote a"}


async def test_keep_both_when_the_remote_cannot_be_listed_fails_safely(env):
    """The re-check itself fails (remote unreachable): nothing is copied, the error is generic."""
    for side in (env.local, env.remote):
        write(side, "a.md", side.name)
    await env.diff()
    env.engine._profile = dataclasses.replace(env.engine._profile, remote_dir="nosuchremote:somewhere")

    result = await env.engine.selective_sync(items(FileAction.KEEP_BOTH, "a.md"))

    assert result.failed == 1
    assert result.errors[0].error == KEEP_BOTH_FAILED
    assert user_files(env.local) == {"a.md": "local"}
    assert user_files(env.remote) == {"a.md": "remote"}


async def test_a_push_when_the_remote_cannot_be_reached_fails_without_detail(env):
    """rclone's error text (paths, config) stays in the log, not in the per-file error."""
    write(env.local, "a.md", "a")
    await env.diff()
    env.engine._profile = dataclasses.replace(env.engine._profile, remote_dir="nosuchremote:somewhere")

    result = await env.engine.selective_sync(items(FileAction.PUSH, "a.md"))

    assert (result.failed, result.errors[0].error) == (1, COPY_FAILED)
    assert "nosuchremote" not in result.errors[0].error


async def test_keep_both_whose_copy_name_is_too_long_keeps_both_versions(env):
    """rclone fails on the conflict copy: the item fails, neither version is lost."""
    name = "n" * 245 + ".md"
    write(env.local, name, "local")
    write(env.remote, name, "remote")
    await env.diff()

    result = await env.engine.selective_sync(items(FileAction.KEEP_BOTH, name))

    assert (result.failed, result.errors[0].error) == (1, KEEP_BOTH_FAILED)
    assert user_files(env.local) == {name: "local"}
    assert user_files(env.remote) == {name: "remote"}


async def test_flagging_a_file_manual_twice_keeps_one_flag(env):
    """Repeating 'manual' is idempotent: one flag row, the file stays out of pending."""
    write(env.local, "a.md", "a")
    await env.diff()

    for _ in range(2):
        result = await env.engine.selective_sync(items(FileAction.MANUAL, "a.md"))
        assert result.succeeded == 1

    async with env.factory() as session:
        flags = (await session.execute(select(ManualFlag))).scalars().all()
    assert [f.file_path for f in flags] == ["a.md"]
    assert env.engine._state.pending_changes == 0


class TestAsDiffed:
    """Whether a destination is still as the diff saw it, from its lsjson entry."""

    mod = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    @pytest.mark.parametrize("entry", [{"Size": 3}, {"Size": 3, "ModTime": "soon"}, {"Size": 3, "ModTime": None}])
    def test_an_unreadable_time_counts_as_changed(self, entry):
        assert not SyncEngine._as_diffed(entry, True, 3, self.mod)

    def test_without_recorded_metadata_existence_is_enough(self):
        assert SyncEngine._as_diffed({"Size": 9}, True, None, None)
        assert not SyncEngine._as_diffed({"Size": 9}, True, 3, None)


# --- stopped or crashed part-way ---


async def hang(env: Env, monkeypatch, method: str) -> asyncio.Event:
    """The next rclone ``method`` call hangs (a stalled transfer); the event says it started."""
    started = asyncio.Event()

    async def hung(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(env.engine._rclone, method, hung)
    return started


@pytest.mark.parametrize(("action", "method"), [
    (FileAction.PUSH, "copy_files"), (FileAction.KEEP_BOTH, "copyto"),
])
async def test_stopping_per_file_actions_records_what_was_left(env, monkeypatch, action, method):
    """A stop part-way: what already ran counts as done, the rest fails with the stop reason.

    Copies run before skips, keep-both after them.
    """
    for side in (env.local, env.remote):
        write(side, "a.md", side.name)
    write(env.local, "skip.md", "x")
    await env.diff()
    started = await hang(env, monkeypatch, method)

    task = asyncio.create_task(env.engine.selective_sync(
        [*items(FileAction.SKIP, "skip.md"), *items(action, "a.md")],
    ))
    await asyncio.wait_for(started.wait(), timeout=30)
    assert await env.engine.stop_current_sync() is True
    result = await task

    assert result.status == JobStatus.FAILED
    unfinished = ["skip.md", "a.md"] if action == FileAction.PUSH else ["a.md"]
    assert [(e.path, e.error) for e in result.errors] == [(p, STOPPED_BY_USER) for p in unfinished]
    assert (result.succeeded, result.failed) == (2 - len(unfinished), len(unfinished))
    assert env.engine.selective_result(result.job_id) == result
    assert await env.errors(result.job_id) == [STOPPED_BY_USER]
    assert (await env.job(result.job_id)).status == "failed"
    assert (env.remote / "a.md").read_text() == "remote"


async def test_cancelling_the_caller_of_per_file_actions_propagates(env, monkeypatch):
    """A shutdown is not swallowed: the job is closed, the cancellation goes on."""
    write(env.local, "a.md", "a")
    await env.diff()
    started = await hang(env, monkeypatch, "copy_files")

    task = asyncio.create_task(env.engine.selective_sync(items(FileAction.PUSH, "a.md")))
    await asyncio.wait_for(started.wait(), timeout=30)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    (job,) = await env.jobs()
    assert job.status == "failed"
    assert await env.errors(job.id) == [ENGINE_STOPPED]


async def test_a_stop_is_reported_even_when_its_job_cannot_be_recorded(env, monkeypatch, caplog):
    write(env.local, "a.md", "a")
    await env.diff()
    started = await hang(env, monkeypatch, "copy_files")

    async def broken(*args, **kwargs):
        raise RuntimeError("disk full")
    monkeypatch.setattr(env.engine, "_record_error", broken)

    task = asyncio.create_task(env.engine.selective_sync(items(FileAction.PUSH, "a.md")))
    await asyncio.wait_for(started.wait(), timeout=30)
    await env.engine.stop_current_sync()
    result = await task

    assert result.errors[0].error == STOPPED_BY_USER
    assert "Could not record stopped job" in caplog.text


async def test_a_launched_run_that_crashes_fails_its_job(env, monkeypatch, caplog):
    """An unexpected exception in the background run is recorded, never left 'running'."""
    write(env.local, "a.md", "a")
    await env.diff()

    async def crash(*args, **kwargs):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(env.engine, "_selective_sync", crash)

    running = await env.engine.launch_selective(items(FileAction.PUSH, "a.md"))
    assert running.status == JobStatus.RUNNING
    launch = env.engine._launch
    assert launch is not None and launch.task is not None
    await asyncio.wait_for(launch.task, timeout=30)

    result = env.engine.selective_result(running.job_id)
    assert result is not None
    assert (result.status, result.failed, result.errors[0].error) == (JobStatus.FAILED, 1, SELECTIVE_CRASHED)
    assert (await env.job(running.job_id)).status == "failed"
    assert await env.errors(running.job_id) == [SELECTIVE_CRASHED]
    assert not env.engine.sync_lock.locked()
    assert user_files(env.remote) == {}


async def test_a_crash_whose_job_cannot_be_recorded_is_logged(env, monkeypatch, caplog):
    write(env.local, "a.md", "a")
    await env.diff()

    async def crash(*args, **kwargs):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(env.engine, "_selective_sync", crash)
    monkeypatch.setattr(env.engine, "_record_error", crash)

    running = await env.engine.launch_selective(items(FileAction.PUSH, "a.md"))
    launch = env.engine._launch
    assert launch is not None and launch.task is not None
    await asyncio.wait_for(launch.task, timeout=30)

    result = env.engine.selective_result(running.job_id)
    assert result is not None and result.status == JobStatus.FAILED
    assert f"Could not record the failed job {running.job_id}" in caplog.text
    assert not env.engine.sync_lock.locked()
