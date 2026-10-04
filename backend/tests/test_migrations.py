"""Versioned schema migrations and referential integrity.

Covers the baseline revision (it builds exactly the schema the models
describe, and downgrades and upgrades cleanly), foreign key enforcement on
every connection, ON DELETE CASCADE, that a failed migration changes
nothing, and the copy of the database taken before migrating.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, func, select, text

from backend.db import database
from backend.db.database import alembic_config, init_database, migrate_database
from backend.db.models import (
    BackupJob,
    BackupTarget,
    Base,
    Conflict,
    FileChange,
    ManualFlag,
    SyncError,
    SyncJob,
    SyncProfile,
)

SCRIPTS = ScriptDirectory.from_config(alembic_config())
HEAD = SCRIPTS.get_current_head()
NOW = "2026-01-01 00:00:00.000000"

TABLES_WITH_FKS = (
    "sync_jobs", "file_changes", "conflicts", "sync_errors",
    "manual_flags", "backup_targets", "backup_jobs",
)


@pytest_asyncio.fixture(autouse=True)
async def _restore_database_globals():
    """init_database replaces the module's engine; put the old one back."""
    saved = database._engine, database._async_session_factory
    database._engine = None
    yield
    if database._engine is not None:
        await database._engine.dispose()
    database._engine, database._async_session_factory = saved


def _insert_profile(conn: sqlite3.Connection, pid: int, slug: str) -> None:
    conn.execute(
        "INSERT INTO sync_profiles (id, slug, name, local_dir, remote_dir, debounce_seconds,"
        " pull_interval_minutes, rclone_filter, rclone_args, max_retries, enabled, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 'gdrive:x', 5, 5, '[]', '[]', 3, 1, ?, ?)",
        (pid, slug, slug, f"/sync/{slug}", NOW, NOW),
    )


def schema_diff(path: Path) -> list:
    """Differences between the database at ``path`` and the ORM models."""
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": True, "compare_server_default": True})
            return compare_metadata(ctx, Base.metadata)
    finally:
        engine.dispose()


def foreign_keys(path: Path) -> dict[str, list[tuple[str, str, str]]]:
    """table -> [(column, referenced table, ON DELETE action)]."""
    conn = sqlite3.connect(path)
    try:
        return {
            table: [(row[3], row[2], row[6]) for row in conn.execute(f"PRAGMA foreign_key_list({table})")]
            for table in TABLES_WITH_FKS
        }
    finally:
        conn.close()


