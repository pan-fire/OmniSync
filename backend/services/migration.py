"""One-time migration from flat TOML config to profile-based sync."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from backend.api import schemas
from backend.api.schemas import ProfileCreateRequest, check_backup_target
from backend.db.models import BackupMode, BackupTarget, BackupTargetType, SyncProfile
from backend.services.config import ConfigService
from backend.services.profile_service import ProfileService

logger = logging.getLogger(__name__)

_LEGACY_KEYS = (
    "local_dir", "remote_dir", "debounce_seconds", "pull_interval_minutes",
    "rclone_filter", "rclone_args", "backup_dir", "max_retries",
)


def _clamp(value: object, low: int, high: int, default: int) -> int:
    """A legacy numeric setting, forced into the range the API accepts."""
    try:
        number = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return default
    return min(max(number, low), high)


def extract_legacy_profile(config_service: ConfigService) -> dict | None:
    """Return per-profile values from TOML if present, else None."""
    raw = config_service._load_toml()
    has_legacy = any(k in raw for k in ("local_dir", "remote_dir"))
    if not has_legacy:
        return None

    defaults = {
        "local_dir": "/sync/local",
        "remote_dir": "remote:backup",
        "debounce_seconds": 5,
        "pull_interval_minutes": 5,
        "rclone_filter": [],
        "rclone_args": [],
        "backup_dir": None,
        "max_retries": 3,
    }
    result = {}
    for key in _LEGACY_KEYS:
        result[key] = raw.get(key, defaults.get(key))
    return result


# What a legacy setting the API would refuse is replaced with. The paths are
# the placeholders a fresh install starts with; a profile that gets any of
# these is created disabled, for the user to fix before enabling it.
_REPLACEMENTS: dict[str, object] = {
    "local_dir": "/sync/local",
    "remote_dir": "remote:backup",
    "rclone_filter": [],
    "rclone_args": [],
    "backup_dir": None,
}


def _legacy_request(values: dict) -> tuple[ProfileCreateRequest, dict[str, object]]:
    """The create request for the migrated profile.

    Settings that fail the API's validation (a relative path, a remote path
    without 'remote:', an rclone flag the API refuses, a value of the wrong
    type) are swapped for _REPLACEMENTS instead of aborting startup.
    Returns the request and the original values that were replaced.
    """
    values = dict(values)
    replaced: dict[str, object] = {}
    while True:
        try:
            return ProfileCreateRequest(**values), replaced
        except ValidationError as exc:
            invalid = {str(err["loc"][0]) for err in exc.errors() if err.get("loc")}
            fixable = (invalid & _REPLACEMENTS.keys()) - replaced.keys()
            if not fixable:
                raise
            for field in sorted(fixable):
                replaced[field] = values.get(field)
                values[field] = _REPLACEMENTS[field]


def remove_legacy_keys(config_service: ConfigService) -> None:
    """Rewrite TOML without per-profile keys."""
    raw = config_service._load_toml()
    changed = False
    for key in _LEGACY_KEYS:
        if key in raw:
            del raw[key]
            changed = True
    if changed:
        config_service._save_toml(raw)


async def migrate_legacy_config(
    config_service: ConfigService,
    profile_service: ProfileService,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """If TOML has legacy per-profile keys and no profiles exist, create a Default profile."""
    # Skip if profiles table already has rows
    existing = await profile_service.get_all()
    if existing:
        return

    legacy = extract_legacy_profile(config_service)
    if legacy is None:
        return

    local_dir = legacy.get("local_dir", "")
    remote_dir = legacy.get("remote_dir", "")

    request, replaced = _legacy_request({
        "name": "Default",
        "local_dir": local_dir or "/sync/local",
        "remote_dir": remote_dir or "remote:backup",
        "debounce_seconds": _clamp(
            legacy.get("debounce_seconds", 5),
            schemas.DEBOUNCE_SECONDS_MIN, schemas.DEBOUNCE_SECONDS_MAX, 5,
        ),
        "pull_interval_minutes": _clamp(
            legacy.get("pull_interval_minutes", 5),
            schemas.PULL_INTERVAL_MINUTES_MIN, schemas.PULL_INTERVAL_MINUTES_MAX, 5,
        ),
        "rclone_filter": legacy.get("rclone_filter", []),
        "rclone_args": legacy.get("rclone_args", []),
        "backup_dir": legacy.get("backup_dir"),
        "max_retries": _clamp(
            legacy.get("max_retries", 3), schemas.MAX_RETRIES_MIN, schemas.MAX_RETRIES_MAX, 3,
        ),
    })
    valid = bool(not replaced and local_dir and local_dir != "/sync/local" and remote_dir)
    profile = await profile_service.create(request)

    if not valid:
        await profile_service.set_enabled(profile.slug, False)
        logger.warning(
            "Legacy migration: created 'Default' profile as DISABLED (invalid or missing settings; "
            "legacy paths: local=%r, remote=%r). Fix it in the profile settings, then enable it.",
            local_dir, remote_dir,
        )
        for field, value in replaced.items():
            logger.warning(
                "Legacy migration: %s %r is not valid and was replaced with %r",
                field, value, _REPLACEMENTS[field],
            )
    else:
        logger.info(
            "Legacy migration: created 'Default' profile (%s ↔ %s)",
            local_dir, remote_dir,
        )

    remove_legacy_keys(config_service)


def _infer_target_type(backup_dir: str) -> BackupTargetType:
    """Infer backup target type from path format.

    Paths containing ':' are remote (e.g. 'remote:path/to/backup').
    All others are local filesystem paths.
    """
    if ":" in backup_dir:
        return BackupTargetType.REMOTE
    return BackupTargetType.LOCAL


def _extract_remote_name(backup_dir: str) -> str | None:
    """Extract remote name from a path like 'myremote:path/to/backup'."""
    if ":" in backup_dir:
        return backup_dir.split(":", 1)[0]
    return None


async def migrate_backup_dir_to_targets(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Migrate legacy backup_dir fields to BackupTarget rows.

    For each profile with a non-null backup_dir that has no existing
    backup_targets, creates a BackupTarget row and nulls the backup_dir.
    Idempotent — safe to call on every startup.
    """
    async with db_session_factory() as session:
        stmt = (
            select(SyncProfile)
            .where(SyncProfile.backup_dir.isnot(None))
            .where(SyncProfile.backup_dir != "")
            .options(selectinload(SyncProfile.backup_targets))
        )
        result = await session.execute(stmt)
        profiles = result.scalars().all()

        for profile in profiles:
            if profile.backup_targets:
                logger.debug(
                    "Skipping backup_dir migration for '%s' — already has targets",
                    profile.name,
                )
                continue

            target_type = _infer_target_type(profile.backup_dir)  # type: ignore[arg-type]
            remote_name = _extract_remote_name(profile.backup_dir)  # type: ignore[arg-type]
            # The API refuses such a target; keep it for the user to fix,
            # but never run rclone on it.
            try:
                check_backup_target(target_type, profile.backup_dir, remote_name)  # type: ignore[arg-type]
                enabled = True
            except ValueError as exc:
                enabled = False
                remote_name = None
                logger.warning(
                    "Legacy migration: backup_dir %r of profile '%s' is not a valid backup target (%s); "
                    "created it DISABLED. Fix its path, then enable it.",
                    profile.backup_dir, profile.name, exc,
                )
            now = datetime.now(timezone.utc)
            target = BackupTarget(
                profile_id=profile.id,
                name="Legacy backup",
                target_path=profile.backup_dir,  # type: ignore[arg-type]
                target_type=target_type.value,
                remote_name=remote_name,
                retention_days=7,
                frequency_hours=24,
                backup_mode=BackupMode.MIRROR.value,
                enabled=enabled,
                created_at=now,
                updated_at=now,
            )
            session.add(target)
            profile.backup_dir = None  # type: ignore[assignment]

            logger.info(
                "Migrated backup_dir → BackupTarget for profile '%s' (type=%s, path=%s)",
                profile.name,
                target_type.value,
                target.target_path,
            )

        await session.commit()
