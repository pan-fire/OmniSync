"""Tests for the conflicts endpoints, through HTTP against seeded rows.

Resolutions that change files run through the profile's engine; see
test_conflict_resolution_integration.py (real rclone) for those.
"""

from __future__ import annotations

from datetime import datetime

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import delete

from backend.db.models import Conflict, SyncJob, SyncProfile

resolutions = st.sampled_from(["keep_local", "keep_remote", "keep_both", "dismiss"])

file_paths = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S"), blacklist_characters="\x00"
    ),
    min_size=1,
    max_size=200,
)

fixture_settings = settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture],
)


async def _seed(factory, *rows) -> list[int]:
    async with factory() as session:
        session.add_all(rows)
        await session.commit()
        return [r.id for r in rows]


async def _clear(factory) -> None:
    async with factory() as session:
        await session.execute(delete(Conflict))
        await session.commit()


# --- Conflicts endpoint returns only unresolved conflicts ---


@fixture_settings
@given(
    resolved=st.lists(st.tuples(file_paths, resolutions), max_size=8),
    unresolved=st.lists(file_paths, max_size=8),
)
async def test_conflicts_returns_only_unresolved(
    test_client, test_db_factory, resolved: list[tuple[str, str]], unresolved: list[str],
) -> None:
    """Conflicts endpoint returns only unresolved conflicts.

    GET /conflicts lists exactly the unresolved conflicts, oldest first,
    with their paths unchanged.
    """
    await _clear(test_db_factory)
    await _seed(test_db_factory, *[Conflict(file_path=p, resolved=True, resolution=r, profile_id=1) for p, r in resolved])
    ids = await _seed(test_db_factory, *[Conflict(file_path=p, resolved=False, profile_id=1) for p in unresolved])

    resp = await test_client.get("/conflicts")
    assert resp.status_code == 200
    body = resp.json()
    assert [c["id"] for c in body] == ids
    assert [c["file_path"] for c in body] == unresolved
    assert all(c["resolved"] is False and c["resolution"] is None for c in body)


# --- Conflict resolution updates record ---


@fixture_settings
@given(file_path=file_paths)
async def test_dismiss_resolves_the_record(test_client, test_db_factory, file_path: str) -> None:
    """Conflict resolution updates record.

    Dismissing an unresolved conflict marks it resolved with that
    resolution, and it leaves the unresolved list.
    """
    await _clear(test_db_factory)
    (conflict_id,) = await _seed(test_db_factory, Conflict(file_path=file_path, resolved=False, profile_id=1))

    resp = await test_client.post(f"/conflicts/{conflict_id}/resolve", json={"resolution": "dismiss"})
    assert resp.status_code == 200
    body = resp.json()
    assert (body["id"], body["file_path"], body["resolved"], body["resolution"]) == (
        conflict_id, file_path, True, "dismiss",
    )
    assert (await test_client.get("/conflicts")).json() == []


# --- Unit tests ---


async def test_no_conflicts_returns_empty(test_client) -> None:
    resp = await test_client.get("/conflicts")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_profile_filter_and_names(test_client, test_db_factory) -> None:
    now = datetime(2024, 1, 1)
    profile = SyncProfile(slug="docs", name="Docs", local_dir="/a", remote_dir="r:a", created_at=now, updated_at=now)
    await _seed(test_db_factory, profile)
    job = SyncJob(direction="push", started_at=now, status="completed", profile_id=profile.id)
    await _seed(test_db_factory, job)
    mine, _other = await _seed(
        test_db_factory,
        Conflict(file_path="a.txt", resolved=False, profile_id=profile.id, job_id=job.id,
                 local_modified=datetime(2024, 1, 2, 3, 4, 5)),
        Conflict(file_path="b.txt", resolved=False, profile_id=profile.id + 1),  # another profile
    )

    (conflict,) = (await test_client.get("/conflicts", params={"profile": "docs"})).json()
    assert (conflict["id"], conflict["job_id"], conflict["profile_slug"], conflict["profile_name"]) == (
        mine, job.id, "docs", "Docs",
    )
    # stored as naive UTC, returned with the offset
    assert conflict["local_modified"] in ("2024-01-02T03:04:05Z", "2024-01-02T03:04:05+00:00")
    assert (await test_client.get("/conflicts", params={"profile": "nope"})).json() == []


async def test_two_way_keep_both_closes_the_record_without_the_engine(test_client, test_db_factory, test_services) -> None:
    # a two-way sync already kept both versions: nothing to change on disk
    (conflict_id,) = await _seed(test_db_factory, Conflict(
        file_path="plan.md", resolved=False, profile_id=1,
        local_kept_as="plan.md.local-conflict1", remote_kept_as="plan.md.remote-conflict1",
    ))

    resp = await test_client.post(f"/conflicts/{conflict_id}/resolve", json={"resolution": "keep_both"})

    assert resp.status_code == 200
    body = resp.json()
    assert (body["resolved"], body["resolution"]) == (True, "keep_both")
    assert (body["local_kept_as"], body["remote_kept_as"]) == ("plan.md.local-conflict1", "plan.md.remote-conflict1")
    test_services.manager.get_engine.return_value.resolve_conflict.assert_not_awaited()


async def test_file_resolutions_go_through_the_profiles_engine(test_client, test_db_factory, test_services) -> None:
    (conflict_id,) = await _seed(test_db_factory, Conflict(file_path="a.txt", resolved=False, profile_id=1))
    engine = test_services.manager.get_engine_by_id(1)

    resp = await test_client.post(f"/conflicts/{conflict_id}/resolve", json={"resolution": "keep_remote"})

    assert resp.status_code == 200
    engine.resolve_conflict.assert_awaited_once()
    assert engine.resolve_conflict.await_args.args[0] == conflict_id
    assert engine.resolve_conflict.await_args.args[1].value == "keep_remote"


async def test_file_resolution_without_a_running_profile_is_409(test_client, test_db_factory) -> None:
    ids = await _seed(
        test_db_factory,
        Conflict(file_path="a.txt", resolved=False, profile_id=7),  # not running
        Conflict(file_path="b.txt", resolved=False, profile_id=8),  # not running either
    )
    for conflict_id in ids:
        resp = await test_client.post(f"/conflicts/{conflict_id}/resolve", json={"resolution": "keep_local"})
        assert resp.status_code == 409
    assert len((await test_client.get("/conflicts")).json()) == 2


async def test_resolved_conflict_cannot_be_resolved_again(test_client, test_db_factory) -> None:
    (conflict_id,) = await _seed(test_db_factory, Conflict(file_path="a.txt", resolved=True, resolution="keep_local", profile_id=1))

    assert (await test_client.get("/conflicts")).json() == []
    resp = await test_client.post(f"/conflicts/{conflict_id}/resolve", json={"resolution": "dismiss"})
    assert resp.status_code == 409


async def test_unknown_conflict_and_bad_resolution(test_client, test_db_factory) -> None:
    assert (await test_client.post("/conflicts/999/resolve", json={"resolution": "dismiss"})).status_code == 404
    (conflict_id,) = await _seed(test_db_factory, Conflict(file_path="a.txt", resolved=False, profile_id=1))
    resp = await test_client.post(f"/conflicts/{conflict_id}/resolve", json={"resolution": "keep_neither"})
    assert resp.status_code == 422
