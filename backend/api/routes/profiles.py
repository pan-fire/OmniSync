"""Profile management and profile-scoped sync endpoints."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit import audited
from backend.api.errors import SEE_LOG, api_error, failed_test_sync, public_test_sync_result
from backend.api.schemas import (
    DiffResponse,
    PauseAllResponse,
    SyncWindow,
    TrashActionRequest,
    TrashActionResponse,
    TrashListResponse,
    TrashSide,
    bwlimit_conflict,
    ManualFlagsResponse,
    ProfileCreateRequest,
    ProfileResponse,
    ProfileStatusResponse,
    ProfileUpdateRequest,
    ResyncRequest,
    ResumeIntervalsResponse,
    SelectiveSyncRequest,
    SelectiveSyncResponse,
    SyncCheckResponse,
    JobDirection,
    SyncDirection,
    SyncMode,
    SyncJobResponse,
    SyncStartRequest,
    SyncStartResponse,
    SyncState,
    SyncStatusResponse,
    SyncPreviewResponse,
    SyncStopResponse,
    JobStatus,
    ConflictResponse,
    TestSyncResponse,
    two_way_rclone_args_error,
)
from backend.api.routes.conflicts import CONFLICT_PAGE_DEFAULT, CONFLICT_PAGE_MAX, page_of_conflicts
from backend.db.database import get_session
from backend.db.models import SyncJob
from backend.exceptions import (
    IntervalsNotResumableError,
    InvalidFilePathsError,
    NoCachedDiffError,
    ProfileConflictError,
    ProfileNotFoundError,
    SyncBusyError,
)
from backend.models.profile_config import ProfileConfig
from backend.services.path_guard import check_in_browse_roots, same_path
from backend.services.profile_service import ProfileService
from backend.services.rclone import RcloneService
from backend.services import sync_engine
from backend.services.sync_engine import (
    SyncEngine,
    effective_max_delete,
    reset_bisync_state,
)
from backend.services.sync_engine_manager import SyncEngineManager
from backend.services.trash import apply_to_trash, list_trash

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profiles", tags=["profiles"])


def _trash_fields(kwargs: Mapping[str, Any]) -> dict[str, object]:
    """Audit fields of a trash action: which side and how many items (not their paths)."""
    request: TrashActionRequest = kwargs["request"]
    return {"side": request.side, "items": len(request.ids), "overwrite": request.overwrite}

_manager: SyncEngineManager | None = None
_profile_service: ProfileService | None = None
_rclone_service: RcloneService | None = None


def set_manager(manager: SyncEngineManager | None) -> None:
    global _manager
    _manager = manager


def set_profile_service(service: ProfileService | None) -> None:
    global _profile_service
    _profile_service = service


def set_rclone_service(service: RcloneService | None) -> None:
    global _rclone_service
    _rclone_service = service


def _get_manager() -> SyncEngineManager:
    if _manager is None:
        raise api_error(503, "service_unavailable", "Sync engine manager not available")
    return _manager


def _get_profile_service() -> ProfileService:
    if _profile_service is None:
        raise api_error(503, "service_unavailable", "Profile service not available")
    return _profile_service


async def _get_profile_or_404(slug: str):
    try:
        return await _get_profile_service().get_by_slug(slug)
    except ProfileNotFoundError:
        raise api_error(404, "profile_not_found", f"Profile '{slug}' not found")


async def _engine_for(slug: str):
    """slug -> profile id -> running engine, or 404.

    Engines are keyed by the profile id; the slug only names the profile
    in the URL and changes when the profile is renamed.
    """
    mgr = _get_manager()
    try:
        profile = await _get_profile_service().get_by_slug(slug)
    except ProfileNotFoundError:
        profile = None
    engine = mgr.get_engine_by_id(profile.id) if profile is not None else None
    if engine is None:
        raise api_error(404, "profile_not_running", f"Profile '{slug}' not found or not running")
    return engine


async def _start_engine(profile) -> None:
    """Run the profile's engine with its current config (idempotent)."""
    try:
        await _get_manager().create_engine(ProfileConfig.from_orm(profile))
    except Exception as exc:
        logger.warning("Could not start engine for profile '%s': %s", profile.slug, exc)


