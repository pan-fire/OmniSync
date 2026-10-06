"""Resolving a conflict never loses a version, also when it goes wrong.

The error and refusal paths of SyncEngine.resolve_conflict (mirror conflicts
a diff found, and two-way conflicts whose both versions bisync kept), run
against the real rclone binary as in test_conflict_resolution_integration.py:
the "remote" is an rclone remote of type `local` in a temp rclone.conf and
the database a real, migrated SQLite file. Each test checks the files on
both sides afterwards, and the job and conflict rows. Skipped when rclone
is not installed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from backend.api.routes import conflicts as conflict_routes
from backend.api.schemas import ChangeCategory, ConflictResolution, FileDiff
from backend.db import database
from backend.db.database import init_database
from backend.db.models import Conflict, FileChange, SyncError, SyncJob, SyncProfile
from backend.exceptions import ConflictResolutionError, RcloneError
from backend.main import app
from backend.models.profile_config import ProfileConfig
from backend.services.rclone import SENTINEL_FILE, TRASH_DIR, RcloneService
from backend.services.sync_engine import SyncEngine
from backend.services.sync_engine.common import ENGINE_STOPPED, STOPPED_BY_USER
from backend.tests.auth import AUTH_HEADERS

if shutil.which("rclone") is None and os.environ.get("OMNISYNC_REQUIRE_RCLONE") == "1":
    raise RuntimeError("OMNISYNC_REQUIRE_RCLONE=1 but rclone is not on PATH")
pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")

needs_permissions = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores read-only folders",
)

# File names that trip up naive path handling: spaces, non-ASCII, a leading
# '-' (an rclone flag if passed bare), and a name near the 255-byte limit.
ODD_NAMES = [
    "with space/my notes.md",
    "ünïcödé/Résumé 日本語.txt",
    "-dash/-rf.md",
    "l" * 240 + ".md",
]


def files_under(root: Path) -> dict[str, str]:
    """Relative path -> content for every file under root, trash included."""
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


def user_files(root: Path) -> dict[str, str]:
    """Files under root outside the trash, without the sync marker."""
    return {k: v for k, v in files_under(root).items()
            if k != SENTINEL_FILE and not k.startswith(TRASH_DIR + "/")}


def trash(root: Path) -> dict[str, str]:
    """Trashed files by their path inside the timestamp folder."""
    return {k.split("/", 2)[2]: v for k, v in files_under(root).items() if k.startswith(TRASH_DIR + "/")}


def write(root: Path, rel: str, content: str, mtime: float | None = None) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


@contextmanager
def read_only(folder: Path) -> Iterator[None]:
    """Nothing can be created in or removed from ``folder`` (or its subfolders) meanwhile."""
    dirs = [folder, *(p for p in folder.rglob("*") if p.is_dir())]
    for d in dirs:
        d.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        yield
    finally:
        for d in dirs:
            d.chmod(stat.S_IRWXU)


@dataclass
class Env:
    engine: SyncEngine
    local: Path
    remote: Path
    factory: object
    client: AsyncClient
    profile_id: int

    async def conflicts(self) -> list[Conflict]:
        async with self.factory() as session:
            return list((await session.execute(select(Conflict).order_by(Conflict.id))).scalars().all())

    async def conflict(self, conflict_id: int) -> Conflict:
        async with self.factory() as session:
            return await session.get(Conflict, conflict_id)

    async def jobs(self) -> list[SyncJob]:
        async with self.factory() as session:
            return list((await session.execute(select(SyncJob).order_by(SyncJob.id))).scalars().all())

    async def errors(self, job_id: int) -> list[str]:
        async with self.factory() as session:
            rows = (await session.execute(select(SyncError).where(SyncError.job_id == job_id))).scalars().all()
            return [r.message for r in rows]

    async def changes(self, job_id: int) -> set[tuple[str, str, str | None]]:
        async with self.factory() as session:
            rows = (await session.execute(select(FileChange).where(FileChange.job_id == job_id))).scalars().all()
            return {(r.file_path, r.action, r.side) for r in rows}

    async def seed(self, **fields) -> int:
        async with self.factory() as session:
            row = Conflict(profile_id=self.profile_id, resolved=False, **fields)
            session.add(row)
            await session.commit()
            return row.id

    async def diff_with_conflict(self, path: str = "plan.md", local: str = "local edit",
                                 remote: str = "remote edit") -> Conflict:
        """Both sides changed `path` since the last sync; a diff records it."""
        write(self.local, path, local)
        write(self.remote, path, remote)
        self.engine._state.last_sync = datetime.now(timezone.utc) - timedelta(hours=1)
        diff = await self.engine.enhanced_diff()
        assert diff.error is None, diff.error
        assert path in [f.path for f in diff.files if f.is_conflict]
        (conflict,) = [c for c in await self.conflicts() if c.file_path == path and not c.resolved]
        return conflict

    async def two_way_conflict(self, path: str = "plan.md", local_as: str = "plan.local-conflict1.md",
                               remote_as: str = "plan.md") -> int:
        """What a two-way run leaves: both versions on both sides, and a row naming them."""
        for side in (self.local, self.remote):
            write(side, local_as, "local version")
            write(side, remote_as, "remote version")
        return await self.seed(file_path=path, local_kept_as=local_as, remote_kept_as=remote_as)


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
    await init_database(str(tmp_path / "conflicts.db"))
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
    conflict_routes.set_manager(SimpleNamespace(  # type: ignore[arg-type]
        get_engine_by_id=lambda pid: engine if pid == profile_id else None,
    ))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH_HEADERS) as client:
        yield Env(engine, local, remote, factory, client, profile_id)

    conflict_routes.set_manager(None)
    await database._engine.dispose()
    database._engine, database._async_session_factory = saved


async def resolve(env: Env, conflict_id: int, resolution: str):
    return await env.client.post(f"/conflicts/{conflict_id}/resolve", json={"resolution": resolution})


def failing_factory():
    """A session factory whose database is gone."""
    def factory():
        raise RuntimeError("database is locked")
    return factory


# --- mirror conflicts: refusals change nothing ---


async def test_dismiss_through_the_engine_is_refused(env):
    """dismiss changes no file; the engine refuses it rather than guessing a side."""
    conflict = await env.diff_with_conflict()

    with pytest.raises(ValueError, match="dismiss"):
        await env.engine.resolve_conflict(conflict.id, ConflictResolution.DISMISS)

    assert (await env.conflict(conflict.id)).resolved is False
    assert await env.jobs() == []


async def test_an_already_resolved_conflict_is_refused_by_the_engine(env):
    """A second resolve (e.g. two tabs) must not copy over the result of the first."""
    conflict = await env.diff_with_conflict()
    await env.engine.resolve_conflict(conflict.id, ConflictResolution.KEEP_LOCAL)
    write(env.local, "plan.md", "edited after resolving")

    with pytest.raises(ConflictResolutionError, match="already resolved"):
        await env.engine.resolve_conflict(conflict.id, ConflictResolution.KEEP_LOCAL)

    assert (env.remote / "plan.md").read_text() == "local edit"
    assert len(await env.jobs()) == 1


@pytest.mark.parametrize("resolution", ["keep_local", "keep_remote", "keep_both"])
async def test_a_missing_local_folder_is_refused(env, resolution):
    """An unmounted drive looks like an empty folder: nothing may be copied either way."""
    conflict = await env.diff_with_conflict()
    remote_before = files_under(env.remote)
    env.local.rename(env.local.with_name("unmounted"))

    resp = await resolve(env, conflict.id, resolution)

    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "conflict_resolution_refused"
    assert "missing or not mounted" in resp.json()["detail"]
    assert not env.local.exists()
    assert files_under(env.remote) == remote_before
    assert await env.jobs() == []
    assert (await env.conflict(conflict.id)).resolved is False


async def test_keep_remote_over_a_local_file_changed_since_the_diff_is_refused(env):
    """The local edit made after the diff was never seen by the user: it is not overwritten."""
    conflict = await env.diff_with_conflict()
    write(env.local, "plan.md", "edited again locally", mtime=time.time() + 30)

    resp = await resolve(env, conflict.id, "keep_remote")

    assert resp.status_code == 409
    assert "local file changed since the conflict was found" in resp.json()["detail"]
    assert (env.local / "plan.md").read_text() == "edited again locally"
    assert not (env.local / TRASH_DIR).exists()
    assert await env.jobs() == []


@pytest.mark.parametrize(("gone", "resolution"), [
    ("local", "keep_local"), ("local", "keep_both"), ("remote", "keep_both"),
])
async def test_a_version_that_vanished_since_the_diff_is_refused(env, gone, resolution):
    """The chosen version no longer exists: copying it would copy nothing (or delete)."""
    conflict = await env.diff_with_conflict()
    ((env.local if gone == "local" else env.remote) / "plan.md").unlink()
    before = files_under(env.local), files_under(env.remote)

    resp = await resolve(env, conflict.id, resolution)

    assert resp.status_code == 409
    assert f"The {gone} file no longer exists" in resp.json()["detail"]
    assert (files_under(env.local), files_under(env.remote)) == before
    assert await env.jobs() == []


async def test_keep_local_when_the_remote_file_is_gone_restores_it(env):
    """The replaced side vanished meanwhile: nothing unseen is lost, the kept version is copied."""
    conflict = await env.diff_with_conflict()
    (env.remote / "plan.md").unlink()

    resp = await resolve(env, conflict.id, "keep_local")

    assert resp.status_code == 200, resp.text
    assert (env.remote / "plan.md").read_text() == "local edit"
    assert trash(env.remote) == {}


# --- mirror conflicts: rclone failing part-way ---


@needs_permissions
async def test_an_rclone_failure_leaves_both_versions_and_fails_the_job(env, caplog):
    """The remote cannot be written: both versions stay, the job says failed, the API says 502."""
    conflict = await env.diff_with_conflict()

    with read_only(env.remote):
        resp = await resolve(env, conflict.id, "keep_local")

    assert resp.status_code == 502, resp.text
    body = resp.json()
    assert body["code"] == "rclone_failed"
    # rclone's own text (paths, config) is only in the log.
    assert str(env.remote) not in body["detail"]
    assert "failed in rclone" in caplog.text
    assert (env.local / "plan.md").read_text() == "local edit"
    assert (env.remote / "plan.md").read_text() == "remote edit"
    (job,) = await env.jobs()
    assert (job.status, job.errors, job.direction) == ("failed", 1, "selective")
    assert len(await env.errors(job.id)) == 1
    assert (await env.conflict(conflict.id)).resolved is False


async def test_a_copy_that_did_not_arrive_fails_the_job(env, monkeypatch):
    """rclone exits 0 but the file is not on the other side: not reported as resolved."""
    conflict = await env.diff_with_conflict()

    async def nothing_arrived(root, paths):
        return set()
    monkeypatch.setattr(env.engine._rclone, "existing_paths", nothing_arrived)

    with pytest.raises(RcloneError, match="did not arrive"):
        await env.engine.resolve_conflict(conflict.id, ConflictResolution.KEEP_REMOTE)

    (job,) = await env.jobs()
    assert job.status == "failed"
    assert await env.errors(job.id) == ["The file did not arrive on the other side."]
    assert (await env.conflict(conflict.id)).resolved is False
    # The replaced local version went to the trash, not lost.
    assert trash(env.local) == {"plan.md": "local edit"}


# --- mirror conflicts: stopped while resolving ---


async def hang_copy(env: Env, monkeypatch) -> asyncio.Event:
    """The next copy hangs (a stalled transfer) until cancelled; the event says it started."""
    started = asyncio.Event()

    async def hung(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(env.engine._rclone, "copy_files", hung)
    return started


async def test_stopping_a_resolution_records_the_job_and_changes_nothing(env, monkeypatch):
    """Stop by the user: the job is failed with the reason, the conflict stays open."""
    conflict = await env.diff_with_conflict()
    started = await hang_copy(env, monkeypatch)

    task = asyncio.create_task(env.engine.resolve_conflict(conflict.id, ConflictResolution.KEEP_LOCAL))
    await asyncio.wait_for(started.wait(), timeout=30)
    assert await env.engine.stop_current_sync() is True

    with pytest.raises(ConflictResolutionError, match=STOPPED_BY_USER):
        await task
    (job,) = await env.jobs()
    assert job.status == "failed"
    assert await env.errors(job.id) == [STOPPED_BY_USER]
    assert (env.remote / "plan.md").read_text() == "remote edit"
    assert (await env.conflict(conflict.id)).resolved is False
    assert env.engine.is_running_operation is False


async def test_cancelling_the_caller_propagates_and_still_records_the_job(env, monkeypatch):
    """A shutdown cancels the caller itself: the cancellation goes on, the job is closed."""
    conflict = await env.diff_with_conflict()
    started = await hang_copy(env, monkeypatch)

    task = asyncio.create_task(env.engine.resolve_conflict(conflict.id, ConflictResolution.KEEP_REMOTE))
    await asyncio.wait_for(started.wait(), timeout=30)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    (job,) = await env.jobs()
    assert job.status == "failed"
    assert await env.errors(job.id) == [ENGINE_STOPPED]
    assert (env.local / "plan.md").read_text() == "local edit"


async def test_a_stop_is_not_masked_when_recording_the_job_fails(env, monkeypatch, caplog):
    """The database failing while recording a stop must not turn the stop into a crash."""
    conflict = await env.diff_with_conflict()
    started = await hang_copy(env, monkeypatch)

    async def broken(*args, **kwargs):
        raise RuntimeError("disk full")
    monkeypatch.setattr(env.engine, "_record_error", broken)

    task = asyncio.create_task(env.engine.resolve_conflict(conflict.id, ConflictResolution.KEEP_LOCAL))
    await asyncio.wait_for(started.wait(), timeout=30)
    await env.engine.stop_current_sync()

    with pytest.raises(ConflictResolutionError, match=STOPPED_BY_USER):
        await task
    assert "Could not record stopped job" in caplog.text
    assert (env.remote / "plan.md").read_text() == "remote edit"


# --- mirror conflicts: odd file names ---


@pytest.mark.parametrize("name", ODD_NAMES, ids=["space", "unicode", "dash", "long"])
@pytest.mark.parametrize("resolution", ["keep_local", "keep_remote"])
async def test_odd_file_names_are_resolved_exactly(env, name, resolution):
    """Only the named file changes, and the replaced version is in the trash under its name."""
    write(env.local, "bystander.md", "untouched")
    conflict = await env.diff_with_conflict(path=name)

    resp = await resolve(env, conflict.id, resolution)

    assert resp.status_code == 200, resp.text
    kept, replaced_side = (("local edit", env.remote) if resolution == "keep_local" else ("remote edit", env.local))
    assert (env.local / name).read_text() == kept
    assert (env.remote / name).read_text() == kept
    assert trash(replaced_side) == {name: "remote edit" if resolution == "keep_local" else "local edit"}
    assert (env.local / "bystander.md").read_text() == "untouched"
    assert not (env.remote / "bystander.md").exists()


async def test_keep_both_of_a_name_too_long_for_the_copy_fails_safely(env):
    """The conflict copy's name would exceed 255 bytes: rclone fails, both versions stay."""
    name = "n" * 245 + ".md"
    conflict = await env.diff_with_conflict(path=name)

    resp = await resolve(env, conflict.id, "keep_both")

    assert resp.status_code == 502
    assert user_files(env.local) == {name: "local edit"}
    assert user_files(env.remote) == {name: "remote edit"}
    (job,) = await env.jobs()
    assert job.status == "failed"
    assert (await env.conflict(conflict.id)).resolved is False


