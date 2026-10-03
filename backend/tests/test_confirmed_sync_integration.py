"""A confirmed (forced) push or pull of a paused profile, with real rclone.

Any diff pauses a profile that has differences, so the clients send
force=true once the user has confirmed a push or pull. force only lifts the
pause guard of POST /profiles/{slug}/sync/start: every safety check of the
sync itself (sync marker, empty source, delete limit, trash) still runs.

Same pattern as test_sync_safety_integration.py: the "remote" is an rclone
remote of type `local`, the database a real migrated SQLite file.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
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
from backend.services.sync_engine import SyncEngine
from backend.tests.auth import AUTH_HEADERS
from backend.tests.sync_jobs import finished

if shutil.which("rclone") is None and os.environ.get("OMNISYNC_REQUIRE_RCLONE") == "1":
    raise RuntimeError("OMNISYNC_REQUIRE_RCLONE=1 but rclone is not on PATH")
pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")


def files_under(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


def write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@dataclass
class Env:
    engine: SyncEngine
    local: Path
    remote: Path
    factory: object
    client: AsyncClient

    async def start(self, direction: str, force: bool):
        body = {"direction": direction, **({"force": True} if force else {})}
        return await self.client.post("/profiles/docs/sync/start", json=body)

    async def jobs(self) -> list[SyncJob]:
        async with self.factory() as session:
            return list((await session.execute(select(SyncJob).order_by(SyncJob.id))).scalars().all())


@pytest_asyncio.fixture
async def env(tmp_path: Path):
    local, remote = tmp_path / "local", tmp_path / "remote"
    local.mkdir()
    remote.mkdir()
    conf = tmp_path / "rclone.conf"
    conf.write_text("[testremote]\ntype = local\n")

    saved = database._engine, database._async_session_factory
    database._engine = None
    await init_database(str(tmp_path / "confirmed.db"))
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
    # Paused, as any diff with differences leaves it.
    engine._state.set_paused()
    engine._state.pending_changes = 3

    profile_routes.set_manager(SimpleNamespace(  # type: ignore[arg-type]
        get_engine_by_id=lambda pid: engine if pid == profile_id else None,
    ))
    profile_routes.set_profile_service(ProfileService(factory))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH_HEADERS) as client:
        yield Env(engine, local, remote, factory, client)

    profile_routes.set_manager(None)
    profile_routes.set_profile_service(None)
    await database._engine.dispose()
    database._engine, database._async_session_factory = saved


def mark_both(env: Env) -> None:
    write(env.local, SENTINEL_FILE, "marker")
    write(env.remote, SENTINEL_FILE, "marker")


async def test_paused_profile_needs_the_confirmation(env):
    mark_both(env)
    write(env.local, "a.txt", "new")

    resp = await env.start("push", force=False)

    assert resp.status_code == 409
    assert "paused" in resp.json()["detail"]
    assert "a.txt" not in files_under(env.remote)
    assert await env.jobs() == []


async def test_confirmed_push_of_a_paused_profile_runs(env):
    mark_both(env)
    write(env.local, "a.txt", "new")
    write(env.local, "report.txt", "v2")
    write(env.remote, "report.txt", "v1")

    resp = await env.start("push", force=True)

    assert resp.status_code == 202, resp.text
    assert (await finished(env.client, env.engine, resp))["status"] == "completed"
    after = files_under(env.remote)
    assert after["a.txt"] == "new" and after["report.txt"] == "v2"
    trash = {k.split("/", 2)[2]: v for k, v in after.items() if k.startswith(TRASH_DIR + "/")}
    assert trash == {"report.txt": "v1"}
    assert [j.status for j in await env.jobs()] == ["completed"]
    # Nothing left unresolved: the sync resolved the pause.
    assert not env.engine._state.intervals_paused


async def test_confirmed_push_still_refuses_to_empty_the_other_side(env):
    mark_both(env)
    for i in range(3):
        write(env.remote, f"doc{i}.txt", "keep me")

    resp = await env.start("push", force=True)

    # Refused before anything changed: answered at once, with the reason.
    assert resp.status_code == 200
    assert resp.json()["state"] == SyncState.ERROR.value
    assert "empty" in resp.json()["error"]
    assert {k for k in files_under(env.remote) if k.startswith("doc")} == {"doc0.txt", "doc1.txt", "doc2.txt"}
    assert [j.status for j in await env.jobs()] == ["failed"]
    assert "empty" in (env.engine._state.last_error or "")
    assert env.engine._state.intervals_paused


async def test_confirmed_pull_still_checks_the_sync_marker(env):
    write(env.local, SENTINEL_FILE, "marker")  # the remote folder lost its marker
    write(env.local, "thesis.docx", "years of work")
    write(env.remote, "other.txt", "x")

    resp = await env.start("pull", force=True)

    assert resp.status_code == 200
    assert SENTINEL_FILE in resp.json()["error"]
    assert files_under(env.local)["thesis.docx"] == "years of work"
    assert SENTINEL_FILE in (env.engine._state.last_error or "")


async def test_confirmed_push_still_stops_at_the_delete_limit(env, monkeypatch):
    mark_both(env)
    write(env.local, "kept.txt", "x")
    for i in range(6):
        write(env.remote, f"old{i}.txt", "precious")
    monkeypatch.setattr("backend.services.sync_engine.safety.DEFAULT_MAX_DELETE", 2)

    resp = await env.start("push", force=True)

    # rclone's --max-delete stops the running push: recorded on the job.
    assert resp.status_code == 202
    assert (await finished(env.client, env.engine, resp))["status"] == "failed"
    after = files_under(env.remote)
    remaining = [f for f in after if f.startswith("old")]
    assert len(remaining) >= 4, remaining
    trashed = [f for f in after if f.startswith(TRASH_DIR)]
    assert len(trashed) == 6 - len(remaining)
    assert "would delete more than" in (env.engine._state.last_error or "")
