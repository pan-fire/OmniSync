"""Enforceable foreign keys with ON DELETE CASCADE; manual flags per profile.

* sync_jobs, file_changes, conflicts and sync_errors are rebuilt so every
  foreign key carries ON DELETE CASCADE (the profile_id columns an older
  release added with ALTER TABLE had no ON DELETE clause, and the job_id
  columns never had one), and the foreign key columns get indexes.
* manual_flags gets a NOT NULL profile_id and UNIQUE (profile_id, file_path)
  instead of UNIQUE (file_path). A flag written without a profile was, until
  now, honoured by every profile, so it becomes one flag per existing profile:
  nothing that was excluded from a sync before starts syncing now.
* Rows that point at a parent that no longer exists (left behind while
  foreign keys were not enforced) are removed first, so the rebuilt tables
  pass PRAGMA foreign_key_check.

SQLite cannot alter constraints in place, so each table is rebuilt with
batch_alter_table: create the new table, copy the rows, drop the old one,
rename. backend.db.database runs this with foreign key enforcement off and
inside one transaction.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("backend.migrations")


def _tables(cascade_jobs: bool) -> dict[str, sa.Table]:
    """Table definitions after (cascade_jobs=True) or before this revision."""
    meta = sa.MetaData()
    job_fk = {"ondelete": "CASCADE"} if cascade_jobs else {}
    indexes = cascade_jobs

    sync_jobs = sa.Table(
        "sync_jobs", meta,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("files_changed", sa.Integer(), nullable=False),
        sa.Column("conflicts", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["sync_profiles.id"], ondelete="CASCADE"),
    )
    file_changes = sa.Table(
        "file_changes", meta,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["sync_jobs.id"], **job_fk),
    )
    conflicts = sa.Table(
        "conflicts", meta,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("local_modified", sa.DateTime(), nullable=True),
        sa.Column("remote_modified", sa.DateTime(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("resolution", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["sync_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["sync_jobs.id"], **job_fk),
    )
    sync_errors = sa.Table(
        "sync_errors", meta,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("message", sa.String(length=2048), nullable=False),
        sa.Column("stderr_output", sa.String(length=4096), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["sync_jobs.id"], **job_fk),
    )
    if cascade_jobs:
        manual_flags = sa.Table(
            "manual_flags", meta,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("profile_id", sa.Integer(), nullable=False),
            sa.Column("file_path", sa.String(length=1024), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["profile_id"], ["sync_profiles.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("profile_id", "file_path", name="uq_manual_flags_profile_path"),
        )
    else:
        manual_flags = sa.Table(
            "manual_flags", meta,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("profile_id", sa.Integer(), nullable=True),
            sa.Column("file_path", sa.String(length=1024), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["profile_id"], ["sync_profiles.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("file_path"),
        )
    if indexes:
        sa.Index("ix_sync_jobs_profile_id", sync_jobs.c.profile_id)
        sa.Index("ix_file_changes_job_id", file_changes.c.job_id)
        sa.Index("ix_conflicts_profile_id", conflicts.c.profile_id)
        sa.Index("ix_conflicts_job_id", conflicts.c.job_id)
        sa.Index("ix_sync_errors_job_id", sync_errors.c.job_id)
    return {
        t.name: t for t in (sync_jobs, file_changes, conflicts, sync_errors, manual_flags)
    }


# Children after parents, so each DELETE sees the previous step's result.
ORPHANS = (
    ("sync_jobs", "profile_id IS NOT NULL AND profile_id NOT IN (SELECT id FROM sync_profiles)"),
    ("file_changes", "job_id NOT IN (SELECT id FROM sync_jobs)"),
    ("sync_errors", "job_id NOT IN (SELECT id FROM sync_jobs)"),
    ("conflicts", "job_id NOT IN (SELECT id FROM sync_jobs)"
                  " OR (profile_id IS NOT NULL AND profile_id NOT IN (SELECT id FROM sync_profiles))"),
    ("manual_flags", "profile_id IS NOT NULL AND profile_id NOT IN (SELECT id FROM sync_profiles)"),
    ("backup_targets", "profile_id NOT IN (SELECT id FROM sync_profiles)"),
    ("backup_jobs", "target_id NOT IN (SELECT id FROM backup_targets)"),
)


def _rebuild(table: sa.Table) -> None:
    with op.batch_alter_table(table.name, recreate="always", copy_from=table):
        pass


def upgrade() -> None:
    for table, where in ORPHANS:
        delete = f"DELETE FROM {table} WHERE {where}"
        if context.is_offline_mode():
            op.execute(delete)
            continue
        removed = op.get_bind().execute(sa.text(delete)).rowcount
        if removed:
            logger.warning("Migration 0002: removed %d orphaned row(s) from %s", removed, table)

    # Flags without a profile: set aside, then give one to every profile.
    op.execute(
        "CREATE TEMP TABLE _unscoped_manual_flags AS "
        "SELECT file_path, created_at FROM manual_flags WHERE profile_id IS NULL"
    )
    op.execute("DELETE FROM manual_flags WHERE profile_id IS NULL")

    for table in _tables(cascade_jobs=True).values():
        _rebuild(table)

    op.execute(
        "INSERT OR IGNORE INTO manual_flags (profile_id, file_path, created_at) "
        "SELECT p.id, f.file_path, f.created_at "
        "FROM _unscoped_manual_flags AS f CROSS JOIN sync_profiles AS p"
    )
    op.execute("DROP TABLE _unscoped_manual_flags")


def downgrade() -> None:
    # UNIQUE (file_path) again: keep the oldest flag for each path.
    op.execute(
        "DELETE FROM manual_flags WHERE id NOT IN "
        "(SELECT MIN(id) FROM manual_flags GROUP BY file_path)"
    )
    for table in _tables(cascade_jobs=False).values():
        _rebuild(table)
