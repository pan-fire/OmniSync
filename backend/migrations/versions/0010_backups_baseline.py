"""Baseline: the schema of OmniSync 0.12.0, created in one step.

The revision ID is the head revision of the 0.12.0 release, so a database
that release created is already at this revision and nothing runs for it;
a new database gets the whole schema from here. Later schema changes are
new revisions on top of this one.

Revision ID: 0010_backups
Revises:
Create Date: 2026-10-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_backups"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Children after the tables they reference.
TABLES = (
    "sync_profiles", "remotes", "notification_log", "push_subscriptions", "sync_jobs",
    "manual_flags", "backup_targets", "file_changes", "conflicts", "sync_errors", "backup_jobs",
)


def upgrade() -> None:
    op.create_table(
        "sync_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("local_dir", sa.String(length=1024), nullable=False),
        sa.Column("remote_dir", sa.String(length=1024), nullable=False),
        sa.Column("debounce_seconds", sa.Integer(), nullable=False),
        sa.Column("pull_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("rclone_filter", sa.Text(), nullable=False),
        sa.Column("rclone_args", sa.Text(), nullable=False),
        sa.Column("backup_dir", sa.String(length=1024), nullable=True),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("sync_mode", sa.String(length=10), nullable=False, server_default="mirror"),
        sa.Column("mirror_notice_dismissed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pause_reason", sa.Text(), nullable=True),
        sa.Column("bwlimit", sa.String(length=500), nullable=True),
        sa.Column("sync_window", sa.Text(), nullable=True),
        sa.Column("user_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_profiles_slug", "sync_profiles", ["slug"], unique=True)

    op.create_table(
        "remotes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("last_verified", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "notification_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.String(length=2000), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("channels_delivered", sa.String(length=500), nullable=False),
        sa.Column("profile_slug", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("endpoint", sa.String(length=2048), nullable=False),
        sa.Column("p256dh_key", sa.String(length=512), nullable=False),
        sa.Column("auth_key", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint"),
    )

    op.create_table(
        "sync_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("files_changed", sa.Integer(), nullable=False),
        sa.Column("conflicts", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["sync_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_jobs_profile_id", "sync_jobs", ["profile_id"])

    op.create_table(
        "manual_flags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "file_path", name="uq_manual_flags_profile_path"),
        sa.ForeignKeyConstraint(["profile_id"], ["sync_profiles.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "backup_targets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("target_path", sa.String(length=1024), nullable=False),
        sa.Column("target_type", sa.String(length=20), nullable=False),
        sa.Column("remote_name", sa.String(length=255), nullable=True),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("frequency_hours", sa.Integer(), nullable=False),
        sa.Column("backup_mode", sa.String(length=10), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_liveness_ok", sa.Boolean(), nullable=True),
        sa.Column("last_liveness_error", sa.String(length=2048), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("keep_last", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("encryption_password", sa.String(length=2048), nullable=True),
        sa.Column("verify_after_backup", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["profile_id"], ["sync_profiles.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_backup_targets_profile_id", "backup_targets", ["profile_id"])

    op.create_table(
        "file_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("side", sa.String(length=10), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["job_id"], ["sync_jobs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_file_changes_job_id", "file_changes", ["job_id"])

    op.create_table(
        "conflicts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("local_modified", sa.DateTime(), nullable=True),
        sa.Column("remote_modified", sa.DateTime(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("resolution", sa.String(length=20), nullable=True),
        sa.Column("local_kept_as", sa.String(length=1024), nullable=True),
        sa.Column("remote_kept_as", sa.String(length=1024), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["profile_id"], ["sync_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["sync_jobs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_conflicts_job_id", "conflicts", ["job_id"])
    op.create_index("ix_conflicts_profile_id", "conflicts", ["profile_id"])

    op.create_table(
        "sync_errors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("message", sa.String(length=2048), nullable=False),
        sa.Column("stderr_output", sa.String(length=4096), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["job_id"], ["sync_jobs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_sync_errors_job_id", "sync_errors", ["job_id"])

    op.create_table(
        "backup_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("snapshot_id", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.String(length=4096), nullable=True),
        sa.Column("verify_status", sa.String(length=20), nullable=True),
        sa.Column("verify_message", sa.String(length=4096), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["target_id"], ["backup_targets.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_backup_jobs_target_id", "backup_jobs", ["target_id"])


def downgrade() -> None:
    for name in reversed(TABLES):
        op.drop_table(name)
