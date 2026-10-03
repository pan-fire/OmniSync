"""Two-way sync: a sync mode per profile, conflict copies, and the changed side.

* sync_profiles.sync_mode ("two_way" or "mirror"). Every existing profile
  becomes "mirror", which is how it synced so far, so nothing changes for it
  until the user switches it; the API creates new profiles as "two_way".
* conflicts.local_kept_as / remote_kept_as: for a conflict a two-way sync
  found, the names under which it kept the local and the remote version
  (both versions are kept on both sides). NULL for conflicts a diff found.
* file_changes.side ("local" or "remote"): the folder a recorded change
  happened in. A two-way sync changes both; NULL for older rows.

Only columns are added (SQLite ADD COLUMN), so no table is rebuilt on
upgrade. Downgrading drops them again, which forgets the mode (every profile
is a mirror in the older schema) and the conflict copy names.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sync_profiles") as batch:
        batch.add_column(sa.Column("sync_mode", sa.String(length=10), nullable=False, server_default="mirror"))
    with op.batch_alter_table("conflicts") as batch:
        batch.add_column(sa.Column("local_kept_as", sa.String(length=1024), nullable=True))
        batch.add_column(sa.Column("remote_kept_as", sa.String(length=1024), nullable=True))
    with op.batch_alter_table("file_changes") as batch:
        batch.add_column(sa.Column("side", sa.String(length=10), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("file_changes") as batch:
        batch.drop_column("side")
    with op.batch_alter_table("conflicts") as batch:
        batch.drop_column("remote_kept_as")
        batch.drop_column("local_kept_as")
    with op.batch_alter_table("sync_profiles") as batch:
        batch.drop_column("sync_mode")
