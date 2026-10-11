"""Tests for the jobs endpoints, through HTTP against seeded rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import delete

from backend.db.models import FileChange, SyncJob, SyncProfile

BASE = datetime(2024, 1, 1)


def _job(started_at: datetime, **kw) -> SyncJob:
    defaults = dict(direction="push", status="completed", files_changed=0, conflicts=0, errors=0, profile_id=1)
    return SyncJob(started_at=started_at, **{**defaults, **kw})


async def _seed(factory, *rows) -> list[int]:
    async with factory() as session:
        session.add_all(rows)
        await session.commit()
        return [r.id for r in rows]


# --- Jobs are listed most recent first ---


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    offsets=st.lists(st.integers(min_value=0, max_value=50), min_size=1, max_size=15),
    limit=st.integers(min_value=1, max_value=10),
)
async def test_jobs_sorted_by_most_recent_first(test_client, test_db_factory, offsets: list[int], limit: int) -> None:
    """Jobs endpoint returns results sorted by most recent first.

    Pages of GET /jobs, read one after the other, return every job exactly
    once, newest first; jobs started at the same time come newest-row first.
    """
    async with test_db_factory() as session:
        await session.execute(delete(SyncJob))
        await session.commit()
    ids = await _seed(test_db_factory, *[_job(BASE + timedelta(minutes=o)) for o in offsets])
    expected = [i for _, i in sorted(zip(offsets, ids, strict=True), key=lambda p: (p[0], p[1]), reverse=True)]

    seen: list[int] = []
    for skip in range(0, len(ids) + limit, limit):
        resp = await test_client.get("/jobs", params={"skip": skip, "limit": limit})
        assert resp.status_code == 200
        page = resp.json()
        assert len(page) <= limit
        seen += [j["id"] for j in page]

    assert seen == expected


# --- Unit tests ---


async def test_jobs_empty_database(test_client) -> None:
    resp = await test_client.get("/jobs")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_single_job_returned_with_all_fields(test_client, test_db_factory) -> None:
    (job_id,) = await _seed(test_db_factory, _job(
        datetime(2024, 6, 15, 12, 0, 0), finished_at=datetime(2024, 6, 15, 12, 0, 9),
        direction="pull", status="failed", files_changed=5, conflicts=1, errors=2,
    ))

    (job,) = (await test_client.get("/jobs")).json()
    assert job["id"] == job_id
    assert (job["direction"], job["status"]) == ("pull", "failed")
    assert (job["files_changed"], job["conflicts"], job["errors"]) == (5, 1, 2)
    assert job["started_at"].startswith("2024-06-15T12:00:00")
    assert job["finished_at"].startswith("2024-06-15T12:00:09")
    assert job["profile_slug"] is None and job["profile_name"] is None
    assert (await test_client.get(f"/jobs/{job_id}")).json() == job


async def test_default_page_is_twenty_jobs(test_client, test_db_factory) -> None:
    await _seed(test_db_factory, *[_job(BASE + timedelta(minutes=i)) for i in range(25)])
    assert len((await test_client.get("/jobs")).json()) == 20


async def test_profile_filter_and_names(test_client, test_db_factory) -> None:
    now = datetime(2024, 1, 1)
    profile = SyncProfile(slug="docs", name="Docs", local_dir="/a", remote_dir="r:a", created_at=now, updated_at=now)
    await _seed(test_db_factory, profile)
    mine, _other = await _seed(
        test_db_factory, _job(BASE, profile_id=profile.id), _job(BASE + timedelta(hours=1), profile_id=profile.id + 1),
    )

    filtered = (await test_client.get("/jobs", params={"profile": "docs"})).json()
    assert [(j["id"], j["profile_slug"], j["profile_name"]) for j in filtered] == [(mine, "docs", "Docs")]
    assert (await test_client.get("/jobs", params={"profile": "nope"})).json() == []


async def test_unknown_job_is_404(test_client) -> None:
    assert (await test_client.get("/jobs/999")).status_code == 404
    assert (await test_client.get("/jobs/999/files")).status_code == 404


async def test_job_files(test_client, test_db_factory) -> None:
    (job_id,) = await _seed(test_db_factory, _job(BASE))
    await _seed(
        test_db_factory,
        FileChange(job_id=job_id, file_path="a.txt", action="created", size_bytes=3, side="remote"),
        FileChange(job_id=job_id, file_path="b.txt", action="deleted", size_bytes=None, side=None),
    )

    files = (await test_client.get(f"/jobs/{job_id}/files")).json()
    assert sorted((f["file_path"], f["action"], f["size_bytes"], f["side"]) for f in files) == [
        ("a.txt", "created", 3, "remote"),
        ("b.txt", "deleted", None, None),
    ]


async def test_paging_parameters_are_validated(test_client) -> None:
    for params in ({"limit": 0}, {"limit": 101}, {"skip": -1}):
        assert (await test_client.get("/jobs", params=params)).status_code == 422
