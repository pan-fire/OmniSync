"""Job history endpoints for OmniSync."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.errors import api_error
from backend.api.schemas import (
    FileChangeAction,
    FileChangeResponse,
    FileSide,
    JobDirection,
    JobStatus,
    SyncJobResponse,
)
from backend.db.database import get_session
from backend.db.models import FileChange, SyncJob, SyncProfile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs")


def job_to_response(job: SyncJob, profile: SyncProfile | None) -> SyncJobResponse:
    """A job with the slug and name its profile has now (None for jobs from before profiles)."""
    return SyncJobResponse(
        id=job.id,
        direction=JobDirection(job.direction),
        started_at=job.started_at,
        finished_at=job.finished_at,
        status=JobStatus(job.status),
        files_changed=job.files_changed,
        conflicts=job.conflicts,
        errors=job.errors,
        profile_slug=profile.slug if profile is not None else None,
        profile_name=profile.name if profile is not None else None,
    )


def _jobs_with_profile():
    return select(SyncJob, SyncProfile).outerjoin(SyncProfile, SyncJob.profile_id == SyncProfile.id)


@router.get("", response_model=list[SyncJobResponse])
async def list_jobs(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    profile: str | None = Query(None, description="Filter by profile slug"),
    session: AsyncSession = Depends(get_session),
) -> list[SyncJobResponse]:
    """Return paginated list of sync jobs sorted by most recent first."""
    stmt = _jobs_with_profile().order_by(SyncJob.started_at.desc(), SyncJob.id.desc())
    if profile is not None:
        stmt = stmt.where(SyncProfile.slug == profile)
    stmt = stmt.offset(skip).limit(limit)
    result = await session.execute(stmt)
    return [job_to_response(j, p) for j, p in result.all()]


@router.get("/{job_id}", response_model=SyncJobResponse)
async def get_job(
    job_id: int,
    session: AsyncSession = Depends(get_session),
) -> SyncJobResponse:
    """Return job detail for a specific job ID."""
    row = (await session.execute(_jobs_with_profile().where(SyncJob.id == job_id))).first()
    if row is None:
        raise api_error(404, "job_not_found", f"Job {job_id} not found")
    return job_to_response(*row)


@router.get("/{job_id}/files", response_model=list[FileChangeResponse])
async def get_job_files(
    job_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[FileChangeResponse]:
    """Return files affected in a specific job."""
    # Verify job exists
    job = await session.get(SyncJob, job_id)
    if job is None:
        raise api_error(404, "job_not_found", f"Job {job_id} not found")

    stmt = select(FileChange).where(FileChange.job_id == job_id)
    result = await session.execute(stmt)
    changes = result.scalars().all()
    return [
        FileChangeResponse(
            id=fc.id,
            job_id=fc.job_id,
            file_path=fc.file_path,
            action=FileChangeAction(fc.action),
            size_bytes=fc.size_bytes,
            side=FileSide(fc.side) if fc.side else None,
        )
        for fc in changes
    ]