# --- recording conflicts from a diff ---


async def test_duplicate_open_rows_for_one_file_are_closed_as_dismissed(env):
    """One file, one open conflict: extra rows (e.g. from an old race) are closed on the next diff."""
    first = await env.diff_with_conflict()
    second = await env.seed(file_path="plan.md")

    await env.engine.enhanced_diff()

    rows = {r.id: r for r in await env.conflicts()}
    assert rows[first.id].resolved is False
    assert (rows[second].resolved, rows[second].resolution) == (True, "dismiss")


async def test_a_one_sided_difference_keeps_its_open_conflict(env):
    """A file now changed on one side only still differs: its open conflict is not silently closed."""
    write(env.local, "draft.md", "only local now")
    open_id = await env.seed(file_path="draft.md")
    gone_id = await env.seed(file_path="same-on-both.md")

    await env.engine.enhanced_diff()

    rows = {r.id: r for r in await env.conflicts()}
    assert rows[open_id].resolved is False
    assert (rows[gone_id].resolved, rows[gone_id].resolution) == (True, None)


async def test_recording_conflicts_survives_a_database_failure(env, caplog):
    """A diff must still answer when the conflict list cannot be written."""
    env.engine._db_session_factory = failing_factory()
    diff = [FileDiff(path="plan.md", category=ChangeCategory.MODIFIED_BOTH, is_conflict=True)]

    with caplog.at_level(logging.WARNING):
        await env.engine._record_conflicts(diff)
        await env.engine._close_conflicts({"plan.md": ConflictResolution.KEEP_LOCAL})

    assert caplog.text.count("Could not record conflicts for 'docs'") == 1
    assert "Could not close conflicts for 'docs'" in caplog.text


