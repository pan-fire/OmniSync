"""Two-way sync (rclone bisync) against the real rclone binary.

Same pattern as test_sync_safety_integration.py: the "remote" is an rclone
remote of type `local` in a temp rclone.conf, the database a real migrated
SQLite file. Every bisync, dry run, backup dir and resync below is executed
by rclone itself. Skipped when rclone is not installed.

Every test runs twice (see the ``env`` fixture): on short folder paths,
which bisync is given as they are, and on paths too long for bisync's file
names, which it is given through the profile's short-name remotes.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from backend.api.routes import conflicts as conflict_routes
from backend.api.routes import profiles as profile_routes
from backend.api.schemas import ConflictResolution, SyncState
from backend.db import database
from backend.db.database import init_database
from backend.db.models import Conflict, FileChange, SyncError, SyncJob, SyncProfile
from backend.main import app
from backend.models.profile_config import ProfileConfig
from backend.services.rclone import SENTINEL_FILE, TRASH_DIR, RcloneService, bisync_session_name, needs_short_names
from backend.services.sync_engine import SyncEngine, bisync_workdir
from backend.tests.auth import AUTH_HEADERS
from backend.tests.sync_jobs import finished

if shutil.which("rclone") is None and os.environ.get("OMNISYNC_REQUIRE_RCLONE") == "1":
    raise RuntimeError("OMNISYNC_REQUIRE_RCLONE=1 but rclone is not on PATH")
pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")


def files_under(root: Path, trash: bool = False) -> dict[str, str]:
    """Relative path -> content of every file under root (trash only if asked)."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_file() and (trash or not rel.startswith(TRASH_DIR)):
            out[rel] = p.read_text(errors="replace")
    return out


def trash_of(root: Path) -> dict[str, str]:
    """Path inside the timestamp folder -> content, for everything in root's trash."""
    return {k.split("/", 2)[2]: v for k, v in files_under(root, trash=True).items() if k.startswith(TRASH_DIR + "/")}


def write(root: Path, rel: str, content: str, mtime: float | None = None) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


@dataclass
class Env:
    local: Path
    remote: Path
    factory: object
    conf: Path
    profile_id: int
    client: AsyncClient
    engines: list[SyncEngine]

    def engine(self, **changes) -> SyncEngine:
        """A two-way engine for the profile (changes: ProfileConfig fields)."""
        config = ProfileConfig(
            profile_id=self.profile_id, slug="docs", name="Docs",
            local_dir=str(self.local), remote_dir=f"testremote:{self.remote}",
            max_retries=1, sync_mode="two_way",
        )
        engine = SyncEngine(replace(config, **changes), RcloneService(rclone_config_path=str(self.conf)), self.factory)
        self.engines.append(engine)
        return engine

    @property
    def workdir(self) -> Path:
        return Path(bisync_workdir(self.profile_id))

    async def jobs(self) -> list[SyncJob]:
        async with self.factory() as session:
            return list((await session.execute(select(SyncJob).order_by(SyncJob.id))).scalars().all())

    async def last_job(self) -> SyncJob:
        return (await self.jobs())[-1]

    async def errors(self, job_id: int) -> list[str]:
        async with self.factory() as session:
            rows = (await session.execute(select(SyncError).where(SyncError.job_id == job_id))).scalars().all()
            return [r.message for r in rows]

    async def changes(self, job_id: int) -> set[tuple[str, str, str | None]]:
        async with self.factory() as session:
            rows = (await session.execute(select(FileChange).where(FileChange.job_id == job_id))).scalars().all()
            return {(r.file_path, r.action, r.side) for r in rows}

    async def conflicts(self) -> list[Conflict]:
        async with self.factory() as session:
            return list((await session.execute(select(Conflict).order_by(Conflict.id))).scalars().all())


def bisync_files_named_after(workdir: Path) -> set[str]:
    """The session names (see rclone/bisync_names.py) bisync's files in ``workdir`` are named after."""
    return {p.name.split(".path")[0].removesuffix(".lck") for p in workdir.glob("*.*")
            if ".path" in p.name or p.name.endswith(".lck")} if workdir.is_dir() else set()