def _profile_to_response(profile) -> ProfileResponse:
    """Convert a SyncProfile ORM object to a ProfileResponse."""
    return ProfileResponse(
        id=profile.id,
        slug=profile.slug,
        name=profile.name,
        local_dir=profile.local_dir,
        remote_dir=profile.remote_dir,
        debounce_seconds=profile.debounce_seconds,
        pull_interval_minutes=profile.pull_interval_minutes,
        rclone_filter=json.loads(profile.rclone_filter) if profile.rclone_filter else [],
        rclone_args=json.loads(profile.rclone_args) if profile.rclone_args else [],
        max_retries=profile.max_retries,
        enabled=profile.enabled,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        sync_mode=SyncMode(profile.sync_mode),
        mirror_notice_dismissed=bool(profile.mirror_notice_dismissed),
        bwlimit=profile.bwlimit or None,
        sync_window=_stored_window(profile.sync_window),
    )


def _stored_window(value: str | None) -> SyncWindow | None:
    """A stored sync window, or None (also for one that no longer validates)."""
    if not value:
        return None
    try:
        return SyncWindow.model_validate_json(value)
    except ValueError:
        logger.warning("Ignoring an unreadable stored sync window: %r", value)
        return None


def _profile_to_status_response(profile, engine=None) -> ProfileStatusResponse:
    """Convert a SyncProfile ORM + optional engine to a ProfileStatusResponse."""
    base = _profile_to_response(profile)
    status_fields: dict = {"max_delete": effective_max_delete(base.rclone_args)}
    if engine is not None:
        status_fields.update(engine._state.to_status_response().model_dump())
    else:
        status_fields["user_paused"] = bool(profile.user_paused)
    return ProfileStatusResponse(**base.model_dump(), **status_fields)


# --- CRUD ---


def _check_browse_roots(local_dir: str) -> None:
    """422 when OMNISYNC_BROWSE_ROOTS is set and a new local_dir lies outside it."""
    try:
        check_in_browse_roots(local_dir, "local_dir")
    except ValueError as exc:
        raise api_error(422, "path_not_allowed", str(exc))


@router.post("", status_code=201, response_model=ProfileResponse)
@audited("profile.create", lambda kw: {"name": kw["request"].name})
async def create_profile(request: ProfileCreateRequest) -> ProfileResponse:
    svc = _get_profile_service()
    _get_manager()
    _check_browse_roots(request.local_dir)
    try:
        profile = await svc.create(request)
    except ProfileConflictError as exc:
        raise api_error(409, "profile_conflict", str(exc))

    await _start_engine(profile)
    return _profile_to_response(profile)


@router.post("/pause-all", response_model=PauseAllResponse)
@audited("sync.pause_all")
async def pause_all_profiles() -> PauseAllResponse:
    """Pause automatic syncing of every enabled profile ("Paused by user").

    Stored with each profile, so it holds across restarts. Syncs the user
    starts still run, and a successful one does not lift the pause; only
    a resume does (resume-all, or the profile's own resume).
    """
    svc = _get_profile_service()
    mgr = _get_manager()
    result = PauseAllResponse()
    for profile in await svc.get_all():
        if not profile.enabled:
            continue
        engine = mgr.get_engine_by_id(profile.id)
        if engine is not None:
            changed = await engine.pause_by_user()
        else:
            changed = not profile.user_paused
            await svc.set_user_paused(profile.id, True)
        (result.changed if changed else result.unchanged).append(profile.slug)
    return result


