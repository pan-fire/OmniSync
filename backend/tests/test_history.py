"""Job history housekeeping: interrupted jobs, retention and VACUUM (backend/services/history.py)."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.api.schemas import GlobalConfigUpdateRequest
from backend.db.database import create_db_engine
from backend.db.models import (
    BackupJob,
    BackupTarget,
    Base,
    Conflict,
    FileChange,
    SyncError,
    SyncJob,
    SyncProfile,
)
from backend.services import history
from backend.services.config import ConfigService
from backend.services.history import (
    INTERRUPTED,
    HistoryMaintenance,
    prune_history,
    recover_interrupted_jobs,
    vacuum_if_fragmented,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    """A file database with the app's connection settings (foreign keys enforced)."""
    engine = create_db_engine(f"sqlite+aiosqlite:///{tmp_path / 'history.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def add_profile(db: async_sessionmaker[AsyncSession], slug: str) -> int:
    async with db() as session:
        profile = SyncProfile(slug=slug, name=slug, local_dir=f"/l/{slug}", remote_dir=f"r:{slug}",
                              created_at=NOW, updated_at=NOW)
        session.add(profile)
        await session.commit()
        return profile.id


async def add_target(db: async_sessionmaker[AsyncSession], profile_id: int) -> int:
    async with db() as session:
        target = BackupTarget(profile_id=profile_id, name="t", target_path="/b", target_type="local",
                              backup_mode="archive", created_at=NOW, updated_at=NOW)
        session.add(target)
        await session.commit()
        return target.id


async def add_job(
    db: async_sessionmaker[AsyncSession], profile_id: int, days_ago: float,
    status: str = "completed", changes: int = 0, errors: int = 0,
) -> int:
    started = NOW - timedelta(days=days_ago)
    async with db() as session:
        job = SyncJob(profile_id=profile_id, direction="push", started_at=started,
                      finished_at=None if status == "running" else started, status=status)
        session.add(job)
        await session.flush()
        session.add_all(FileChange(job_id=job.id, file_path=f"f{i}", action="created") for i in range(changes))
        session.add_all(SyncError(job_id=job.id, message="e", created_at=started) for _ in range(errors))
        await session.commit()
        return job.id


async def add_backup_job(
    db: async_sessionmaker[AsyncSession], target_id: int, days_ago: float,
    status: str = "completed", direction: str = "backup",
) -> int:
    started = NOW - timedelta(days=days_ago)
    async with db() as session:
        job = BackupJob(target_id=target_id, started_at=started, status=status, direction=direction,
                        finished_at=None if status == "running" else started)
        session.add(job)
        await session.commit()
        return job.id


async def ids(db: async_sessionmaker[AsyncSession], model) -> set[int]:
    async with db() as session:
        return set((await session.execute(select(model.id))).scalars().all())


async def rows(db: async_sessionmaker[AsyncSession], model) -> int:
    async with db() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# --- jobs left running by a crash ---


async def test_jobs_left_running_are_closed_as_failed(db) -> None:
    pid = await add_profile(db, "docs")
    target = await add_target(db, pid)
    stuck = await add_job(db, pid, 1, status="running", errors=0)
    done = await add_job(db, pid, 2, status="completed")
    stuck_backup = await add_backup_job(db, target, 1, status="running")
    done_backup = await add_backup_job(db, target, 2)

    assert await recover_interrupted_jobs(db) == (1, 1)

    async with db() as session:
        job = await session.get(SyncJob, stuck)
        assert job is not None
        assert job.status == "failed" and job.finished_at is not None and job.errors == 1
        messages = (await session.execute(select(SyncError.message).where(SyncError.job_id == stuck))).scalars().all()
        assert messages == [INTERRUPTED]
        assert (await session.get(SyncJob, done)).status == "completed"  # type: ignore[union-attr]
        backup = await session.get(BackupJob, stuck_backup)
        assert backup is not None
        assert backup.status == "failed" and backup.error_message == INTERRUPTED and backup.finished_at is not None
        assert (await session.get(BackupJob, done_backup)).status == "completed"  # type: ignore[union-attr]

    assert await recover_interrupted_jobs(db) == (0, 0)  # nothing left


# --- retention ---


async def test_old_jobs_are_removed_with_their_rows(db) -> None:
    pid = await add_profile(db, "docs")
    old = await add_job(db, pid, 100, changes=3, errors=2)
    recent = await add_job(db, pid, 10, changes=1, errors=1)

    result = await prune_history(db, 90, keep_jobs=0, now=NOW)

    assert result.sync_jobs == 1
    assert await ids(db, SyncJob) == {recent}
    async with db() as session:  # the foreign keys cascaded
        assert (await session.execute(select(FileChange.job_id))).scalars().all() == [recent]
        assert (await session.execute(select(SyncError.job_id))).scalars().all() == [recent]
    assert old not in await ids(db, SyncJob)


async def test_the_newest_jobs_of_each_profile_are_kept(db) -> None:
    a = await add_profile(db, "a")
    b = await add_profile(db, "b")
    a_jobs = [await add_job(db, a, 200 + i) for i in range(5)]  # all old; a_jobs[0] newest
    b_jobs = [await add_job(db, b, 300 + i) for i in range(2)]

    await prune_history(db, 90, keep_jobs=2, now=NOW)

    assert await ids(db, SyncJob) == {*a_jobs[:2], *b_jobs}


async def test_running_jobs_and_unresolved_conflicts_are_kept(db) -> None:
    pid = await add_profile(db, "docs")
    running = await add_job(db, pid, 200, status="running")
    open_conflict = await add_job(db, pid, 200)
    resolved_conflict = await add_job(db, pid, 200)
    async with db() as session:
        session.add(Conflict(profile_id=pid, job_id=open_conflict, file_path="a", resolved=False))
        session.add(Conflict(profile_id=pid, job_id=resolved_conflict, file_path="b", resolved=True))
        session.add(Conflict(profile_id=pid, job_id=None, file_path="c", resolved=False))
        await session.commit()

    await prune_history(db, 90, keep_jobs=0, now=NOW)

    assert await ids(db, SyncJob) == {running, open_conflict}
    async with db() as session:
        assert sorted((await session.execute(select(Conflict.file_path))).scalars().all()) == ["a", "c"]


async def test_old_backup_jobs_are_removed_but_the_newest_backup_is_kept(db) -> None:
    pid = await add_profile(db, "docs")
    target = await add_target(db, pid)
    newest_backup = await add_backup_job(db, target, 150)
    older_backup = await add_backup_job(db, target, 160)
    failed = await add_backup_job(db, target, 120, status="failed")
    restore = await add_backup_job(db, target, 130, direction="restore")
    recent = await add_backup_job(db, target, 5, status="failed")

    result = await prune_history(db, 90, keep_jobs=0, now=NOW)

    assert result.backup_jobs == 3
    # The schedule counts from the newest completed backup, however old.
    assert await ids(db, BackupJob) == {newest_backup, recent}
    assert not {older_backup, failed, restore} & await ids(db, BackupJob)


async def test_zero_days_keeps_everything(db) -> None:
    pid = await add_profile(db, "docs")
    job = await add_job(db, pid, 5000)
    assert await prune_history(db, 0, keep_jobs=0, now=NOW) == history.PruneResult()
    assert await ids(db, SyncJob) == {job}


async def test_large_deletes_go_in_batches(db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(history, "DELETE_BATCH", 7)
    pid = await add_profile(db, "docs")
    for i in range(30):
        await add_job(db, pid, 100 + i, changes=2)
    result = await prune_history(db, 90, keep_jobs=5, now=NOW)
    assert result.sync_jobs == 25
    assert await rows(db, SyncJob) == 5
    assert await rows(db, FileChange) == 10


# --- VACUUM ---


async def test_vacuum_runs_when_much_of_the_file_is_free(db) -> None:
    engine = db.kw["bind"]
    pid = await add_profile(db, "docs")
    for i in range(40):
        await add_job(db, pid, 100 + i, changes=50)

    async def pages() -> tuple[int, int]:
        async with engine.connect() as conn:
            return ((await conn.execute(text("PRAGMA page_count"))).scalar(),
                    (await conn.execute(text("PRAGMA freelist_count"))).scalar())

    assert await vacuum_if_fragmented(engine) is False  # nothing free yet
    await prune_history(db, 90, keep_jobs=0, now=NOW)
    total_before, free_before = await pages()
    assert free_before / total_before >= history.VACUUM_FREE_RATIO

    assert await vacuum_if_fragmented(engine) is True
    total_after, free_after = await pages()
    assert free_after == 0 and total_after < total_before


# --- the daily run ---


async def test_maintenance_prunes_with_the_configured_days(db, tmp_path: Path) -> None:
    pid = await add_profile(db, "docs")
    now = datetime.now(timezone.utc)
    await add_job(db, pid, (NOW - now).total_seconds() / 86400 + 40)  # 40 days old
    config = ConfigService(tmp_path / "config.toml")
    maintenance = HistoryMaintenance(db, lambda: config.read_global().history_days)

    await maintenance.run_once()
    assert await rows(db, SyncJob) == 1  # default 90 days

    config.write_global(GlobalConfigUpdateRequest(history_days=30))
    # The newest jobs are always kept: make it more than HISTORY_KEEP_JOBS.
    for _ in range(history.HISTORY_KEEP_JOBS):
        await add_job(db, pid, (NOW - now).total_seconds() / 86400)
    result = await maintenance.run_once()
    assert result.sync_jobs == 1


async def test_maintenance_runs_at_start_and_then_on_its_interval(db, monkeypatch: pytest.MonkeyPatch) -> None:
    runs: list[int] = []

    async def fake_prune(_db, days, *args, **kwargs):
        runs.append(days)
        if len(runs) == 2:
            raise RuntimeError("database is locked")  # a failed run does not end the loop
        return history.PruneResult()

    monkeypatch.setattr(history, "prune_history", fake_prune)
    maintenance = HistoryMaintenance(db, lambda: 7, interval=timedelta(seconds=0.01))
    maintenance.start()
    for _ in range(200):
        if len(runs) >= 3:
            break
        await asyncio.sleep(0.01)
    await maintenance.stop()
    assert runs[:3] == [7, 7, 7]


async def test_stop_is_bounded_when_the_cleanup_does_not_end(db, monkeypatch) -> None:
    """Shutdown never waits longer than stop()'s timeout for the cleanup (it runs before the engines stop)."""
    release = asyncio.Event()

    async def stuck_prune(_db, _days):
        while not release.is_set():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                if release.is_set():
                    raise
                # like a VACUUM in the driver's thread: cancelling does not end it
        return history.PruneResult()

    monkeypatch.setattr(history, "prune_history", stuck_prune)
    maintenance = HistoryMaintenance(db, lambda: 7)
    maintenance.start()
    await asyncio.sleep(0.05)
    task = maintenance._task
    assert task is not None
    loop = asyncio.get_running_loop()
    started = loop.time()
    await maintenance.stop(timeout=0.2)
    assert loop.time() - started < 2 and not task.done()
    release.set()
    task.cancel()
    await asyncio.wait({task}, timeout=5)
    assert task.done()


# --- the setting ---


def test_history_days_setting(tmp_path: Path) -> None:
    config = ConfigService(tmp_path / "config.toml")
    assert config.read_global().history_days == 90
    assert config.write_global(GlobalConfigUpdateRequest(history_days=0)).history_days == 0
    assert config.read_global().log_level == "INFO"  # untouched
    (tmp_path / "config.toml").write_text('history_days = "lots"\n')
    assert config.read_global().history_days == 90  # a bad hand edit falls back to the default


@pytest.fixture
def test_services(test_services, tmp_path: Path):
    """test_client with a real ConfigService on a scratch config.toml."""
    return dataclasses.replace(test_services, config_service=ConfigService(tmp_path / "config.toml"))


async def test_history_days_through_the_api(test_client) -> None:
    resp = await test_client.get("/config")
    assert resp.status_code == 200 and resp.json()["history_days"] == 90
    resp = await test_client.put("/config", json={"history_days": 30})
    assert resp.status_code == 200 and resp.json() == {"log_level": "INFO", "history_days": 30}
    assert (await test_client.put("/config", json={"history_days": -1})).status_code == 422


def test_startup_closes_interrupted_jobs_before_engines_start() -> None:
    import inspect

    import backend.main as main

    source = inspect.getsource(main.lifespan)
    assert source.index("recover_interrupted_jobs(") < source.index("manager.start_all()")
    assert "history.start()" in source and "history.stop()" in source
