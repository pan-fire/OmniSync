"""SQLAlchemy ORM models for OmniSync database.

The schema is owned by the Alembic revisions in backend/migrations/versions:
a change here needs a matching revision (``alembic -c backend/alembic.ini
revision --autogenerate -m ...``), or existing databases fall behind the
models. test_migrations.py fails when the two disagree.

Foreign keys carry ON DELETE CASCADE and are enforced (backend.db.database
turns on PRAGMA foreign_keys for every connection). The sync history
relationships (jobs, conflicts, file changes, errors) use passive_deletes:
deleting a profile or job lets SQLite remove those rows rather than the ORM
loading every one first. The small configuration collections (manual flags,
backup targets and their jobs) are deleted by the ORM, which keeps objects
already loaded in the session consistent.
"""

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, false, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class BackupTargetType(str, enum.Enum):
    LOCAL = "local"
    REMOTE = "remote"
    CUSTOM_REMOTE = "custom_remote"


class BackupMode(str, enum.Enum):
    ARCHIVE = "archive"
    MIRROR = "mirror"


class BackupJobStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


class SyncProfile(Base):
    """A sync profile linking a local directory to a remote directory."""

    __tablename__ = "sync_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    local_dir: Mapped[str] = mapped_column(String(1024))
    remote_dir: Mapped[str] = mapped_column(String(1024))
    debounce_seconds: Mapped[int] = mapped_column(Integer, default=5)
    pull_interval_minutes: Mapped[int] = mapped_column(Integer, default=5)
    rclone_filter: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    rclone_args: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    backup_dir: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    # "two_way" (rclone bisync) or "mirror" (push local changes, pull on the
    # interval). Rows from before 0004 are "mirror"; the API creates two_way.
    sync_mode: Mapped[str] = mapped_column(String(10), default="mirror", server_default="mirror")
    # The user dismissed the mirror-mode explanation for this profile (web UI
    # and TUI). Only shown for mirror profiles; kept when the mode changes.
    mirror_notice_dismissed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Why automatic syncing is held (e.g. a one-sided restore), or None. Kept
    # across restarts and engine replacements until the user resumes or a
    # sync of the whole profile succeeds (SyncEngine.hold).
    pause_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # rclone --bwlimit for this profile's syncs (a rate or a timetable), or None.
    bwlimit: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # JSON {"days": [0..6], "start": "HH:MM", "end": "HH:MM"}: automatic
    # syncs run only inside this window (server time); None: any time.
    sync_window: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The user paused automatic syncing (Pause / Pause all). Separate from
    # pause_reason: only the user's resume lifts it, never a successful sync.
    user_paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    jobs: Mapped[list["SyncJob"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True,
    )
    profile_conflicts: Mapped[list["Conflict"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True,
    )
    manual_flags: Mapped[list["ManualFlag"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan",
    )
    backup_targets: Mapped[list["BackupTarget"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan",
    )


class SyncJob(Base):
    """Tracks a single sync session (push or pull) from start to finish."""

    __tablename__ = "sync_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # NULL only for jobs recorded before profiles existed.
    profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("sync_profiles.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    direction: Mapped[str] = mapped_column(String(10))  # a JobDirection value
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20))  # "running", "completed", "failed"
    files_changed: Mapped[int] = mapped_column(Integer, default=0)
    conflicts: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)

    profile: Mapped["SyncProfile | None"] = relationship(back_populates="jobs")
    file_changes: Mapped[list["FileChange"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True,
    )
    sync_errors: Mapped[list["SyncError"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True,
    )
    job_conflicts: Mapped[list["Conflict"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True,
    )


class FileChange(Base):
    """Records a single file created, modified, or deleted during a sync job."""

    __tablename__ = "file_changes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("sync_jobs.id", ondelete="CASCADE"), index=True)
    file_path: Mapped[str] = mapped_column(String(1024))
    action: Mapped[str] = mapped_column(String(20))  # "created", "modified", "deleted"
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    side: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "local" / "remote"

    job: Mapped["SyncJob"] = relationship(back_populates="file_changes")


class Conflict(Base):
    """A file changed on both sides since the last sync.

    Diffs record these per profile (one unresolved row per path); job_id is
    set only for a conflict that a sync job recorded. A two-way sync keeps
    both versions and records where: local_kept_as / remote_kept_as.
    """

    __tablename__ = "conflicts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("sync_profiles.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("sync_jobs.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    file_path: Mapped[str] = mapped_column(String(1024))
    local_modified: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remote_modified: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution: Mapped[str | None] = mapped_column(String(20), nullable=True)
    local_kept_as: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    remote_kept_as: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    profile: Mapped["SyncProfile | None"] = relationship(back_populates="profile_conflicts")
    job: Mapped["SyncJob | None"] = relationship(back_populates="job_conflicts")


class SyncError(Base):
    """Records an error that occurred during a sync job."""

    __tablename__ = "sync_errors"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("sync_jobs.id", ondelete="CASCADE"), index=True)
    message: Mapped[str] = mapped_column(String(2048))
    stderr_output: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    job: Mapped["SyncJob"] = relationship(back_populates="sync_errors")


class Remote(Base):
    """Stores rclone remote configurations."""

    __tablename__ = "remotes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    type: Mapped[str] = mapped_column(String(50))
    last_verified: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ManualFlag(Base):
    """Tracks files marked for manual resolution outside the app.

    A flag belongs to one profile: the same relative path in another profile
    is a different file.
    """

    __tablename__ = "manual_flags"
    __table_args__ = (
        UniqueConstraint("profile_id", "file_path", name="uq_manual_flags_profile_path"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("sync_profiles.id", ondelete="CASCADE"))
    file_path: Mapped[str] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime)

    profile: Mapped["SyncProfile"] = relationship(back_populates="manual_flags")


class NotificationLog(Base):
    """Records dispatched notification events for history."""

    __tablename__ = "notification_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(50))
    severity: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(2000))
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    channels_delivered: Mapped[str] = mapped_column(String(500))  # JSON array
    profile_slug: Mapped[str | None] = mapped_column(String(255), nullable=True)


class PushSubscription(Base):
    """Stores browser Web Push subscriptions."""

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    endpoint: Mapped[str] = mapped_column(String(2048), unique=True)
    p256dh_key: Mapped[str] = mapped_column(String(512))
    auth_key: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class BackupTarget(Base):
    """A backup destination for a sync profile."""

    __tablename__ = "backup_targets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("sync_profiles.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    target_path: Mapped[str] = mapped_column(String(1024))
    target_type: Mapped[str] = mapped_column(String(20))  # BackupTargetType value
    remote_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retention_days: Mapped[int] = mapped_column(Integer, default=7)
    # Retention never deletes the newest keep_last snapshots, however old.
    keep_last: Mapped[int] = mapped_column(Integer, default=3, server_default=text("3"))
    frequency_hours: Mapped[int] = mapped_column(Integer, default=24)
    backup_mode: Mapped[str] = mapped_column(String(10))  # BackupMode value
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_liveness_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_liveness_error: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # The passphrase of an encrypted target, in rclone's obscured form (as in
    # rclone.conf), or None for an unencrypted target. Never sent by the API.
    encryption_password: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Compare the backup with the folder after each run (rclone check /
    # cryptcheck for mirrors, an archive read-back for archives).
    verify_after_backup: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)

    profile: Mapped["SyncProfile"] = relationship(back_populates="backup_targets")
    backup_jobs: Mapped[list["BackupJob"]] = relationship(
        back_populates="target", cascade="all, delete-orphan",
    )


class BackupJob(Base):
    """Records a single backup or restore execution."""

    __tablename__ = "backup_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    target_id: Mapped[int] = mapped_column(
        ForeignKey("backup_targets.id", ondelete="CASCADE"), index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20))  # BackupJobStatus value
    direction: Mapped[str] = mapped_column(String(10), default="backup")
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    # Why it failed or was skipped, as a stable code (backup_service.jobs.JOB_ERRORS);
    # error_message keeps the (redacted) details. None for jobs recorded before codes.
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Outcome of the verification after a backup: "verified", "failed" or
    # None (not verified), with a one-line summary.
    verify_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    verify_message: Mapped[str | None] = mapped_column(String(4096), nullable=True)

    target: Mapped["BackupTarget"] = relationship(back_populates="backup_jobs")