@router.post("/resume-all", response_model=PauseAllResponse)
@audited("sync.resume_all")
async def resume_all_profiles() -> PauseAllResponse:
    """Lift the user's pause of every profile (pause-all, or a profile's own Pause).

    Only the user's pause: a pause OmniSync set itself (unresolved
    differences, a one-sided restore, a two-way profile that needs a
    resync) stays, and is reported in still_paused; it needs that
    profile's own resume after review.
    """
    svc = _get_profile_service()
    mgr = _get_manager()
    result = PauseAllResponse()
    for profile in await svc.get_all():
        engine = mgr.get_engine_by_id(profile.id)
        if engine is None:
            if profile.user_paused:
                await svc.set_user_paused(profile.id, False)
                result.changed.append(profile.slug)
            continue
        if not engine._state.user_paused:
            result.unchanged.append(profile.slug)
            continue
        try:
            still = await engine.resume_user_pause()
        except IntervalsNotResumableError as exc:
            result.still_paused[profile.slug] = str(exc)
            continue
        result.changed.append(profile.slug)
        if still:
            result.still_paused[profile.slug] = still
    return result


@router.get("", response_model=list[ProfileStatusResponse])
async def list_profiles() -> list[ProfileStatusResponse]:
    svc = _get_profile_service()
    mgr = _get_manager()
    profiles = await svc.get_all()
    result = []
    for p in profiles:
        result.append(_profile_to_status_response(p, mgr.get_engine_by_id(p.id)))
    return result


@router.get("/{slug}", response_model=ProfileStatusResponse)
async def get_profile(slug: str) -> ProfileStatusResponse:
    mgr = _get_manager()
    profile = await _get_profile_or_404(slug)
    return _profile_to_status_response(profile, mgr.get_engine_by_id(profile.id))


@router.put("/{slug}", response_model=ProfileResponse)
@audited("profile.update", lambda kw: {"fields": sorted(kw["request"].model_fields_set)}, profile="slug")
async def update_profile(slug: str, request: ProfileUpdateRequest) -> ProfileResponse:
    svc = _get_profile_service()
    _get_manager()
    before = await _get_profile_or_404(slug)
    # Only a new folder must be inside the browse roots: a profile stored
    # before they were set keeps working.
    if request.local_dir is not None and not same_path(request.local_dir, before.local_dir):
        _check_browse_roots(request.local_dir)
    # The flag check of a two-way profile needs the resulting mode and flags.
    mode = request.sync_mode or SyncMode(before.sync_mode)
    args = request.rclone_args if request.rclone_args is not None else \
        (json.loads(before.rclone_args) if before.rclone_args else [])
    if mode == SyncMode.TWO_WAY and (error := two_way_rclone_args_error(args)):
        raise api_error(422, "invalid_rclone_args", error)
    bwlimit = request.bwlimit if "bwlimit" in request.model_fields_set else before.bwlimit
    if error := bwlimit_conflict(bwlimit, args):
        raise api_error(422, "invalid_rclone_args", error)
    try:
        profile = await svc.update(slug, request)
    except ProfileNotFoundError:
        raise api_error(404, "profile_not_found", f"Profile '{slug}' not found")
    except ProfileConflictError as exc:
        raise api_error(409, "profile_conflict", str(exc))

    # Leaving two-way forgets its sync state: a later switch back starts
    # with a resync instead of comparing against listings from before the
    # mirror syncs.
    if before.sync_mode == SyncMode.TWO_WAY.value and profile.sync_mode != SyncMode.TWO_WAY.value:
        mgr = _get_manager()
        await mgr.remove_engine(profile.id)
        reset_bisync_state(profile.id)

    # The engine is found by id, so a rename cannot orphan it. It is
    # replaced only if its config changed (name, slug, folders, timing...).
    if profile.enabled:
        await _start_engine(profile)

    return _profile_to_response(profile)


@router.delete("/{slug}", status_code=204)
@audited("profile.delete", profile="slug")
async def delete_profile(slug: str, confirm: bool = Query(False)) -> Response:
    if not confirm:
        raise api_error(400, "confirmation_required", "Pass ?confirm=true to delete a profile")
    svc = _get_profile_service()
    mgr = _get_manager()
    profile = await _get_profile_or_404(slug)
    try:
        await mgr.remove_engine(profile.id, deleted=True)
    except Exception as exc:
        logger.warning("Error stopping engine of deleted profile '%s': %s", slug, exc)
    try:
        await svc.delete(slug)
    except ProfileNotFoundError:
        raise api_error(404, "profile_not_found", f"Profile '{slug}' not found")
    reset_bisync_state(profile.id)
    return Response(status_code=204)


