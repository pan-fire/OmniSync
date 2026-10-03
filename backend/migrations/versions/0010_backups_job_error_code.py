"""Error codes of backup and restore jobs.

* backup_jobs.error_code (string, nullable): why a job failed or was skipped,
  as a stable code the API passes on with a fixed message (the details stay
  in error_message, redacted, and in the log). Jobs recorded before this
  revision have none; the API derives a code from their direction.

Only a column is added (SQLite ADD COLUMN), so no table is rebuilt on
upgrade. Downgrading drops it again.

Revision ID: 0010_backups
Revises: 0009
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_backups"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backup_jobs") as batch:
        batch.add_column(sa.Column("error_code", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("backup_jobs") as batch:
        batch.drop_column("error_code")
