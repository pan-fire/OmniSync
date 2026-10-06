"""Error answers and partial outcomes of the /profiles routes.

The routes are wired to the mock services of conftest.py (test_client,
test_services) with a real database, so a race (a profile deleted between
two calls), an engine that fails to stop or a sync holding the lock can be
produced on demand. Each case checks the status and error code, and that
nothing else changed: no engine started, no file moved, no row written.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.routes import profiles as profiles_router
from backend.db.models import SyncJob, SyncProfile
from backend.exceptions import (
    IntervalsNotResumableError,
    ProfileConflictError,
    ProfileNotFoundError,
    RcloneAuthError,
    RcloneError,
)
from backend.main import app
from backend.services.rclone import TRASH_DIR

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
LEAK = "stderr: token=secret-value /home/someone/.config"


def row(**changes) -> SimpleNamespace:
    """A stored profile as ProfileService returns it."""
    values = dict(
        id=1, slug="default", name="Default", local_dir="/srv/docs", remote_dir="remote:backup",
        debounce_seconds=5, pull_interval_minutes=5, rclone_filter="[]", rclone_args="[]", max_retries=3,
        enabled=True, created_at=NOW, updated_at=NOW, sync_mode="mirror", mirror_notice_dismissed=False,
        bwlimit=None, sync_window=None, user_paused=False,
    )
    values.update(changes)
    return SimpleNamespace(**values)


def assert_error(resp, status: int, code: str) -> None:
    assert resp.status_code == status, resp.text
    assert resp.json()["code"] == code
    assert "secret-value" not in resp.text and "/home/someone" not in resp.text


@pytest.fixture
def svc(test_services):
    return test_services.profile_service


@pytest.fixture
def manager(test_services):
    return test_services.manager


def by_slug(*profiles: SimpleNamespace):
    known = {p.slug: p for p in profiles}

    async def get(slug: str):
        if slug not in known:
            raise ProfileNotFoundError(slug)
        return known[slug]

    return AsyncMock(side_effect=get)


# --- services not wired ---


async def test_routes_answer_503_before_startup(test_client):
    """Without the manager, profile service or rclone the routes say so instead of crashing."""
    profiles_router.set_manager(None)
    assert_error(await test_client.get("/profiles"), 503, "service_unavailable")
    profiles_router.set_profile_service(None)
    assert_error(await test_client.get("/profiles/default/jobs"), 503, "service_unavailable")
    profiles_router.set_rclone_service(None)
    assert_error(await test_client.post("/profiles/default/config/test-sync"), 503, "service_unavailable")


async def test_trash_action_without_rclone_is_503(test_client, svc, tmp_path):
    svc.get_by_slug = by_slug(row(local_dir=str(tmp_path)))
    profiles_router.set_rclone_service(None)
    assert_error(await test_client.post("/profiles/default/trash/delete", json={"side": "local", "ids": ["a/b"]}),
                 503, "service_unavailable")


async def test_trash_listing_without_rclone_is_503(test_client, svc, tmp_path, caplog):
    """Same answer as the trash actions: rclone not available is 503, not a failed rclone call
    (and it is not logged as a crash of the listing)."""
    svc.get_by_slug = by_slug(row(local_dir=str(tmp_path)))
    profiles_router.set_rclone_service(None)
    with caplog.at_level(logging.ERROR, logger="backend.api.routes.profiles"):
        assert_error(await test_client.get("/profiles/default/trash"), 503, "service_unavailable")
    assert "Listing the local trash" not in caplog.text


# --- reading ---


async def test_unreadable_stored_sync_window_is_ignored(test_client, svc, caplog):
    """A stored window that no longer validates shows as none (logged), not as a 500 for the whole profile."""
    svc.get_by_slug = by_slug(row(sync_window='{"start": "25:99"}'))
    with caplog.at_level(logging.WARNING, logger="backend.api.routes.profiles"):
        resp = await test_client.get("/profiles/default")
    assert resp.status_code == 200 and resp.json()["sync_window"] is None
    assert "unreadable stored sync window" in caplog.text


@pytest.mark.parametrize("path", [
    "/profiles/nope", "/profiles/nope/jobs", "/profiles/nope/conflicts", "/profiles/nope/trash",
])
async def test_unknown_profile_is_404(test_client, path):
    assert_error(await test_client.get(path), 404, "profile_not_found")


async def test_conflicts_of_a_known_profile_without_any(test_client):
    """The 404 above is about the profile, not about an empty list: a known profile answers []."""
    resp = await test_client.get("/profiles/default/conflicts")
    assert resp.status_code == 200 and resp.json() == []


async def test_unknown_profile_sync_test_is_404(test_client, test_services):
    assert_error(await test_client.post("/profiles/nope/config/test-sync"), 404, "profile_not_found")
    test_services.rclone.test_sync.assert_not_awaited()


async def test_jobs_are_listed_newest_first_for_this_profile_only(test_client, test_db_factory):
    """The job history pages through this profile's jobs; another profile's never shows up."""
    async with test_db_factory() as session:
        for pid, slug in ((1, "default"), (2, "other")):
            session.add(SyncProfile(id=pid, slug=slug, name=slug, local_dir=f"/{slug}", remote_dir=f"r:{slug}",
                                    created_at=NOW, updated_at=NOW))
        for minutes, pid in ((1, 1), (2, 2), (3, 1), (4, 1)):
            session.add(SyncJob(profile_id=pid, direction="push", status="completed",
                                started_at=NOW + timedelta(minutes=minutes), files_changed=minutes))
        await session.commit()
    first = (await test_client.get("/profiles/default/jobs", params={"limit": 2})).json()
    assert [j["files_changed"] for j in first] == [4, 3]
    assert {j["profile_slug"] for j in first} == {"default"} and first[0]["profile_name"] == "Default"
    rest = (await test_client.get("/profiles/default/jobs", params={"skip": 2})).json()
    assert [j["files_changed"] for j in rest] == [1]
    assert (await test_client.get("/profiles/default/jobs", params={"limit": 0})).status_code == 422


async def test_selective_result_falls_back_to_the_job_record(test_client, test_services, test_db_factory):
    """After a restart the per-file result comes from the job row; another job kind or profile is 404."""
    engine = test_services.manager.engines["default"]
    engine.selective_result = MagicMock(return_value=None)
    engine.profile_id = 1
    async with test_db_factory() as session:
        jobs = [SyncJob(profile_id=1, direction="selective", status="failed", started_at=NOW, errors=2),
                SyncJob(profile_id=1, direction="push", status="completed", started_at=NOW),
                SyncJob(profile_id=2, direction="selective", status="completed", started_at=NOW)]
        session.add_all(jobs)
        await session.commit()
        ids = [j.id for j in jobs]
    resp = await test_client.get(f"/profiles/default/sync/selective/{ids[0]}")
    assert resp.status_code == 200
    assert resp.json()["job_id"] == ids[0] and resp.json()["status"] == "failed" and resp.json()["failed"] == 2
    for job_id in (ids[1], ids[2], 999):
        assert_error(await test_client.get(f"/profiles/default/sync/selective/{job_id}"), 404, "job_not_found")


# --- pause-all / resume-all ---


async def test_pause_all_pauses_profiles_without_a_running_engine(test_client, svc, manager):
    """A profile whose engine is not running is paused in the database; disabled ones are left alone."""
    svc.get_all = AsyncMock(return_value=[
        row(), row(id=2, slug="idle"), row(id=3, slug="held", user_paused=True),
        row(id=4, slug="off", enabled=False),
    ])
    svc.set_user_paused = AsyncMock()
    manager.engines["default"].pause_by_user = AsyncMock(return_value=True)
    resp = await test_client.post("/profiles/pause-all")
    assert resp.status_code == 200
    assert resp.json()["changed"] == ["default", "idle"] and resp.json()["unchanged"] == ["held"]
    assert [c.args for c in svc.set_user_paused.await_args_list] == [(2, True), (3, True)]


async def test_resume_all_reports_what_stays_paused(test_client, svc, manager):
    """Only the user's pause lifts; a profile that still needs review is listed with the reason."""
    def engine(paused: bool, resume: AsyncMock) -> SimpleNamespace:
        return SimpleNamespace(_state=SimpleNamespace(user_paused=paused), resume_user_pause=resume)

    blocked = engine(True, AsyncMock(side_effect=IntervalsNotResumableError(3)))
    partly = engine(True, AsyncMock(return_value="Paused: a resync is needed."))
    running = engine(False, AsyncMock())
    engines = {1: running, 5: blocked, 6: partly}
    manager.get_engine_by_id = MagicMock(side_effect=engines.get)
    svc.get_all = AsyncMock(return_value=[
        row(), row(id=2, slug="stopped-paused", user_paused=True), row(id=3, slug="stopped"),
        row(id=5, slug="blocked"), row(id=6, slug="partly"),
    ])
    svc.set_user_paused = AsyncMock()
    body = (await test_client.post("/profiles/resume-all")).json()
    assert body["changed"] == ["stopped-paused", "partly"]
    assert body["unchanged"] == ["default"]
    assert body["still_paused"] == {"blocked": "Cannot resume: 3 unresolved differences remain.",
                                    "partly": "Paused: a resync is needed."}
    svc.set_user_paused.assert_awaited_once_with(2, False)
    running.resume_user_pause.assert_not_awaited()


