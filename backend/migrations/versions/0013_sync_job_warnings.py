"""Add sync_jobs.warnings.

A JSON array of the job's warnings: local names a sync could not carry as
they are (names equal after Unicode normalisation, names that are not
UTF-8) and symbolic links that share a name with a remote file. Jobs from
before have none.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sync_jobs") as batch:
        batch.add_column(sa.Column("warnings", sa.Text(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("sync_jobs") as batch:
        batch.drop_column("warnings")
