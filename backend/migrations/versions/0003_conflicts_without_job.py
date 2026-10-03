"""Conflicts found by a diff: conflicts.job_id becomes nullable.

A diff that finds a file changed on both sides records a conflict for the
profile. No sync job exists at that point, so job_id may now be NULL; the
profile_id says which profile the conflict belongs to. Both foreign keys
keep ON DELETE CASCADE.

Downgrading deletes the conflicts that have no job, since the older schema
cannot hold them.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _conflicts(job_id_nullable: bool) -> sa.Table:
    meta = sa.MetaData()
    table = sa.Table(
        "conflicts", meta,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=job_id_nullable),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("local_modified", sa.DateTime(), nullable=True),
        sa.Column("remote_modified", sa.DateTime(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("resolution", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["sync_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["sync_jobs.id"], ondelete="CASCADE"),
    )
    sa.Index("ix_conflicts_profile_id", table.c.profile_id)
    sa.Index("ix_conflicts_job_id", table.c.job_id)
    return table


def _rebuild(table: sa.Table) -> None:
    with op.batch_alter_table(table.name, recreate="always", copy_from=table):
        pass


def upgrade() -> None:
    _rebuild(_conflicts(job_id_nullable=True))


def downgrade() -> None:
    op.execute("DELETE FROM conflicts WHERE job_id IS NULL")
    _rebuild(_conflicts(job_id_nullable=False))
