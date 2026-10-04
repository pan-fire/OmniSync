"""Profile lifecycle against a real, migrated database.

- Engines are keyed by profile id: rename, reconfigure, disable, delete
  and repeated enables act on the right engine.
- Manual flags are per profile; profile folders and backup targets may
  not overlap (same folder or nested), compared after normalising.
- Profile settings, log_level and snapshot ids are validated.
- Deleting a profile with history removes the dependent rows.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from backend.api.routes import backups
from backend.api.routes import config as config_routes
from backend.api.routes import profiles as profiles_router
from backend.api.schemas import (
    ChangeCategory,
    DiffResponse,
    DiffSummary,
    FileAction,
    FileDiff,
    ProfileCreateRequest,
    SelectiveSyncItem,
)
from backend.db import database
from backend.db.database import init_database
from backend.db.models import (
    BackupTarget,
    Conflict,
    FileChange,
    ManualFlag,
    SyncError,
    SyncJob,
    SyncProfile,
)
from backend.exceptions import ProfileConflictError, ProfileNotFoundError
from backend.main import app
from backend.models.profile_config import ProfileConfig
from backend.services.config import ConfigService
from backend.services.path_overlap import locations_overlap, normalize_location
from backend.services.profile_service import ProfileService
from backend.services.sync_engine import SyncEngine
from backend.services.sync_engine_manager import SyncEngineManager
from backend.tests.auth import AUTH_HEADERS


@dataclass
class Env:
    client: AsyncClient
    manager: SyncEngineManager
    service: ProfileService
    factory: object
    root: Path
    started: list[SyncEngine] = field(default_factory=list)
    stopped: list[SyncEngine] = field(default_factory=list)

    def folder(self, name: str) -> str:
        path = self.root / name
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    async def create(self, name: str, local: str, remote: str | None = None, mode: str | None = None) -> dict:
        body = {"name": name, "local_dir": local, "remote_dir": remote or f"gdrive:{name}"}
        if mode is not None:
            body["sync_mode"] = mode
        resp = await self.client.post("/profiles", json=body)
        assert resp.status_code == 201, resp.text
        return resp.json()

    def running(self) -> dict[int, SyncEngine]:
        return self.manager.engines_by_id


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    saved = database._engine, database._async_session_factory
    database._engine = None
    await init_database(str(tmp_path / "profiles.db"))
    factory = database._async_session_factory

    e = Env(client=None, manager=None, service=ProfileService(factory), factory=factory, root=tmp_path)  # type: ignore[arg-type]

    # Real engines, minus the file watcher, scheduler and rclone.
    async def fake_start(self):
        e.started.append(self)

    async def fake_stop(self):
        e.stopped.append(self)

    monkeypatch.setattr(SyncEngine, "start", fake_start)
    monkeypatch.setattr(SyncEngine, "stop", fake_stop)

    e.manager = SyncEngineManager(AsyncMock(), AsyncMock(), factory)
    profiles_router.set_manager(e.manager)
    profiles_router.set_profile_service(e.service)

    backup_service = MagicMock()
    backup_service.get_next_run_time = MagicMock(return_value=None)
    backup_service.restore = AsyncMock()
    backup_service.remote_names = AsyncMock(return_value={"gdrive"})
    backups.set_backup_service(backup_service)
    backups.set_db_factory(factory)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=AUTH_HEADERS,
    ) as client:
        e.client = client
        yield e

    profiles_router.set_manager(None)
    profiles_router.set_profile_service(None)
    backups.set_backup_service(None)
    backups.set_db_factory(None)
    await database._engine.dispose()
    database._engine, database._async_session_factory = saved


# --- engine identity ---


async def test_rename_replaces_the_engine(env):
    p = await env.create("Alpha", env.folder("a"))
    old = env.started[-1]

    resp = await env.client.put("/profiles/alpha", json={"name": "Beta"})
    assert resp.status_code == 200
    assert resp.json()["slug"] == "beta"

    # The renamed profile is reachable under its new slug, not the old one ...
    assert (await env.client.get("/profiles/beta/sync/status")).status_code == 200
    assert (await env.client.get("/profiles/alpha/sync/status")).status_code == 404
    # ... and the old engine, still configured with the old slug, is gone.
    assert old in env.stopped
    assert [e.profile.slug for e in env.manager.engines.values()] == ["beta"]

    new = env.running()[p["id"]]
    assert new is not old
    assert new.profile.slug == "beta" and new.profile.name == "Beta"
    listed = (await env.client.get("/profiles")).json()
    assert [x["slug"] for x in listed] == ["beta"]


async def test_disable_after_rename_stops_the_engine(env):
    await env.create("Alpha", env.folder("a"))
    await env.client.put("/profiles/alpha", json={"name": "Beta"})

    resp = await env.client.post("/profiles/beta/disable")
    assert resp.status_code == 200
    assert env.manager.engines == {}
    assert set(env.started) <= set(env.stopped)
    assert (await env.client.get("/profiles/beta/sync/status")).status_code == 404


async def test_delete_after_rename_stops_every_engine(env):
    p = await env.create("Alpha", env.folder("a"))
    await env.client.put("/profiles/alpha", json={"name": "Beta"})

    resp = await env.client.delete("/profiles/beta", params={"confirm": "true"})
    assert resp.status_code == 204
    assert env.manager.engines == {}
    assert set(env.started) <= set(env.stopped)
    with pytest.raises(ProfileNotFoundError):
        await env.service.get_by_slug("beta")
    assert p["id"] not in env.manager._sync_locks


async def test_local_dir_change_restarts_with_new_folder(env):
    p = await env.create("Alpha", env.folder("a"))
    old = env.running()[p["id"]]
    new_dir = env.folder("elsewhere")

    resp = await env.client.put("/profiles/alpha", json={"local_dir": new_dir})
    assert resp.status_code == 200

    new = env.running()[p["id"]]
    assert new is not old and old in env.stopped
    assert new.profile.local_dir == new_dir
    # The replacement shares the profile's sync lock, so it waits for any
    # rclone run the old engine still has going.
    assert new.sync_lock is old.sync_lock


async def test_update_without_config_change_keeps_the_engine(env):
    p = await env.create("Alpha", env.folder("a"))
    engine = env.running()[p["id"]]
    resp = await env.client.put("/profiles/alpha", json={"debounce_seconds": 5})
    assert resp.status_code == 200
    assert env.running()[p["id"]] is engine
    assert env.stopped == []


async def test_mirror_notice_dismissal_is_stored_and_keeps_the_engine(env):
    p = await env.create("Alpha", env.folder("a"), mode="mirror")
    assert p["mirror_notice_dismissed"] is False
    engine = env.running()[p["id"]]

    resp = await env.client.put("/profiles/alpha", json={"mirror_notice_dismissed": True})
    assert resp.status_code == 200
    assert resp.json()["mirror_notice_dismissed"] is True
    assert resp.json()["sync_mode"] == "mirror"
    # Every client reads it from the profile.
    assert (await env.client.get("/profiles/alpha")).json()["mirror_notice_dismissed"] is True
    assert [x["mirror_notice_dismissed"] for x in (await env.client.get("/profiles")).json()] == [True]
    # Only a display setting: the running engine is kept.
    assert env.running()[p["id"]] is engine
    assert env.stopped == []

    # An update that leaves it out keeps it; false shows the notice again.
    resp = await env.client.put("/profiles/alpha", json={"debounce_seconds": 7})
    assert resp.json()["mirror_notice_dismissed"] is True
    resp = await env.client.put("/profiles/alpha", json={"mirror_notice_dismissed": False})
    assert resp.json()["mirror_notice_dismissed"] is False


async def test_update_of_disabled_profile_starts_nothing(env):
    await env.create("Alpha", env.folder("a"))
    await env.client.post("/profiles/alpha/disable")
    await env.client.put("/profiles/alpha", json={"name": "Beta"})
    assert env.running() == {}


async def test_enabling_twice_runs_one_engine(env):
    p = await env.create("Alpha", env.folder("a"))
    await env.client.post("/profiles/alpha/disable")
    env.started.clear()

    first, second = await asyncio.gather(
        env.client.post("/profiles/alpha/enable"),
        env.client.post("/profiles/alpha/enable"),
    )
    assert first.status_code == second.status_code == 200
    await env.client.post("/profiles/alpha/enable")

    assert len(env.started) == 1
    assert list(env.running()) == [p["id"]]


async def test_manager_create_is_idempotent_and_replaces_on_change(env):
    cfg = ProfileConfig(profile_id=7, slug="x", name="X", local_dir="/sync/x", remote_dir="gdrive:x")
    first = await env.manager.create_engine(cfg)
    assert await env.manager.create_engine(replace(cfg)) is first
    renamed = await env.manager.create_engine(replace(cfg, slug="y", name="Y"))
    assert renamed is not first and first in env.stopped
    assert env.manager.get_engine("y") is renamed
    with pytest.raises(ProfileNotFoundError):
        env.manager.get_engine("x")
    assert env.manager.engines == {"y": renamed}


async def test_engine_hooks_run_for_every_started_engine(env):
    seen: list[int] = []
    env.manager.on_engine_started(lambda engine: seen.append(engine.profile_id))
    p = await env.create("Alpha", env.folder("a"))
    await env.client.put("/profiles/alpha", json={"name": "Beta"})
    assert seen == [p["id"], p["id"]]


# --- delete with history ---


async def test_delete_profile_with_jobs_changes_and_errors(env):
    p = await env.create("Alpha", env.folder("a"))
    other = await env.create("Other", env.folder("o"))
    now = datetime.now(timezone.utc)
    async with env.factory() as session:
        for pid in (p["id"], other["id"]):
            job = SyncJob(profile_id=pid, direction="push", started_at=now, status="failed",
                          files_changed=1, conflicts=1, errors=1)
            session.add(job)
            await session.flush()
            session.add_all([
                FileChange(job_id=job.id, file_path="a.txt", action="modified"),
                SyncError(job_id=job.id, message="boom", retry_count=2, created_at=now),
                Conflict(profile_id=pid, job_id=job.id, file_path="c.txt", resolved=False),
                ManualFlag(profile_id=pid, file_path="m.txt", created_at=now),
            ])
        await session.commit()

    resp = await env.client.delete("/profiles/alpha", params={"confirm": "true"})
    assert resp.status_code == 204

    async with env.factory() as session:
        for model in (SyncJob, FileChange, SyncError, Conflict, ManualFlag):
            count = (await session.execute(select(func.count()).select_from(model))).scalar_one()
            assert count == 1, model.__tablename__  # only the other profile's row
        assert (await session.execute(select(SyncProfile.slug))).scalars().all() == ["other"]


# --- manual flags per profile ---


def _engine(env: Env, profile_id: int, slug: str) -> SyncEngine:
    cfg = ProfileConfig(profile_id=profile_id, slug=slug, name=slug, local_dir="/sync/" + slug,
                        remote_dir="gdrive:" + slug)
    return SyncEngine(cfg, AsyncMock(), env.factory)


async def test_manual_flags_are_scoped_to_their_profile(env):
    a = await env.create("A", env.folder("a"))
    b = await env.create("B", env.folder("b"))
    engine_a, engine_b = _engine(env, a["id"], "a"), _engine(env, b["id"], "b")

    # Profile A flags notes.txt through a per-file action.
    engine_a._state.cached_diff = DiffResponse(
        files=[FileDiff(path="notes.txt", category=ChangeCategory.LOCAL_ONLY)],
        summary=DiffSummary(local_only=1, total=1),
    )
    result = await engine_a.selective_sync([SelectiveSyncItem(path="notes.txt", action=FileAction.MANUAL)])
    assert result.succeeded == 1

    async with env.factory() as session:
        flags = (await session.execute(select(ManualFlag.profile_id, ManualFlag.file_path))).all()
    assert flags == [(a["id"], "notes.txt")]

    assert await engine_a.get_manual_flags() == ["notes.txt"]
    assert await engine_b.get_manual_flags() == []  # B's bulk sync does not exclude it
    assert await engine_b.clear_manual_flag("notes.txt") is False  # and B cannot clear it
    assert await engine_a.get_manual_flags() == ["notes.txt"]

    # B may flag the same relative path for itself.
    engine_b._state.cached_diff = DiffResponse(
        files=[FileDiff(path="notes.txt", category=ChangeCategory.LOCAL_ONLY)],
        summary=DiffSummary(local_only=1, total=1),
    )
    await engine_b.selective_sync([SelectiveSyncItem(path="notes.txt", action=FileAction.MANUAL)])
    assert await engine_b.get_manual_flags() == ["notes.txt"]

    assert await engine_a.clear_manual_flag("notes.txt") is True
    assert await engine_a.get_manual_flags() == []
    assert await engine_b.get_manual_flags() == ["notes.txt"]


async def test_manual_flag_routes_use_the_profile_engine(env):
    a = await env.create("A", env.folder("a"))
    await env.create("B", env.folder("b"))
    async with env.factory() as session:
        session.add(ManualFlag(profile_id=a["id"], file_path="x/y.txt", created_at=datetime.now(timezone.utc)))
        await session.commit()

    assert (await env.client.get("/profiles/a/manual-flags")).json() == {"flags": ["x/y.txt"]}
    assert (await env.client.get("/profiles/b/manual-flags")).json() == {"flags": []}
    assert (await env.client.delete("/profiles/b/manual-flags/x/y.txt")).status_code == 404
    assert (await env.client.delete("/profiles/a/manual-flags/x/y.txt")).status_code == 204


# --- overlapping folders ---


@pytest.mark.parametrize(("a", "b", "overlap"), [
    ("/data/a", "/data/a", True),
    ("/data/a", "/data/a/", True),
    ("/data/a//", "/data/./a", True),
    ("/data/a", "/data/a/sub", True),
    ("/data/a/sub/", "/data", True),
    ("/data/a", "/data/ab", False),
    ("/data/a", "/data/b/../a/x", True),
    ("/", "/anything", True),
    ("gdrive:Photos", "gdrive:Photos/", True),
    ("gdrive:Photos", "gdrive:/Photos/2024", True),
    ("gdrive:", "gdrive:Photos", True),
    ("gdrive:Photos", "gdrive:Photos2", False),
    ("gdrive:Photos", "onedrive:Photos", False),
    ("gdrive:Photos", "/Photos", False),
])
def test_locations_overlap(a, b, overlap):
    assert locations_overlap(a, b) is overlap
    assert locations_overlap(b, a) is overlap


def test_local_paths_resolve_symlinks(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    os.symlink(real, link)
    assert normalize_location(str(link) + "/") == (None, str(real.resolve()))
    assert locations_overlap(str(link), str(real / "sub"))


@pytest.mark.parametrize("variant", ["same/", "same/nested", "parent"])
async def test_nested_local_dirs_are_rejected(env, variant):
    base = env.folder("parent/same")
    await env.create("First", base)
    candidate = {
        "same/": base + "/",
        "same/nested": env.folder("parent/same/nested"),
        "parent": str(env.root / "parent"),
    }[variant]
    resp = await env.client.post("/profiles", json={
        "name": "Second", "local_dir": candidate, "remote_dir": "gdrive:second",
    })
    assert resp.status_code == 409
    assert "first" in resp.json()["detail"]


async def test_sibling_with_common_prefix_is_allowed(env):
    await env.create("First", env.folder("data/a"))
    await env.create("Second", env.folder("data/ab"))


async def test_nested_remote_dirs_are_rejected(env):
    await env.create("First", env.folder("one"), "gdrive:Photos")
    resp = await env.client.post("/profiles", json={
        "name": "Second", "local_dir": env.folder("two"), "remote_dir": "gdrive:Photos/2024/",
    })
    assert resp.status_code == 409
    assert "remote_dir" in resp.json()["detail"]


async def test_update_into_another_profiles_folder_is_rejected(env):
    await env.create("First", env.folder("one"))
    await env.create("Second", env.folder("two"))
    resp = await env.client.put("/profiles/second", json={"local_dir": env.folder("one/inner")})
    assert resp.status_code == 409


async def test_enabling_an_overlapping_disabled_profile_is_rejected(env):
    await env.create("First", env.folder("one"))
    await env.client.post("/profiles/first/disable")
    await env.create("Second", env.folder("one/inner"))
    resp = await env.client.post("/profiles/first/enable")
    assert resp.status_code == 409
    assert env.manager.get_engine_by_id(1) is None


async def test_profile_folder_containing_a_backup_target_is_rejected(env):
    p = await env.create("First", env.folder("one"))
    now = datetime.now(timezone.utc)
    async with env.factory() as session:
        session.add(BackupTarget(
            profile_id=p["id"], name="nightly", target_path=env.folder("vault/nightly"),
            target_type="local", retention_days=7, frequency_hours=24, backup_mode="mirror",
            enabled=True, created_at=now, updated_at=now,
        ))
        await session.commit()
    with pytest.raises(ProfileConflictError, match="nightly"):
        await env.service.create(ProfileCreateRequest(
            name="Second", local_dir=env.folder("vault"), remote_dir="gdrive:second",
        ))


@pytest.mark.parametrize(("target", "ok"), [
    ("{a}", False),
    ("{a}/", False),
    ("{a}/backups", False),     # inside the synced folder
    ("{root}", False),          # contains the synced folder
    ("{b}/backups", False),     # inside another profile's folder
    ("gdrive:A/versions", False),
    ("gdrive:A-backups", True),
    ("{root}/vault", True),
])
async def test_backup_target_overlap(env, target, ok):
    a = env.folder("tree/a")
    b = env.folder("tree/b")
    await env.create("A", a, "gdrive:A")
    await env.create("B", b, "gdrive:B")
    path = target.format(a=a, b=b, root=str(env.root / "tree"))
    resp = await env.client.post("/profiles/a/backups", json={
        "name": "t", "target_path": path,
        "target_type": "remote" if ":" in path and not path.startswith("/") else "local",
    })
    assert (resp.status_code == 201) is ok, resp.text
    if not ok:
        assert resp.status_code == 400


async def test_backup_target_update_is_checked_too(env):
    a = env.folder("a")
    await env.create("A", a)
    created = await env.client.post("/profiles/a/backups", json={
        "name": "t", "target_path": env.folder("vault"), "target_type": "local",
    })
    assert created.status_code == 201
    resp = await env.client.put(
        f"/profiles/a/backups/{created.json()['id']}", json={"target_path": a + "/.backups/"},
    )
    assert resp.status_code == 400


# --- validation ---


@pytest.mark.parametrize("override", [
    {"debounce_seconds": 0},
    {"debounce_seconds": -5},
    {"debounce_seconds": 3601},
    {"pull_interval_minutes": 0},
    {"pull_interval_minutes": 10081},
    {"max_retries": 0},
    {"max_retries": 11},
    {"name": ""},
    {"name": "!!!"},
    {"name": "   "},
    {"name": "äöü"},
])
async def test_invalid_profile_settings_are_rejected(env, override):
    body = {"name": "Good", "local_dir": env.folder("g"), "remote_dir": "gdrive:g", **override}
    assert (await env.client.post("/profiles", json=body)).status_code == 422

    await env.create("Existing", env.folder("e"))
    assert (await env.client.put("/profiles/existing", json=override)).status_code == 422


async def test_profile_settings_limits_are_inclusive(env):
    resp = await env.client.post("/profiles", json={
        "name": "Edge", "local_dir": env.folder("edge"), "remote_dir": "gdrive:edge",
        "debounce_seconds": 1, "pull_interval_minutes": 1, "max_retries": 1,
    })
    assert resp.status_code == 201
    resp = await env.client.put("/profiles/edge", json={
        "debounce_seconds": 3600, "pull_interval_minutes": 10080, "max_retries": 10,
    })
    assert resp.status_code == 200


@pytest.mark.parametrize("snapshot_id", ["../etc", "..", "a/b", "a\\b", "/abs", "x/../../y", "", "."])
async def test_restore_rejects_path_like_snapshot_ids(env, snapshot_id):
    p = await env.create("A", env.folder("a"))
    now = datetime.now(timezone.utc)
    async with env.factory() as session:
        target = BackupTarget(
            profile_id=p["id"], name="t", target_path=env.folder("vault"), target_type="local",
            retention_days=7, frequency_hours=24, backup_mode="mirror", enabled=True,
            created_at=now, updated_at=now,
        )
        session.add(target)
        await session.commit()
        target_id = target.id
    resp = await env.client.post(f"/profiles/a/backups/{target_id}/restore", json={
        "snapshot_id": snapshot_id, "restore_scope": "local_only",
    })
    assert resp.status_code == 422
    backups._backup_service.restore.assert_not_called()


@pytest.fixture
def backend_logger_level():
    logger = logging.getLogger("backend")
    level = logger.level
    yield logger
    logger.setLevel(level)


async def test_log_level_is_validated_and_applied(tmp_path, backend_logger_level):
    config_routes.set_config_service(ConfigService(tmp_path / "config.toml"))
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers=AUTH_HEADERS,
        ) as client:
            assert (await client.put("/config", json={"log_level": "LOUD"})).status_code == 422
            resp = await client.put("/config", json={"log_level": "DEBUG"})
            assert resp.status_code == 200 and resp.json()["log_level"] == "DEBUG"
            assert backend_logger_level.level == logging.DEBUG
            await client.put("/config", json={"log_level": "WARNING"})
            assert backend_logger_level.level == logging.WARNING
    finally:
        config_routes.set_config_service(None)


def test_unknown_configured_log_level_falls_back_to_info(backend_logger_level):
    from backend.services.config import apply_log_level

    apply_log_level("chatty")
    assert backend_logger_level.level == logging.INFO
    apply_log_level("error")
    assert backend_logger_level.level == logging.ERROR


# --- Profiles router: confirmation and unknown slugs (multi-sync-profiles 12.2) ---


@pytest.mark.parametrize("params", [{}, {"confirm": "false"}])
async def test_delete_without_confirm_is_refused(env, params):
    p = await env.create("Alpha", env.folder("a"))
    engine = env.running()[p["id"]]

    resp = await env.client.delete("/profiles/alpha", params=params)

    assert resp.status_code == 400
    assert "confirm=true" in resp.json()["detail"]
    # Nothing was deleted or stopped.
    assert (await env.service.get_by_slug("alpha")).id == p["id"]
    assert env.running()[p["id"]] is engine
    assert env.stopped == []
    assert (await env.client.get("/profiles/alpha")).status_code == 200


async def test_get_unknown_profile_is_404(env):
    await env.create("Alpha", env.folder("a"))
    resp = await env.client.get("/profiles/nope")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Profile 'nope' not found"


async def test_delete_unknown_profile_with_confirm_is_404(env):
    resp = await env.client.delete("/profiles/nope", params={"confirm": "true"})
    assert resp.status_code == 404