async def test_closing_no_conflicts_touches_nothing(env):
    """An action that settled no conflict does not open a database session at all."""
    env.engine._db_session_factory = failing_factory()
    await env.engine._close_conflicts({})


async def test_a_two_way_run_closes_the_diffs_row_and_records_its_names(env):
    """The run kept both versions: the diff's open row is settled as keep_both, a new row names the copies."""
    diff_row = await env.seed(file_path="plan.md")
    write(env.local, "plan.local-conflict1.md", "local version")
    async with env.factory() as session:
        job = SyncJob(direction="two_way", started_at=datetime.now(timezone.utc), status="running",
                      profile_id=env.profile_id)
        session.add(job)
        await session.commit()
        job_id = job.id
    found = {"plan.md": {"local": "plan.local-conflict1.md"}}

    assert await env.engine._record_two_way_conflicts(job_id, found) == 1
    assert found == {}  # recorded once
    assert await env.engine._record_two_way_conflicts(job_id, found) == 0

    old, new = await env.conflicts()
    assert (old.id, old.resolved, old.resolution) == (diff_row, True, "keep_both")
    assert (new.job_id, new.local_kept_as, new.remote_kept_as) == (job_id, "plan.local-conflict1.md", "plan.md")
    assert new.local_modified is not None
    assert new.remote_modified is None  # plan.md is not in the local folder: no time to read