@pytest_asyncio.fixture(params=["paths", "short"], ids=["short-paths", "long-paths"])
async def env(request, tmp_path: Path, monkeypatch):
    """A two-way profile; its folders: short ones, or so deep that bisync runs on short names."""
    if request.param == "paths":
        # Not under tmp_path, which may already be too long; the temp dir
        # itself (/tmp on CI) must be short enough for real-path names.
        if needs_short_names(f"{tempfile.gettempdir()}/osync-xxxxxxxx/local",
                             f"testremote:{tempfile.gettempdir()}/osync-xxxxxxxx/remote"):
            pytest.skip("TMPDIR is too long for the real-path variant; set TMPDIR=/tmp to run it")
        base = Path(tempfile.mkdtemp(prefix="osync-"))
        # Removed even when the test or the checks after it fail.
        request.addfinalizer(lambda: shutil.rmtree(base, ignore_errors=True))
    else:
        base = tmp_path / ("deep-" + "d" * 100) / ("folder-" + "f" * 30)
    local, remote = base / "local", base / "remote"
    local.mkdir(parents=True)
    remote.mkdir()
    assert needs_short_names(str(local), f"testremote:{remote}") == (request.param == "short"), base
    conf = tmp_path / "rclone.conf"
    conf.write_text("[testremote]\ntype = local\n")
    monkeypatch.setattr("backend.services.sync_engine.two_way.BISYNC_DIR", str(tmp_path / "bisync"))

    saved = database._engine, database._async_session_factory
    database._engine = None
    await init_database(str(tmp_path / "two-way.db"))
    factory = database._async_session_factory
    now = datetime.now(timezone.utc)
    async with factory() as session:
        profile = SyncProfile(
            slug="docs", name="Docs", local_dir=str(local), remote_dir=f"testremote:{remote}",
            debounce_seconds=5, pull_interval_minutes=5, rclone_filter="[]", rclone_args="[]",
            max_retries=1, enabled=True, created_at=now, updated_at=now, sync_mode="two_way",
        )
        session.add(profile)
        await session.commit()
        profile_id = profile.id

    engines: list[SyncEngine] = []

    def engine_by_id(pid: int):
        return engines[-1] if engines and pid == profile_id else None

    manager = SimpleNamespace(get_engine_by_id=engine_by_id)
    profile_routes.set_manager(manager)  # type: ignore[arg-type]
    conflict_routes.set_manager(manager)  # type: ignore[arg-type]
    from backend.services.profile_service import ProfileService
    profile_routes.set_profile_service(ProfileService(factory))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH_HEADERS) as client:
        yield Env(local, remote, factory, conf, profile_id, client, engines)

    for engine in engines:
        await engine.stop()
    # bisync's files are named after the real paths only where they fit.
    workdir = Path(bisync_workdir(profile_id))
    expected = (f"omnisync_bisync_{profile_id}_local_root..omnisync_bisync_{profile_id}_remote_root"
                if request.param == "short" else bisync_session_name(str(local), f"testremote:{remote}"))
    assert bisync_files_named_after(workdir) <= {expected}
    if (workdir / "omnisync-state.json").is_file() and bisync_files_named_after(workdir):
        assert '"names": "%s"' % request.param in (workdir / "omnisync-state.json").read_text()
    profile_routes.set_manager(None)
    profile_routes.set_profile_service(None)
    conflict_routes.set_manager(None)
    await database._engine.dispose()
    database._engine, database._async_session_factory = saved


async def synced(env: Env, files: dict[str, str]) -> SyncEngine:
    """Both sides hold ``files`` and the first two-way run (a resync) is done."""
    for rel, content in files.items():
        write(env.local, rel, content, mtime=time.time() - 3600)
        write(env.remote, rel, content, mtime=time.time() - 3600)
    engine = env.engine()
    await engine.two_way_sync()
    job = await env.last_job()
    assert (job.direction, job.status) == ("resync", "completed"), await env.errors(job.id)
    return engine


# --- (e) the first run is a resync: the union, nothing deleted ---


async def test_first_run_resyncs_without_deleting(env):
    old, new = time.time() - 7200, time.time() - 60
    write(env.local, "only-local.txt", "L")
    write(env.remote, "only-remote.txt", "R")
    write(env.local, "same.txt", "same")
    write(env.remote, "same.txt", "same")
    write(env.local, "differs.txt", "older local", mtime=old)
    write(env.remote, "differs.txt", "newer remote", mtime=new)
    engine = env.engine()

    job_id = await engine.two_way_sync()

    job = await env.last_job()
    assert job.id == job_id and (job.direction, job.status) == ("resync", "completed"), await env.errors(job_id)
    expected = {
        SENTINEL_FILE: files_under(env.local)[SENTINEL_FILE],
        "only-local.txt": "L", "only-remote.txt": "R", "same.txt": "same", "differs.txt": "newer remote",
    }
    assert files_under(env.local) == expected
    assert files_under(env.remote) == expected
    # The replaced older version is recoverable; nothing was deleted anywhere.
    assert trash_of(env.local) == {"differs.txt": "older local"}
    assert trash_of(env.remote) == {}
    assert ("only-remote.txt", "created", "local") in await env.changes(job_id)
    assert ("only-local.txt", "created", "remote") in await env.changes(job_id)
    assert not any(action == "deleted" for _, action, _ in await env.changes(job_id))
    assert engine._state.state == SyncState.IDLE and not engine._state.resync_required

    # The next run is an ordinary two-way sync with nothing to do.
    await engine.two_way_sync()
    job = await env.last_job()
    assert (job.direction, job.status, job.files_changed) == ("two_way", "completed", 0)


# --- (a) offline edits and collaborators' new files survive ---


async def test_offline_local_edit_is_not_reverted(env):
    engine = await synced(env, {"notes.txt": "v1", "other.txt": "x"})
    # Edited while OmniSync was not running; the remote did not change.
    write(env.local, "notes.txt", "offline edit")

    # What the interval runs (a mirror profile would pull here and revert it).
    await engine._scheduled_sync()

    assert (await env.last_job()).status == "completed"
    assert files_under(env.local)["notes.txt"] == "offline edit"
    assert files_under(env.remote)["notes.txt"] == "offline edit"
    assert trash_of(env.remote) == {"notes.txt": "v1"}


async def test_remote_only_new_file_is_not_deleted_by_a_local_change(env):
    engine = await synced(env, {"notes.txt": "v1"})
    write(env.remote, "from-collaborator.txt", "their work")
    write(env.local, "notes.txt", "my edit")

    # What the file watcher runs after a local change (a mirror push would
    # delete from-collaborator.txt here).
    job_id = await engine._auto_sync()

    assert (await env.last_job()).status == "completed"
    for side in (env.local, env.remote):
        assert files_under(side)["from-collaborator.txt"] == "their work"
        assert files_under(side)["notes.txt"] == "my edit"
    assert ("from-collaborator.txt", "created", "local") in await env.changes(job_id)
    assert ("notes.txt", "modified", "remote") in await env.changes(job_id)


