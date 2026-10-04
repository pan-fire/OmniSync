"""Drop sync_profiles.backup_dir.

A profile's backup_dir was the backup setting from before backup targets.
Since 0.12.0 the app turned any value into a backup target at startup and
cleared the column, and no client sets it, so it is always empty.

Revision ID: 0011
Revises: 0010_backups
Create Date: 2026-10-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010_backups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sync_profiles") as batch:
        batch.drop_column("backup_dir")


def downgrade() -> None:
    with op.batch_alter_table("sync_profiles") as batch:
        batch.add_column(sa.Column("backup_dir", sa.String(length=1024), nullable=True))