@router.post("/{slug}/enable", response_model=ProfileResponse)
@audited("profile.enable", profile="slug")
async def enable_profile(slug: str) -> ProfileResponse:
    svc = _get_profile_service()
    _get_manager()
    try:
        profile = await svc.set_enabled(slug, True)
    except ProfileNotFoundError:
        raise api_error(404, "profile_not_found", f"Profile '{slug}' not found")
    except ProfileConflictError as exc:
        raise api_error(409, "profile_conflict", str(exc))
    await _start_engine(profile)
    return _profile_to_response(profile)


@router.post("/{slug}/disable", response_model=ProfileResponse)
@audited("profile.disable", profile="slug")
async def disable_profile(slug: str) -> ProfileResponse:
    svc = _get_profile_service()
    mgr = _get_manager()
    try:
        profile = await svc.set_enabled(slug, False)
    except ProfileNotFoundError:
        raise api_error(404, "profile_not_found", f"Profile '{slug}' not found")
    try:
        await mgr.remove_engine(profile.id)
    except Exception as exc:
        logger.warning("Error stopping engine of disabled profile '%s': %s", slug, exc)
    return _profile_to_response(profile)


# --- Profile-scoped sync operations ---


@router.get("/{slug}/sync/status", response_model=SyncStatusResponse)
async def get_profile_sync_status(slug: str) -> SyncStatusResponse:
    engine = await _engine_for(slug)
    return await engine.get_status()


async def _launch_sync(
    engine: SyncEngine, response: Response, direction: SyncDirection, resync: bool = False,
) -> SyncStartResponse:
    """Start the run in the background and answer without waiting for it.

    202 once rclone starts changing files (or after SYNC_START_WAIT while
    the safety checks still run): the client follows the job. A run that
    ended before changing anything (refused by its own checks: sync marker,
    empty side, delete limit, pending resync) is answered with 200 and the
    refusal, as when this request waited for the whole run.
    """
    try:
        launch = await engine.launch(direction, resync=resync)
    except SyncBusyError as exc:
        raise api_error(409, "sync_busy", str(exc))
    ended = await launch.wait_started(sync_engine.SYNC_START_WAIT)
    if launch.job_id is None:
        # Crashed before recording a job; the engine logged the exception.
        raise api_error(500, "internal_error", f"The sync could not start. {SEE_LOG}")
    status = await engine.get_status()
    note = None
    if status.outside_sync_window:
        opens = status.next_window_start
        note = (
            "Outside this profile's sync window: this sync runs because you started it. Automatic "
            "syncs wait until the window opens" + (f" ({opens:%a %H:%M})." if opens else ".")
        )
    if not ended:
        response.status_code = 202
        return SyncStartResponse(job_id=launch.job_id, state=status.state, note=note)
    response.status_code = 200
    error = status.last_error if status.state == SyncState.ERROR else None
    return SyncStartResponse(job_id=launch.job_id, state=status.state, error=error, note=note)


@router.post(
    "/{slug}/sync/start", status_code=202, response_model=SyncStartResponse,
    responses={200: {"model": SyncStartResponse, "description": "The run ended before changing anything"}},
)
@audited("sync.start", lambda kw: {"direction": kw["request"].direction, "force": kw["request"].force}, profile="slug")
async def start_profile_sync(slug: str, request: SyncStartRequest, response: Response) -> SyncStartResponse:
    """Start a push, pull or two-way sync; answers 202 while it runs (see _launch_sync).

    409 while the profile is paused (unless force), needs a resync, is in
    mirror mode (two-way), or another operation of the profile runs.
    """
    engine = await _engine_for(slug)

    if request.direction == SyncDirection.TWO_WAY:
        if not engine.profile.two_way:
            raise api_error(
                409, "not_two_way",
                "This profile syncs in mirror mode. Switch it to two-way first, or push or pull.",
            )
        if engine._state.resync_required:
            raise api_error(
                409, "resync_required",
                engine._state.last_error or "This profile needs a resync. Run Resync first.",
            )

    if engine._state.intervals_paused and not request.force:
        n = engine._state.pending_changes
        detail = (
            f"Sync intervals paused due to {n} unresolved differences. Pass force=true to override."
            if n or not engine.profile.two_way
            else "Syncing is paused" + (f": {engine._state.last_error}" if engine._state.last_error else ".")
            + " Pass force=true to override."
        )
        raise api_error(409, "sync_paused", detail)

    return await _launch_sync(engine, response, request.direction)