# --- changes that race or partly fail ---


async def test_update_of_a_profile_deleted_meanwhile_is_404(test_client, svc, manager):
    svc.get_by_slug = by_slug(row())
    svc.update = AsyncMock(side_effect=ProfileNotFoundError("default"))
    assert_error(await test_client.put("/profiles/default", json={"name": "Renamed"}), 404, "profile_not_found")
    manager.create_engine.assert_not_awaited()


async def test_delete_goes_ahead_when_the_engine_fails_to_stop(test_client, svc, manager, caplog):
    """A stuck engine must not keep a profile the user deleted; the error is logged."""
    svc.delete = AsyncMock()
    manager.remove_engine = AsyncMock(side_effect=RuntimeError(LEAK))
    with caplog.at_level(logging.WARNING, logger="backend.api.routes.profiles"):
        resp = await test_client.delete("/profiles/default", params={"confirm": "true"})
    assert resp.status_code == 204
    svc.delete.assert_awaited_once_with("default")
    assert "Error stopping engine of deleted profile 'default'" in caplog.text


async def test_delete_of_a_profile_deleted_meanwhile_is_404(test_client, svc, manager):
    svc.delete = AsyncMock(side_effect=ProfileNotFoundError("default"))
    assert_error(await test_client.delete("/profiles/default", params={"confirm": "true"}), 404, "profile_not_found")


