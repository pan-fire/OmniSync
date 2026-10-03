"""Conflicts are recorded by diffs and resolved on the real files.

Same pattern as test_sync_safety_integration.py: the "remote" is an rclone
remote of type `local` in a temp rclone.conf, so every copy and every
--backup-dir move below is done by rclone itself. The database is a real,
migrated SQLite file (foreign keys enforced), and the resolve route is
called through the app. Skipped when rclone is not installed.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from backend.api.routes import conflicts as conflict_routes
from backend.api.schemas import ConflictResolution, FileAction, SelectiveSyncItem
from backend.db import database
from backend.db.database import init_database
from backend.db.models import Conflict, FileChange, SyncJob, SyncProfile
from backend.exceptions import ConflictResolutionError
from backend.main import app
from backend.models.profile_config import ProfileConfig
from backend.services.rclone import SENTINEL_FILE, TRASH_DIR, RcloneService
from backend.services.sync_engine import SyncEngine
from backend.tests.auth import AUTH_HEADERS

if shutil.which("rclone") is None and os.environ.get("OMNISYNC_REQUIRE_RCLONE") == "1":
    raise RuntimeError("OMNISYNC_REQUIRE_RCLONE=1 but rclone is not on PATH")
pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")

HOUR_AGO = time.time() - 3600


def files_under(root: Path) -> dict[str, str]:
    """Relative path -> content for every file under root, trash included."""
    return {
        str(p.relative_to(root)): p.read_text()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def trash(root: Path) -> dict[str, str]:
    """Trashed files by their path inside the timestamp folder."""
    return {k.split("/", 2)[2]: v for k, v in files_under(root).items() if k.startswith(TRASH_DIR + "/")}


def write(root: Path, rel: str, content: str, mtime: float | None = None) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


@dataclass
class Env:
    engine: SyncEngine
    local: Path
    remote: Path
    factory: object
    client: AsyncClient
    profile_id: int

    async def conflicts(self, unresolved_only: bool = False) -> list[Conflict]:
        async with self.factory() as session:
            stmt = select(Conflict).order_by(Conflict.id)
            if unresolved_only:
                stmt = stmt.where(Conflict.resolved == False)  # noqa: E712
            return list((await session.execute(stmt)).scalars().all())

    async def last_job(self) -> SyncJob:
        async with self.factory() as session:
            return (await session.execute(select(SyncJob).order_by(SyncJob.id.desc()))).scalars().first()

    async def diff_with_conflict(self, path: str = "plan.md", local: str = "local edit",
                                 remote: str = "remote edit") -> Conflict:
        """Both sides changed `path` since the last sync; a diff records it."""
        write(self.local, path, local)
        write(self.remote, path, remote)
        self.engine._state.last_sync = datetime.now(timezone.utc) - timedelta(hours=1)
        diff = await self.engine.enhanced_diff()
        assert diff.error is None, diff.error
        assert [f.path for f in diff.files if f.is_conflict] == [path]
        (conflict,) = await self.conflicts(unresolved_only=True)
        return conflict


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


# --- recording ---


async def test_a_diff_records_one_conflict_per_path_and_refreshes_it(env):
    conflict = await env.diff_with_conflict()
    assert conflict.profile_id == env.profile_id
    assert conflict.job_id is None
    assert conflict.file_path == "plan.md"
    assert conflict.local_modified is not None and conflict.remote_modified is not None

    # A later diff refreshes the same row instead of adding one.
    write(env.remote, "plan.md", "remote edit 2", mtime=time.time() + 5)
    await env.engine.enhanced_diff()
    rows = await env.conflicts()
    assert [r.id for r in rows] == [conflict.id]
    assert rows[0].remote_modified != conflict.remote_modified

    listed = (await env.client.get("/conflicts")).json()
    assert [(c["id"], c["job_id"], c["profile_slug"], c["profile_name"]) for c in listed] == \
        [(conflict.id, None, "docs", "Docs")]
    assert listed[0]["remote_modified"].endswith("Z") or listed[0]["remote_modified"].endswith("+00:00")


async def test_a_conflict_whose_files_no_longer_differ_is_closed(env):
    conflict = await env.diff_with_conflict()
    write(env.remote, "plan.md", "local edit")
    os.utime(env.remote / "plan.md", (os.path.getmtime(env.local / "plan.md"),) * 2)

    diff = await env.engine.enhanced_diff()

    assert diff.files == []
    (row,) = await env.conflicts()
    assert row.id == conflict.id and row.resolved and row.resolution is None
    assert (await env.client.get("/conflicts")).json() == []


async def test_a_per_file_action_on_a_conflict_closes_it(env):
    await env.diff_with_conflict()

    result = await env.engine.selective_sync([SelectiveSyncItem(path="plan.md", action=FileAction.PULL)])

    assert result.failed == 0
    (row,) = await env.conflicts()
    assert row.resolved and row.resolution == "keep_remote"


# --- resolving through the route ---


async def resolve(env: Env, conflict_id: int, resolution: str):
    return await env.client.post(f"/conflicts/{conflict_id}/resolve", json={"resolution": resolution})


async def test_keep_local_copies_the_local_file_over_the_remote_one(env):
    conflict = await env.diff_with_conflict()

    resp = await resolve(env, conflict.id, "keep_local")

    assert resp.status_code == 200, resp.text
    assert resp.json()["resolved"] is True and resp.json()["resolution"] == "keep_local"
    assert (env.remote / "plan.md").read_text() == "local edit"
    assert (env.local / "plan.md").read_text() == "local edit"
    assert trash(env.remote) == {"plan.md": "remote edit"}
    assert not (env.local / TRASH_DIR).exists()
    # The cached diff no longer lists it, and the job says what happened.
    assert env.engine._state.get_cached_paths() == set()
    assert env.engine._state.pending_changes == 0
    job = await env.last_job()
    assert job.direction == "selective" and job.status == "completed" and job.profile_id == env.profile_id
    async with env.factory() as session:
        changes = (await session.execute(select(FileChange).where(FileChange.job_id == job.id))).scalars().all()
    assert [(c.file_path, c.action) for c in changes] == [("plan.md", "modified")]


async def test_keep_remote_copies_the_remote_file_over_the_local_one(env):
    conflict = await env.diff_with_conflict()

    resp = await resolve(env, conflict.id, "keep_remote")

    assert resp.status_code == 200, resp.text
    assert (env.local / "plan.md").read_text() == "remote edit"
    assert (env.remote / "plan.md").read_text() == "remote edit"
    assert trash(env.local) == {"plan.md": "local edit"}
    (row,) = await env.conflicts()
    assert row.resolved and row.resolution == "keep_remote"


async def test_keep_both_keeps_both_versions_on_both_sides(env):
    conflict = await env.diff_with_conflict()

    resp = await resolve(env, conflict.id, "keep_both")

    assert resp.status_code == 200, resp.text
    for side in (env.local, env.remote):
        files = {k: v for k, v in files_under(side).items() if k != SENTINEL_FILE}
        assert files["plan.md"] == "local edit"
        copies = [v for k, v in files.items() if k.startswith("plan") and "conflict" in k]
        assert copies == ["remote edit"], (side, files)
    (row,) = await env.conflicts()
    assert row.resolved and row.resolution == "keep_both"


async def test_keep_local_copies_even_when_size_and_time_match(env):
    """Same size, same modification time, different content: still copied."""
    conflict = await env.diff_with_conflict(local="AAAA", remote="BBBB")
    mtime = time.time() - 60
    os.utime(env.local / "plan.md", (mtime, mtime))
    os.utime(env.remote / "plan.md", (mtime, mtime))
    await env.engine.enhanced_diff()  # refresh the recorded times

    resp = await resolve(env, conflict.id, "keep_local")

    assert resp.status_code == 200, resp.text
    assert (env.remote / "plan.md").read_text() == "AAAA"
    assert trash(env.remote) == {"plan.md": "BBBB"}


async def test_a_file_changed_since_the_diff_is_not_overwritten(env):
    conflict = await env.diff_with_conflict()
    write(env.remote, "plan.md", "edited again on the remote", mtime=time.time() + 30)

    resp = await resolve(env, conflict.id, "keep_local")

    assert resp.status_code == 409
    assert "changed since the conflict was found" in resp.json()["detail"]
    assert (env.remote / "plan.md").read_text() == "edited again on the remote"
    assert not (env.remote / TRASH_DIR).exists()
    (row,) = await env.conflicts()
    assert not row.resolved


async def test_keep_remote_of_a_vanished_remote_file_is_refused(env):
    conflict = await env.diff_with_conflict()
    (env.remote / "plan.md").unlink()

    resp = await resolve(env, conflict.id, "keep_remote")

    assert resp.status_code == 409
    assert "remote file no longer exists" in resp.json()["detail"]
    assert (env.local / "plan.md").read_text() == "local edit"


async def test_dismiss_only_closes_the_record(env):
    conflict = await env.diff_with_conflict()

    resp = await resolve(env, conflict.id, "dismiss")

    assert resp.status_code == 200
    assert resp.json()["resolution"] == "dismiss"
    assert (env.local / "plan.md").read_text() == "local edit"
    assert (env.remote / "plan.md").read_text() == "remote edit"
    assert await env.last_job() is None
    # Resolving it again is refused.
    assert (await resolve(env, conflict.id, "keep_local")).status_code == 409


async def test_resolving_needs_the_running_profile(env):
    conflict = await env.diff_with_conflict()
    conflict_routes.set_manager(SimpleNamespace(get_engine_by_id=lambda pid: None))  # type: ignore[arg-type]

    resp = await resolve(env, conflict.id, "keep_local")

    assert resp.status_code == 409
    assert "not running" in resp.json()["detail"]
    assert (env.remote / "plan.md").read_text() == "remote edit"
    assert (await resolve(env, 999, "dismiss")).status_code == 404


async def test_resolving_waits_for_the_profiles_running_sync(env):
    conflict = await env.diff_with_conflict()

    await env.engine.sync_lock.acquire()  # e.g. a push is running
    task = asyncio.create_task(env.engine.resolve_conflict(conflict.id, ConflictResolution.KEEP_LOCAL))
    await asyncio.sleep(0.3)
    assert not task.done()
    assert (env.remote / "plan.md").read_text() == "remote edit"
    env.engine.sync_lock.release()
    await asyncio.wait_for(task, timeout=30)
    assert (env.remote / "plan.md").read_text() == "local edit"


async def test_a_conflict_of_another_profile_is_refused(env):
    async with env.factory() as session:
        session.add(Conflict(profile_id=None, job_id=None, file_path="plan.md", resolved=False))
        await session.commit()
    (row,) = await env.conflicts()

    with pytest.raises(ConflictResolutionError):
        await env.engine.resolve_conflict(row.id, ConflictResolution.KEEP_LOCAL)


async def test_deleting_the_profile_removes_its_diff_conflicts(env):
    await env.diff_with_conflict()
    async with env.factory() as session:
        await session.delete(await session.get(SyncProfile, env.profile_id))
        await session.commit()
    assert await env.conflicts() == []
