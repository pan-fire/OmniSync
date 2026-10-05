"""The error answers of the sync job history routes (/jobs): auth, unknown jobs, bad parameters.

Through HTTP against test_client's real in-memory database. Listing and
paging themselves are in test_jobs.py.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from backend.db.models import FileChange, SyncJob
from backend.main import app


async def seed_job(factory) -> int:
    async with factory() as session:
        job = SyncJob(started_at=datetime(2024, 1, 1), direction="push", status="completed",
                      files_changed=1, conflicts=0, errors=0, profile_id=1)
        session.add(job)
        await session.flush()
        session.add(FileChange(job_id=job.id, file_path="private/tax-2024.pdf", action="created", size_bytes=1))
        await session.commit()
        return job.id


@pytest.mark.parametrize("path", ["/jobs", "/jobs/{id}", "/jobs/{id}/files"])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong-token"}])
async def test_job_history_needs_the_api_token(test_client, test_db_factory, path, headers):
    """The history names the files that were synced: nothing of it without the token."""
    job_id = await seed_job(test_db_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as bare:
        resp = await bare.get(path.format(id=job_id))
    assert resp.status_code == 401
    assert resp.json()["code"] in ("token_missing", "token_invalid")
    assert "tax-2024" not in resp.text


@pytest.mark.parametrize("path", ["/jobs/{id}", "/jobs/{id}/files"])
async def test_unknown_job_answers_job_not_found(test_client, test_db_factory, path):
    """A deleted or never-existing job is a 404 with the stable code, also when other jobs exist."""
    job_id = await seed_job(test_db_factory)
    resp = await test_client.get(path.format(id=job_id + 1))
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "job_not_found" and str(job_id + 1) in body["detail"]
    assert "tax-2024" not in resp.text


@pytest.mark.parametrize("path", ["/jobs/abc", "/jobs/1.5/files", "/jobs/-/files"])
async def test_non_numeric_job_ids_are_validation_errors(test_client, path):
    resp = await test_client.get(path)
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_failed"


@pytest.mark.parametrize("params", [{"skip": "x"}, {"limit": "many"}, {"limit": 1000}])
async def test_bad_paging_parameters_name_the_parameter(test_client, params):
    resp = await test_client.get("/jobs", params=params)
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "validation_failed"
    assert next(iter(params)) in resp.text


async def test_a_job_without_changes_has_an_empty_file_list(test_client, test_db_factory):
    async with test_db_factory() as session:
        job = SyncJob(started_at=datetime(2024, 1, 2), direction="pull", status="completed",
                      files_changed=0, conflicts=0, errors=0, profile_id=1)
        session.add(job)
        await session.commit()
        job_id = job.id
    resp = await test_client.get(f"/jobs/{job_id}/files")
    assert resp.status_code == 200 and resp.json() == []
