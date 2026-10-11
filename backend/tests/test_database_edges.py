"""Edge cases of backend/db/database.py: start-up order, odd database files, failed checks.

test_migrations.py covers the migrations themselves; these are the paths a
normal start does not take (a database that cannot use WAL, a migration that
leaves orphan rows, a pre-Alembic file, a copy that cannot be pruned).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import Connection, text

from backend.db import database
from backend.db.database import backup_before_migration, enable_wal, get_session, init_database, migrate_database


@pytest_asyncio.fixture(autouse=True)
async def _restore_database_globals() -> AsyncIterator[None]:
    """init_database replaces the module's engine; put the old one back."""
    saved = database._engine, database._async_session_factory
    database._engine, database._async_session_factory = None, None
    yield
    if database._engine is not None:
        await database._engine.dispose()
    database._engine, database._async_session_factory = saved


def _query(db: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _tables(db: Path) -> set[str]:
    return {row[0] for row in _query(db, "SELECT name FROM sqlite_master WHERE type = 'table'")}


def _legacy_database(db: Path) -> None:
    """A database with a table and a row but no alembic_version (never migrated)."""
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sync_profiles (id INTEGER PRIMARY KEY, slug TEXT)")
    conn.execute("INSERT INTO sync_profiles VALUES (1, 'legacy')")
    conn.commit()
    conn.close()


async def test_get_session_before_init_fails_clearly() -> None:
    """A request served before the start finished gets a clear error, not an AttributeError on None."""
    sessions = get_session()
    with pytest.raises(RuntimeError, match="Database not initialized"):
        await anext(sessions)


async def test_init_without_a_path_uses_the_configured_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OMNISYNC_DB_PATH decides where the database lives (its folder is created),
    and get_session then hands out sessions on it."""
    db = tmp_path / "nested" / "omnisync.db"
    monkeypatch.setenv("OMNISYNC_DB_PATH", str(db))

    await init_database()

    assert "sync_profiles" in _tables(db)
    sessions = get_session()
    session = await anext(sessions)
    assert (await session.execute(text("SELECT COUNT(*) FROM sync_profiles"))).scalar_one() == 0
    await sessions.aclose()


async def test_second_init_replaces_the_engine_and_keeps_the_data(tmp_path: Path) -> None:
    """Initializing again (a restart in the same process) disposes of the old
    engine and opens a new one on the same, unchanged database."""
    db = tmp_path / "omnisync.db"
    await init_database(str(db))
    first_engine = database._engine
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO sync_profiles (id, slug, name, local_dir, remote_dir, debounce_seconds, pull_interval_minutes,"
        " rclone_filter, rclone_args, max_retries, enabled, created_at, updated_at)"
        " VALUES (1, 'one', 'One', '/sync/one', 'gdrive:one', 5, 5, '[]', '[]', 3, 1, '2026-01-01', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    await init_database(str(db))

    assert database._engine is not first_engine
    assert database._async_session_factory is not None
    async with database._async_session_factory() as session:
        assert (await session.execute(text("SELECT slug FROM sync_profiles"))).scalars().all() == ["one"]
    assert list(tmp_path.glob("*.bak")) == []  # nothing was pending, so nothing was copied


def test_database_that_cannot_use_wal_is_logged_not_fatal(caplog: pytest.LogCaptureFixture) -> None:
    """Some filesystems refuse WAL; the app then keeps working in the old mode and says so."""
    with caplog.at_level(logging.WARNING, logger="backend.db.database"):
        enable_wal(":memory:")  # an in-memory database stays in "memory" mode

    assert "Could not switch the database to WAL mode (it is in memory mode)" in caplog.text


async def test_migration_leaving_orphan_rows_is_rolled_back(tmp_path: Path) -> None:
    """Migrations run with foreign keys off; one that leaves a row pointing at a
    missing parent is refused and rolled back, so the old database is kept."""
    db = tmp_path / "omnisync.db"
    await init_database(str(db))
    before = _tables(db)

    def orphan_then_new_table(connection: Connection) -> None:
        connection.exec_driver_sql(
            "INSERT INTO sync_jobs (id, profile_id, direction, started_at, status, files_changed, conflicts, errors)"
            " VALUES (1, 999, 'push', '2026-01-01', 'completed', 0, 0, 0)"
        )
        connection.exec_driver_sql("CREATE TABLE added_by_migration (id INTEGER)")

    with pytest.raises(RuntimeError, match=r"left 1 row\(s\) that break a foreign key"):
        await migrate_database(f"sqlite+aiosqlite:///{db}", runner=orphan_then_new_table)

    assert _tables(db) == before
    assert _query(db, "SELECT COUNT(*) FROM sync_jobs") == [(0,)]


def test_file_without_tables_is_not_copied(tmp_path: Path) -> None:
    """A database file with pages but no tables holds nothing worth a copy."""
    db = tmp_path / "omnisync.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.execute("DROP TABLE t")
    conn.commit()
    conn.close()
    assert db.stat().st_size > 0

    assert backup_before_migration(str(db)) is None
    assert list(tmp_path.glob("*.bak")) == []


def test_database_from_before_alembic_is_copied_first(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A database with tables but no alembic_version is copied, rows and all,
    before the first migration touches it."""
    db = tmp_path / "omnisync.db"
    _legacy_database(db)

    with caplog.at_level(logging.INFO, logger="backend.db.database"):
        copy = backup_before_migration(str(db))

    assert copy is not None and copy.name.startswith("omnisync.db.pre-") and copy.suffix == ".bak"
    assert _query(copy, "SELECT slug FROM sync_profiles") == [("legacy",)]
    assert "from revision (none)" in caplog.text


def test_old_copy_that_cannot_be_removed_does_not_block_the_new_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Pruning old copies is housekeeping: one that cannot be removed is
    logged, and the new copy (the one that protects this start) is kept."""
    db = tmp_path / "omnisync.db"
    _legacy_database(db)
    stuck = tmp_path / "omnisync.db.pre-0001.bak"
    stuck.mkdir()
    (stuck / "keep").write_text("x")  # a non-empty folder: unlink() fails
    for i, rev in enumerate(["0001", "0002", "0003", "0004"]):  # oldest first
        old = tmp_path / f"omnisync.db.pre-{rev}.bak"
        if not old.exists():
            old.write_text("old copy")
        os.utime(old, (1_000_000 + i, 1_000_000 + i))

    copy = backup_before_migration(str(db))

    assert copy is not None and _query(copy, "SELECT slug FROM sync_profiles") == [("legacy",)]
    assert stuck.is_dir()
    assert "Could not remove the old database copy" in caplog.text
    # Kept: the new copy and the two newest old ones; 0002 was removed, 0001 could not be.
    left = sorted(p.name for p in tmp_path.glob("omnisync.db.pre-*.bak"))
    assert left == sorted([copy.name, "omnisync.db.pre-0001.bak", "omnisync.db.pre-0003.bak",
                           "omnisync.db.pre-0004.bak"])