async def test_recording_two_way_conflicts_survives_a_database_failure(env, caplog):
    """The run itself succeeded; a failed record is logged, the count still reported."""
    env.engine._db_session_factory = failing_factory()

    assert await env.engine._record_two_way_conflicts(1, {"a.md": {}, "b.md": {}}) == 2
    assert "Could not record conflicts for 'docs'" in caplog.text


class TestUnchangedSince:
    """Whether the replaced file is still as the conflict recorded it."""

    seen = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    def test_nothing_to_compare_counts_as_unchanged(self):
        assert SyncEngine._unchanged_since(None, self.seen)
        assert SyncEngine._unchanged_since({"ModTime": "2026-01-02T03:04:05Z"}, None)

    def test_naive_times_from_sqlite_are_utc(self):
        assert SyncEngine._unchanged_since({"ModTime": "2026-01-02T03:04:05Z"}, self.seen.replace(tzinfo=None))
        assert not SyncEngine._unchanged_since({"ModTime": "2026-01-02T03:04:06Z"}, self.seen)

    @pytest.mark.parametrize("entry", [{}, {"ModTime": "yesterday"}, {"ModTime": None}])
    def test_an_unreadable_time_counts_as_changed(self, entry):
        """Better refuse than overwrite a file whose state cannot be confirmed."""
        assert not SyncEngine._unchanged_since(entry, self.seen)