@router.post(
    "/{slug}/sync/resync", status_code=202, response_model=SyncStartResponse,
    responses={200: {"model": SyncStartResponse, "description": "The run ended before changing anything"}},
)
@audited("sync.resync", profile="slug")
async def resync_profile(slug: str, request: ResyncRequest, response: Response) -> SyncStartResponse:
    """Confirmed two-way resync: the union of both folders, nothing deleted.

    Files missing on one side are copied there; where a file differs the
    newer version wins and the older one goes to that side's
    .omnisync-trash. Clears a pending resync and resumes syncing. The sync
    marker and the other safety checks still apply. Answers like
    /sync/start: 202 while it runs.
    """
    if not request.confirm:
        raise api_error(400, "confirmation_required", "Pass confirm=true to resync.")
    engine = await _engine_for(slug)
    if not engine.profile.two_way:
        raise api_error(409, "not_two_way", "Resync is for two-way profiles; this one syncs in mirror mode.")
    return await _launch_sync(engine, response, SyncDirection.TWO_WAY, resync=True)


@router.post("/{slug}/sync/stop", response_model=SyncStopResponse)
@audited("sync.stop", profile="slug")
async def stop_profile_sync(slug: str) -> SyncStopResponse:
    engine = await _engine_for(slug)
    # Stops the running rclone (the job is recorded as failed); the watcher
    # and scheduler keep running, so the profile keeps syncing afterwards.
    stopped = await engine.stop_current_sync()
    status = await engine.get_status()
    return SyncStopResponse(state=status.state, message="Sync stopped" if stopped else "No sync was running")


@router.post("/{slug}/sync/check", response_model=SyncCheckResponse)
async def check_profile_sync(slug: str) -> SyncCheckResponse:
    engine = await _engine_for(slug)
    return await engine.check_diff()


@router.post("/{slug}/sync/preview", response_model=SyncPreviewResponse)
async def preview_profile_sync(slug: str) -> SyncPreviewResponse:
    """What a push and a pull would delete and replace now, and the delete limit.

    Changes nothing: not the pending count, the cached diff, the conflict
    records or the pause state (POST /sync/check and /diff update those).
    """
    engine = await _engine_for(slug)
    return await engine.preview_sync()


@router.post("/{slug}/diff", response_model=DiffResponse)
async def get_profile_diff(
    slug: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(0, ge=0, le=500),
) -> DiffResponse:
    engine = await _engine_for(slug)
    diff = await engine.enhanced_diff()
    if limit == 0:
        return diff
    total = len(diff.files)
    page_files = diff.files[offset : offset + limit]
    from backend.api.schemas import DiffPagination
    pagination = DiffPagination(
        offset=offset, limit=limit, total=total,
        has_more=(offset + limit) < total,
    )
    return DiffResponse(
        files=page_files, summary=diff.summary,
        pagination=pagination, error=diff.error,
    )


@router.post("/{slug}/sync/selective", status_code=202, response_model=SelectiveSyncResponse)
@audited("sync.selective", lambda kw: {"items": len(kw["request"].items)}, profile="slug")
async def profile_selective_sync(slug: str, request: SelectiveSyncRequest) -> SelectiveSyncResponse:
    """Start per-file actions on the cached diff; answers 202 (status "running") at once.

    Follow them with GET /profiles/{slug}/sync/selective/{job_id}, which has
    the per-file errors once they ended. Refused at once: 409 no_cached_diff,
    400 invalid_paths, 409 sync_busy while another operation of the profile
    runs or waits.
    """
    engine = await _engine_for(slug)
    try:
        return await engine.launch_selective(request.items)
    except SyncBusyError as exc:
        raise api_error(409, "sync_busy", str(exc))
    except NoCachedDiffError:
        raise api_error(409, "no_cached_diff", f"No diff cached. Run POST /profiles/{slug}/diff first.")
    except InvalidFilePathsError as e:
        raise api_error(400, "invalid_paths", "Invalid file paths: " + ", ".join(e.invalid_paths), invalid_paths=e.invalid_paths)


