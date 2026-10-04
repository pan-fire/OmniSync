"""Async database engine, schema migrations and session setup for OmniSync.

The schema evolves only through the Alembic revisions in backend/migrations;
init_database applies any pending ones at startup, after copying the
database to ``<db>.pre-<revision>.bak`` (the newest PRE_MIGRATION_BACKUPS
copies are kept). Every connection the app opens enforces foreign keys
(PRAGMA foreign_keys=ON), so ON DELETE CASCADE and parent checks hold.
SQLite turns enforcement off by default, per connection.

init_database also switches the database to WAL mode: readers (the API) and
the one writer (a sync recording its job) do not block each other, and a
crash cannot leave a half-written transaction behind. A connection waits up to
BUSY_TIMEOUT_MS for another one's write lock instead of failing at once
with "database is locked".
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from collections.abc import AsyncGenerator, Callable
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# How long (ms) a connection waits for another one's write lock.
BUSY_TIMEOUT_MS = 30_000

# Copies of the database taken before migrations that are kept.
PRE_MIGRATION_BACKUPS = 3

_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None


def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def enable_foreign_keys(engine: AsyncEngine | Engine) -> None:
    """Make every new connection of ``engine`` enforce foreign keys."""
    sync_engine = engine.sync_engine if isinstance(engine, AsyncEngine) else engine
    event.listen(sync_engine, "connect", _enable_foreign_keys)


def _set_busy_timeout(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    cursor.close()


def create_db_engine(url: str) -> AsyncEngine:
    """An async engine whose connections enforce foreign keys and wait for a busy database."""
    engine = create_async_engine(url, echo=False)
    enable_foreign_keys(engine)
    event.listen(engine.sync_engine, "connect", _set_busy_timeout)
    return engine


def enable_wal(db_path: str) -> None:
    """Switch the database file to WAL mode (stored in the file; a no-op once set).

    Done once at startup, before the app's connections open: switching
    needs the database to itself.
    """
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000)
    try:
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    finally:
        conn.close()
    if mode != "wal":
        logger.warning("Could not switch the database to WAL mode (it is in %s mode)", mode)


def _prepare_migration_engine(engine: AsyncEngine) -> None:
    """Settings for the connection that runs migrations.

    Foreign keys are off: a batch rebuild drops and renames tables, which
    with enforcement on would cascade-delete child rows or fail. The PRAGMA
    must be set outside a transaction, hence on connect. The driver's own
    transaction handling is replaced by an explicit BEGIN so the DDL is part
    of the transaction too, and a failed upgrade rolls back completely.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _connect(dbapi_connection, _connection_record) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.close()

    @event.listens_for(engine.sync_engine, "begin")
    def _begin(connection: Connection) -> None:
        connection.exec_driver_sql("BEGIN")


def alembic_config() -> Config:
    """Alembic config pointing at backend/migrations (no alembic.ini needed)."""
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


def _upgrade_to_head(connection: Connection) -> None:
    cfg = alembic_config()
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, "head")


def _check_foreign_keys(connection: Connection) -> None:
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(
            f"Database migration left {len(violations)} row(s) that break a foreign key, "
            f"e.g. {tuple(violations[0])}; the migration was rolled back."
        )


async def migrate_database(
    url: str, runner: Callable[[Connection], None] = _upgrade_to_head,
) -> None:
    """Apply pending migrations to the database at ``url`` in one transaction.

    ``runner`` does the Alembic work on the open connection; the default
    upgrades to the newest revision. Nothing is committed if it fails or if
    the result breaks a foreign key.
    """
    engine = create_async_engine(url, echo=False)
    _prepare_migration_engine(engine)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(runner)
            await conn.run_sync(_check_foreign_keys)
    finally:
        await engine.dispose()


def backup_before_migration(db_path: str) -> Path | None:
    """Copy the database to ``<db>.pre-<head>.bak`` if migrations to ``head`` are pending.

    Uses SQLite's backup API, so the copy is consistent even with a WAL
    file next to the database. A new (empty) database, or one already at
    the newest revision, is not copied. Only the newest
    PRE_MIGRATION_BACKUPS copies are kept. Returns the copy's path.
    """
    db = Path(db_path)
    if not db.is_file() or db.stat().st_size == 0:
        return None
    newest = ScriptDirectory.from_config(alembic_config()).get_current_head()
    src = sqlite3.connect(db)
    try:
        tables = {row[0] for row in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not tables:
            return None
        current = None
        if "alembic_version" in tables:
            row = src.execute("SELECT version_num FROM alembic_version").fetchone()
            current = row[0] if row else None
        if current == newest:
            return None
        target = db.with_name(f"{db.name}.pre-{newest}.bak")
        partial = target.with_name(target.name + ".tmp")
        partial.unlink(missing_ok=True)
        dst = sqlite3.connect(partial)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    os.chmod(partial, db.stat().st_mode & 0o777)
    os.replace(partial, target)
    logger.info("Copied the database to %s before migrating it from revision %s to %s",
                target, current or "(none)", newest)

    copies = sorted(db.parent.glob(f"{db.name}.pre-*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in copies[PRE_MIGRATION_BACKUPS:]:
        try:
            old.unlink()
        except OSError as exc:
            logger.warning("Could not remove the old database copy %s: %s", old, exc)
    return target


async def init_database(db_path: str = "") -> None:
    """Migrate the database to the current schema and set up sessions."""
    global _engine, _async_session_factory

    if not db_path:
        db_path = os.environ.get("OMNISYNC_DB_PATH", "/data/omnisync/omnisync.db")

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite+aiosqlite:///{db_path}"

    # A failed copy stops the start: migrating without one is what it guards against.
    await asyncio.to_thread(backup_before_migration, db_path)
    await migrate_database(url)
    await asyncio.to_thread(enable_wal, db_path)

    if _engine is not None:
        await _engine.dispose()
    _engine = create_db_engine(url)
    _async_session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    logger.info("Database initialized at %s", db_path)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    if _async_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")

    async with _async_session_factory() as session:
        yield session
