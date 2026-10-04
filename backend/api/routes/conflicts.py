"""Conflict management endpoints for OmniSync.

A diff records a conflict for every file changed on both sides (see
SyncEngine._record_conflicts). Resolving one acts on the files through the
profile's engine, under its sync lock; 'dismiss' only closes the record.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit import audited
from backend.api.errors import SEE_LOG, api_error
from backend.api.schemas import ConflictResolveRequest, ConflictResolution, ConflictResponse
from backend.db.database import get_session
from backend.db.models import Conflict, SyncProfile
from backend.exceptions import ConflictResolutionError, RcloneError
from backend.services.sync_engine_manager import SyncEngineManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conflicts")

_manager: SyncEngineManager | None = None


def set_manager(manager: SyncEngineManager | None) -> None:
    """Set the engine manager reference (called by main.py)."""
    global _manager
    _manager = manager


def _aware(value: datetime | None) -> datetime | None:
    """Stored times are UTC; SQLite returns them without an offset."""
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def conflict_to_response(c: Conflict, profile: SyncProfile | None) -> ConflictResponse:
    return ConflictResponse(
        id=c.id,
        job_id=c.job_id,
        file_path=c.file_path,
        local_modified=_aware(c.local_modified),
        remote_modified=_aware(c.remote_modified),
        resolved=c.resolved,
        resolution=ConflictResolution(c.resolution) if c.resolution else None,
        profile_slug=profile.slug if profile is not None else None,
        profile_name=profile.name if profile is not None else None,
        local_kept_as=c.local_kept_as,
        remote_kept_as=c.remote_kept_as,
    )


def unresolved_conflicts_query(profile_id: int | None = None, profile_slug: str | None = None):
    """Unresolved conflicts with their profile, oldest first."""
    stmt = (
        select(Conflict, SyncProfile)
        .outerjoin(SyncProfile, Conflict.profile_id == SyncProfile.id)
        .where(Conflict.resolved == False)  # noqa: E712
        .order_by(Conflict.id)
    )
    if profile_id is not None:
        stmt = stmt.where(Conflict.profile_id == profile_id)
    if profile_slug is not None:
        stmt = stmt.where(SyncProfile.slug == profile_slug)
    return stmt


# One page of conflicts. The default is large enough that clients reading a
# single page (the web UI and TUI) see every conflict in practice;
# X-Total-Count says when there are more.
CONFLICT_PAGE_DEFAULT = 1000
CONFLICT_PAGE_MAX = 1000


async def page_of_conflicts(
    session: AsyncSession, response: Response, skip: int, limit: int,
    profile_id: int | None = None, profile_slug: str | None = None,
) -> list[ConflictResponse]:
    """One page of unresolved conflicts; the total goes in X-Total-Count."""
    stmt = unresolved_conflicts_query(profile_id=profile_id, profile_slug=profile_slug)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    response.headers["X-Total-Count"] = str(total)
    result = await session.execute(stmt.offset(skip).limit(limit))
    return [conflict_to_response(c, p) for c, p in result.all()]


@router.get("", response_model=list[ConflictResponse])
async def list_conflicts(
    response: Response,
    profile: str | None = Query(None, description="Filter by profile slug"),
    skip: int = Query(0, ge=0),
    limit: int = Query(CONFLICT_PAGE_DEFAULT, ge=1, le=CONFLICT_PAGE_MAX),
    session: AsyncSession = Depends(get_session),
) -> list[ConflictResponse]:
    """Return unresolved conflicts, oldest first, one page at a time."""
    return await page_of_conflicts(session, response, skip, limit, profile_slug=profile)


@router.post("/{conflict_id}/resolve", response_model=ConflictResponse)
@audited("conflict.resolve", lambda kw: {"resolution": kw["request"].resolution}, conflict="conflict_id")
async def resolve_conflict(
    conflict_id: int,
    request: ConflictResolveRequest,
    session: AsyncSession = Depends(get_session),
) -> ConflictResponse:
    """Resolve a conflict.

    keep_local / keep_remote / keep_both change files (through the
    profile's running engine; the replaced version goes to
    .omnisync-trash), then close the record. dismiss only closes it.
    For a conflict a two-way sync found (both versions already kept,
    local_kept_as / remote_kept_as), keep_both closes it like dismiss, and
    keep_local / keep_remote keep only that version under the original
    name on both sides (the other copy goes to each side's trash).
    409 when the action is not possible now (already resolved, profile not
    running, a file changed or vanished since the diff); 502 when rclone
    fails.
    """
    conflict = await session.get(Conflict, conflict_id)
    if conflict is None:
        raise api_error(404, "conflict_not_found", f"Conflict {conflict_id} not found")
    if conflict.resolved:
        raise api_error(409, "conflict_already_resolved", "This conflict is already resolved.")

    resolution = request.resolution
    two_way = conflict.local_kept_as is not None or conflict.remote_kept_as is not None
    if resolution == ConflictResolution.DISMISS or (two_way and resolution == ConflictResolution.KEEP_BOTH):
        # A two-way sync already kept both versions: nothing to change.
        conflict.resolved = True
        conflict.resolution = resolution.value
        await session.commit()
    else:
        engine = None
        if _manager is not None and conflict.profile_id is not None:
            engine = _manager.get_engine_by_id(conflict.profile_id)
        if engine is None:
            raise api_error(
                409, "profile_not_running",
                "The profile of this conflict is not running. Enable it to act on the files, or dismiss the conflict.",
            )
        # The engine works in its own sessions; this one must not hold a
        # read transaction open across the rclone run.
        await session.rollback()
        try:
            await engine.resolve_conflict(conflict_id, resolution)
        except ConflictResolutionError as exc:
            raise api_error(409, "conflict_resolution_refused", str(exc))
        except RcloneError:
            # rclone stderr can carry paths and config fragments; log it only.
            logger.exception("Resolving conflict %s (%s) failed in rclone", conflict_id, resolution.value)
            raise api_error(502, "rclone_failed", f"Could not resolve the conflict. {SEE_LOG}")

    session.expire_all()
    row = (await session.execute(
        select(Conflict, SyncProfile)
        .outerjoin(SyncProfile, Conflict.profile_id == SyncProfile.id)
        .where(Conflict.id == conflict_id)
    )).one()
    return conflict_to_response(*row)
