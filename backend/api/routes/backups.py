"""Backup target management and backup/restore operations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.audit import audited
from backend.api.errors import SEE_LOG, ApiError, api_error
from backend.api.schemas import (
    BackupJobResponse,
    BackupJobStatus,
    BackupMode,
    BackupTargetCreateRequest,
    BackupTargetResponse,
    BackupTargetType,
    BackupTargetUpdateRequest,
    RestoreFilesRequest,
    RestorePreviewResponse,
    RestoreRequest,
    RestoreScope,
    SnapshotFilesResponse,
    SnapshotResponse,
    check_backup_target,
    check_snapshot_id,
)
from backend.db.models import BackupJob, BackupTarget, SyncProfile
from backend.exceptions import RcloneError, SyncBusyError
from backend.services.backup_service import (
    BackupRunning,
    BackupService,
    RestoreRefused,
    backup_is_overdue,
    backup_paths_overlap,
    browse_snapshot,
    public_job_error,
)
from backend.services.path_guard import check_in_browse_roots, check_not_data_dir
from backend.services.path_overlap import locations_overlap

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profiles/{slug}/backups", tags=["backups"])

_backup_service: BackupService | None = None
_db_factory: async_sessionmaker[AsyncSession] | None = None


def set_backup_service(service: BackupService | None) -> None:
    global _backup_service
    _backup_service = service


def set_db_factory(factory: async_sessionmaker[AsyncSession] | None) -> None:
    global _db_factory
    _db_factory = factory


def _get_backup_service() -> BackupService:
    if _backup_service is None:
        raise api_error(503, "service_unavailable", "Backup service not available")
    return _backup_service


def _get_db_factory() -> async_sessionmaker[AsyncSession]:
    if _db_factory is None:
        raise api_error(503, "service_unavailable", "Database not available")
    return _db_factory


async def _resolve_profile(slug: str, session: AsyncSession) -> SyncProfile:
    """Fetch a SyncProfile by slug or raise 404."""
    stmt = select(SyncProfile).where(SyncProfile.slug == slug)
    result = await session.execute(stmt)
    profile = result.scalar_one_or_none()
    if profile is None:
        raise api_error(404, "profile_not_found", f"Profile '{slug}' not found")
    return profile


async def _resolve_target(
    slug: str, target_id: int, session: AsyncSession
) -> tuple[SyncProfile, BackupTarget]:
    """Fetch a profile and its backup target, or raise 404."""
    profile = await _resolve_profile(slug, session)
    stmt = (
        select(BackupTarget)
        .where(BackupTarget.id == target_id, BackupTarget.profile_id == profile.id)
    )
    result = await session.execute(stmt)
    target = result.scalar_one_or_none()
    if target is None:
        raise api_error(
            404, "backup_target_not_found",
            f"Backup target {target_id} not found for profile '{slug}'",
        )
    return profile, target


def _target_to_response(
    target: BackupTarget, svc: BackupService, last_job: BackupJob | None = None,
    last_completed_at: datetime | None = None,
) -> BackupTargetResponse:
    """Convert a BackupTarget ORM object to a BackupTargetResponse.

    ``last_completed_at``: when its last completed backup finished, for ``overdue``.
    """
    last_backup_at = None
    last_backup_status = None
    if last_job is not None:
        last_backup_at = last_job.finished_at or last_job.started_at
        last_backup_status = last_job.status

    return BackupTargetResponse(
        id=target.id,
        profile_id=target.profile_id,
        name=target.name,
        target_path=target.target_path,
        target_type=BackupTargetType(target.target_type),
        remote_name=target.remote_name,
        retention_days=target.retention_days,
        keep_last=target.keep_last,
        frequency_hours=target.frequency_hours,
        backup_mode=BackupMode(target.backup_mode),
        enabled=target.enabled,
        encrypted=bool(target.encryption_password),
        verify_after_backup=bool(target.verify_after_backup),
        overdue=backup_is_overdue(target, last_completed_at, datetime.now(timezone.utc)),
        last_liveness_ok=target.last_liveness_ok,
        last_liveness_error=target.last_liveness_error,
        last_backup_at=last_backup_at,
        last_backup_status=last_backup_status,
        last_verify_status=last_job.verify_status if last_job is not None else None,
        last_verify_message=last_job.verify_message if last_job is not None else None,
        next_scheduled_at=svc.get_next_run_time(target.id),
        created_at=target.created_at,
        updated_at=target.updated_at,
    )


def _job_response(job: BackupJob) -> BackupJobResponse:
    """A job as clients see it: an error code and a fixed message, never rclone's output."""
    error_code, error_message = public_job_error(job)
    return BackupJobResponse(
        id=job.id,
        target_id=job.target_id,
        started_at=job.started_at,
        finished_at=job.finished_at,
        status=BackupJobStatus(job.status),
        direction=job.direction,
        size_bytes=job.size_bytes,
        snapshot_id=job.snapshot_id,
        error_code=error_code,
        error_message=error_message,
        verify_status=job.verify_status,
        verify_message=job.verify_message,
    )