# --- two-way conflicts ---


async def test_two_way_keep_both_through_the_engine_only_closes_the_record(env):
    conflict_id = await env.two_way_conflict()
    before = files_under(env.local), files_under(env.remote)

    assert await env.engine.resolve_conflict(conflict_id, ConflictResolution.KEEP_BOTH) is None

    assert (files_under(env.local), files_under(env.remote)) == before
    assert (await env.conflict(conflict_id)).resolution == "keep_both"
    assert await env.jobs() == []


async def test_two_way_resolution_with_a_missing_local_folder_is_refused(env):
    conflict_id = await env.two_way_conflict()
    remote_before = files_under(env.remote)
    env.local.rename(env.local.with_name("unmounted"))

    with pytest.raises(ConflictResolutionError, match="missing or not mounted"):
        await env.engine.resolve_conflict(conflict_id, ConflictResolution.KEEP_LOCAL)

    assert files_under(env.remote) == remote_before
    assert await env.jobs() == []


@pytest.mark.parametrize("field", ["local_kept_as", "remote_kept_as", "file_path"])
@pytest.mark.parametrize("bad", ["../outside.md", "/../../outside.md", ".", "sub/../.."])
async def test_names_outside_the_local_folder_are_refused(env, field, bad):
    """A tampered or corrupt record must not move or trash anything outside the folder."""
    outside = env.local.parent / "outside.md"
    outside.write_text("not ours")
    names = {"file_path": "plan.md", "local_kept_as": "plan.local-conflict1.md", "remote_kept_as": "plan.md"}
    for side in (env.local, env.remote):
        write(side, "plan.local-conflict1.md", "local version")
        write(side, "plan.md", "remote version")
    names[field] = bad
    conflict_id = await env.seed(**names)
    before = files_under(env.local), files_under(env.remote)

    resp = await resolve(env, conflict_id, "keep_local")

    assert resp.status_code == 409
    assert "is not inside the local folder" in resp.json()["detail"]
    assert outside.read_text() == "not ours"
    assert (files_under(env.local), files_under(env.remote)) == before
    assert await env.jobs() == []


