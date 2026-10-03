"""Alembic environment for the OmniSync database.

Two ways in:

* The app (backend.db.database.migrate_database) passes an open connection in
  ``config.attributes["connection"]``, already inside a transaction and with
  foreign key enforcement off, so the whole upgrade commits or rolls back as
  one unit.
* The alembic CLI (backend/alembic.ini) has no connection: this file builds a
  migration engine for OMNISYNC_DB_PATH (or ``-x db=...``) with the same
  settings and runs the upgrade in one transaction.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context

from backend.db.models import Base

config = context.config
target_metadata = Base.metadata


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        # SQLite cannot ALTER constraints: autogenerate emits batch operations
        # (copy table, move rows, swap) instead.
        render_as_batch=True,
        compare_type=True,
        **kwargs,
    )


def _db_url() -> str:
    db_path = context.get_x_argument(as_dictionary=True).get("db") or os.environ.get(
        "OMNISYNC_DB_PATH", "/data/omnisync/omnisync.db"
    )
    return f"sqlite+aiosqlite:///{db_path}"


def run_migrations_offline() -> None:
    """Emit the SQL to stdout (``alembic upgrade head --sql``)."""
    _configure(url=_db_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run_on_connection(connection) -> None:
    # The connection comes from backend.db.database.migrate_database, which
    # issues a real BEGIN, so SQLite DDL is part of the transaction.
    _configure(connection=connection, transactional_ddl=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run_on_connection(connection)
        return

    from backend.db.database import migrate_database

    asyncio.run(migrate_database(_db_url(), runner=_run_on_connection))


if context.is_offline_mode():
    run_migrations_offline()
else:
    if config.config_file_name is not None and "connection" not in config.attributes:
        fileConfig(config.config_file_name, disable_existing_loggers=False)
    run_migrations_online()