async def test_enable_into_an_overlapping_folder_is_409_and_starts_nothing(test_client, svc, manager):
    svc.set_enabled = AsyncMock(side_effect=ProfileConflictError("/srv/docs", "other"))
    assert_error(await test_client.post("/profiles/default/enable"), 409, "profile_conflict")
    manager.create_engine.assert_not_awaited()


async def test_enable_unknown_profile_is_404(test_client, svc, manager):
    svc.set_enabled = AsyncMock(side_effect=ProfileNotFoundError("nope"))
    assert_error(await test_client.post("/profiles/nope/enable"), 404, "profile_not_found")
    manager.create_engine.assert_not_awaited()


async def test_disable_unknown_profile_is_404(test_client, svc, manager):
    svc.set_enabled = AsyncMock(side_effect=ProfileNotFoundError("nope"))
    assert_error(await test_client.post("/profiles/nope/disable"), 404, "profile_not_found")
    manager.remove_engine.assert_not_awaited()


async def test_disable_is_stored_even_when_the_engine_fails_to_stop(test_client, svc, manager, caplog):
    svc.set_enabled = AsyncMock(return_value=row(enabled=False))
    manager.remove_engine = AsyncMock(side_effect=RuntimeError(LEAK))
    with caplog.at_level(logging.WARNING, logger="backend.api.routes.profiles"):
        resp = await test_client.post("/profiles/default/disable")
    assert resp.status_code == 200 and resp.json()["enabled"] is False
    assert "Error stopping engine of disabled profile 'default'" in caplog.text


# --- trash ---


