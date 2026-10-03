"""Stored pause: keep a profile paused across restarts and engine replacements.

* sync_profiles.pause_reason (text, nullable). A restore to one side only
  pauses automatic syncing so the next pull or watcher push does not undo
  or spread it. That pause used to live only in the running engine, so a
  restart or a profile edit (which replaces the engine) lifted it. It is
  now stored here and cleared when the user resumes or a sync of the
  whole profile succeeds.

Only a column is added (SQLite ADD COLUMN), so no table is rebuilt on
upgrade. Downgrading drops it, which forgets a stored pause.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sync_profiles") as batch:
        batch.add_column(sa.Column("pause_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sync_profiles") as batch:
        batch.drop_column("pause_reason")