@router.get("/{slug}/sync/selective/{job_id}", response_model=SelectiveSyncResponse)
async def profile_selective_result(
    slug: str, job_id: int, session: AsyncSession = Depends(get_session),
) -> SelectiveSyncResponse:
    """Per-file actions started with POST .../sync/selective: running, or their result.

    The per-file errors are kept for the most recent runs while OmniSync
    runs; for an older run (or after a restart) the answer comes from the
    job record, with counts but no per-file errors.
    """
    engine = await _engine_for(slug)
    result = engine.selective_result(job_id)
    if result is not None:
        return result
    job = await session.get(SyncJob, job_id)
    if job is None or job.direction != JobDirection.SELECTIVE.value or job.profile_id != engine.profile_id:
        raise api_error(404, "job_not_found", f"Per-file job {job_id} not found for profile '{slug}'")
    return SelectiveSyncResponse(
        job_id=job.id, status=JobStatus(job.status), total=0, succeeded=0, failed=job.errors,
    )


@router.post("/{slug}/sync/pause", response_model=ResumeIntervalsResponse)
@audited("sync.pause", profile="slug")
async def profile_pause(slug: str) -> ResumeIntervalsResponse:
    """Pause automatic syncing of this profile for the user (see pause-all); resume-intervals lifts it."""
    engine = await _engine_for(slug)
    changed = await engine.pause_by_user()
    return ResumeIntervalsResponse(detail="Automatic syncing paused." if changed else "Already paused.")


@router.post("/{slug}/sync/resume-intervals", response_model=ResumeIntervalsResponse)
@audited("sync.resume", profile="slug")
async def profile_resume_intervals(slug: str) -> ResumeIntervalsResponse:
    engine = await _engine_for(slug)
    try:
        detail = await engine.resume_intervals()
        return ResumeIntervalsResponse(detail=detail)
    except IntervalsNotResumableError as e:
        raise api_error(409, "intervals_not_resumable", str(e))


@router.get("/{slug}/manual-flags", response_model=ManualFlagsResponse)
async def get_profile_manual_flags(slug: str) -> ManualFlagsResponse:
    engine = await _engine_for(slug)
    flags = await engine.get_manual_flags()
    return ManualFlagsResponse(flags=flags)


@router.delete("/{slug}/manual-flags/{file_path:path}", status_code=204)
async def clear_profile_manual_flag(slug: str, file_path: str) -> Response:
    engine = await _engine_for(slug)
    found = await engine.clear_manual_flag(file_path)
    if not found:
        raise api_error(404, "manual_flag_not_found", "Manual flag not found for path")
    return Response(status_code=204)


@router.post("/{slug}/config/test-sync", response_model=TestSyncResponse)
async def test_profile_sync(slug: str) -> TestSyncResponse:
    if _rclone_service is None:
        raise api_error(503, "service_unavailable", "rclone service not available")
    svc = _get_profile_service()
    try:
        profile = await svc.get_by_slug(slug)
    except ProfileNotFoundError:
        raise api_error(404, "profile_not_found", f"Profile '{slug}' not found")
    try:
        result = await _rclone_service.test_sync(profile.local_dir, profile.remote_dir)
    except Exception:
        return failed_test_sync(logger)
    return public_test_sync_result(result, logger)


# --- Profile-scoped jobs and conflicts ---