@pytest.fixture
def trash_profile(svc, manager, tmp_path: Path) -> Path:
    """Profile 'default' on a real local folder with one trashed file; the sync lock is real."""
    local = tmp_path / "local"
    trashed = local / TRASH_DIR / "2026-01-02T03-04-05Z" / "a.txt"
    trashed.parent.mkdir(parents=True)
    trashed.write_text("only copy")
    old = time.time() - 3600
    os.utime(trashed, (old, old))
    svc.get_by_slug = by_slug(row(local_dir=str(local)))
    lock = asyncio.Lock()
    manager.sync_lock = MagicMock(return_value=lock)
    return local


async def test_trash_actions_wait_while_a_sync_runs(test_client, manager, trash_profile):
    """A sync may be moving files into the trash: restore/delete are refused (409), the file stays."""
    async with manager.sync_lock(1):
        for action in ("restore", "delete"):
            resp = await test_client.post(f"/profiles/default/trash/{action}",
                                          json={"side": "local", "ids": ["2026-01-02T03-04-05Z/a.txt"]})
            assert_error(resp, 409, "sync_busy")
    assert (trash_profile / TRASH_DIR / "2026-01-02T03-04-05Z" / "a.txt").read_text() == "only copy"
    assert not (trash_profile / "a.txt").exists()


async def test_remote_trash_listing_failure_is_502_without_details(test_client, test_services, trash_profile, caplog):
    test_services.rclone.lsjson = AsyncMock(side_effect=RcloneError(LEAK))
    with caplog.at_level(logging.ERROR, logger="backend.api.routes.profiles"):
        resp = await test_client.get("/profiles/default/trash", params={"side": "remote"})
    assert_error(resp, 502, "rclone_failed")
    assert "Listing the remote trash of 'default' failed" in caplog.text


async def test_remote_sign_in_failure_stops_a_trash_action_with_502(test_client, test_services, trash_profile):
    """An expired sign-in is not reported per file: the whole action fails and nothing is deleted."""
    test_services.rclone.lsjson_paths = AsyncMock(side_effect=RcloneAuthError(LEAK))
    test_services.rclone.delete_file = AsyncMock()
    resp = await test_client.post("/profiles/default/trash/delete",
                                  json={"side": "remote", "ids": ["2026-01-02T03-04-05Z/a.txt"]})
    assert_error(resp, 502, "rclone_failed")
    test_services.rclone.delete_file.assert_not_awaited()


async def test_local_trash_restore_through_the_route(test_client, manager, trash_profile):
    """The happy path through the route for contrast: the file is back, the lock is free again."""
    resp = await test_client.post("/profiles/default/trash/restore",
                                  json={"side": "local", "ids": ["2026-01-02T03-04-05Z/a.txt"]})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"done": ["2026-01-02T03-04-05Z/a.txt"], "failed": []}
    assert (trash_profile / "a.txt").read_text() == "only copy"
    assert not manager.sync_lock(1).locked()


@pytest.mark.parametrize("body", [
    {"side": "local", "ids": []}, {"side": "sideways", "ids": ["a/b"]}, {"side": "local", "ids": [""]},
    {"side": "local", "ids": ["a/b"] * 1001},
])
async def test_malformed_trash_requests_are_422(test_client, trash_profile, body):
    resp = await test_client.post("/profiles/default/trash/delete", json=body)
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_failed"
    assert (trash_profile / TRASH_DIR / "2026-01-02T03-04-05Z" / "a.txt").exists()


# --- authentication ---


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/profiles"), ("POST", "/profiles/pause-all"), ("DELETE", "/profiles/default?confirm=true"),
    ("POST", "/profiles/default/trash/delete"), ("GET", "/profiles/default/jobs"),
])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong-token"}])
async def test_profile_routes_need_the_token(test_client, svc, manager, method, path, headers):
    """Without the right token nothing is read, paused or deleted."""
    svc.delete = AsyncMock()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as anon:
        resp = await anon.request(method, path, json={"side": "local", "ids": ["a/b"]} if method == "POST" else None)
    assert resp.status_code == 401
    svc.delete.assert_not_awaited()
    manager.remove_engine.assert_not_awaited()
