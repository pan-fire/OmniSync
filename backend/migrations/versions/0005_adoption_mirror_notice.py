"""Mirror-mode notice: remember per profile that the user dismissed it.

* sync_profiles.mirror_notice_dismissed (boolean, default false). The web UI
  and the TUI explain mirror mode on every mirror profile, with a switch to
  two-way, until the user dismisses the explanation for that profile. It is
  stored here rather than in the browser so every client honours it.

Only a column is added (SQLite ADD COLUMN), so no table is rebuilt on
upgrade. Downgrading drops it again, which shows the notice once more.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sync_profiles") as batch:
        batch.add_column(sa.Column(
            "mirror_notice_dismissed", sa.Boolean(), nullable=False, server_default=sa.false(),
        ))


def downgrade() -> None:
    with op.batch_alter_table("sync_profiles") as batch:
        batch.drop_column("mirror_notice_dismissed")
