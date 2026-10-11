"""Profile status fields, job profile names, and the side-effect-free sync preview.

* GET /profiles and GET /profiles/{slug} carry last_error and the effective
  delete limit (max_delete).
* GET /jobs and GET /jobs/{id} name the job's profile.
* POST /profiles/{slug}/sync/preview counts what a push and a pull would do
  and changes nothing (unlike /sync/check).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from backend.api.schemas import (
    ChangeCategory,
    DiffResponse,
    DiffSummary,
    FileDiff,
    SyncPreviewCounts,
)
from backend.db.models import ManualFlag, SyncJob
from backend.services import sync_engine as sync_engine_module
from backend.services.sync_engine import effective_max_delete
from backend.tests import test_profiles
from backend.tests.test_profiles import Env

# The real-database profile environment of test_profiles.py.
env = test_profiles.env


# --- effective delete limit ---


@pytest.mark.parametrize(("args", "expected"), [
    ([], 50),
    (["--transfers", "4"], 50),
    (["--max-delete", "10"], 10),
    (["--max-delete=7"], 7),
    (["--max-delete", "0"], 0),
    (["--max-delete", "10", "--max-delete=3"], 3),  # rclone takes the last one
    (["--max-delete=-1"], None),  # rclone: no limit
    (["--max-delete", "lots"], None),
    (["--max-delete"], None),
    (["5", "--max-delete"], None),  # the value follows the flag, never precedes it
    (["--max-delete", "7", "--transfers"], 7),
    (["--max-delete=5=6"], None),  # not a number rclone would accept
])
def test_effective_max_delete(monkeypatch, args, expected):
    monkeypatch.setattr("backend.services.sync_engine.safety.DEFAULT_MAX_DELETE", 50)
    assert effective_max_delete(args) == expected


def test_a_default_of_zero_allows_no_deletion(monkeypatch):
    """OMNISYNC_MAX_DELETE=0 is a limit of nothing, not "no limit"."""
    monkeypatch.setattr("backend.services.sync_engine.safety.DEFAULT_MAX_DELETE", 0)
    assert effective_max_delete([]) == 0


def test_negative_default_means_no_limit(monkeypatch):
    monkeypatch.setattr("backend.services.sync_engine.safety.DEFAULT_MAX_DELETE", -1)
    assert effective_max_delete([]) is None


# --- profile status ---


async def test_profile_status_carries_last_error_and_max_delete(env: Env, monkeypatch):
    monkeypatch.setattr("backend.services.sync_engine.safety.DEFAULT_MAX_DELETE", 50)
    a = await env.create("A", env.folder("a"))
    resp = await env.client.put("/profiles/a", json={"rclone_args": ["--max-delete", "12"]})
    assert resp.status_code == 200, resp.text
    await env.create("B", env.folder("b"))
    env.running()[a["id"]]._state.set_error("Local folder '/x' is missing or not mounted.")

    one = (await env.client.get("/profiles/a")).json()
    assert one["last_error"] == "Local folder '/x' is missing or not mounted."
    assert one["max_delete"] == 12

    listed = {p["slug"]: p for p in (await env.client.get("/profiles")).json()}
    assert listed["a"]["last_error"] == one["last_error"]
    assert listed["a"]["max_delete"] == 12
    assert listed["b"]["last_error"] is None
    assert listed["b"]["max_delete"] == 50

    # A disabled profile has no engine, but its delete limit is still known.
    await env.client.post("/profiles/b/disable")
    b = (await env.client.get("/profiles/b")).json()
    assert b["max_delete"] == 50 and b["last_error"] is None


# --- jobs name their profile ---


async def test_jobs_carry_the_profile_slug_and_name(env: Env):
    a = await env.create("Alpha Docs", env.folder("alpha"))
    now = datetime.now(timezone.utc)
    async with env.factory() as session:
        session.add_all([
            SyncJob(profile_id=a["id"], direction="push", started_at=now, status="completed"),
        ])
        await session.commit()

    jobs = (await env.client.get("/jobs")).json()
    assert [(j["direction"], j["profile_slug"], j["profile_name"]) for j in jobs] == [
        ("push", "alpha-docs", "Alpha Docs"),
    ]
    one = (await env.client.get(f"/jobs/{jobs[0]['id']}")).json()
    assert (one["profile_slug"], one["profile_name"]) == ("alpha-docs", "Alpha Docs")
    assert (await env.client.get("/jobs/9999")).status_code == 404

    filtered = (await env.client.get("/jobs", params={"profile": "alpha-docs"})).json()
    assert [j["id"] for j in filtered] == [jobs[0]["id"]]

    # The name follows a rename.
    await env.client.put("/profiles/alpha-docs", json={"name": "Beta"})
    assert (await env.client.get(f"/jobs/{jobs[0]['id']}")).json()["profile_name"] == "Beta"


# --- the preview changes nothing ---


def _check(local_only=(), remote_only=(), differ=(), error=None) -> dict:
    return {
        "has_changes": bool(local_only or remote_only or differ),
        "local_only": list(local_only), "remote_only": list(remote_only),
        "differ": list(differ), "error": error,
    }


async def test_preview_counts_both_directions_and_changes_nothing(env: Env, monkeypatch):
    monkeypatch.setattr("backend.services.sync_engine.safety.DEFAULT_MAX_DELETE", 2)
    a = await env.create("A", env.folder("a"), mode="mirror")
    engine = env.running()[a["id"]]
    engine._rclone.check_diff = AsyncMock(return_value=_check(
        local_only=["l1", "l2", "flagged-local"],
        remote_only=["r1", "r2", "r3"],
        differ=["d1", "conflict.txt"],
    ))
    async with env.factory() as session:
        session.add(ManualFlag(profile_id=a["id"], file_path="flagged-local", created_at=datetime.now(timezone.utc)))
        await session.commit()
    cached = DiffResponse(
        files=[FileDiff(path="conflict.txt", category=ChangeCategory.MODIFIED_BOTH, is_conflict=True)],
        summary=DiffSummary(modified_both=1, total=1),
    )
    engine._state.cache_diff(cached)
    engine._state.pending_changes = 1
    before = (engine._state.pending_changes, engine._state.intervals_paused, engine._state.paused_at)

    resp = await env.client.post("/profiles/a/sync/preview")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["max_delete"] == 2
    assert body["excluded"] == 2  # the flagged file and the unresolved conflict
    assert body["push"] == SyncPreviewCounts(deletes=3, replaces=1, creates=2, exceeds_max_delete=True).model_dump()
    assert body["pull"] == SyncPreviewCounts(deletes=2, replaces=1, creates=3, exceeds_max_delete=False).model_dump()
    assert body["error"] is None
    # Nothing moved: pending count, cached diff and pause state are as before.
    assert (engine._state.pending_changes, engine._state.intervals_paused, engine._state.paused_at) == before
    assert engine._state.cached_diff is cached

    # /sync/check, by contrast, updates the pending count and pauses.
    await env.client.post("/profiles/a/sync/check")
    assert engine._state.pending_changes == 7 and engine._state.intervals_paused


async def test_preview_reports_a_failed_comparison(env: Env):
    a = await env.create("A", env.folder("a"))
    engine = env.running()[a["id"]]
    engine._rclone.check_diff = AsyncMock(return_value=_check(error="remote unreachable"))

    body = (await env.client.post("/profiles/a/sync/preview")).json()

    assert body["error"] == "remote unreachable"
    assert body["push"]["deletes"] == 0 and body["max_delete"] == sync_engine_module.DEFAULT_MAX_DELETE
    assert not engine._state.intervals_paused


async def test_preview_of_an_unknown_or_stopped_profile_is_404(env: Env):
    await env.create("A", env.folder("a"))
    await env.client.post("/profiles/a/disable")
    assert (await env.client.post("/profiles/a/sync/preview")).status_code == 404
    assert (await env.client.post("/profiles/nope/sync/preview")).status_code == 404


async def test_profile_without_own_limit_uses_no_limit_when_set_to_minus_one(env: Env):
    a = await env.create("A", env.folder("a"))
    await env.client.put("/profiles/a", json={"rclone_args": ["--max-delete=-1"]})
    engine = env.running()[a["id"]]
    engine._rclone.check_diff = AsyncMock(return_value=_check(remote_only=[f"r{i}" for i in range(500)]))

    body = (await env.client.post("/profiles/a/sync/preview")).json()

    assert body["max_delete"] is None
    assert body["push"]["deletes"] == 500 and body["push"]["exceeds_max_delete"] is False