# --- (b) edits on both sides keep both versions and record a conflict ---


async def test_edits_on_both_sides_keep_both_versions(env):
    engine = await synced(env, {"docs/plan.md": "base"})
    write(env.local, "docs/plan.md", "local edit", mtime=time.time() - 120)
    write(env.remote, "docs/plan.md", "remote edit (newer)", mtime=time.time() - 10)

    job_id = await engine.two_way_sync()

    job = await env.last_job()
    assert (job.status, job.conflicts) == ("completed", 1), await env.errors(job_id)
    for side in (env.local, env.remote):
        files = files_under(side)
        assert files["docs/plan.md"] == "remote edit (newer)"
        assert files["docs/plan.local-conflict1.md"] == "local edit"
    [conflict] = await env.conflicts()
    assert (conflict.file_path, conflict.job_id, conflict.resolved) == ("docs/plan.md", job_id, False)
    assert (conflict.local_kept_as, conflict.remote_kept_as) == ("docs/plan.local-conflict1.md", "docs/plan.md")
    assert conflict.local_modified is not None and conflict.remote_modified is not None

    # The Conflicts page shows it with both names.
    listed = (await env.client.get("/conflicts")).json()
    assert [(c["file_path"], c["local_kept_as"], c["remote_kept_as"]) for c in listed] == [
        ("docs/plan.md", "docs/plan.local-conflict1.md", "docs/plan.md"),
    ]
    # A diff does not close it although the file no longer differs.
    await engine.enhanced_diff()
    assert not (await env.conflicts())[0].resolved