async def test_a_symlink_out_of_the_local_folder_is_refused(env):
    """A kept name that resolves (through a link) outside the folder is refused like '..'."""
    elsewhere = env.local.parent / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "plan.md").write_text("someone else's")
    (env.local / "linked").symlink_to(elsewhere)
    conflict_id = await env.seed(file_path="plan.md", local_kept_as="linked/plan.md", remote_kept_as="plan.md")

    with pytest.raises(ConflictResolutionError, match="not inside the local folder"):
        await env.engine.resolve_conflict(conflict_id, ConflictResolution.KEEP_LOCAL)

    assert (elsewhere / "plan.md").read_text() == "someone else's"


@pytest.mark.parametrize(("gone_from", "resolution"), [
    ("local", "keep_local"), ("remote", "keep_local"), ("remote", "keep_remote"),
])
async def test_a_kept_version_no_longer_on_both_sides_is_refused(env, gone_from, resolution):
    """Keeping a version that one side lost would spread the loss: run a sync first."""
    conflict_id = await env.two_way_conflict()
    name = "plan.local-conflict1.md" if resolution == "keep_local" else "plan.md"
    ((env.local if gone_from == "local" else env.remote) / name).unlink()
    before = files_under(env.local), files_under(env.remote)

    resp = await resolve(env, conflict_id, resolution)

    assert resp.status_code == 409
    assert "no longer on both sides" in resp.json()["detail"]
    assert (files_under(env.local), files_under(env.remote)) == before
    assert await env.jobs() == []


