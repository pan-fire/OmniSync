"""Live progress, user pause, bandwidth limit, sync window, trash and folder rules, against real rclone.

Same pattern as test_two_way_sync_integration.py: the "remote" is an rclone
remote of type `local` in a temp rclone.conf and the database a real
migrated SQLite file, so every transfer, stats line, --bwlimit, backup dir
and filter rule below is rclone's own. Skipped when rclone is not installed.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from backend.api.routes import profiles as profile_routes
from backend.api.schemas import SyncState
from backend.db import database
from backend.db.database import init_database
from backend.db.models import SyncJob, SyncProfile
from backend.main import app
from backend.models.profile_config import ProfileConfig
from backend.services.profile_service import ProfileService
from backend.services.rclone import SENTINEL_FILE, TRASH_DIR, RcloneService
from backend.services.sync_engine import USER_PAUSE_REASON, SyncEngine
from backend.tests.auth import AUTH_HEADERS

if shutil.which("rclone") is None and os.environ.get("OMNISYNC_REQUIRE_RCLONE") == "1":
    raise RuntimeError("OMNISYNC_REQUIRE_RCLONE=1 but rclone is not on PATH")
pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")


def write(root: Path, rel: str, content: str | bytes, mtime: float | None = None) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def files_under(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                  if p.is_file() and not str(p.relative_to(root)).startswith(TRASH_DIR))


@dataclass
class Env:
    local: Path
    remote: Path
    factory: object
    conf: Path
    profile_id: int
    client: AsyncClient
    engines: dict[int, SyncEngine]
    locks: dict[int, asyncio.Lock]

    def engine(self, profile_id: int | None = None, **changes) -> SyncEngine:
        pid = profile_id or self.profile_id
        config = ProfileConfig(
            profile_id=pid, slug="docs" if pid == self.profile_id else f"p{pid}", name="Docs",
            local_dir=str(self.local), remote_dir=f"testremote:{self.remote}", max_retries=1,
        )
        engine = SyncEngine(replace(config, **changes), RcloneService(rclone_config_path=str(self.conf)),
                            self.factory, sync_lock=self.locks.setdefault(pid, asyncio.Lock()))
        self.engines[pid] = engine
        return engine

    async def jobs(self) -> list[SyncJob]:
        async with self.factory() as session:  # type: ignore[operator]
            return list((await session.execute(select(SyncJob).order_by(SyncJob.id))).scalars().all())

    async def profile(self, pid: int | None = None) -> SyncProfile:
        async with self.factory() as session:  # type: ignore[operator]
            row = await session.get(SyncProfile, pid or self.profile_id)
            assert row is not None
            return row

    async def add_profile(self, slug: str, local: Path, remote: Path, enabled: bool = True) -> int:
        now = datetime.now(timezone.utc)
        async with self.factory() as session:  # type: ignore[operator]
            row = SyncProfile(
                slug=slug, name=slug, local_dir=str(local), remote_dir=f"testremote:{remote}",
                debounce_seconds=5, pull_interval_minutes=5, rclone_filter="[]", rclone_args="[]",
                max_retries=1, enabled=enabled, created_at=now, updated_at=now, sync_mode="mirror",
            )
            session.add(row)
            await session.commit()
            return row.id


@pytest_asyncio.fixture
async def env(tmp_path: Path, monkeypatch):
    local, remote = tmp_path / "local", tmp_path / "remote"
    local.mkdir()
    remote.mkdir()
    conf = tmp_path / "rclone.conf"
    conf.write_text("[testremote]\ntype = local\n")
    monkeypatch.setattr("backend.services.sync_engine.two_way.BISYNC_DIR", str(tmp_path / "bisync"))

    saved = database._engine, database._async_session_factory
    database._engine = None
    await init_database(str(tmp_path / "features.db"))
    factory = database._async_session_factory
    now = datetime.now(timezone.utc)
    async with factory() as session:
        profile = SyncProfile(
            slug="docs", name="Docs", local_dir=str(local), remote_dir=f"testremote:{remote}",
            debounce_seconds=5, pull_interval_minutes=5, rclone_filter="[]", rclone_args="[]",
            max_retries=1, enabled=True, created_at=now, updated_at=now, sync_mode="mirror",
        )
        session.add(profile)
        await session.commit()
        profile_id = profile.id

    engines: dict[int, SyncEngine] = {}
    locks: dict[int, asyncio.Lock] = {}
    manager = SimpleNamespace(
        get_engine_by_id=engines.get,
        sync_lock=lambda pid: locks.setdefault(pid, asyncio.Lock()),
    )
    profile_routes.set_manager(manager)  # type: ignore[arg-type]
    profile_routes.set_profile_service(ProfileService(factory))
    profile_routes.set_rclone_service(RcloneService(rclone_config_path=str(conf)))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH_HEADERS) as client:
        yield Env(local, remote, factory, conf, profile_id, client, engines, locks)

    for engine in engines.values():
        await engine.stop()
    profile_routes.set_manager(None)
    profile_routes.set_profile_service(None)
    profile_routes.set_rclone_service(None)
    await database._engine.dispose()
    database._engine, database._async_session_factory = saved


async def pushed(env: Env, files: dict[str, str | bytes], **changes) -> SyncEngine:
    """Both sides marked and holding ``files`` after a first push."""
    for rel, content in files.items():
        write(env.local, rel, content, mtime=time.time() - 3600)
    engine = env.engine(**changes)
    await engine.push()
    job = (await env.jobs())[-1]
    assert job.status == "completed"
    return engine


# --- 1. live progress (and the bandwidth limit that makes it observable) ---


async def _progress_while_running(engine: SyncEngine, run) -> list[dict]:
    task = asyncio.create_task(run())
    seen: list[dict] = []
    while not task.done():
        status = await engine.get_status()
        if status.progress is not None:
            seen.append(status.progress.model_dump())
        await asyncio.sleep(0.2)
    await task
    return seen


async def test_push_reports_live_progress_limited_by_the_profile_bwlimit(env):
    engine = await pushed(env, {"a.txt": "a"}, bwlimit="1M")
    write(env.local, "big.bin", os.urandom(3_500_000))

    seen = await _progress_while_running(engine, engine.push)

    assert seen, "no progress was reported while the push ran"
    last = seen[-1]
    assert 0 < last["bytes"] <= last["total_bytes"] == 3_500_000
    assert last["files_total"] == 1
    assert any(f["name"] == "big.bin" for p in seen for f in p["current_files"])
    # --bwlimit 1M: about 1 MiB/s (rclone's burst allows a little more).
    assert all(p["speed"] < 2.5 * 1024 * 1024 for p in seen)
    assert len(seen) >= 2  # several seconds at 1 MiB/s
    job = (await env.jobs())[-1]
    assert job.status == "completed" and (env.remote / "big.bin").stat().st_size == 3_500_000
    status = await engine.get_status()
    assert status.state == SyncState.IDLE and status.progress is None


async def test_two_way_sync_reports_live_progress(env):
    write(env.local, "a.txt", "a")
    engine = env.engine(sync_mode="two_way", bwlimit="2M")
    await engine.two_way_sync()  # first run: a resync
    write(env.local, "big.bin", os.urandom(4_000_000))

    seen = await _progress_while_running(engine, engine.two_way_sync)

    assert seen and any(f["name"] == "big.bin" for p in seen for f in p["current_files"])
    job = (await env.jobs())[-1]
    assert (job.direction, job.status) == ("two_way", "completed")
    assert (await engine.get_status()).progress is None


async def test_errors_are_still_reported_with_the_stats_flags(env):
    """A failing transfer still names the failure: the stats lines never mask or pollute it."""
    engine = await pushed(env, {"a.txt": "a"})
    write(env.local, "b.bin", os.urandom(200_000))
    limited = env.engine(rclone_args=["--max-transfer", "1k"])
    await limited.push()
    status = await limited.get_status()
    assert status.state == SyncState.ERROR and status.progress is None
    assert status.last_error and "max transfer" in status.last_error.lower()
    assert "Transferred:" not in status.last_error and "Elapsed time" not in status.last_error
    assert engine is not None


# --- 2. pause all / resume all ---


async def test_pause_all_holds_automatic_syncs_until_resume_all(env):
    engine = await pushed(env, {"a.txt": "a"})
    other_local, other_remote = env.local.parent / "l2", env.remote.parent / "r2"
    other_local.mkdir()
    other_remote.mkdir()
    disabled = await env.add_profile("off", other_local, other_remote, enabled=False)

    resp = await env.client.post("/profiles/pause-all")
    assert resp.status_code == 200, resp.text
    assert resp.json()["changed"] == ["docs"]  # disabled profiles are left alone
    assert (await env.profile()).user_paused and not (await env.profile(disabled)).user_paused
    status = (await env.client.get("/profiles/docs")).json()
    assert status["intervals_paused"] and status["user_paused"]
    assert status["last_error"] is None  # a user pause is not an error

    write(env.local, "b.txt", "b")
    assert await engine.push(automatic=True) is None  # the watcher waits
    assert "b.txt" not in files_under(env.remote)
    await engine.push()  # the user's own sync still runs ...
    assert "b.txt" in files_under(env.remote)
    assert engine._state.user_paused  # ... and does not lift the pause
    assert (await env.client.post("/profiles/pause-all")).json()["unchanged"] == ["docs"]

    # Kept across a restart (a new engine reads it back).
    restarted = env.engine()
    await restarted._load_hold()
    assert restarted._state.user_paused and restarted._state.auto_paused

    resp = await env.client.post("/profiles/resume-all")
    assert resp.json() == {"changed": ["docs"], "unchanged": [], "still_paused": {}}
    assert not restarted._state.auto_paused and not (await env.profile()).user_paused


async def test_resume_all_leaves_a_restore_hold_in_place(env):
    engine = await pushed(env, {"a.txt": "a"})
    await engine.hold("Restored the local side from a backup; review the diff.")
    await env.client.post("/profiles/pause-all")
    assert engine._state.user_paused

    resp = await env.client.post("/profiles/resume-all")
    body = resp.json()
    assert body["changed"] == ["docs"]
    assert "Restored the local side" in body["still_paused"]["docs"]
    assert not engine._state.user_paused and engine._state.intervals_paused
    assert (await env.profile()).pause_reason.startswith("Restored")

    # The profile's own resume lifts it after review.
    assert (await env.client.post("/profiles/docs/sync/resume-intervals")).status_code == 200
    assert not engine._state.auto_paused and (await env.profile()).pause_reason is None


async def test_profile_pause_and_resume(env):
    engine = await pushed(env, {"a.txt": "a"})
    assert (await env.client.post("/profiles/docs/sync/pause")).json()["detail"] == "Automatic syncing paused."
    assert engine._state.user_paused and USER_PAUSE_REASON == "Paused by user"
    resp = await env.client.post("/profiles/docs/sync/resume-intervals")
    assert resp.status_code == 200 and not engine._state.auto_paused


# --- 3. bandwidth limit and sync window ---


def _hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def closed_window() -> dict:
    now = datetime.now().astimezone()
    return {"days": list(range(7)), "start": _hhmm(now + timedelta(hours=2)), "end": _hhmm(now + timedelta(hours=3))}


def open_window() -> dict:
    now = datetime.now().astimezone()
    return {"days": list(range(7)), "start": _hhmm(now - timedelta(hours=1)), "end": _hhmm(now + timedelta(hours=1))}


async def test_outside_the_window_automatic_syncs_wait_and_run_when_it_opens(env):
    engine = await pushed(env, {"a.txt": "a"}, sync_window=closed_window())
    write(env.local, "b.txt", "b")

    assert await engine._auto_sync() is None  # the watcher's push waits ...
    assert await engine.pull(automatic=True) is None  # ... and so does the interval pull
    status = await engine.get_status()
    assert status.outside_sync_window and status.waiting_for_window and status.next_window_start
    assert "b.txt" not in files_under(env.remote)
    assert engine._paused_edits == 0  # waiting is not a pause: nothing to compare on resume

    await engine._window_tick()  # still closed: nothing runs
    assert "b.txt" not in files_under(env.remote)

    engine._profile = replace(engine.profile, sync_window=open_window())
    engine._state.sync_window = engine.profile.sync_window
    await engine._window_tick()
    assert "b.txt" in files_under(env.remote)
    assert [j.direction for j in (await env.jobs())][-2:] == ["push", "pull"]
    assert not (await engine.get_status()).waiting_for_window


async def test_a_user_started_sync_runs_outside_the_window_with_a_note(env):
    engine = await pushed(env, {"a.txt": "a"})
    async with env.factory() as session:  # type: ignore[operator]
        row = await session.get(SyncProfile, env.profile_id)
        row.sync_window = '{"days": [0,1,2,3,4,5,6], "start": "%s", "end": "%s"}' % (
            closed_window()["start"], closed_window()["end"])
        await session.commit()
    engine = env.engine(sync_window=closed_window())
    write(env.local, "b.txt", "b")
    resp = await env.client.post("/profiles/docs/sync/start", json={"direction": "push"})
    assert resp.status_code in (200, 202), resp.text
    assert "sync window" in resp.json()["note"]
    while engine.is_running_operation or engine._launch is not None:
        await asyncio.sleep(0.05)
    assert "b.txt" in files_under(env.remote)
    profile = (await env.client.get("/profiles/docs")).json()
    assert profile["sync_window"]["start"] == closed_window()["start"] and profile["outside_sync_window"]


async def test_bwlimit_field_is_validated_and_conflicts_with_the_flag(env):
    ok = await env.client.put("/profiles/docs", json={"bwlimit": "08:00,512k 19:00,10M 23:00,off"})
    assert ok.status_code == 200 and ok.json()["bwlimit"] == "08:00,512k 19:00,10M 23:00,off"
    bad = await env.client.put("/profiles/docs", json={"bwlimit": "8:00,1M"})
    assert bad.status_code == 422
    clash = await env.client.put("/profiles/docs", json={"rclone_args": ["--bwlimit", "1M"]})
    assert clash.status_code == 422 and "twice" in clash.text
    cleared = await env.client.put("/profiles/docs", json={"bwlimit": None, "rclone_args": ["--bwlimit=1M"]})
    assert cleared.status_code == 200 and cleared.json()["bwlimit"] is None
    window = await env.client.put("/profiles/docs", json={"sync_window": {"days": [5, 6], "start": "22:00",
                                                                            "end": "06:00"}})
    assert window.json()["sync_window"] == {"days": [5, 6], "start": "22:00", "end": "06:00"}
    assert (await env.client.put("/profiles/docs", json={"name": "Docs"})).json()["sync_window"] is not None
    assert (await env.client.put("/profiles/docs", json={"sync_window": None})).json()["sync_window"] is None


# --- 4. rules the folder picker generates ---


@pytest.mark.parametrize("rules, expected", [
    (["+ /Docs/**", "- **"], ["Docs/a.txt", "Docs/sub/b.txt"]),
    (["- /Big/**"], ["Docs/a.txt", "Docs/sub/b.txt", "top.txt"]),
    (["+ /Big/keep/**", "- /Big/**"], ["Big/keep/k.txt", "Docs/a.txt", "Docs/sub/b.txt", "top.txt"]),
    (["- /Docs/sub/**", "+ /Docs/**", "- **"], ["Docs/a.txt"]),
])
@pytest.mark.parametrize("mode", ["mirror", "two_way"])
async def test_generated_folder_rules_sync_exactly_the_chosen_folders(env, rules, expected, mode):
    for rel in ["Docs/a.txt", "Docs/sub/b.txt", "Big/x.bin", "Big/keep/k.txt", "top.txt"]:
        write(env.local, rel, rel)
    engine = env.engine(rclone_filter=rules, sync_mode=mode)
    await (engine.two_way_sync() if mode == "two_way" else engine.push())
    job = (await env.jobs())[-1]
    assert job.status == "completed"
    # The sync marker stays visible on both sides (two-way --check-access needs it).
    assert sorted(files_under(env.remote)) == sorted([*expected, SENTINEL_FILE])
    if mode == "two_way":
        write(env.remote, expected[0], "changed remotely")
        await engine.two_way_sync()
        job = (await env.jobs())[-1]
        assert (job.direction, job.status) == ("two_way", "completed")
        assert (env.local / expected[0]).read_text() == "changed remotely"


# --- 5. trash browser ---


async def trashed(env: Env) -> SyncEngine:
    """A push that replaced remote/a.txt and deleted remote/gone.txt; a pull that trashed local/new.txt."""
    old = time.time() - 7200
    engine = await pushed(env, {"a.txt": "v1", "gone.txt": "bye", "sub/s.txt": "s1"})
    write(env.local, "a.txt", "v2")
    (env.local / "gone.txt").unlink()
    write(env.local, "sub/s.txt", "s2")
    await engine.push()
    write(env.local, "new.txt", "local only", mtime=old)
    await engine.pull()  # mirror pull: new.txt goes to the local trash
    return engine


async def test_trash_lists_both_sides(env):
    await trashed(env)
    remote = (await env.client.get("/profiles/docs/trash", params={"side": "remote"})).json()
    assert sorted(e["path"] for e in remote["entries"]) == ["a.txt", "gone.txt", "sub/s.txt"]
    assert remote["total_files"] == 3 and remote["total_bytes"] == len("v1") + len("bye") + len("s1")
    entry = next(e for e in remote["entries"] if e["path"] == "a.txt")
    assert entry["id"] == f"{entry['folder']}/a.txt" and entry["trashed_at"] and entry["size"] == 2
    local = (await env.client.get("/profiles/docs/trash")).json()
    assert [e["path"] for e in local["entries"]] == ["new.txt"] and local["side"] == "local"


async def test_restore_moves_back_and_keeps_what_was_there(env):
    await trashed(env)
    remote = (await env.client.get("/profiles/docs/trash", params={"side": "remote"})).json()
    ids = {e["path"]: e["id"] for e in remote["entries"]}

    # gone.txt: nothing in the way. a.txt: the current (newer) v2 is in the way.
    resp = await env.client.post("/profiles/docs/trash/restore",
                                 json={"side": "remote", "ids": [ids["gone.txt"], ids["a.txt"]]})
    body = resp.json()
    assert body["done"] == [ids["gone.txt"]]
    assert [(f["id"], f["code"]) for f in body["failed"]] == [(ids["a.txt"], "target_newer")]
    assert (env.remote / "gone.txt").read_text() == "bye"
    assert (env.remote / "a.txt").read_text() == "v2"

    resp = await env.client.post("/profiles/docs/trash/restore",
                                 json={"side": "remote", "ids": [ids["a.txt"]], "overwrite": True})
    assert resp.json() == {"done": [ids["a.txt"]], "failed": []}
    assert (env.remote / "a.txt").read_text() == "v1"
    after = (await env.client.get("/profiles/docs/trash", params={"side": "remote"})).json()
    kept = [e for e in after["entries"] if e["path"] == "a.txt"]
    assert len(kept) == 1 and (env.remote / TRASH_DIR / kept[0]["id"]).read_text() == "v2"

    # Local side: the file comes back where it was; its emptied folder goes.
    local = (await env.client.get("/profiles/docs/trash")).json()
    entry = local["entries"][0]
    resp = await env.client.post("/profiles/docs/trash/restore", json={"side": "local", "ids": [entry["id"]]})
    assert resp.json()["done"] == [entry["id"]]
    assert (env.local / "new.txt").read_text() == "local only"
    assert not (env.local / TRASH_DIR / entry["folder"]).exists()


async def test_delete_removes_trash_entries_for_good(env):
    await trashed(env)
    remote = (await env.client.get("/profiles/docs/trash", params={"side": "remote"})).json()
    target = next(e["id"] for e in remote["entries"] if e["path"] == "sub/s.txt")
    resp = await env.client.post("/profiles/docs/trash/delete", json={"side": "remote", "ids": [target, target]})
    assert resp.json() == {"done": [target], "failed": []}
    again = await env.client.post("/profiles/docs/trash/delete", json={"side": "remote", "ids": [target]})
    assert again.json()["failed"][0]["code"] == "not_found"
    local = (await env.client.get("/profiles/docs/trash")).json()
    resp = await env.client.post("/profiles/docs/trash/delete",
                                 json={"side": "local", "ids": [local["entries"][0]["id"]]})
    assert resp.json()["failed"] == []
    assert (await env.client.get("/profiles/docs/trash")).json()["total_files"] == 0


@pytest.mark.parametrize("side", ["local", "remote"])
@pytest.mark.parametrize("bad", [
    "../outside.txt", "/etc/passwd", "a/../../outside.txt", "./a.txt", "a//b", f"x/{TRASH_DIR}/y",
])
async def test_trash_ids_cannot_leave_the_trash(env, side, bad):
    await trashed(env)
    secret = env.local.parent / "outside.txt"
    secret.write_text("secret")
    for action in ("restore", "delete"):
        resp = await env.client.post(f"/profiles/docs/trash/{action}", json={"side": side, "ids": [bad]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["done"] == [] and resp.json()["failed"][0]["code"] in ("invalid", "not_found")
    assert secret.read_text() == "secret"


async def test_local_trash_does_not_follow_symlinks(env):
    await trashed(env)
    outside = env.local.parent / "elsewhere"
    outside.mkdir()
    (outside / "victim.txt").write_text("keep me")
    (env.local / TRASH_DIR / "linked").symlink_to(outside)
    listed = (await env.client.get("/profiles/docs/trash")).json()
    assert all(not e["id"].startswith("linked/") for e in listed["entries"])
    resp = await env.client.post("/profiles/docs/trash/delete", json={"side": "local", "ids": ["linked/victim.txt"]})
    assert resp.json()["failed"][0]["code"] == "invalid"
    assert (outside / "victim.txt").read_text() == "keep me"

    # A symlinked folder in the synced folder cannot take a restore elsewhere either.
    entry = listed["entries"][0]
    write(env.local, f"{TRASH_DIR}/{entry['folder']}/out/x.txt", "x")
    (env.local / "out").symlink_to(outside)
    resp = await env.client.post("/profiles/docs/trash/restore",
                                 json={"side": "local", "ids": [f"{entry['folder']}/out/x.txt"]})
    assert resp.json()["failed"][0]["code"] == "invalid"
    assert not (outside / "x.txt").exists()


async def test_trash_actions_wait_for_no_running_sync(env):
    await trashed(env)
    lock = env.locks[env.profile_id]
    async with lock:
        resp = await env.client.post("/profiles/docs/trash/delete", json={"side": "local", "ids": ["x/y"]})
    assert resp.status_code == 409


async def test_restore_never_overwrites_a_trashed_file_in_the_same_second(env, monkeypatch):
    """The replaced version goes to a later folder, not onto the file being restored."""
    from backend.services import trash as trash_module

    await trashed(env)
    remote = (await env.client.get("/profiles/docs/trash", params={"side": "remote"})).json()
    entry = next(e for e in remote["entries"] if e["path"] == "a.txt")
    monkeypatch.setattr(trash_module, "_now_stamp", lambda: entry["folder"])
    resp = await env.client.post("/profiles/docs/trash/restore",
                                 json={"side": "remote", "ids": [entry["id"]], "overwrite": True})
    assert resp.json()["done"] == [entry["id"]]
    assert (env.remote / "a.txt").read_text() == "v1"
    kept = [e for e in (await env.client.get("/profiles/docs/trash", params={"side": "remote"})).json()["entries"]
            if e["path"] == "a.txt"]
    assert len(kept) == 1 and kept[0]["folder"] > entry["folder"]
    assert (env.remote / TRASH_DIR / kept[0]["id"]).read_text() == "v2"

    # Same on the local side.
    write(env.local, f"{TRASH_DIR}/{entry['folder']}/a.txt", "old local", mtime=time.time() - 9000)
    resp = await env.client.post("/profiles/docs/trash/restore",
                                 json={"side": "local", "ids": [f"{entry['folder']}/a.txt"], "overwrite": True})
    assert resp.json()["done"] == [f"{entry['folder']}/a.txt"]
    assert (env.local / "a.txt").read_text() == "old local"
    assert [p.read_text() for p in (env.local / TRASH_DIR).glob("*/a.txt")] == ["v2"]