def version(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [row[0] for row in conn.execute("SELECT version_num FROM alembic_version")]
    finally:
        conn.close()


def count(path: Path, sql: str) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def tables(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def assert_current_schema(path: Path) -> None:
    assert version(path) == [HEAD]
    assert schema_diff(path) == []
    for table, fks in foreign_keys(path).items():
        assert fks, f"{table} has no foreign key"
        for column, parent, on_delete in fks:
            assert on_delete == "CASCADE", f"{table}.{column} -> {parent} is ON DELETE {on_delete}"


def _alembic(command_name: str, target: str):
    def run(connection):
        cfg = alembic_config()
        cfg.attributes["connection"] = connection
        getattr(command, command_name)(cfg, target)
    return run


# --- the baseline ---


def test_baseline_is_the_0_12_0_head():
    """A database 0.12.0 created is stamped 0010_backups: the baseline must carry that ID."""
    (baseline,) = [s for s in SCRIPTS.walk_revisions() if s.down_revision is None]
    assert baseline.revision == "0010_backups"


async def test_fresh_database_is_built_by_migrations(tmp_path):
    db = tmp_path / "fresh.db"
    await init_database(str(db))
    assert_current_schema(db)


async def test_init_database_has_no_hand_written_alters():
    source = Path(database.__file__).read_text()
    assert "ALTER TABLE" not in source
    assert "create_all" not in source


async def test_baseline_downgrades_and_upgrades(tmp_path):
    db = tmp_path / "roundtrip.db"
    url = f"sqlite+aiosqlite:///{db}"
    await migrate_database(url)
    assert_current_schema(db)

    await migrate_database(url, runner=_alembic("downgrade", "base"))
    assert tables(db) == {"alembic_version"}
    assert version(db) == []

    await migrate_database(url)
    assert_current_schema(db)


async def test_foreign_keys_enforced_on_every_connection(tmp_path):
    await init_database(str(tmp_path / "fk.db"))
    factory = database._async_session_factory
    assert factory is not None
    for _ in range(3):
        async with factory() as session:
            assert (await session.execute(text("PRAGMA foreign_keys"))).scalar() == 1
    # A connection opened directly on the engine, outside any session.
    async with database._engine.connect() as conn:
        assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar() == 1


async def test_orphan_rows_are_rejected(tmp_path):
    from sqlalchemy.exc import IntegrityError

    await init_database(str(tmp_path / "orphan.db"))
    async with database._async_session_factory() as session:
        session.add(SyncError(job_id=12345, message="x", retry_count=0, created_at=datetime.now(timezone.utc)))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_restart_is_a_no_op(tmp_path):
    db = tmp_path / "again.db"
    await init_database(str(db))
    conn = sqlite3.connect(db)
    _insert_profile(conn, 1, "one")
    conn.commit()
    conn.close()
    await init_database(str(db))
    assert_current_schema(db)
    assert count(db, "SELECT COUNT(*) FROM sync_profiles") == 1


async def test_server_defaults_fill_rows_written_without_the_column(tmp_path):
    db = tmp_path / "defaults.db"
    await init_database(str(db))
    conn = sqlite3.connect(db)
    try:
        _insert_profile(conn, 1, "one")
        conn.execute(
            "INSERT INTO backup_targets (id, profile_id, name, target_path, target_type, retention_days,"
            " frequency_hours, backup_mode, enabled, created_at, updated_at)"
            " VALUES (1, 1, 't', '/backups/one', 'local', 7, 24, 'mirror', 1, ?, ?)",
            (NOW, NOW),
        )
        conn.commit()
        assert conn.execute(
            "SELECT sync_mode, mirror_notice_dismissed, user_paused FROM sync_profiles"
        ).fetchone() == ("mirror", 0, 0)
        assert conn.execute(
            "SELECT keep_last, verify_after_backup FROM backup_targets"
        ).fetchone() == (3, 0)
    finally:
        conn.close()


async def test_failed_migration_changes_nothing(tmp_path):
    db = tmp_path / "rollback.db"
    sqlite3.connect(db).close()

    def upgrade_then_fail(connection):
        _alembic("upgrade", "head")(connection)
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError, match="disk full"):
        await migrate_database(f"sqlite+aiosqlite:///{db}", runner=upgrade_then_fail)

    assert tables(db) == set()


# --- cascades through the ORM ---


async def _factory(tmp_path):
    await init_database(str(tmp_path / "cascade.db"))
    return database._async_session_factory


async def _profile(session, slug: str) -> SyncProfile:
    now = datetime.now(timezone.utc)
    profile = SyncProfile(
        slug=slug, name=slug, local_dir=f"/sync/{slug}", remote_dir="gdrive:x",
        debounce_seconds=5, pull_interval_minutes=5, rclone_filter="[]", rclone_args="[]",
        max_retries=3, enabled=True, created_at=now, updated_at=now,
    )
    session.add(profile)
    await session.flush()
    return profile


async def _job_with_children(session, profile: SyncProfile) -> SyncJob:
    now = datetime.now(timezone.utc)
    job = SyncJob(
        profile_id=profile.id, direction="selective", started_at=now, status="completed",
        files_changed=2, conflicts=1, errors=1,
    )
    session.add(job)
    await session.flush()
    session.add_all([
        FileChange(job_id=job.id, file_path="a.txt", action="created", size_bytes=10),
        FileChange(job_id=job.id, file_path="b.txt", action="deleted"),
        SyncError(job_id=job.id, message="boom", retry_count=1, created_at=now),
        Conflict(profile_id=profile.id, job_id=job.id, file_path="c.txt", resolved=False),
        ManualFlag(profile_id=profile.id, file_path="d.txt", created_at=now),
    ])
    await session.flush()
    return job


async def _counts(session) -> dict[str, int]:
    return {
        model.__tablename__: (await session.execute(select(func.count()).select_from(model))).scalar_one()
        for model in (SyncJob, FileChange, SyncError, Conflict, ManualFlag, BackupTarget, BackupJob)
    }


async def test_delete_profile_with_history_removes_dependents(tmp_path):
    from backend.services.profile_service import ProfileService

    factory = await _factory(tmp_path)
    async with factory() as session:
        doomed = await _profile(session, "doomed")
        kept = await _profile(session, "kept")
        await _job_with_children(session, doomed)
        await _job_with_children(session, kept)
        now = datetime.now(timezone.utc)
        target = BackupTarget(
            profile_id=doomed.id, name="t", target_path="/backups/t", target_type="local",
            retention_days=7, frequency_hours=24, backup_mode="mirror", enabled=True,
            created_at=now, updated_at=now,
        )
        session.add(target)
        await session.flush()
        session.add(BackupJob(target_id=target.id, started_at=now, status="completed", direction="backup"))
        await session.commit()

    await ProfileService(factory).delete("doomed")

    async with factory() as session:
        assert await _counts(session) == {
            "sync_jobs": 1, "file_changes": 2, "sync_errors": 1, "conflicts": 1,
            "manual_flags": 1, "backup_targets": 0, "backup_jobs": 0,
        }


async def test_job_history_rows_are_written_and_linked(tmp_path):
    factory = await _factory(tmp_path)
    async with factory() as session:
        profile = await _profile(session, "p")
        job = await _job_with_children(session, profile)
        await session.commit()
        job_id = job.id

    async with factory() as session:
        job = await session.get(SyncJob, job_id)
        await session.refresh(job, ["file_changes", "sync_errors", "job_conflicts", "profile"])
        assert sorted(fc.file_path for fc in job.file_changes) == ["a.txt", "b.txt"]
        assert [e.message for e in job.sync_errors] == ["boom"]
        assert [c.file_path for c in job.job_conflicts] == ["c.txt"]
        assert job.job_conflicts[0].profile_id == job.profile.id

    # Deleting a job takes its file changes, errors and conflicts with it.
    async with factory() as session:
        await session.delete(await session.get(SyncJob, job_id))
        await session.commit()
        counts = await _counts(session)
    assert counts["sync_jobs"] == 0
    assert counts["file_changes"] == counts["sync_errors"] == counts["conflicts"] == 0
    assert counts["manual_flags"] == 1  # flags belong to the profile, not the job


async def test_manual_flag_is_unique_per_profile_not_globally(tmp_path):
    from sqlalchemy.exc import IntegrityError

    factory = await _factory(tmp_path)
    now = datetime.now(timezone.utc)
    async with factory() as session:
        a = await _profile(session, "a")
        b = await _profile(session, "b")
        session.add_all([
            ManualFlag(profile_id=a.id, file_path="notes.txt", created_at=now),
            ManualFlag(profile_id=b.id, file_path="notes.txt", created_at=now),
        ])
        await session.commit()
        session.add(ManualFlag(profile_id=a.id, file_path="notes.txt", created_at=now))
        with pytest.raises(IntegrityError):
            await session.commit()


# --- WAL, busy timeout and the copy taken before migrating ---


async def test_app_connections_use_wal_and_a_busy_timeout(tmp_path):
    db = tmp_path / "wal.db"
    await init_database(str(db))
    async with database._async_session_factory() as session:
        assert (await session.execute(text("PRAGMA journal_mode"))).scalar() == "wal"
        assert (await session.execute(text("PRAGMA busy_timeout"))).scalar() == database.BUSY_TIMEOUT_MS
    # WAL is stored in the file: other tools (sqlite3, a backup) see it too.
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    finally:
        conn.close()


async def test_a_writer_waits_for_another_instead_of_failing(tmp_path):
    db = tmp_path / "busy.db"
    await init_database(str(db))
    blocker = sqlite3.connect(db, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")  # holds the write lock
    _insert_profile(blocker, 1, "one")

    async def write() -> None:
        async with database._async_session_factory() as session:
            await session.execute(text(
                "INSERT INTO sync_profiles (id, slug, name, local_dir, remote_dir, debounce_seconds, "
                "pull_interval_minutes, rclone_filter, rclone_args, max_retries, enabled, created_at, updated_at) "
                f"VALUES (2, 'two', 'two', '/l', 'r:x', 5, 5, '[]', '[]', 3, 1, '{NOW}', '{NOW}')"
            ))
            await session.commit()

    task = asyncio.create_task(write())
    await asyncio.sleep(0.3)
    assert not task.done()  # waiting for the lock, not failed with "database is locked"
    blocker.execute("COMMIT")
    blocker.close()
    await asyncio.wait_for(task, timeout=10)
    assert count(db, "SELECT COUNT(*) FROM sync_profiles") == 2


class _NextHead:
    """Stands in for a script directory whose head is a newer revision."""

    @staticmethod
    def from_config(_cfg):
        return _NextHead()

    def get_current_head(self) -> str:
        return "9999_next"


async def test_pending_migrations_copy_the_database_first(tmp_path, monkeypatch):
    db = tmp_path / "omnisync.db"
    await init_database(str(db))
    conn = sqlite3.connect(db)
    _insert_profile(conn, 1, "one")
    conn.commit()
    conn.close()

    # Nothing pending: no copy.
    assert database.backup_before_migration(str(db)) is None
    assert list(tmp_path.glob("*.bak")) == []

    monkeypatch.setattr(database, "ScriptDirectory", _NextHead)
    copy = database.backup_before_migration(str(db))

    assert copy == tmp_path / "omnisync.db.pre-9999_next.bak"
    assert version(copy) == [HEAD]  # the database as it was before migrating
    assert count(copy, "SELECT COUNT(*) FROM sync_profiles") == 1
    assert (copy.stat().st_mode & 0o777) == (db.stat().st_mode & 0o777)


async def test_new_databases_are_not_copied(tmp_path):
    await init_database(str(tmp_path / "omnisync.db"))
    assert list(tmp_path.glob("*.bak")) == []


async def test_only_the_newest_pre_migration_copies_are_kept(tmp_path, monkeypatch):
    db = tmp_path / "omnisync.db"
    await init_database(str(db))
    for i, rev in enumerate(["0001", "0002", "0003"]):
        old = tmp_path / f"omnisync.db.pre-{rev}.bak"
        old.write_text("old copy")
        os.utime(old, (1_000_000 + i, 1_000_000 + i))

    monkeypatch.setattr(database, "ScriptDirectory", _NextHead)
    copy = database.backup_before_migration(str(db))

    assert copy == tmp_path / "omnisync.db.pre-9999_next.bak"
    kept = sorted(p.name for p in tmp_path.glob("omnisync.db.pre-*.bak"))
    assert kept == sorted(["omnisync.db.pre-0002.bak", "omnisync.db.pre-0003.bak", copy.name])
    assert not list(tmp_path.glob("*.tmp"))