async def test_keep_local_of_a_two_way_conflict_keeps_only_that_version(env):
    engine = await synced(env, {"plan.md": "base"})
    write(env.local, "plan.md", "local edit", mtime=time.time() - 120)
    write(env.remote, "plan.md", "remote edit (newer)", mtime=time.time() - 10)
    await engine.two_way_sync()
    [conflict] = await env.conflicts()

    resp = await env.client.post(f"/conflicts/{conflict.id}/resolve", json={"resolution": "keep_local"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["resolved"] and resp.json()["resolution"] == "keep_local"
    for side in (env.local, env.remote):
        assert {k: v for k, v in files_under(side).items() if k != SENTINEL_FILE} == {"plan.md": "local edit"}
        # The dropped version is recoverable on both sides.
        assert "remote edit (newer)" in trash_of(side).values()

    # The next two-way sync sees both sides alike: no new conflict.
    await engine.two_way_sync()
    job = await env.last_job()
    assert (job.status, job.conflicts) == ("completed", 0)
    for side in (env.local, env.remote):
        assert {k: v for k, v in files_under(side).items() if k != SENTINEL_FILE} == {"plan.md": "local edit"}
    assert [c.resolved for c in await env.conflicts()] == [True]


async def test_keep_remote_when_the_local_version_won(env):
    engine = await synced(env, {"plan.md": "base"})
    write(env.local, "plan.md", "local edit (newer)", mtime=time.time() - 10)
    write(env.remote, "plan.md", "remote edit", mtime=time.time() - 120)
    await engine.two_way_sync()
    [conflict] = await env.conflicts()
    assert (conflict.local_kept_as, conflict.remote_kept_as) == ("plan.md", "plan.remote-conflict1.md")

    await engine.resolve_conflict(conflict.id, ConflictResolution.KEEP_REMOTE)
    await engine.two_way_sync()

    assert (await env.last_job()).conflicts == 0
    for side in (env.local, env.remote):
        assert {k: v for k, v in files_under(side).items() if k != SENTINEL_FILE} == {"plan.md": "remote edit"}
        assert "local edit (newer)" in trash_of(side).values()


async def test_keep_both_of_a_two_way_conflict_changes_nothing(env):
    engine = await synced(env, {"plan.md": "base"})
    write(env.local, "plan.md", "local edit", mtime=time.time() - 120)
    write(env.remote, "plan.md", "remote edit (newer)", mtime=time.time() - 10)
    await engine.two_way_sync()
    before = files_under(env.local), files_under(env.remote)
    [conflict] = await env.conflicts()

    resp = await env.client.post(f"/conflicts/{conflict.id}/resolve", json={"resolution": "keep_both"})

    assert resp.status_code == 200 and resp.json()["resolution"] == "keep_both"
    assert (files_under(env.local), files_under(env.remote)) == before


# --- (c) deletions propagate both ways and stay recoverable ---


async def test_deletions_propagate_both_ways_and_go_to_the_trash(env):
    engine = await synced(env, {"a.txt": "A", "b.txt": "B", "c.txt": "C"})
    (env.local / "a.txt").unlink()
    (env.remote / "b.txt").unlink()

    job_id = await engine.two_way_sync()

    assert (await env.last_job()).status == "completed"
    for side in (env.local, env.remote):
        assert sorted(files_under(side)) == [SENTINEL_FILE, "c.txt"]
    assert trash_of(env.remote) == {"a.txt": "A"}
    assert trash_of(env.local) == {"b.txt": "B"}
    changes = await env.changes(job_id)
    assert {("a.txt", "deleted", "remote"), ("b.txt", "deleted", "local")} <= changes


# --- (d) an emptied or unmounted local folder aborts without deleting ---


async def test_emptied_local_folder_without_marker_deletes_nothing(env):
    engine = await synced(env, {"a.txt": "A", "b.txt": "B"})
    before = files_under(env.remote)
    for p in env.local.iterdir():
        p.unlink() if p.is_file() else shutil.rmtree(p)

    job_id = await engine.two_way_sync()

    assert files_under(env.remote) == before
    job = await env.last_job()
    assert job.id == job_id and job.status == "failed"
    assert any(SENTINEL_FILE in m for m in await env.errors(job_id))
    assert engine._state.state == SyncState.ERROR


async def test_emptied_local_folder_with_marker_deletes_nothing(env):
    engine = await synced(env, {"a.txt": "A", "b.txt": "B"})
    before = files_under(env.remote)
    for name in ("a.txt", "b.txt"):
        (env.local / name).unlink()

    await engine.two_way_sync()

    assert files_under(env.remote) == before
    assert (await env.last_job()).status == "failed"
    assert "local folder is empty" in (engine._state.last_error or "")


async def test_emptied_remote_folder_with_marker_deletes_nothing(env):
    """The mirror image: a remote emptied but for the marker (a wiped bucket,
    a restored share) must not empty the local folder."""
    engine = await synced(env, {"a.txt": "A", "b.txt": "B"})
    before = files_under(env.local)
    for name in ("a.txt", "b.txt"):
        (env.remote / name).unlink()

    await engine.two_way_sync()

    assert files_under(env.local) == before
    assert (await env.last_job()).status == "failed"
    assert "remote folder is empty" in (engine._state.last_error or "")


async def test_unreadable_local_folder_deletes_nothing(env):
    engine = await synced(env, {"a.txt": "A"})
    before = files_under(env.remote)
    env.local.chmod(0)
    try:
        if os.access(env.local, os.R_OK):
            pytest.skip("running as root: permissions do not apply")
        await engine.two_way_sync()
    finally:
        env.local.chmod(0o755)

    assert files_under(env.remote) == before
    assert (await env.last_job()).status == "failed"
    assert "cannot be read" in (engine._state.last_error or "")


async def test_unmounted_local_folder_deletes_nothing(env):
    engine = await synced(env, {"a.txt": "A"})
    before = files_under(env.remote)
    shutil.rmtree(env.local)

    await engine.two_way_sync()

    assert not env.local.exists()  # never created
    assert files_under(env.remote) == before
    assert "missing or not mounted" in (engine._state.last_error or "")


# --- (f) the delete limit stops a mass deletion before anything changes ---


async def test_delete_limit_stops_a_mass_deletion(env, monkeypatch):
    monkeypatch.setattr("backend.services.sync_engine.safety.DEFAULT_MAX_DELETE", 2)
    engine = await synced(env, {f"doc{i}.txt": "precious" for i in range(6)})
    for i in range(5):
        (env.local / f"doc{i}.txt").unlink()
    write(env.local, "new.txt", "also not carried over")

    job_id = await engine.two_way_sync()

    remote = files_under(env.remote)
    assert all(f"doc{i}.txt" in remote for i in range(6)) and "new.txt" not in remote
    assert trash_of(env.remote) == {}
    job = await env.last_job()
    assert job.id == job_id and job.status == "failed"
    message = engine._state.last_error or ""
    assert "5 file(s) in the remote folder" in message and "limit of 2" in message
    assert engine._state.intervals_paused

    # Raising the limit for this profile lets the deletions through (to the trash).
    engine = env.engine(rclone_args=["--max-delete", "10"])
    await engine.two_way_sync()
    assert (await env.last_job()).status == "completed"
    assert sorted(files_under(env.remote)) == [SENTINEL_FILE, "doc5.txt", "new.txt"]
    assert len(trash_of(env.remote)) == 5


async def test_delete_limit_is_absolute_not_a_percentage(env, monkeypatch):
    # bisync's own --max-delete would stop this run (60 % of the files, its
    # default limit is 50 %); OmniSync's absolute limit of 50 files allows it.
    engine = await synced(env, {f"f{i}.txt": "x" for i in range(10)})
    for i in range(6):
        (env.remote / f"f{i}.txt").unlink()

    await engine.two_way_sync()

    assert (await env.last_job()).status == "completed"
    assert sorted(files_under(env.local)) == [SENTINEL_FILE, *(f"f{i}.txt" for i in range(6, 10))]


# --- (g) recovery after an interrupted run ---


def rclone_pids(workdir: Path) -> list[int]:
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            cmdline = Path(f"/proc/{entry}/cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if cmdline and cmdline[0].endswith(b"rclone") and b"bisync" in cmdline and str(workdir).encode() in cmdline:
            pids.append(int(entry))
    return pids


async def wait_for_transfer(env: Env, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(p.name.endswith(".partial") for p in env.remote.rglob("*")):
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the transfer never started")


@pytest.mark.parametrize("how", ["stopped", "killed"])
async def test_interrupted_run_recovers_without_resync(env, how):
    engine = await synced(env, {"a.txt": "A"})
    (env.local / "big.bin").write_bytes(os.urandom(40_000_000))
    write(env.local, "small.txt", "s")
    slow = env.engine(rclone_args=["--bwlimit", "8M"])

    task = asyncio.create_task(slow.two_way_sync())
    await wait_for_transfer(env)
    if how == "stopped":
        assert await slow.stop_current_sync()  # SIGINT: bisync shuts down gracefully
        job_id = await task
        assert "Stopped by user." in await env.errors(job_id)
    else:
        pids = rclone_pids(env.workdir)
        assert pids
        for pid in pids:
            os.kill(pid, signal.SIGKILL)  # a crash: the lock file stays behind
        await task
        assert list(env.workdir.glob("*.lck"))
        assert not (env.remote / "big.bin").exists()  # really interrupted mid-transfer
    # (A graceful stop may still finish the transfer in flight.)
    assert (await env.last_job()).status == "failed"

    engine = env.engine()
    await engine.two_way_sync()  # --recover (and a stale lock is cleared)

    job = await env.last_job()
    assert (job.direction, job.status) == ("two_way", "completed"), await env.errors(job.id)
    assert not engine._state.resync_required
    assert (env.remote / "big.bin").read_bytes() == (env.local / "big.bin").read_bytes()
    assert files_under(env.remote)["small.txt"] == "s"
    # rclone's leftover in-progress files are never carried over.
    assert not any(p.name.endswith(".partial") for p in env.local.rglob("*"))


# --- leftover .partial files of an interrupted transfer ---


def partial_leftovers(root: Path) -> list[str]:
    """rclone's in-progress files under root (trash included), by relative path."""
    return sorted(str(p.relative_to(root)) for p in root.rglob("*.partial")
                  if p.is_file() and len(p.name.split(".")) >= 3 and len(p.name.split(".")[-2]) == 8)


def sync_pids(remote: Path) -> list[int]:
    """The rclone processes of a mirror sync to ``remote``."""
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            cmdline = Path(f"/proc/{entry}/cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if cmdline and cmdline[0].endswith(b"rclone") and b"sync" in cmdline and f"testremote:{remote}".encode() in cmdline:
            pids.append(int(entry))
    return pids


def plant_user_files(env: Env) -> None:
    """What the clean-up must leave alone: a user's file with a .partial name on both
    sides, a partial-named file newer than the next run, and one in the trash."""
    for side in (env.local, env.remote):
        write(side, "notes.partial", "my notes", mtime=time.time() - 7200)
    write(env.local, "draft.0123abcd.partial", "newer than the run", mtime=time.time() + 3600)
    write(env.remote, f"{TRASH_DIR}/20260101T000000Z/old.0123abcd.partial", "in the trash", mtime=time.time() - 7200)


def assert_user_files_kept(env: Env) -> None:
    assert (env.local / "notes.partial").read_text() == "my notes"
    assert (env.remote / "notes.partial").read_text() == "my notes"
    assert (env.local / "draft.0123abcd.partial").read_text() == "newer than the run"
    assert (env.remote / TRASH_DIR / "20260101T000000Z" / "old.0123abcd.partial").exists()


@pytest.mark.parametrize("env", ["short"], indirect=True)
async def test_a_two_way_run_after_a_killed_transfer_removes_its_partial_files(env, caplog):
    engine = await synced(env, {"a.txt": "A"})
    (env.local / "big.bin").write_bytes(os.urandom(40_000_000))
    slow = env.engine(rclone_args=["--bwlimit", "8M"])
    task = asyncio.create_task(slow.two_way_sync())
    await wait_for_transfer(env)
    pids = rclone_pids(env.workdir)
    assert pids
    for pid in pids:
        os.kill(pid, signal.SIGKILL)
    await task
    assert (await env.last_job()).status == "failed"
    leftovers = [p for p in partial_leftovers(env.remote) if p.startswith("big.bin.")]
    assert len(leftovers) == 1  # really left behind by the killed transfer
    # Not transferred again (which would reuse and rename the partial file).
    (env.local / "big.bin").unlink()
    plant_user_files(env)

    caplog.set_level("INFO", logger="backend.services.sync_engine")
    engine = env.engine()
    await engine.two_way_sync()

    job = await env.last_job()
    assert (job.direction, job.status) == ("two_way", "completed"), await env.errors(job.id)
    assert not any(p.startswith("big.bin.") for p in partial_leftovers(env.remote))
    assert not (env.remote / "big.bin").exists()
    assert_user_files_kept(env)
    assert f"(job {job.id}): removed 1 leftover partial file(s) of an interrupted transfer (local 0, remote 1)" \
        in caplog.text
    [record] = [r for r in caplog.records if "leftover partial" in r.getMessage()]
    assert record.fields["partials_removed"] == 1  # type: ignore[attr-defined]

    # The next run (after a completed one) does not look again.
    caplog.clear()
    write(env.local, "late.0123abcd.partial", "x", mtime=time.time() - 7200)
    await engine.two_way_sync()
    assert (await env.last_job()).status == "completed"
    assert (env.local / "late.0123abcd.partial").exists()
    assert "leftover partial" not in caplog.text


@pytest.mark.parametrize("env", ["short"], indirect=True)
async def test_a_push_after_a_killed_transfer_removes_its_partial_files(env, caplog):
    for side in (env.local, env.remote):
        write(side, SENTINEL_FILE, "marker")
    write(env.local, "a.txt", "A")
    (env.local / "big.bin").write_bytes(os.urandom(40_000_000))
    slow = env.engine(sync_mode="mirror", rclone_args=["--bwlimit", "8M"])
    task = asyncio.create_task(slow.push())
    await wait_for_transfer(env)
    pids = sync_pids(env.remote)
    assert pids
    for pid in pids:
        os.kill(pid, signal.SIGKILL)
    await task
    assert (await env.last_job()).status == "failed"
    assert any(p.startswith("big.bin.") for p in partial_leftovers(env.remote))
    (env.local / "big.bin").unlink()  # not transferred again (see above)
    plant_user_files(env)

    caplog.set_level("INFO", logger="backend.services.sync_engine")
    engine = env.engine(sync_mode="mirror")
    await engine.push()

    job = await env.last_job()
    assert (job.direction, job.status) == ("push", "completed"), await env.errors(job.id)
    assert not any(p.startswith("big.bin.") for p in partial_leftovers(env.remote))
    assert not (env.remote / "big.bin").exists()
    assert_user_files_kept(env)
    assert f"(job {job.id}): removed 1 leftover partial file(s)" in caplog.text


@pytest.mark.parametrize("env", ["short"], indirect=True)
async def test_a_failed_clean_up_does_not_fail_the_run(env, caplog, monkeypatch):
    from backend.exceptions import RcloneError

    engine = await synced(env, {"a.txt": "A"})
    async with env.factory() as session:  # an earlier run that did not complete
        session.add(SyncJob(direction="two_way", started_at=datetime.now(timezone.utc), status="failed",
                            files_changed=0, conflicts=0, errors=1, profile_id=env.profile_id))
        await session.commit()
    write(env.local, "x.0123abcd.partial", "left over", mtime=time.time() - 7200)

    async def broken(root, older_than):
        raise RcloneError("listing failed")

    monkeypatch.setattr(engine._rclone, "remove_partials", broken)
    caplog.set_level("INFO", logger="backend.services.sync_engine")
    await engine.two_way_sync()
    assert (await env.last_job()).status == "completed"
    assert "could not remove the leftover partial files in the local folder: listing failed" in caplog.text
    assert "removed 0 leftover partial file(s)" in caplog.text
    assert (env.local / "x.0123abcd.partial").exists()


# --- resync needed: never automatic, always confirmed ---


async def test_lost_sync_state_pauses_until_a_confirmed_resync(env):
    engine = await synced(env, {"a.txt": "A", "b.txt": "B"})
    for listing in env.workdir.glob("*.lst"):
        listing.unlink()  # e.g. left as .lst-err by a run rclone could not recover
    (env.remote / "b.txt").unlink()  # a deletion a blind resync would undo

    job_id = await engine.two_way_sync()

    job = await env.last_job()
    assert job.id == job_id and job.status == "failed"
    assert engine._state.resync_required and engine._state.intervals_paused
    assert "needs a resync" in (engine._state.last_error or "")
    assert "b.txt" not in files_under(env.remote) and "b.txt" in files_under(env.local)

    # It stays that way across a restart, and Sync now is refused.
    engine = env.engine()
    await engine.start()
    assert engine._state.resync_required and engine._state.intervals_paused
    resp = await env.client.post("/profiles/docs/sync/start", json={"direction": "two_way", "force": True})
    assert resp.status_code == 409 and "resync" in resp.json()["detail"].lower()
    assert (await env.client.get("/profiles/docs")).json()["resync_required"] is True

    # The confirmed resync makes the union and resumes syncing.
    assert (await env.client.post("/profiles/docs/sync/resync", json={})).status_code == 400
    resp = await env.client.post("/profiles/docs/sync/resync", json={"confirm": True})
    assert resp.status_code == 202, resp.text
    await finished(env.client, engine, resp)
    job = await env.last_job()
    assert (job.direction, job.status) == ("resync", "completed"), await env.errors(job.id)
    assert files_under(env.remote)["b.txt"] == "B"
    assert not engine._state.resync_required and not engine._state.intervals_paused
    status = (await env.client.get("/profiles/docs")).json()
    assert status["resync_required"] is False and status["sync_mode"] == "two_way"


async def test_filter_change_carries_pending_changes_then_resyncs(env):
    engine = await synced(env, {"keep.txt": "k", "gone.txt": "g", "draft.tmp": "t"})
    (env.local / "gone.txt").unlink()  # pending deletion under the old filters
    write(env.remote, "later.tmp", "excluded from now on")

    engine = env.engine(rclone_filter=["- *.tmp"])
    await engine.two_way_sync()

    job = await env.last_job()
    assert (job.direction, job.status) == ("resync", "completed"), await env.errors(job.id)
    # The flush with the old filters carried the deletion (a plain resync
    # would have copied gone.txt back).
    for side in (env.local, env.remote):
        assert "gone.txt" not in files_under(side)
    # It also carried later.tmp (still included then); the rule applies from now on.
    write(env.local, "new.tmp", "not synced")
    await engine.two_way_sync()
    job = await env.last_job()
    assert (job.direction, job.status) == ("two_way", "completed")
    assert "new.tmp" not in files_under(env.remote)


async def test_include_style_filter_keeps_the_sync_marker_visible(env):
    """'+ /Docs/**' then '- **' must not hide .omnisync-check from --check-access."""
    write(env.local, "Docs/a.txt", "a")
    write(env.local, "other.bin", "not synced")
    engine = env.engine(rclone_filter=["+ /Docs/**", "- **"])

    await engine.two_way_sync()  # the first run: a resync
    job = await env.last_job()
    assert (job.direction, job.status) == ("resync", "completed"), await env.errors(job.id)

    write(env.remote, "Docs/b.txt", "b")
    await engine.two_way_sync()
    job = await env.last_job()
    assert (job.direction, job.status) == ("two_way", "completed"), await env.errors(job.id)
    assert sorted(files_under(env.remote)) == [SENTINEL_FILE, "Docs/a.txt", "Docs/b.txt"]
    assert files_under(env.local)["Docs/b.txt"] == "b"
    assert not engine._state.intervals_paused and not engine._state.resync_required


@pytest.mark.parametrize("args", [
    ["--include", "/Docs/**"], ["--exclude=.*"], ["--filter", "- .omnisync-check"],
    ["--max-age", "30d"], ["--min-size=1k"], ["--max-size", "100b"], ["--max-depth=0"],
    ["--exclude-if-present", ".nosync"],
])
async def test_two_way_profile_rejects_flags_that_can_hide_the_marker(env, args):
    resp = await env.client.put("/profiles/docs", json={"rclone_args": args})
    assert resp.status_code == 422, resp.text
    # "--filter" with its value as a separate argument is refused before the
    # marker check (it needs --filter=<value>); validation errors do not
    # echo the submitted value back.
    message = resp.json()["detail"]
    assert args[0].split("=")[0] in message
    assert ".omnisync-check" in message or "needs a value" in message

    resp = await env.client.post("/profiles", json={
        "name": "Other", "local_dir": str(env.local.parent / "other"), "remote_dir": "testremote:/x",
        "rclone_args": args,
    })
    assert resp.status_code == 422, resp.text
    message = resp.json()["detail"]
    assert ".omnisync-check" in message or "needs a value" in message


async def test_switching_to_two_way_checks_the_existing_flags(env):
    async with env.factory() as session:  # a mirror profile may use --include
        profile = await session.get(SyncProfile, env.profile_id)
        profile.sync_mode, profile.rclone_args = "mirror", '["--include", "*.txt"]'
        await session.commit()
    resp = await env.client.put("/profiles/docs", json={"rclone_args": ["--include", "*.txt", "--bwlimit", "1M"]})
    assert resp.status_code == 200, resp.text
    resp = await env.client.put("/profiles/docs", json={"sync_mode": "two_way"})
    assert resp.status_code == 422 and "--include" in resp.text
    # Flags that keep the marker visible are fine.
    resp = await env.client.put("/profiles/docs", json={
        "sync_mode": "two_way", "rclone_args": ["--max-size", "1G", "--max-depth=3", "--bwlimit", "1M"],
    })
    assert resp.status_code == 200, resp.text


# --- API and preview ---


async def test_preview_counts_the_next_two_way_run_and_changes_nothing(env, monkeypatch):
    monkeypatch.setattr("backend.services.sync_engine.safety.DEFAULT_MAX_DELETE", 1)
    await synced(env, {"a.txt": "A", "b.txt": "B", "c.txt": "C", "d.txt": "D"})
    (env.remote / "a.txt").unlink()
    (env.remote / "b.txt").unlink()
    write(env.local, "c.txt", "edited")
    write(env.remote, "new.txt", "N")
    listings = {p.name: p.read_bytes() for p in env.workdir.iterdir()}
    before = files_under(env.local, trash=True), files_under(env.remote, trash=True)

    resp = await env.client.post("/profiles/docs/sync/preview")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sync_mode"] == "two_way" and body["max_delete"] == 1
    tw = body["two_way"]
    assert tw["error"] is None and not tw["resync"] and not tw["resync_required"]
    assert tw["local"] == {"deletes": 2, "replaces": 0, "creates": 1, "exceeds_max_delete": True}
    assert tw["remote"] == {"deletes": 0, "replaces": 1, "creates": 0, "exceeds_max_delete": False}
    # Nothing changed: files, trash, bisync's listings.
    assert (files_under(env.local, trash=True), files_under(env.remote, trash=True)) == before
    assert {p.name: p.read_bytes() for p in env.workdir.iterdir()} == listings
    assert (await env.last_job()).direction == "resync"  # the preview recorded no job


async def test_sync_now_endpoint_and_mode_checks(env):
    await synced(env, {"a.txt": "A"})
    write(env.local, "b.txt", "B")

    resp = await env.client.post("/profiles/docs/sync/start", json={"direction": "two_way"})

    assert resp.status_code == 202, resp.text
    await finished(env.client, env.engines[-1], resp)
    assert files_under(env.remote)["b.txt"] == "B"
    assert (await env.last_job()).direction == "two_way"
    files = (await env.client.get(f"/jobs/{resp.json()['job_id']}/files")).json()
    assert [(f["file_path"], f["action"], f["side"]) for f in files] == [("b.txt", "created", "remote")]

    # A mirror profile has no two-way sync and no resync.
    env.engine(sync_mode="mirror")
    resp = await env.client.post("/profiles/docs/sync/start", json={"direction": "two_way"})
    assert resp.status_code == 409 and "mirror" in resp.json()["detail"]
    resp = await env.client.post("/profiles/docs/sync/resync", json={"confirm": True})
    assert resp.status_code == 409


# --- (h) mirror profiles behave exactly as before ---


async def test_mirror_profile_still_pushes_and_pulls(env):
    for side in (env.local, env.remote):
        write(side, SENTINEL_FILE, "marker")
    write(env.local, "mine.txt", "local")
    write(env.remote, "theirs.txt", "remote only")
    engine = env.engine(sync_mode="mirror")

    # The watcher's trigger is a push: the remote becomes a mirror of local.
    await engine._auto_sync()
    job = await env.last_job()
    assert (job.direction, job.status) == ("push", "completed")
    assert sorted(files_under(env.remote)) == [SENTINEL_FILE, "mine.txt"]
    assert trash_of(env.remote) == {"theirs.txt": "remote only"}

    # The interval is a pull.
    write(env.remote, "later.txt", "pulled")
    await engine._scheduled_sync()
    job = await env.last_job()
    assert (job.direction, job.status) == ("pull", "completed")
    assert files_under(env.local)["later.txt"] == "pulled"
    assert not env.workdir.exists()  # no two-way state for a mirror profile


# --- (i) folder paths too long for bisync's file names ---


def edit_state(env: Env, **changes) -> None:
    """Change omnisync-state.json as a sync of the pair in another form would have left it."""
    path = env.workdir / "omnisync-state.json"
    state = {**json.loads(path.read_text()), **changes}
    path.write_text(json.dumps({k: v for k, v in state.items() if v is not None}))


@pytest.mark.parametrize("env", ["short"], indirect=True)
async def test_short_names_are_defined_only_in_the_environment(env, monkeypatch):
    monkeypatch.setattr("backend.services.sync_engine.safety.DEFAULT_MAX_DELETE", 1)
    calls: list[tuple[list[str], dict[str, str] | None]] = []
    spawn = asyncio.create_subprocess_exec

    async def recording(*cmd, **kwargs):
        calls.append(([str(c) for c in cmd], kwargs.get("env")))
        return await spawn(*cmd, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recording)
    engine = await synced(env, {"a.txt": "A", "b.txt": "B"})
    (env.remote / "a.txt").unlink()
    await engine.two_way_sync()  # the delete limit's dry run, then the run
    assert (await env.last_job()).status == "completed"

    bisyncs = [(cmd, proc_env) for cmd, proc_env in calls if "bisync" in cmd]
    assert len(bisyncs) == 3 and sum("--dry-run" in cmd for cmd, _ in bisyncs) == 1
    pid = env.profile_id
    for cmd, proc_env in bisyncs:
        # The command line names the short roots, never the real folders.
        assert cmd[-2:] == [f"omnisync_bisync_{pid}_local:root", f"omnisync_bisync_{pid}_remote:root"]
        assert not any(str(env.local) in arg or str(env.remote) in arg for arg in cmd)
        backup_dirs = [cmd[cmd.index("--backup-dir1") + 1], cmd[cmd.index("--backup-dir2") + 1]]
        assert backup_dirs[0].startswith(f"omnisync_bisync_{pid}_local:root/{TRASH_DIR}/")
        assert backup_dirs[1].startswith(f"omnisync_bisync_{pid}_remote:root/{TRASH_DIR}/")
        # Their definitions are in the environment of the process.
        assert proc_env is not None
        assert {k: v for k, v in proc_env.items() if k.startswith("RCLONE_CONFIG_OMNISYNC_BISYNC_")} == {
            f"RCLONE_CONFIG_OMNISYNC_BISYNC_{pid}_LOCAL_TYPE": "combine",
            f"RCLONE_CONFIG_OMNISYNC_BISYNC_{pid}_LOCAL_UPSTREAMS": f'"root={env.local}"',
            f"RCLONE_CONFIG_OMNISYNC_BISYNC_{pid}_REMOTE_TYPE": "combine",
            f"RCLONE_CONFIG_OMNISYNC_BISYNC_{pid}_REMOTE_UPSTREAMS": f'"root=testremote:{env.remote}"',
        }
    # Other rclone processes do not receive them.
    assert all(proc_env is None or not any(k.startswith("RCLONE_CONFIG_OMNISYNC_BISYNC_") for k in proc_env)
               for cmd, proc_env in calls if "bisync" not in cmd)
    # The deletion went to the trash of the right side.
    assert trash_of(env.local) == {"a.txt": "A"}


@pytest.mark.parametrize("env", ["short"], indirect=True)
async def test_a_pair_synced_on_paths_that_no_longer_fit_needs_a_confirmed_resync(env):
    engine = await synced(env, {"a.txt": "A"})
    # As if this pair had been synced on its real paths (the form is kept
    # until a resync, so it never changes silently).
    edit_state(env, names="paths")
    write(env.local, "b.txt", "B")

    await engine.two_way_sync()

    job = await env.last_job()
    assert job.status == "failed" and engine._state.resync_required and engine._state.intervals_paused
    message = engine._state.last_error or ""
    assert "needs a resync" in message and "paths are too long" in message
    assert "b.txt" not in files_under(env.remote)

    await engine.resync()
    job = await env.last_job()
    assert (job.direction, job.status) == ("resync", "completed"), await env.errors(job.id)
    assert files_under(env.remote)["b.txt"] == "B"
    assert json.loads((env.workdir / "omnisync-state.json").read_text())["names"] == "short"


@pytest.mark.parametrize("env", ["paths"], indirect=True)
async def test_a_pair_keeps_its_names_form_until_a_resync(env):
    engine = await synced(env, {"a.txt": "A"})
    assert json.loads((env.workdir / "omnisync-state.json").read_text())["names"] == "paths"
    listings = sorted(p.name for p in env.workdir.glob("*.lst"))
    write(env.local, "b.txt", "B")

    await engine.two_way_sync()

    job = await env.last_job()
    assert (job.direction, job.status) == ("two_way", "completed"), await env.errors(job.id)
    assert sorted(p.name for p in env.workdir.glob("*.lst")) == listings

    # A pair recorded with short names keeps them until a resync chooses anew.
    edit_state(env, names="short")
    assert engine._short_names(resync=False) and not engine._short_names(resync=True)
    edit_state(env, names="paths")
