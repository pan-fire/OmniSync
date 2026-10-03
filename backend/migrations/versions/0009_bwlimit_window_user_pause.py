"""Per-profile bandwidth limit, sync window and the user's own pause.

* sync_profiles.bwlimit (string, nullable): rclone --bwlimit for the
  profile's syncs, a rate or a timetable ("08:00,512k 19:00,10M 23:00,off").
* sync_profiles.sync_window (text, nullable): JSON {"days": [0..6],
  "start": "HH:MM", "end": "HH:MM"}; automatic syncs wait outside it.
* sync_profiles.user_paused (boolean, default false): the user paused
  automatic syncing (Pause / Pause all). Kept apart from pause_reason (a
  pause the engine or a restore set), so resuming one never lifts the
  other, and kept across restarts.

Only columns are added (SQLite ADD COLUMN), so no table is rebuilt on
upgrade. Downgrading drops them, which forgets those settings.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sync_profiles") as batch:
        batch.add_column(sa.Column("bwlimit", sa.String(length=500), nullable=True))
        batch.add_column(sa.Column("sync_window", sa.Text(), nullable=True))
        batch.add_column(sa.Column("user_paused", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("sync_profiles") as batch:
        batch.drop_column("user_paused")
        batch.drop_column("sync_window")
        batch.drop_column("bwlimit")
