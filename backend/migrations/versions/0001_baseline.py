"""Baseline: the schema as it was before Alembic.

Until this revision the app built its schema with ``Base.metadata.create_all``
plus a hand-written list of ``ALTER TABLE ... ADD COLUMN`` statements, run on
every startup. This revision reproduces that schema exactly.

It also adopts databases created by that code. Such a database has no
``alembic_version`` table, so Alembic runs this revision against it: every
table and column that already exists is left alone, and whatever an older
release had not created yet (a table added later, or a profile column the
old ALTER list would have added) is created the way the old code did. The
database then continues through the later revisions like any other.

Revision ID: 0001
Revises:
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _sync_profiles() -> None:
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_profiles_slug", "sync_profiles", ["slug"], unique=True)


def _remotes() -> None:
    op.create_table(
        "remotes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("last_verified", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )


def _notification_log() -> None:
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


def _push_subscriptions() -> None:
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


def _sync_jobs() -> None:
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


def _manual_flags() -> None:
    op.create_table(
        "manual_flags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["sync_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_path"),
    )


def _backup_targets() -> None:
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
        sa.ForeignKeyConstraint(["profile_id"], ["sync_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backup_targets_profile_id", "backup_targets", ["profile_id"])


def _file_changes() -> None:
    op.create_table(
        "file_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["sync_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def _conflicts() -> None:
    op.create_table(
        "conflicts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("local_modified", sa.DateTime(), nullable=True),
        sa.Column("remote_modified", sa.DateTime(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("resolution", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["sync_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["sync_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def _sync_errors() -> None:
    op.create_table(
        "sync_errors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("message", sa.String(length=2048), nullable=False),
        sa.Column("stderr_output", sa.String(length=4096), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["sync_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def _backup_jobs() -> None:
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
        sa.ForeignKeyConstraint(["target_id"], ["backup_targets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backup_jobs_target_id", "backup_jobs", ["target_id"])


# In dependency order: a table comes after the tables it references.
TABLES = (
    ("sync_profiles", _sync_profiles),
    ("remotes", _remotes),
    ("notification_log", _notification_log),
    ("push_subscriptions", _push_subscriptions),
    ("sync_jobs", _sync_jobs),
    ("manual_flags", _manual_flags),
    ("backup_targets", _backup_targets),
    ("file_changes", _file_changes),
    ("conflicts", _conflicts),
    ("sync_errors", _sync_errors),
    ("backup_jobs", _backup_jobs),
)

# Columns the pre-Alembic startup code added to tables created by earlier
# releases, with the exact statements it used.
LEGACY_COLUMNS = (
    ("sync_jobs", "profile_id", "ALTER TABLE sync_jobs ADD COLUMN profile_id INTEGER REFERENCES sync_profiles(id)"),
    ("conflicts", "profile_id", "ALTER TABLE conflicts ADD COLUMN profile_id INTEGER REFERENCES sync_profiles(id)"),
    ("manual_flags", "profile_id", "ALTER TABLE manual_flags ADD COLUMN profile_id INTEGER REFERENCES sync_profiles(id)"),
    ("notification_log", "profile_slug", "ALTER TABLE notification_log ADD COLUMN profile_slug VARCHAR(255)"),
)


def upgrade() -> None:
    if context.is_offline_mode():
        existing: set[str] = set()
    else:
        existing = set(sa.inspect(op.get_bind()).get_table_names())

    for name, create in TABLES:
        if name not in existing:
            create()

    if context.is_offline_mode():
        return
    inspector = sa.inspect(op.get_bind())
    for table, column, ddl in LEGACY_COLUMNS:
        if table in existing and column not in {c["name"] for c in inspector.get_columns(table)}:
            op.execute(ddl)


def downgrade() -> None:
    for name, _ in reversed(TABLES):
        op.drop_table(name)
