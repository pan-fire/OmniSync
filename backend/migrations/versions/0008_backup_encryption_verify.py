"""Encrypted backup targets and verification after backup.

* backup_targets.encryption_password (string, nullable): the passphrase of an
  encrypted target in rclone's obscured form; NULL for an unencrypted target.
  Every target stored before this revision is unencrypted.
* backup_targets.verify_after_backup (boolean, default false): compare the
  backup with the folder after each run. Existing targets keep running
  without it (an upgrade adds no work to them); new targets created through
  the API turn it on by default.
* backup_jobs.verify_status / verify_message (nullable): the outcome of that
  verification ("verified" or "failed", with a summary).

Only columns are added (SQLite ADD COLUMN), so no table is rebuilt on
upgrade. Downgrading drops them again; an encrypted target then looks
unencrypted, so its backups would be unreadable to the old version.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backup_targets") as batch:
        batch.add_column(sa.Column("encryption_password", sa.String(2048), nullable=True))
        batch.add_column(sa.Column("verify_after_backup", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("backup_jobs") as batch:
        batch.add_column(sa.Column("verify_status", sa.String(20), nullable=True))
        batch.add_column(sa.Column("verify_message", sa.String(4096), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("backup_jobs") as batch:
        batch.drop_column("verify_message")
        batch.drop_column("verify_status")
    with op.batch_alter_table("backup_targets") as batch:
        batch.drop_column("verify_after_backup")
        batch.drop_column("encryption_password")
