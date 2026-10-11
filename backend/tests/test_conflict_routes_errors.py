"""Every documented error answer of the conflict endpoints (docs/api-errors.md).

Through HTTP against seeded rows, with the test_client's mock engine; the
real file operations behind them are in test_conflicts_safety_integration.py.
Each case checks the status, the stable ``code`` and that the record was
left as it was.
"""

from __future__ import annotations

import logging

import pytest
from httpx import ASGITransport, AsyncClient

from backend.db.models import Conflict
from backend.exceptions import ConflictResolutionError, RcloneError
from backend.main import app


async def seed(factory, *rows: Conflict) -> list[int]:
    async with factory() as session:
        session.add_all(rows)
        await session.commit()
        return [r.id for r in rows]


async def row(factory, conflict_id: int) -> Conflict:
    async with factory() as session:
        return await session.get(Conflict, conflict_id)


def engine_of(test_services):
    return test_services.manager.get_engine_by_id(1)


async def resolve(client: AsyncClient, conflict_id: int | str, resolution: str = "keep_local"):
    return await client.post(f"/conflicts/{conflict_id}/resolve", json={"resolution": resolution})


# --- authentication ---


@pytest.mark.parametrize(("method", "url"), [
    ("GET", "/conflicts"), ("POST", "/conflicts/1/resolve"), ("GET", "/profiles/default/conflicts"),
])
@pytest.mark.parametrize(("headers", "code"), [
    ({}, "token_missing"), ({"Authorization": "Bearer wrong-token"}, "token_invalid"),
], ids=["none", "wrong"])
async def test_without_a_valid_token_nothing_is_read_or_changed(test_client, test_db_factory, test_services,
                                                                 method, url, headers, code):
    """File paths in conflicts are private, and a resolve changes files: 401 first."""
    (conflict_id,) = await seed(test_db_factory, Conflict(file_path="secret-plan.md", resolved=False, profile_id=1))
    assert conflict_id == 1
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers=headers) as anonymous:
        resp = await anonymous.request(method, url, json={"resolution": "keep_local"})

    assert resp.status_code == 401
    assert resp.json()["code"] == code
    assert "secret-plan.md" not in resp.text
    engine_of(test_services).resolve_conflict.assert_not_awaited()
    assert (await row(test_db_factory, conflict_id)).resolved is False


# --- resolve ---


async def test_unknown_conflict_is_404_with_its_code(test_client):
    resp = await resolve(test_client, 4242, "dismiss")
    assert resp.status_code == 404
    assert resp.json()["code"] == "conflict_not_found"
    assert "4242" in resp.json()["detail"]


async def test_resolving_twice_is_409_already_resolved(test_client, test_db_factory, test_services):
    (conflict_id,) = await seed(test_db_factory, Conflict(file_path="a.md", resolved=True, resolution="keep_remote",
                                                          profile_id=1))
    resp = await resolve(test_client, conflict_id)

    assert resp.status_code == 409
    assert resp.json()["code"] == "conflict_already_resolved"
    assert (await row(test_db_factory, conflict_id)).resolution == "keep_remote"
    engine_of(test_services).resolve_conflict.assert_not_awaited()


@pytest.mark.parametrize("profile_id", [7], ids=["not-running"])
async def test_a_file_resolution_without_its_engine_is_409_profile_not_running(test_client, test_db_factory,
                                                                                 profile_id):
    (conflict_id,) = await seed(test_db_factory, Conflict(file_path="a.md", resolved=False, profile_id=profile_id))

    resp = await resolve(test_client, conflict_id, "keep_both")

    assert resp.status_code == 409
    assert resp.json()["code"] == "profile_not_running"
    assert (await row(test_db_factory, conflict_id)).resolved is False


async def test_an_engine_refusal_is_409_and_leaves_the_record_open(test_client, test_db_factory, test_services):
    """E.g. the file changed since the diff: the user is told why, nothing is closed."""
    (conflict_id,) = await seed(test_db_factory, Conflict(file_path="a.md", resolved=False, profile_id=1))
    engine_of(test_services).resolve_conflict.side_effect = ConflictResolutionError(
        "The remote file changed since the conflict was found; not overwriting it unseen.",
    )

    resp = await resolve(test_client, conflict_id)

    assert resp.status_code == 409
    assert resp.json()["code"] == "conflict_resolution_refused"
    assert "changed since the conflict was found" in resp.json()["detail"]
    assert (await row(test_db_factory, conflict_id)).resolved is False


async def test_an_rclone_failure_is_502_without_rclones_text(test_client, test_db_factory, test_services, caplog):
    """rclone's stderr can carry paths and config fragments: logged, never answered."""
    (conflict_id,) = await seed(test_db_factory, Conflict(file_path="a.md", resolved=False, profile_id=1))
    leak = "Failed to copy: /srv/private/a.md: token=fake-secret-value"
    engine_of(test_services).resolve_conflict.side_effect = RcloneError(leak)

    with caplog.at_level(logging.ERROR):
        resp = await resolve(test_client, conflict_id, "keep_remote")

    assert resp.status_code == 502
    assert resp.json()["code"] == "rclone_failed"
    assert "fake-secret-value" not in resp.text and "/srv/private" not in resp.text
    assert f"Resolving conflict {conflict_id} (keep_remote) failed in rclone" in caplog.text
    assert (await row(test_db_factory, conflict_id)).resolved is False


@pytest.mark.parametrize(("url", "body"), [
    ("/conflicts/1/resolve", {"resolution": "keep_neither"}),
    ("/conflicts/1/resolve", {}),
    ("/conflicts/1/resolve", None),
    ("/conflicts/not-a-number/resolve", {"resolution": "dismiss"}),
])
async def test_a_malformed_request_is_422_and_changes_nothing(test_client, test_db_factory, test_services, url, body):
    (conflict_id,) = await seed(test_db_factory, Conflict(file_path="a.md", resolved=False, profile_id=1))
    assert conflict_id == 1

    resp = await test_client.post(url, json=body)

    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_failed"
    assert resp.json()["details"]["errors"]
    assert (await row(test_db_factory, conflict_id)).resolved is False
    engine_of(test_services).resolve_conflict.assert_not_awaited()


# --- listing ---


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 1001}, {"skip": -1}, {"limit": "many"}])
@pytest.mark.parametrize("url", ["/conflicts", "/profiles/default/conflicts"])
async def test_out_of_range_paging_is_422(test_client, url, params):
    resp = await test_client.get(url, params=params)
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_failed"


async def test_a_profiles_conflicts_are_paged_with_their_total(test_client, test_db_factory):
    """Only this profile's open conflicts, oldest first; X-Total-Count tells there are more."""
    await seed(
        test_db_factory,
        *(Conflict(file_path=f"f{i}.md", resolved=False, profile_id=1) for i in range(3)),
        Conflict(file_path="other.md", resolved=False, profile_id=2),
        Conflict(file_path="done.md", resolved=True, profile_id=1),
    )

    resp = await test_client.get("/profiles/default/conflicts", params={"skip": 1, "limit": 1})

    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "3"
    assert [c["file_path"] for c in resp.json()] == ["f1.md"]


async def test_conflicts_of_an_unknown_profile_are_404(test_client):
    resp = await test_client.get("/profiles/nope/conflicts")
    assert resp.status_code == 404
    assert resp.json()["code"] == "profile_not_found"