async def _get_last_backup_job(
    target_id: int, session: AsyncSession,
) -> BackupJob | None:
    """Get the most recent backup job (direction='backup') for a target."""
    stmt = (
        select(BackupJob)
        .where(BackupJob.target_id == target_id, BackupJob.direction == "backup")
        .order_by(BackupJob.started_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _check_new_local_target(target_type: BackupTargetType, target_path: str) -> None:
    """422 when OMNISYNC_BROWSE_ROOTS is set and a new local target lies outside it."""
    if target_type != BackupTargetType.LOCAL:
        return
    try:
        check_in_browse_roots(target_path, "target_path")
    except ValueError as exc:
        raise api_error(422, "path_not_allowed", str(exc))


async def _last_completed_at(target_id: int, session: AsyncSession) -> datetime | None:
    """When the target's last completed backup finished."""
    return (await BackupService.last_completed_backups(session, [target_id])).get(target_id)


async def _validate_target_path(
    target_path: str, profile: SyncProfile, session: AsyncSession,
    exclude_target_id: int | None = None,
) -> None:
    """Reject a target that overlaps a synced folder or another backup target.

    Overlap is the same folder or one inside the other, after normalising
    (trailing slashes, symlinks, ".."). A backup inside a synced folder is
    synced (and may be deleted) with it; a synced folder inside a backup
    target is rewritten by the backup. Every profile's folders count, not
    only this one's.
    """
    result = await session.execute(select(SyncProfile).order_by(SyncProfile.id != profile.id))
    for other in result.scalars():
        for field in ("local_dir", "remote_dir"):
            path = getattr(other, field)
            if not locations_overlap(target_path, path):
                continue
            if other.id == profile.id:
                detail = (
                    f"target_path cannot be the same as, inside, or contain the profile's {field}"
                )
            else:
                detail = (
                    f"target_path cannot be the same as, inside, or contain the {field} "
                    f"of profile '{other.slug}'"
                )
            raise api_error(400, "path_overlap", detail)
    # Another backup target in the same folder, or one inside the other:
    # each run would push the other's files into versions/ and retention
    # would delete them. run_backup refuses such a target too.
    conflict = await BackupService.find_path_conflict(session, target_path, exclude_target_id=exclude_target_id)
    if conflict is not None:
        raise api_error(400, "path_overlap", conflict)


async def _check_remote_configured(
    svc: BackupService, target_type: BackupTargetType, target_path: str,
) -> None:
    """Reject a remote target on a remote that is not configured in rclone.

    check_backup_target has made sure a remote target's path is
    '<remote>:<path>' (and, for a custom remote, on remote_name), so the
    remote to look for is the path's.
    """
    if target_type == BackupTargetType.LOCAL:
        return
    remote = target_path.split(":", 1)[0]
    try:
        configured = await svc.remote_names()
    except Exception:
        logger.exception("Listing remotes for backup target '%s' failed", target_path)
        raise api_error(503, "rclone_unavailable", f"Could not read the configured remotes. {SEE_LOG}")
    if remote not in configured:
        raise api_error(422, "remote_not_configured", f"Remote '{remote}' is not configured")


async def _obscure(svc: BackupService, passphrase: str) -> str:
    try:
        return await svc.obscure_passphrase(passphrase)
    except Exception:
        logger.exception("Obscuring a backup passphrase failed")
        raise api_error(503, "rclone_unavailable", f"Could not store the passphrase. {SEE_LOG}")


async def _check_encryption_change(svc: BackupService, target_path: str) -> None:
    """409 when the passphrase of a target would change while its location holds backups.

    The backups there were written with the old passphrase (or none): with
    another one they can no longer be read, and retention would never clean
    them up. Setting, changing or removing it needs an empty location.
    """
    try:
        has_data = await svc.location_has_data(target_path)
    except Exception:
        logger.exception("Checking backup location '%s' failed", target_path)
        raise api_error(503, "rclone_unavailable", f"Could not check the backup location. {SEE_LOG}")
    if has_data:
        raise api_error(
            409, "backup_location_not_empty",
            "The backup location already holds backups. Setting, changing or removing the passphrase "
            "would make them unreadable. Choose a new, empty folder for this target (in the same edit), "
            "or create a new target.",
        )


# --- Endpoints ---


@router.post("", status_code=201, response_model=BackupTargetResponse)
@audited("backup.target_create", lambda kw: {"name": kw["request"].name, "type": kw["request"].target_type, "encrypted": bool(kw["request"].encryption_passphrase)}, profile="slug")
async def create_backup_target(
    slug: str, request: BackupTargetCreateRequest,
) -> BackupTargetResponse:
    svc = _get_backup_service()
    db = _get_db_factory()

    async with db() as session:
        profile = await _resolve_profile(slug, session)
        _check_new_local_target(request.target_type, request.target_path)
        await _check_remote_configured(svc, request.target_type, request.target_path)
        await _validate_target_path(request.target_path, profile, session)

        encryption_password = None
        if request.encryption_passphrase:
            encryption_password = await _obscure(svc, request.encryption_passphrase)

        now = datetime.now(timezone.utc)
        target = BackupTarget(
            profile_id=profile.id,
            name=request.name,
            target_path=request.target_path,
            target_type=request.target_type.value,
            remote_name=request.remote_name,
            retention_days=request.retention_days,
            keep_last=request.keep_last,
            frequency_hours=request.frequency_hours,
            backup_mode=request.backup_mode.value,
            enabled=request.enabled,
            encryption_password=encryption_password,
            verify_after_backup=request.verify_after_backup,
            created_at=now,
            updated_at=now,
        )
        session.add(target)
        await session.commit()
        await session.refresh(target)

        # Schedule if enabled
        if target.enabled:
            svc.schedule_target(target)

        return _target_to_response(target, svc)


@router.get("", response_model=list[BackupTargetResponse])
async def list_backup_targets(slug: str) -> list[BackupTargetResponse]:
    svc = _get_backup_service()
    db = _get_db_factory()

    async with db() as session:
        profile = await _resolve_profile(slug, session)
        stmt = select(BackupTarget).where(BackupTarget.profile_id == profile.id)
        result = await session.execute(stmt)
        targets = result.scalars().all()
        completed = await BackupService.last_completed_backups(session, [t.id for t in targets])

        responses = []
        for t in targets:
            last_job = await _get_last_backup_job(t.id, session)
            responses.append(_target_to_response(t, svc, last_job, completed.get(t.id)))
        return responses


@router.get("/{target_id}", response_model=BackupTargetResponse)
async def get_backup_target(slug: str, target_id: int) -> BackupTargetResponse:
    svc = _get_backup_service()
    db = _get_db_factory()

    async with db() as session:
        _, target = await _resolve_target(slug, target_id, session)
        last_job = await _get_last_backup_job(target.id, session)
        return _target_to_response(target, svc, last_job, await _last_completed_at(target.id, session))


@router.put("/{target_id}", response_model=BackupTargetResponse)
@audited("backup.target_update", lambda kw: {"fields": sorted(kw["request"].model_fields_set)}, profile="slug", target="target_id")
async def update_backup_target(
    slug: str, target_id: int, request: BackupTargetUpdateRequest,
) -> BackupTargetResponse:
    svc = _get_backup_service()
    db = _get_db_factory()

    async with db() as session:
        profile, target = await _resolve_target(slug, target_id, session)

        updates = request.model_dump(exclude_unset=True)
        if {"target_path", "target_type", "remote_name"} & updates.keys():
            new_type = updates.get("target_type") or BackupTargetType(target.target_type)
            new_path = updates.get("target_path") or target.target_path
            try:
                check_backup_target(
                    new_type,
                    new_path,
                    updates["remote_name"] if "remote_name" in updates else target.remote_name,
                )
            except ValueError as exc:
                raise api_error(422, "invalid_backup_target", str(exc))
            # Only a new location must be inside the browse roots.
            if new_path != target.target_path or new_type.value != target.target_type:
                _check_new_local_target(new_type, new_path)
            await _check_remote_configured(svc, new_type, new_path)
        if updates.get("target_path") is not None:
            await _validate_target_path(updates["target_path"], profile, session, exclude_target_id=target.id)
        if "encryption_passphrase" in updates:
            passphrase = updates.pop("encryption_passphrase")
            await _check_encryption_change(svc, updates.get("target_path") or target.target_path)
            updates["encryption_password"] = await _obscure(svc, passphrase) if passphrase else None

        # Convert enum values to strings for ORM storage
        if "target_type" in updates and updates["target_type"] is not None:
            updates["target_type"] = updates["target_type"].value
        if "backup_mode" in updates and updates["backup_mode"] is not None:
            updates["backup_mode"] = updates["backup_mode"].value

        old_frequency = target.frequency_hours
        old_enabled = target.enabled

        for key, value in updates.items():
            setattr(target, key, value)
        target.updated_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(target)

        # Reschedule (from the last run) if frequency or enabled changed
        if target.frequency_hours != old_frequency or target.enabled != old_enabled:
            await svc.reschedule_target(target)

        last_job = await _get_last_backup_job(target.id, session)
        return _target_to_response(target, svc, last_job, await _last_completed_at(target.id, session))


@router.delete("/{target_id}", status_code=204)
@audited("backup.target_delete", profile="slug", target="target_id")
async def delete_backup_target(
    slug: str,
    target_id: int,
    confirm: bool = Query(False),
) -> None:
    if not confirm:
        raise api_error(400, "confirmation_required", "Deletion requires ?confirm=true")

    svc = _get_backup_service()
    db = _get_db_factory()

    async with db() as session:
        _, target = await _resolve_target(slug, target_id, session)
        svc.unschedule_target(target.id)
        await session.delete(target)
        await session.commit()


def _busy(exc: SyncBusyError) -> ApiError:
    return api_error(409, "sync_busy", str(exc))


@router.post("/{target_id}/run", status_code=202, response_model=BackupJobResponse)
@audited("backup.run", profile="slug", target="target_id")
async def run_backup_now(slug: str, target_id: int) -> BackupJobResponse:
    """Start a backup; answers 202 with the running job (follow it with GET .../jobs/{job_id}).

    Refused at once: 409 backup_running (a backup of this target runs) or
    sync_busy (a sync, backup or restore of the profile runs or waits).
    """
    svc = _get_backup_service()
    db = _get_db_factory()

    async with db() as session:
        _, target = await _resolve_target(slug, target_id, session)
        target_id_resolved = target.id

    try:
        job = await svc.start_backup(target_id_resolved)
    except BackupRunning:
        raise api_error(409, "backup_running", "Backup already running for this target")
    except SyncBusyError as exc:
        raise _busy(exc)
    except ValueError:
        raise api_error(404, "backup_target_not_found", f"Backup target {target_id} not found for profile '{slug}'")

    return _job_response(job)


@router.get("/{target_id}/jobs/{job_id}", response_model=BackupJobResponse)
async def get_backup_job(slug: str, target_id: int, job_id: int) -> BackupJobResponse:
    """A backup or restore run of the target: running, or how it ended."""
    db = _get_db_factory()
    async with db() as session:
        _, target = await _resolve_target(slug, target_id, session)
        job = await session.get(BackupJob, job_id)
        if job is None or job.target_id != target.id:
            raise api_error(404, "backup_job_not_found", f"Job {job_id} not found for backup target {target_id}")
        return _job_response(job)


@router.get("/{target_id}/snapshots", response_model=list[SnapshotResponse])
async def list_snapshots(slug: str, target_id: int) -> list[SnapshotResponse]:
    svc = _get_backup_service()
    db = _get_db_factory()

    async with db() as session:
        _, target = await _resolve_target(slug, target_id, session)
        return await svc.list_snapshots(target)


@router.post("/{target_id}/restore", status_code=202, response_model=BackupJobResponse)
@audited("backup.restore", lambda kw: {"snapshot": kw["request"].snapshot_id, "scope": kw["request"].restore_scope}, profile="slug", target="target_id")
async def restore_from_snapshot(
    slug: str, target_id: int, request: RestoreRequest,
) -> BackupJobResponse:
    """Start a full restore; answers 202 with the running job (follow it with GET .../jobs/{job_id}).

    Refused at once with 409 sync_busy while a sync, backup or restore of
    the profile runs or waits. An unknown snapshot fails the job
    (snapshot_not_found).
    """
    svc = _get_backup_service()
    db = _get_db_factory()

    async with db() as session:
        profile, target = await _resolve_target(slug, target_id, session)
        target_id_resolved = target.id

    # A profile stored before the data-directory check existed may still
    # point there; never restore over OmniSync's own files.
    if request.restore_scope != RestoreScope.REMOTE_ONLY:
        try:
            check_not_data_dir(profile.local_dir, "The profile's local folder")
        except ValueError as exc:
            raise api_error(422, "path_not_allowed", str(exc))

    try:
        job = await svc.start_restore(target_id_resolved, request.snapshot_id, request.restore_scope)
    except SyncBusyError as exc:
        raise _busy(exc)
    except ValueError:
        raise api_error(404, "backup_target_not_found", f"Backup target {target_id} not found for profile '{slug}'")
    return _job_response(job)


async def _snapshot_call(what: str, call):  # noqa: ANN001, ANN202 - a coroutine and its result
    """Await a snapshot read and map its failures to HTTP errors.

    ValueError (unknown or malformed snapshot) -> 404; RestoreRefused (a
    backup that cannot be read as needed, a missing local folder) -> 409
    with its message; SyncBusyError -> 409 sync_busy; rclone failures ->
    502, details in the log.
    """
    try:
        return await call
    except ValueError as exc:
        raise api_error(404, "snapshot_not_found", str(exc))
    except RestoreRefused as exc:
        raise api_error(409, "snapshot_unavailable", str(exc))
    except SyncBusyError as exc:
        raise _busy(exc)
    except RcloneError:
        logger.exception("%s failed", what)
        raise api_error(502, "rclone_failed", f"{what} failed: rclone could not read the backup. {SEE_LOG}")


@router.get("/{target_id}/snapshots/{snapshot_id}/files", response_model=SnapshotFilesResponse)
async def list_snapshot_files(
    slug: str,
    target_id: int,
    snapshot_id: str,
    path: str = Query("", max_length=4096, description="Folder to list; '' for the top"),
    search: str | None = Query(None, max_length=255, description="Every file whose path contains this"),
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
) -> SnapshotFilesResponse:
    """One folder of a snapshot (or the files matching ``search``), a page at a time.

    Folders come first and carry the number and size of the files in them.
    An archive on a remote is downloaded (streamed) once to read its file
    list; later pages come from memory.
    """
    svc = _get_backup_service()
    db = _get_db_factory()
    try:
        check_snapshot_id(snapshot_id)
    except ValueError as exc:
        raise api_error(422, "invalid_snapshot_id", str(exc))

    async with db() as session:
        _, target = await _resolve_target(slug, target_id, session)

    files = await _snapshot_call("Listing the snapshot", svc.snapshot_files(target, snapshot_id))
    entries = browse_snapshot(files, path, search.strip() if search else None)
    return SnapshotFilesResponse(
        snapshot_id=snapshot_id, path=path.strip("/"), search=search or None,
        entries=entries[offset:offset + limit], total=len(entries), offset=offset, limit=limit,
        snapshot_files=len(files),
    )


@router.post("/{target_id}/restore/preview", response_model=RestorePreviewResponse)
async def preview_restore(slug: str, target_id: int, request: RestoreRequest) -> RestorePreviewResponse:
    """What a full restore would add, replace and remove on each side; changes nothing."""
    svc = _get_backup_service()
    db = _get_db_factory()

    async with db() as session:
        _, target = await _resolve_target(slug, target_id, session)
        target_id_resolved = target.id

    return await _snapshot_call(
        "The restore preview", svc.restore_preview(target_id_resolved, request.snapshot_id, request.restore_scope),
    )


@router.post("/{target_id}/restore-files", status_code=202, response_model=BackupJobResponse)
@audited("backup.restore_files", lambda kw: {"snapshot": kw["request"].snapshot_id, "files": len(kw["request"].paths), "elsewhere": kw["request"].target_dir is not None}, profile="slug", target="target_id")
async def restore_snapshot_files(slug: str, target_id: int, request: RestoreFilesRequest) -> BackupJobResponse:
    """Restore chosen files and folders of a snapshot, to their place or into another local folder.

    Only the chosen files are written and replaced ones are kept in the
    destination's ``.omnisync-trash/pre-restore/``; restored into a mirror
    profile's local folder, automatic syncing is paused so the next pull
    does not remove them (see BackupService.restore_files). The snapshot is
    read first (404, 409 snapshot_unavailable); then the run starts and
    the answer is 202 with the running job, or 409 sync_busy.
    """
    svc = _get_backup_service()
    db = _get_db_factory()

    async with db() as session:
        profile, target = await _resolve_target(slug, target_id, session)
        target_id_resolved = target.id
        if request.target_dir is not None:
            try:
                check_in_browse_roots(request.target_dir, "target_dir")
            except ValueError as exc:
                raise api_error(422, "path_not_allowed", str(exc))
            for other in (await session.execute(select(BackupTarget))).scalars():
                if backup_paths_overlap(request.target_dir, other.target_path):
                    raise api_error(
                        422, "path_overlap",
                        f"target_dir cannot be the same as, inside, or contain backup target '{other.name}'",
                    )

    if request.target_dir is None:
        try:
            check_not_data_dir(profile.local_dir, "The profile's local folder")
        except ValueError as exc:
            raise api_error(422, "path_not_allowed", str(exc))

    job = await _snapshot_call(
        "Restoring the selected files",
        svc.start_restore_files(target_id_resolved, request.snapshot_id, request.paths, request.target_dir),
    )
    return _job_response(job)
