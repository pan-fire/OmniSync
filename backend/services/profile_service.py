"""CRUD service for sync profiles."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.api.schemas import ProfileCreateRequest, ProfileUpdateRequest, profile_slug
from backend.db.models import BackupTarget, SyncProfile
from backend.exceptions import ProfileConflictError, ProfileNotFoundError
from backend.services.path_overlap import locations_overlap

logger = logging.getLogger(__name__)


class ProfileService:
    """Manages sync profile lifecycle — create, read, update, delete."""

    def __init__(self, db_session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._db_session_factory = db_session_factory

    @staticmethod
    def generate_slug(name: str) -> str:
        """Convert a profile name into a URL-safe slug."""
        return profile_slug(name)

    async def create(self, request: ProfileCreateRequest) -> SyncProfile:
        """Create a new sync profile."""
        async with self._db_session_factory() as session:
            await self._validate_paths(session, request.local_dir, request.remote_dir)
            base_slug = self.generate_slug(request.name)
            slug = await self._ensure_unique_slug(session, base_slug)
            now = datetime.now(timezone.utc)
            profile = SyncProfile(
                slug=slug,
                name=request.name,
                local_dir=request.local_dir,
                remote_dir=request.remote_dir,
                debounce_seconds=request.debounce_seconds,
                pull_interval_minutes=request.pull_interval_minutes,
                rclone_filter=json.dumps(request.rclone_filter),
                rclone_args=json.dumps(request.rclone_args),
                max_retries=request.max_retries,
                sync_mode=request.sync_mode.value,
                bwlimit=request.bwlimit,
                sync_window=request.sync_window.model_dump_json() if request.sync_window else None,
                enabled=True,
                created_at=now,
                updated_at=now,
            )
            session.add(profile)
            await session.commit()
            await session.refresh(profile)
            return profile

    async def get_all(self) -> list[SyncProfile]:
        """Return all profiles."""
        async with self._db_session_factory() as session:
            result = await session.execute(
                select(SyncProfile).order_by(SyncProfile.name)
            )
            return list(result.scalars().all())

    async def get_by_slug(self, slug: str) -> SyncProfile:
        """Return a profile by slug or raise ProfileNotFoundError."""
        async with self._db_session_factory() as session:
            result = await session.execute(
                select(SyncProfile).where(SyncProfile.slug == slug)
            )
            profile = result.scalar_one_or_none()
            if profile is None:
                raise ProfileNotFoundError(slug)
            return profile

    async def update(self, slug: str, request: ProfileUpdateRequest) -> SyncProfile:
        """Partial update of a profile."""
        async with self._db_session_factory() as session:
            result = await session.execute(
                select(SyncProfile).where(SyncProfile.slug == slug)
            )
            profile = result.scalar_one_or_none()
            if profile is None:
                raise ProfileNotFoundError(slug)

            updates = request.model_dump(exclude_none=True)
            if profile.enabled and ("local_dir" in updates or "remote_dir" in updates):
                await self._validate_paths(
                    session,
                    updates.get("local_dir", profile.local_dir),
                    updates.get("remote_dir", profile.remote_dir),
                    exclude_id=profile.id,
                )

            # Serialise list fields to JSON
            if "rclone_filter" in updates:
                updates["rclone_filter"] = json.dumps(updates["rclone_filter"])
            if "rclone_args" in updates:
                updates["rclone_args"] = json.dumps(updates["rclone_args"])
            if "sync_mode" in updates:
                updates["sync_mode"] = updates["sync_mode"].value if hasattr(updates["sync_mode"], "value") else updates["sync_mode"]
            # bwlimit and sync_window: sent as null (or "" for bwlimit) clears them.
            if "bwlimit" in request.model_fields_set:
                updates["bwlimit"] = request.bwlimit
            if "sync_window" in request.model_fields_set:
                updates["sync_window"] = request.sync_window.model_dump_json() if request.sync_window else None

            for key, value in updates.items():
                setattr(profile, key, value)
            profile.updated_at = datetime.now(timezone.utc)

            # Regenerate slug if name changed
            if "name" in updates:
                base_slug = self.generate_slug(updates["name"])
                profile.slug = await self._ensure_unique_slug(session, base_slug, exclude_id=profile.id)

            await session.commit()
            await session.refresh(profile)
            return profile

    async def delete(self, slug: str) -> None:
        """Delete a profile by slug (cascades to jobs/conflicts/flags)."""
        async with self._db_session_factory() as session:
            result = await session.execute(
                select(SyncProfile).where(SyncProfile.slug == slug)
            )
            profile = result.scalar_one_or_none()
            if profile is None:
                raise ProfileNotFoundError(slug)
            await session.delete(profile)
            await session.commit()

    async def set_enabled(self, slug: str, enabled: bool) -> SyncProfile:
        """Enable or disable a profile."""
        async with self._db_session_factory() as session:
            result = await session.execute(
                select(SyncProfile).where(SyncProfile.slug == slug)
            )
            profile = result.scalar_one_or_none()
            if profile is None:
                raise ProfileNotFoundError(slug)
            if enabled:
                await self._validate_paths(
                    session, profile.local_dir, profile.remote_dir, exclude_id=profile.id,
                )
            profile.enabled = enabled
            profile.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(profile)
            return profile

    async def set_user_paused(self, profile_id: int, paused: bool) -> None:
        """Store the user's pause of a profile whose engine does not run (see SyncEngine.pause_by_user)."""
        async with self._db_session_factory() as session:
            profile = await session.get(SyncProfile, profile_id)
            if profile is not None:
                profile.user_paused = paused
                await session.commit()

    async def _ensure_unique_slug(
        self, session: AsyncSession, base_slug: str, exclude_id: int | None = None,
    ) -> str:
        """Append a numeric suffix if the slug already exists."""
        slug = base_slug
        counter = 2
        while True:
            stmt = select(SyncProfile).where(SyncProfile.slug == slug)
            if exclude_id is not None:
                stmt = stmt.where(SyncProfile.id != exclude_id)
            result = await session.execute(stmt)
            if result.scalar_one_or_none() is None:
                return slug
            slug = f"{base_slug}-{counter}"
            counter += 1

    async def _validate_paths(
        self,
        session: AsyncSession,
        local_dir: str,
        remote_dir: str,
        exclude_id: int | None = None,
    ) -> None:
        """Raise ProfileConflictError if the folders overlap another enabled profile's.

        Overlap is the same folder or one inside the other, after
        normalising (trailing slashes, symlinks, "..") -- two profiles
        syncing nested folders would delete each other's files. A backup
        target (of any profile) inside the synced folders is rejected too.
        """
        stmt = select(SyncProfile).where(SyncProfile.enabled == True)  # noqa: E712
        if exclude_id is not None:
            stmt = stmt.where(SyncProfile.id != exclude_id)
        for other in (await session.execute(stmt)).scalars():
            if locations_overlap(local_dir, other.local_dir):
                raise ProfileConflictError(local_dir, other.slug, "local_dir", f"local_dir '{other.local_dir}'")
            if locations_overlap(remote_dir, other.remote_dir):
                raise ProfileConflictError(remote_dir, other.slug, "remote_dir", f"remote_dir '{other.remote_dir}'")

        targets = await session.execute(
            select(BackupTarget, SyncProfile.slug).join(SyncProfile, BackupTarget.profile_id == SyncProfile.id)
        )
        for target, slug in targets:
            for field, path in (("local_dir", local_dir), ("remote_dir", remote_dir)):
                if locations_overlap(path, target.target_path):
                    raise ProfileConflictError(
                        path, slug, field, f"backup target '{target.name}' ({target.target_path})",
                    )
