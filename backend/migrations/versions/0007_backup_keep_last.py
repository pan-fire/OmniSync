"""Backup retention keeps a minimum number of snapshots.

* backup_targets.keep_last (integer, default 3). Retention deletes snapshots
  older than retention_days, but never the newest keep_last of them: a target
  whose backups stopped (or failed) for longer than its retention keeps its
  last good snapshots instead of ending up with none.

Only a column is added (SQLite ADD COLUMN), so no table is rebuilt on
upgrade. Downgrading drops it again (retention by age only).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backup_targets") as batch:
        batch.add_column(sa.Column("keep_last", sa.Integer(), nullable=False, server_default=sa.text("3")))


def downgrade() -> None:
    with op.batch_alter_table("backup_targets") as batch:
        batch.drop_column("keep_last")
