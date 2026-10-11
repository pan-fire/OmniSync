"""Make sync_jobs.profile_id and conflicts.profile_id NOT NULL.

Both columns were nullable for jobs recorded before profiles existed, a
case no supported database has (0.12.0 and later always set the profile).
NULL was never a way to keep history: the foreign keys are ON DELETE
CASCADE, so deleting a profile deletes its jobs and conflicts. A row
without a profile belongs to no profile's history, cannot be filtered or
acted on, and an unresolved conflict without one can only be dismissed.

Rows like that are removed before the constraint is added (the app copies
the database to ``<db>.pre-<revision>.bak`` first, see
backend.db.database.backup_before_migration):

* jobs without a profile, with their file changes and errors;
* conflicts without a profile. A conflict that has a profile but points
  at such a job keeps its record, with job_id cleared (job_id is optional).

The count of removed rows is logged. The downgrade makes the columns
nullable again; removed rows come back only from the copy.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-05
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("backend.db.migrations")

_ORPHAN_JOBS = "SELECT id FROM sync_jobs WHERE profile_id IS NULL"


def _execute(sql: str) -> int:
    """Run ``sql``; the number of rows it changed."""
    return op.get_bind().exec_driver_sql(sql).rowcount


def upgrade() -> None:
    # Foreign keys are off while migrating (batch rebuilds), so children
    # are removed explicitly rather than by ON DELETE CASCADE.
    file_changes = _execute(f"DELETE FROM file_changes WHERE job_id IN ({_ORPHAN_JOBS})")
    errors = _execute(f"DELETE FROM sync_errors WHERE job_id IN ({_ORPHAN_JOBS})")
    _execute(f"UPDATE conflicts SET job_id = NULL WHERE profile_id IS NOT NULL AND job_id IN ({_ORPHAN_JOBS})")
    conflicts = _execute("DELETE FROM conflicts WHERE profile_id IS NULL")
    jobs = _execute("DELETE FROM sync_jobs WHERE profile_id IS NULL")
    if jobs or conflicts:
        logger.warning(
            "Removed history rows that belonged to no profile: %d sync job(s) (with %d file change(s) and "
            "%d error(s)) and %d conflict record(s). The copy of the database taken before migrating "
            "(.pre-<revision>.bak) still has them.", jobs, file_changes, errors, conflicts,
        )

    with op.batch_alter_table("sync_jobs") as batch:
        batch.alter_column("profile_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("conflicts") as batch:
        batch.alter_column("profile_id", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("conflicts") as batch:
        batch.alter_column("profile_id", existing_type=sa.Integer(), nullable=True)
    with op.batch_alter_table("sync_jobs") as batch:
        batch.alter_column("profile_id", existing_type=sa.Integer(), nullable=True)
