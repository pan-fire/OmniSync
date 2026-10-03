"""Versioned schema migrations and referential integrity (sync-data-safety R5).

Covers fresh databases, databases created by the pre-Alembic startup code
(create_all plus a hand-written ALTER list), foreign key enforcement on every
connection, ON DELETE CASCADE, and that a failed migration changes nothing.
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
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.db import database
from backend.db.database import alembic_config, create_db_engine, init_database, migrate_database
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

FIXTURES = Path(__file__).parent / "fixtures"
HEAD = ScriptDirectory.from_config(alembic_config()).get_current_head()
NOW = "2026-01-01 00:00:00.000000"

# What the pre-Alembic startup code ran on every start (backend/db/database.py
# before migrations existed).
OLD_ALTERS = (
    "ALTER TABLE sync_jobs ADD COLUMN profile_id INTEGER REFERENCES sync_profiles(id)",
    "ALTER TABLE conflicts ADD COLUMN profile_id INTEGER REFERENCES sync_profiles(id)",
    "ALTER TABLE manual_flags ADD COLUMN profile_id INTEGER REFERENCES sync_profiles(id)",
    "ALTER TABLE notification_log ADD COLUMN profile_slug VARCHAR(255)",
)

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


def _schema(name: str) -> str:
    return (FIXTURES / name).read_text()


def _create_all_checkfirst(sql: str) -> str:
    """The pre-Alembic create_all only created what was missing."""
    return (
        sql.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ")
        .replace("CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX IF NOT EXISTS ")
        .replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ")
    )


def _insert_profile(conn: sqlite3.Connection, pid: int, slug: str) -> None:
    conn.execute(
        "INSERT INTO sync_profiles (id, slug, name, local_dir, remote_dir, debounce_seconds,"
        " pull_interval_minutes, rclone_filter, rclone_args, max_retries, enabled, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 'gdrive:x', 5, 5, '[]', '[]', 3, 1, ?, ?)",
        (pid, slug, slug, f"/sync/{slug}", NOW, NOW),
    )


def _insert_job(conn: sqlite3.Connection, jid: int, pid: int | None, with_profile_col: bool = True) -> None:
    if with_profile_col:
        conn.execute(
            "INSERT INTO sync_jobs (id, profile_id, direction, started_at, status, files_changed, conflicts, errors)"
            " VALUES (?, ?, 'push', ?, 'completed', 1, 0, 0)",
            (jid, pid, NOW),
        )
    else:
        conn.execute(
            "INSERT INTO sync_jobs (id, direction, started_at, status, files_changed, conflicts, errors)"
            " VALUES (?, 'push', ?, 'completed', 1, 0, 0)",
            (jid, NOW),
        )


def build_pre_alembic_db(path: Path, variant: str) -> None:
    """A database as the old code left it, with data.

    variants:
      pre_alembic      created by the last pre-Alembic release
      upgraded_by_old  created before profiles existed, then upgraded by the
                       old startup code (create_all + ALTER list)
      pre_profiles     created before profiles existed, never upgraded
    """
    conn = sqlite3.connect(path)
    if variant == "pre_alembic":
        conn.executescript(_schema("pre_alembic_schema.sql"))
    else:
        conn.executescript(_schema("pre_profiles_schema.sql"))
        if variant == "upgraded_by_old":
            conn.executescript(_create_all_checkfirst(_schema("pre_alembic_schema.sql")))
            for ddl in OLD_ALTERS:
                conn.execute(ddl)

    if variant == "pre_profiles":
        _insert_job(conn, 1, None, with_profile_col=False)
        conn.execute("INSERT INTO file_changes (job_id, file_path, action) VALUES (1, 'a.txt', 'modified')")
        conn.execute("INSERT INTO sync_errors (job_id, message, retry_count, created_at) VALUES (999, 'x', 0, ?)", (NOW,))
        conn.execute("INSERT INTO manual_flags (file_path, created_at) VALUES ('a.txt', ?)", (NOW,))
    else:
        _insert_profile(conn, 1, "one")
        _insert_profile(conn, 2, "two")
        _insert_job(conn, 1, 1)
        _insert_job(conn, 2, None)  # from before profiles
        conn.execute("INSERT INTO file_changes (job_id, file_path, action) VALUES (1, 'a.txt', 'modified')")
        conn.execute("INSERT INTO sync_errors (job_id, message, retry_count, created_at) VALUES (1, 'boom', 1, ?)", (NOW,))
        # Left behind while foreign keys were not enforced: job 999 never existed.
        conn.execute("INSERT INTO sync_errors (job_id, message, retry_count, created_at) VALUES (999, 'x', 0, ?)", (NOW,))
        conn.execute(
            "INSERT INTO conflicts (profile_id, job_id, file_path, resolved) VALUES (1, 1, 'c.txt', 0)"
        )
        # Written without a profile, as the old engine did: honoured by every profile.
        conn.execute("INSERT INTO manual_flags (file_path, created_at) VALUES ('a.txt', ?)", (NOW,))
        conn.execute("INSERT INTO manual_flags (profile_id, file_path, created_at) VALUES (1, 'b.txt', ?)", (NOW,))
        conn.execute(
            "INSERT INTO backup_targets (id, profile_id, name, target_path, target_type, retention_days,"
            " frequency_hours, backup_mode, enabled, created_at, updated_at)"
            " VALUES (1, 1, 't', '/backups/one', 'local', 7, 24, 'mirror', 1, ?, ?)",
            (NOW, NOW),
        )
        conn.execute(
            "INSERT INTO backup_jobs (target_id, started_at, status, direction) VALUES (1, ?, 'completed', 'backup')",
            (NOW,),
        )
    conn.commit()
    conn.close()


def schema_diff(path: Path) -> list:
    """Differences between the database at ``path`` and the ORM models."""
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": True})
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


def assert_current_schema(path: Path) -> None:
    assert version(path) == [HEAD]
    assert schema_diff(path) == []
    for table, fks in foreign_keys(path).items():
        assert fks, f"{table} has no foreign key"
        for column, parent, on_delete in fks:
            assert on_delete == "CASCADE", f"{table}.{column} -> {parent} is ON DELETE {on_delete}"


# --- fresh databases ---


async def test_fresh_database_is_built_by_migrations(tmp_path):
    db = tmp_path / "fresh.db"
    await init_database(str(db))
    assert_current_schema(db)


async def test_init_database_has_no_hand_written_alters():
    source = Path(database.__file__).read_text()
    assert "ALTER TABLE" not in source
    assert "create_all" not in source


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


# --- databases created before Alembic ---


@pytest.mark.parametrize("variant", ["pre_alembic", "upgraded_by_old"])
async def test_pre_alembic_database_is_upgraded(tmp_path, variant):
    db = tmp_path / "legacy.db"
    build_pre_alembic_db(db, variant)
    assert schema_diff(db) != []  # the old schema really differs

    await init_database(str(db))

    assert_current_schema(db)
    # Data survived the table rebuilds.
    assert count(db, "SELECT COUNT(*) FROM sync_profiles") == 2
    assert count(db, "SELECT COUNT(*) FROM sync_jobs") == 2
    assert count(db, "SELECT COUNT(*) FROM file_changes") == 1
    assert count(db, "SELECT COUNT(*) FROM conflicts") == 1
    assert count(db, "SELECT COUNT(*) FROM backup_jobs") == 1
    # The orphaned error row is gone, the real one kept.
    assert count(db, "SELECT COUNT(*) FROM sync_errors") == 1
    assert count(db, "SELECT COUNT(*) FROM sync_errors WHERE job_id = 999") == 0
    # The unscoped flag now belongs to every profile; the scoped one stays put.
    conn = sqlite3.connect(db)
    flags = sorted(conn.execute("SELECT profile_id, file_path FROM manual_flags"))
    conn.close()
    assert flags == [(1, "a.txt"), (1, "b.txt"), (2, "a.txt")]

    # Deleting a profile now removes everything that hangs off it.
    engine = create_db_engine(f"sqlite+aiosqlite:///{db}")
    try:
        async with async_sessionmaker(engine)() as session:
            await session.delete(await session.get(SyncProfile, 1))
            await session.commit()
    finally:
        await engine.dispose()
    assert count(db, "SELECT COUNT(*) FROM sync_jobs") == 1  # the profile-less legacy job
    assert count(db, "SELECT COUNT(*) FROM file_changes") == 0
    assert count(db, "SELECT COUNT(*) FROM sync_errors") == 0
    assert count(db, "SELECT COUNT(*) FROM conflicts") == 0
    assert count(db, "SELECT COUNT(*) FROM backup_targets") == 0
    assert count(db, "SELECT COUNT(*) FROM backup_jobs") == 0
    assert count(db, "SELECT COUNT(*) FROM manual_flags") == 1


async def test_database_from_before_profiles_is_upgraded(tmp_path):
    db = tmp_path / "ancient.db"
    build_pre_alembic_db(db, "pre_profiles")

    await init_database(str(db))

    assert_current_schema(db)
    assert count(db, "SELECT COUNT(*) FROM sync_jobs WHERE profile_id IS NULL") == 1
    assert count(db, "SELECT COUNT(*) FROM file_changes") == 1
    assert count(db, "SELECT COUNT(*) FROM sync_errors") == 0
    # No profile existed to own the flag.
    assert count(db, "SELECT COUNT(*) FROM manual_flags") == 0


async def test_failed_migration_changes_nothing(tmp_path):
    db = tmp_path / "rollback.db"
    build_pre_alembic_db(db, "pre_alembic")
    before = sqlite3.connect(db).execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()

    def upgrade_then_fail(connection):
        cfg = alembic_config()
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError, match="disk full"):
        await migrate_database(f"sqlite+aiosqlite:///{db}", runner=upgrade_then_fail)

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == before
    assert conn.execute("SELECT COUNT(*) FROM sync_errors").fetchone()[0] == 2
    conn.close()


async def test_downgrade_and_upgrade_round_trip(tmp_path):
    db = tmp_path / "roundtrip.db"
    build_pre_alembic_db(db, "pre_alembic")
    url = f"sqlite+aiosqlite:///{db}"
    await migrate_database(url)

    def downgrade(connection):
        cfg = alembic_config()
        cfg.attributes["connection"] = connection
        command.downgrade(cfg, "0001")

    await migrate_database(url, runner=downgrade)
    assert version(db) == ["0001"]
    assert foreign_keys(db)["file_changes"] == [("job_id", "sync_jobs", "NO ACTION")]

    await migrate_database(url)
    assert_current_schema(db)
    assert count(db, "SELECT COUNT(*) FROM sync_jobs") == 2


async def test_conflicts_found_by_a_diff_need_no_job(tmp_path):
    """0003: job_id is nullable; both foreign keys still cascade."""
    db = tmp_path / "conflicts.db"
    build_pre_alembic_db(db, "pre_alembic")
    url = f"sqlite+aiosqlite:///{db}"
    await migrate_database(url)
    assert_current_schema(db)
    assert sorted(foreign_keys(db)["conflicts"]) == [
        ("job_id", "sync_jobs", "CASCADE"), ("profile_id", "sync_profiles", "CASCADE"),
    ]

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("INSERT INTO conflicts (profile_id, job_id, file_path, resolved) VALUES (1, NULL, 'd.txt', 0)")
    conn.commit()
    conn.close()
    assert count(db, "SELECT COUNT(*) FROM conflicts") == 2  # plus the job's own conflict

    def downgrade(connection):
        cfg = alembic_config()
        cfg.attributes["connection"] = connection
        command.downgrade(cfg, "0002")

    # The older schema cannot hold a conflict without a job: it is dropped.
    await migrate_database(url, runner=downgrade)
    assert version(db) == ["0002"]
    assert count(db, "SELECT COUNT(*) FROM conflicts") == 1
    assert count(db, "SELECT COUNT(*) FROM conflicts WHERE job_id IS NULL") == 0

    await migrate_database(url)
    assert_current_schema(db)

    # Deleting the profile removes its diff conflicts.
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("INSERT INTO conflicts (profile_id, job_id, file_path, resolved) VALUES (1, NULL, 'e.txt', 0)")
    conn.execute("DELETE FROM sync_profiles WHERE id = 1")
    conn.commit()
    conn.close()
    assert count(db, "SELECT COUNT(*) FROM conflicts") == 0


async def test_existing_profiles_stay_mirror_after_the_two_way_revision(tmp_path):
    """0004: sync_mode (existing rows: mirror), conflict copy names, change side."""
    db = tmp_path / "two-way.db"
    build_pre_alembic_db(db, "pre_alembic")
    url = f"sqlite+aiosqlite:///{db}"

    def to_0003(connection):
        cfg = alembic_config()
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "0003")

    await migrate_database(url, runner=to_0003)
    assert version(db) == ["0003"]

    await migrate_database(url)

    assert_current_schema(db)
    conn = sqlite3.connect(db)
    try:
        assert sorted(conn.execute("SELECT id, sync_mode FROM sync_profiles")) == [(1, "mirror"), (2, "mirror")]
        assert list(conn.execute("SELECT local_kept_as, remote_kept_as FROM conflicts")) == [(None, None)]
        assert list(conn.execute("SELECT side FROM file_changes")) == [(None,)]
        # A row written without the column (older code) is a mirror profile too.
        _insert_profile(conn, 3, "three")
        conn.commit()
        assert conn.execute("SELECT sync_mode FROM sync_profiles WHERE id = 3").fetchone() == ("mirror",)
    finally:
        conn.close()

    def downgrade(connection):
        cfg = alembic_config()
        cfg.attributes["connection"] = connection
        command.downgrade(cfg, "0003")

    await migrate_database(url, runner=downgrade)
    assert version(db) == ["0003"]
    assert count(db, "SELECT COUNT(*) FROM sync_profiles") == 3
    await migrate_database(url)
    assert_current_schema(db)


async def test_mirror_notice_is_shown_until_dismissed_after_the_adoption_revision(tmp_path):
    """0005: mirror_notice_dismissed (existing rows: not dismissed)."""
    db = tmp_path / "adoption.db"
    build_pre_alembic_db(db, "pre_alembic")
    url = f"sqlite+aiosqlite:///{db}"

    def to_0004(connection):
        cfg = alembic_config()
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "0004")

    await migrate_database(url, runner=to_0004)
    assert version(db) == ["0004"]

    await migrate_database(url)

    assert_current_schema(db)
    conn = sqlite3.connect(db)
    try:
        assert sorted(conn.execute("SELECT id, mirror_notice_dismissed FROM sync_profiles")) == [(1, 0), (2, 0)]
        # A row written without the column (older code) shows the notice too.
        _insert_profile(conn, 3, "three")
        conn.commit()
        assert conn.execute("SELECT mirror_notice_dismissed FROM sync_profiles WHERE id = 3").fetchone() == (0,)
    finally:
        conn.close()

    def downgrade(connection):
        cfg = alembic_config()
        cfg.attributes["connection"] = connection
        command.downgrade(cfg, "0004")

    await migrate_database(url, runner=downgrade)
    assert version(db) == ["0004"]
    assert count(db, "SELECT COUNT(*) FROM sync_profiles") == 3
    await migrate_database(url)
    assert_current_schema(db)


async def test_stored_pause_revision_adds_an_empty_pause_reason(tmp_path):
    """0006: sync_profiles.pause_reason (existing rows: not paused)."""
    db = tmp_path / "pause.db"
    build_pre_alembic_db(db, "pre_alembic")
    url = f"sqlite+aiosqlite:///{db}"

    def to_0005(connection):
        cfg = alembic_config()
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "0005")

    await migrate_database(url, runner=to_0005)
    await migrate_database(url)

    assert_current_schema(db)
    conn = sqlite3.connect(db)
    try:
        assert sorted(conn.execute("SELECT id, pause_reason FROM sync_profiles")) == [(1, None), (2, None)]
    finally:
        conn.close()

    def downgrade(connection):
        cfg = alembic_config()
        cfg.attributes["connection"] = connection
        command.downgrade(cfg, "0005")

    await migrate_database(url, runner=downgrade)
    assert version(db) == ["0005"]
    await migrate_database(url)
    assert_current_schema(db)


async def test_existing_backup_targets_keep_three_snapshots_after_the_keep_last_revision(tmp_path):
    """0007: backup_targets.keep_last (existing rows and rows written without it: 3)."""
    db = tmp_path / "keep_last.db"
    build_pre_alembic_db(db, "pre_alembic")
    url = f"sqlite+aiosqlite:///{db}"

    def to_0006(connection):
        cfg = alembic_config()
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "0006")

    await migrate_database(url, runner=to_0006)
    assert version(db) == ["0006"]

    await migrate_database(url)

    assert_current_schema(db)
    conn = sqlite3.connect(db)
    try:
        assert list(conn.execute("SELECT id, keep_last FROM backup_targets")) == [(1, 3)]
        conn.execute(
            "INSERT INTO backup_targets (id, profile_id, name, target_path, target_type, retention_days,"
            " frequency_hours, backup_mode, enabled, created_at, updated_at)"
            " VALUES (2, 1, 't2', '/backups/two', 'local', 7, 24, 'mirror', 1, ?, ?)",
            (NOW, NOW),
        )
        conn.commit()
        assert conn.execute("SELECT keep_last FROM backup_targets WHERE id = 2").fetchone() == (3,)
    finally:
        conn.close()

    def downgrade(connection):
        cfg = alembic_config()
        cfg.attributes["connection"] = connection
        command.downgrade(cfg, "0006")

    await migrate_database(url, runner=downgrade)
    assert version(db) == ["0006"]
    assert count(db, "SELECT COUNT(*) FROM backup_targets") == 2
    await migrate_database(url)
    assert_current_schema(db)


async def test_bwlimit_window_and_user_pause_revision_leaves_profiles_unlimited(tmp_path):
    """0009: sync_profiles.bwlimit, sync_window (NULL) and user_paused (false) on existing rows."""
    db = tmp_path / "bwlimit.db"
    build_pre_alembic_db(db, "pre_alembic")
    url = f"sqlite+aiosqlite:///{db}"

    def to_0008(connection):
        cfg = alembic_config()
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "0008")

    await migrate_database(url, runner=to_0008)
    assert version(db) == ["0008"]
    await migrate_database(url)

    assert_current_schema(db)
    conn = sqlite3.connect(db)
    try:
        assert sorted(conn.execute("SELECT id, bwlimit, sync_window, user_paused FROM sync_profiles")) == [
            (1, None, None, 0), (2, None, None, 0),
        ]
    finally:
        conn.close()

    def downgrade(connection):
        cfg = alembic_config()
        cfg.attributes["connection"] = connection
        command.downgrade(cfg, "0008")

    await migrate_database(url, runner=downgrade)
    assert version(db) == ["0008"]
    assert count(db, "SELECT COUNT(*) FROM sync_profiles") == 2
    await migrate_database(url)
    assert_current_schema(db)


# --- cascades through the ORM (A1, DS-11) ---


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


async def test_pending_migrations_copy_the_database_first(tmp_path):
    db = tmp_path / "omnisync.db"
    build_pre_alembic_db(db, "pre_alembic")
    url = f"sqlite+aiosqlite:///{db}"

    def to_0003(connection):
        cfg = alembic_config()
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "0003")

    await migrate_database(url, runner=to_0003)

    await init_database(str(db))

    copy = tmp_path / f"omnisync.db.pre-{HEAD}.bak"
    assert copy.is_file()
    assert version(copy) == ["0003"]  # the database as it was before migrating
    assert count(copy, "SELECT COUNT(*) FROM sync_jobs") == 2
    assert_current_schema(db)

    # Nothing pending: no new copy.
    copy.unlink()
    await init_database(str(db))
    assert not copy.exists()


async def test_new_databases_are_not_copied(tmp_path):
    await init_database(str(tmp_path / "omnisync.db"))
    assert list(tmp_path.glob("*.bak")) == []


def test_only_the_newest_pre_migration_copies_are_kept(tmp_path):
    db = tmp_path / "omnisync.db"
    for i, rev in enumerate(["0001", "0002", "0003"]):
        old = tmp_path / f"omnisync.db.pre-{rev}.bak"
        old.write_text("old copy")
        os.utime(old, (1_000_000 + i, 1_000_000 + i))
    build_pre_alembic_db(db, "pre_alembic")

    copy = database.backup_before_migration(str(db))

    assert copy == tmp_path / f"omnisync.db.pre-{HEAD}.bak"
    kept = sorted(p.name for p in tmp_path.glob("omnisync.db.pre-*.bak"))
    assert kept == sorted(["omnisync.db.pre-0002.bak", "omnisync.db.pre-0003.bak", copy.name])
    assert not list(tmp_path.glob("*.tmp"))