@router.get("/{slug}/jobs", response_model=list[SyncJobResponse])
async def list_profile_jobs(
    slug: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> list[SyncJobResponse]:
    svc = _get_profile_service()
    try:
        profile = await svc.get_by_slug(slug)
    except ProfileNotFoundError:
        raise api_error(404, "profile_not_found", f"Profile '{slug}' not found")
    stmt = (
        select(SyncJob)
        .where(SyncJob.profile_id == profile.id)
        .order_by(SyncJob.started_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await session.execute(stmt)
    jobs = result.scalars().all()
    return [
        SyncJobResponse(
            id=j.id,
            direction=JobDirection(j.direction),
            started_at=j.started_at,
            finished_at=j.finished_at,
            status=JobStatus(j.status),
            files_changed=j.files_changed,
            conflicts=j.conflicts,
            errors=j.errors,
            profile_slug=slug,
            profile_name=profile.name,
        )
        for j in jobs
    ]


@router.get("/{slug}/conflicts", response_model=list[ConflictResponse])
async def list_profile_conflicts(
    slug: str,
    response: Response,
    skip: int = Query(0, ge=0),
    limit: int = Query(CONFLICT_PAGE_DEFAULT, ge=1, le=CONFLICT_PAGE_MAX),
    session: AsyncSession = Depends(get_session),
) -> list[ConflictResponse]:
    svc = _get_profile_service()
    try:
        profile = await svc.get_by_slug(slug)
    except ProfileNotFoundError:
        raise api_error(404, "profile_not_found", f"Profile '{slug}' not found")
    return await page_of_conflicts(session, response, skip, limit, profile_id=profile.id)


# --- Trash (.omnisync-trash) ---


def _trash_rclone() -> RcloneService:
    if _rclone_service is None:
        raise api_error(503, "service_unavailable", "rclone service not available")
    return _rclone_service


@router.get("/{slug}/trash", response_model=TrashListResponse)
async def list_profile_trash(slug: str, side: TrashSide = Query(TrashSide.LOCAL)) -> TrashListResponse:
    """The files in one side's .omnisync-trash, newest sync first (capped; totals count all)."""
    profile = await _get_profile_or_404(slug)
    # Outside the try: rclone not being wired is 503, not a failed listing.
    rclone = _trash_rclone()
    try:
        return await list_trash(side, profile.local_dir, profile.remote_dir, rclone)
    except Exception:
        logger.exception("Listing the %s trash of '%s' failed", side.value, slug)
        raise api_error(502, "rclone_failed", f"Could not list the trash. {SEE_LOG}")


async def _trash_action(slug: str, action: str, request: TrashActionRequest) -> TrashActionResponse:
    profile = await _get_profile_or_404(slug)
    rclone = _trash_rclone()
    # Not while a sync of this profile runs: it may be moving files into
    # the trash or out of the folders right now.
    lock = _get_manager().sync_lock(profile.id)
    if lock.locked():
        raise api_error(409, "sync_busy", "A sync of this profile is running. Try again when it is done.")
    async with lock:
        try:
            done, failed = await apply_to_trash(
                action, request.side, request.ids, request.overwrite, profile.local_dir, profile.remote_dir, rclone,
            )
        except Exception:
            logger.exception("Trash %s for '%s' failed", action, slug)
            raise api_error(502, "rclone_failed", f"The {action} failed. {SEE_LOG}")
    return TrashActionResponse(done=done, failed=failed)


@router.post("/{slug}/trash/restore", response_model=TrashActionResponse)
@audited("trash.restore", _trash_fields, profile="slug")
async def restore_from_trash(slug: str, request: TrashActionRequest) -> TrashActionResponse:
    """Move trashed files back to where they were.

    A file that exists there is moved into the trash first (nothing is
    lost); one that is newer than the trashed version is only replaced
    with overwrite=true (otherwise it fails with code target_newer). The
    next sync carries the restored files to the other side.
    """
    return await _trash_action(slug, "restore", request)


@router.post("/{slug}/trash/delete", response_model=TrashActionResponse)
@audited("trash.delete", _trash_fields, profile="slug")
async def delete_from_trash(slug: str, request: TrashActionRequest) -> TrashActionResponse:
    """Delete trashed files for good."""
    return await _trash_action(slug, "delete", request)