async def test_keeping_a_version_when_the_original_name_was_reused(env):
    """Both versions renamed and a new file under the original name: all three are kept or trashed."""
    conflict_id = await env.two_way_conflict(path="plan.md", local_as="plan.local.md", remote_as="plan.remote.md")
    for side in (env.local, env.remote):
        write(side, "plan.md", "newer file")

    job_id = await env.engine.resolve_conflict(conflict_id, ConflictResolution.KEEP_LOCAL)

    for side in (env.local, env.remote):
        assert user_files(side) == {"plan.md": "local version"}
        assert trash(side) == {"plan.remote.md": "remote version", "plan.md": "newer file"}
    assert job_id is not None
    assert await env.changes(job_id) == {
        (name, action, side) for side in ("local", "remote")
        for name, action in (("plan.remote.md", "deleted"), ("plan.local.md", "deleted"), ("plan.md", "modified"))
    }
    assert (await env.conflict(conflict_id)).resolution == "keep_local"


async def test_keeping_the_version_already_under_the_original_name(env):
    """keep_remote where the remote version kept its name: only the other copy goes."""
    conflict_id = await env.two_way_conflict()

    job_id = await env.engine.resolve_conflict(conflict_id, ConflictResolution.KEEP_REMOTE)

    for side in (env.local, env.remote):
        assert user_files(side) == {"plan.md": "remote version"}
        assert trash(side) == {"plan.local-conflict1.md": "local version"}
    assert job_id is not None
    assert (await env.jobs())[-1].status == "completed"


async def test_a_copy_already_gone_from_both_sides_is_not_an_error(env):
    """The dropped version was deleted by hand already: the kept one still takes the name."""
    conflict_id = await env.two_way_conflict()
    for side in (env.local, env.remote):
        (side / "plan.md").unlink()

    await env.engine.resolve_conflict(conflict_id, ConflictResolution.KEEP_LOCAL)

    for side in (env.local, env.remote):
        assert user_files(side) == {"plan.md": "local version"}
        assert trash(side) == {}


@needs_permissions
async def test_a_remote_failure_leaves_the_local_folder_untouched(env):
    """The remote goes first; when it fails the local folder still has both versions."""
    conflict_id = await env.two_way_conflict()
    local_before = files_under(env.local)
    remote_before = files_under(env.remote)

    with read_only(env.remote):
        resp = await resolve(env, conflict_id, "keep_local")

    assert resp.status_code == 502
    assert resp.json()["code"] == "rclone_failed"
    assert files_under(env.local) == local_before
    assert files_under(env.remote) == remote_before
    (job,) = await env.jobs()
    assert (job.status, job.errors) == ("failed", 1)
    assert (await env.conflict(conflict_id)).resolved is False


@needs_permissions
async def test_a_local_failure_after_the_remote_keeps_every_version(env):
    """The local folder cannot be written: the job fails, and no version is lost on either side."""
    conflict_id = await env.two_way_conflict()
    local_before = files_under(env.local)

    with read_only(env.local):
        resp = await resolve(env, conflict_id, "keep_local")

    assert resp.status_code == 409
    assert resp.json()["code"] == "conflict_resolution_refused"
    assert "Could not keep the" in resp.json()["detail"]
    assert files_under(env.local) == local_before
    # The remote already holds the kept version; the dropped one is in its trash.
    assert user_files(env.remote) == {"plan.md": "local version"}
    assert trash(env.remote) == {"plan.md": "remote version"}
    (job,) = await env.jobs()
    assert job.status == "failed"
    assert (await env.conflict(conflict_id)).resolved is False


@pytest.mark.parametrize("name", ODD_NAMES[:3], ids=["space", "unicode", "dash"])
async def test_two_way_odd_file_names_are_resolved_exactly(env, name):
    stem, ext = os.path.splitext(name)
    local_as = f"{stem}.local-conflict1{ext}"
    conflict_id = await env.two_way_conflict(path=name, local_as=local_as, remote_as=name)

    resp = await resolve(env, conflict_id, "keep_local")

    assert resp.status_code == 200, resp.text
    for side in (env.local, env.remote):
        assert user_files(side) == {name: "local version"}
        assert trash(side) == {name: "remote version"}


async def test_closing_an_id_that_is_gone_is_harmless(env):
    """The record was deleted meanwhile (profile removed): closing it is a no-op."""
    await env.engine._close_conflict_ids({12345: ConflictResolution.KEEP_BOTH})
    assert await env.conflicts() == []
