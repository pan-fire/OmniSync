"""Frozen dataclass representing a sync profile's configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.db.models import SyncProfile


@dataclass(frozen=True)
class ProfileConfig:
    """Immutable snapshot of a sync profile's settings.

    Used by SyncEngine instead of ConfigService so each engine
    only sees its own profile's values.
    """

    profile_id: int
    slug: str
    name: str
    local_dir: str
    remote_dir: str
    debounce_seconds: int = 5
    pull_interval_minutes: int = 5
    rclone_filter: list[str] = field(default_factory=list)
    rclone_args: list[str] = field(default_factory=list)
    max_retries: int = 3
    sync_mode: str = "mirror"  # a SyncMode value
    bwlimit: str | None = None  # rclone --bwlimit (rate or timetable)
    # {"days": [0..6], "start": "HH:MM", "end": "HH:MM"}, see SyncWindow
    sync_window: dict | None = None

    @property
    def two_way(self) -> bool:
        return self.sync_mode == "two_way"

    @classmethod
    def from_orm(cls, profile: SyncProfile) -> ProfileConfig:
        """Build a ProfileConfig from a SyncProfile ORM instance."""
        try:
            rclone_filter = json.loads(profile.rclone_filter) if profile.rclone_filter else []
        except (json.JSONDecodeError, TypeError):
            rclone_filter = []

        try:
            rclone_args = json.loads(profile.rclone_args) if profile.rclone_args else []
        except (json.JSONDecodeError, TypeError):
            rclone_args = []

        try:
            stored = profile.sync_window
            window = json.loads(stored) if stored else None
        except (json.JSONDecodeError, TypeError):
            window = None

        return cls(
            profile_id=profile.id,
            slug=profile.slug,
            name=profile.name,
            local_dir=profile.local_dir,
            remote_dir=profile.remote_dir,
            debounce_seconds=profile.debounce_seconds,
            pull_interval_minutes=profile.pull_interval_minutes,
            rclone_filter=rclone_filter,
            rclone_args=rclone_args,
            max_retries=profile.max_retries,
            sync_mode=profile.sync_mode,
            bwlimit=profile.bwlimit or None,
            sync_window=window if isinstance(window, dict) else None,
        )
