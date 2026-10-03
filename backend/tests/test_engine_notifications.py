"""Which notifications the engine and the backup service send (R1).

A recording fake stands in for the dispatcher: the engine hands events to
its emit() (background, the sync never waits), the backup service awaits
dispatch().
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.schemas import ChangeCategory, FileDiff
from backend.db.models import Base, SyncProfile
from backend.exceptions import RcloneAuthError, RcloneError
from backend.models.profile_config import ProfileConfig
from backend.services.notification_events import NotificationEvent, NotificationEventType, NotificationSeverity
from backend.services.rclone import BisyncRecorder
from backend.services.sync_engine import SyncEngine, bisync_workdir
from backend.tests import test_profiles
from backend.tests.test_profiles import Env
from backend.tests.test_two_way_unit import NORMAL_RUN, line

env = test_profiles.env


class RecordingDispatcher:
    def __init__(self) -> None:
        self.events: list[NotificationEvent] = []

    def emit(self, event: NotificationEvent) -> None:
        self.events.append(event)

    async def dispatch(self, event: NotificationEvent, only_channel: str | None = None):
        self.events.append(event)
        return [], {}

    def types(self) -> list[NotificationEventType]:
        return [e.event_type for e in self.events]


async def _profile_row(env: Env, tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    async with env.factory() as session:
        session.add(SyncProfile(id=1, slug="docs", name="Docs", local_dir=str(tmp_path / "local"),
                                remote_dir="gdrive:Docs", created_at=now, updated_at=now))
        await session.commit()


def _engine(tmp_path: Path, rclone: AsyncMock, factory, dispatcher, **config) -> SyncEngine:
    local = tmp_path / "local"
    local.mkdir(exist_ok=True)
    profile = ProfileConfig(profile_id=1, slug="docs", name="Docs", local_dir=str(local),
                            remote_dir="gdrive:Docs", max_retries=2, **config)
    return SyncEngine(profile, rclone, factory, dispatcher)


@pytest.fixture
def no_backoff(monkeypatch):
    monkeypatch.setattr("backend.services.sync_engine.common.calculate_backoff_delay", lambda *_: 0)


@pytest.fixture
def bisync_dir(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "bisync"
    monkeypatch.setattr("backend.services.sync_engine.two_way.BISYNC_DIR", str(path))
    return path


# --- mirror sync failures become the specific event ---


@pytest.mark.parametrize(("error", "expected"), [
    ("rclone failed (exit 1): Failed to copy: dial tcp: lookup www.googleapis.com: no such host",
     NotificationEventType.REMOTE_UNREACHABLE),
    ("rclone failed (exit 3): directory not found", NotificationEventType.SYNC_FAILED),
])
async def test_exhausted_retries(tmp_path, env: Env, no_backoff, error, expected):
    await _profile_row(env, tmp_path)
    rclone = AsyncMock()
    rclone.list_top_level = AsyncMock(return_value=[])
    rclone.sync = AsyncMock(side_effect=RcloneError(error))
    dispatcher = RecordingDispatcher()
    engine = _engine(tmp_path, rclone, env.factory, dispatcher, sync_mode="mirror")

    await engine.push()

    assert dispatcher.types() == [expected]
    event = dispatcher.events[0]
    assert event.severity == NotificationSeverity.ERROR and event.profile_slug == "docs"
    if expected == NotificationEventType.REMOTE_UNREACHABLE:
        assert "gdrive:Docs" in event.title and "no such host" in event.body
    else:
        assert event.body.startswith("Failed after 2 attempt(s):")


async def test_auth_error(tmp_path, env: Env):
    await _profile_row(env, tmp_path)
    rclone = AsyncMock()
    rclone.list_top_level = AsyncMock(return_value=[])
    rclone.sync = AsyncMock(side_effect=RcloneAuthError("rclone authentication error: invalid_grant"))
    dispatcher = RecordingDispatcher()
    engine = _engine(tmp_path, rclone, env.factory, dispatcher, sync_mode="mirror")

    await engine.pull()

    assert dispatcher.types() == [NotificationEventType.AUTH_ERROR]


async def test_the_sync_does_not_wait_for_the_channels(tmp_path, env: Env):
    """R2: a slow channel never holds up a sync: the engine only emits."""
    await _profile_row(env, tmp_path)
    rclone = AsyncMock()
    rclone.list_top_level = AsyncMock(return_value=[])
    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock(side_effect=lambda *_: asyncio.sleep(30))
    engine = _engine(tmp_path, rclone, env.factory, dispatcher, sync_mode="mirror")

    await asyncio.wait_for(engine.push(), timeout=5)

    dispatcher.emit.assert_called_once()
    assert dispatcher.emit.call_args[0][0].event_type == NotificationEventType.SYNC_COMPLETED
    dispatcher.dispatch.assert_not_called()


# --- startup ---


@pytest.mark.parametrize(("error", "expected"), [
    ("rclone failed (exit 2): permission denied", NotificationEventType.STARTUP_FAILURE),
    ("rclone authentication error: token expired", NotificationEventType.AUTH_ERROR),
    ("rclone failed: connection refused", NotificationEventType.REMOTE_UNREACHABLE),
])
async def test_failed_startup_check(tmp_path, env: Env, error, expected):
    dispatcher = RecordingDispatcher()
    engine = _engine(tmp_path, AsyncMock(), env.factory, dispatcher, sync_mode="mirror")
    engine.check_diff = AsyncMock(return_value=MagicMock(error=error))

    await engine._startup_check()

    assert dispatcher.types() == [expected]
    assert engine._state.intervals_paused
    if expected == NotificationEventType.STARTUP_FAILURE:
        assert "Docs" in dispatcher.events[0].title and "paused" in dispatcher.events[0].body


@pytest_asyncio.fixture
async def factory():
    """A database for engines that really start (the env fixture stubs start())."""
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(db, expire_on_commit=False)
    await db.dispose()


async def test_missing_local_folder_at_start(tmp_path, factory):
    dispatcher = RecordingDispatcher()
    profile = ProfileConfig(profile_id=1, slug="docs", name="Docs", local_dir=str(tmp_path / "gone"),
                            remote_dir="gdrive:Docs", sync_mode="mirror")
    engine = SyncEngine(profile, AsyncMock(), factory, dispatcher)
    try:
        await engine.start()
    finally:
        await engine.stop()
    assert dispatcher.types() == [NotificationEventType.STARTUP_FAILURE]
    assert "not mounted" in dispatcher.events[0].body


# --- crashes ---


async def test_crash_of_a_scheduled_sync(tmp_path, factory):
    """A scheduled job that raises (instead of recording a failure) is an engine crash."""
    dispatcher = RecordingDispatcher()
    engine = _engine(tmp_path, AsyncMock(), factory, dispatcher, sync_mode="mirror", pull_interval_minutes=60)
    engine.check_diff = AsyncMock(return_value=MagicMock(error=None, local_only=[], remote_only=[], differ=[]))
    await engine.start()
    try:
        engine._scheduled_sync = AsyncMock(side_effect=KeyError("boom"))
        job = engine._scheduler.get_job("periodic_pull")
        job.modify(func=engine._scheduled_sync, next_run_time=datetime.now(timezone.utc))
        engine._scheduler.wakeup()
        for _ in range(100):
            await asyncio.sleep(0.02)
            if dispatcher.events:
                break
    finally:
        await engine.stop()
    assert dispatcher.types() == [NotificationEventType.ENGINE_CRASH]
    assert "KeyError" in dispatcher.events[0].body


async def test_crash_of_a_watcher_triggered_sync(tmp_path, env: Env):
    dispatcher = RecordingDispatcher()
    engine = _engine(tmp_path, AsyncMock(), env.factory, dispatcher, sync_mode="mirror")
    engine._loop = asyncio.get_running_loop()
    engine.push = AsyncMock(side_effect=RuntimeError("database is locked"))

    engine._trigger_debounced_push()
    for _ in range(100):
        await asyncio.sleep(0.01)
        if dispatcher.events:
            break

    assert dispatcher.types() == [NotificationEventType.ENGINE_CRASH]
    assert "database is locked" in dispatcher.events[0].body


# --- conflicts from a diff ---


def _conflict(path: str) -> FileDiff:
    now = datetime.now(timezone.utc)
    return FileDiff(path=path, category=ChangeCategory.MODIFIED_BOTH, is_conflict=True,
                    local_mod_time=now, remote_mod_time=now)


async def test_new_conflicts_notify_once(tmp_path, env: Env):
    await _profile_row(env, tmp_path)
    dispatcher = RecordingDispatcher()
    engine = _engine(tmp_path, AsyncMock(), env.factory, dispatcher, sync_mode="mirror")

    await engine._record_conflicts([_conflict("a.txt"), _conflict("b.txt")])
    await engine._record_conflicts([_conflict("a.txt"), _conflict("b.txt")])  # the same again
    await engine._record_conflicts([_conflict("a.txt"), _conflict("b.txt"), _conflict("c.txt")])

    assert dispatcher.types() == [NotificationEventType.CONFLICT_DETECTED] * 2
    assert dispatcher.events[0].body.startswith("2 file(s)")
    assert dispatcher.events[1].body.startswith("1 file(s)")
    assert "choose which version" in dispatcher.events[0].body


# --- two-way sync (PR #12) ---


def _synced_before(engine: SyncEngine, tmp_path: Path) -> None:
    (tmp_path / "local" / ".omnisync-check").write_text("m")
    (tmp_path / "local" / "a.txt").write_text("a")
    workdir = Path(bisync_workdir(1))
    workdir.mkdir(parents=True)
    (workdir / "omnisync-state.json").write_text(json.dumps({"pair": engine._pair}))
    (workdir / "x.path1.lst").write_text("# bisync listing\n")
    (workdir / "x.path2.lst").write_text("# bisync listing\n")
    (workdir / "filters.txt").write_text(engine._bisync_filters([]))


def _two_way_rclone(bisync) -> AsyncMock:
    rclone = AsyncMock()
    rclone.list_top_level = AsyncMock(return_value=[".omnisync-check", "a.txt"])
    rclone.bisync = AsyncMock(side_effect=bisync)
    return rclone


async def test_two_way_sync_with_conflicts_kept(tmp_path, env: Env, bisync_dir):
    async def bisync(*args, recorder: BisyncRecorder, dry_run: bool = False, **kwargs):
        if not dry_run:
            for entry in NORMAL_RUN:
                recorder.feed(entry)

    await _profile_row(env, tmp_path)
    dispatcher = RecordingDispatcher()
    engine = _engine(tmp_path, _two_way_rclone(bisync), env.factory, dispatcher, sync_mode="two_way")
    _synced_before(engine, tmp_path)

    await engine.two_way_sync()

    assert dispatcher.types() == [NotificationEventType.SYNC_COMPLETED, NotificationEventType.CONFLICT_DETECTED]
    completed, conflicts = dispatcher.events
    assert completed.title == "Two-way sync completed — Docs"
    assert "both directions" in completed.body and "1 file(s) changed on both sides" in completed.body
    assert conflicts.severity == NotificationSeverity.WARNING
    assert "Both versions were kept" in conflicts.body


async def test_two_way_first_run_is_a_resync(tmp_path, env: Env, bisync_dir):
    async def bisync(*args, recorder: BisyncRecorder, **kwargs):
        pass

    await _profile_row(env, tmp_path)
    dispatcher = RecordingDispatcher()
    engine = _engine(tmp_path, _two_way_rclone(bisync), env.factory, dispatcher, sync_mode="two_way")
    (tmp_path / "local" / ".omnisync-check").write_text("m")

    await engine.two_way_sync()

    assert dispatcher.types() == [NotificationEventType.SYNC_COMPLETED]
    assert dispatcher.events[0].title == "Two-way resync completed — Docs"


async def test_two_way_resync_required_pause(tmp_path, env: Env, bisync_dir):
    async def bisync(*args, recorder: BisyncRecorder, dry_run: bool = False, **kwargs):
        if dry_run:
            return
        recorder.feed(line("Bisync critical error: chtimes /r/x.partial: no such file or directory", "error"))
        recorder.feed(line("Bisync aborted. Must run --resync to recover.", "error"))
        raise RcloneError("rclone failed (exit 7)")

    await _profile_row(env, tmp_path)
    dispatcher = RecordingDispatcher()
    engine = _engine(tmp_path, _two_way_rclone(bisync), env.factory, dispatcher, sync_mode="two_way")
    _synced_before(engine, tmp_path)

    await engine.two_way_sync()

    assert dispatcher.types() == [NotificationEventType.INTERVALS_PAUSED]
    event = dispatcher.events[0]
    assert event.severity == NotificationSeverity.ERROR
    assert event.title == "Two-way sync needs a resync — Docs"
    assert "chtimes" in event.body and "Resync" in event.body


async def test_two_way_failure(tmp_path, env: Env, bisync_dir, no_backoff):
    async def bisync(*args, recorder: BisyncRecorder, dry_run: bool = False, **kwargs):
        if not dry_run:
            raise RcloneError("rclone failed (exit 1): quota exceeded")

    await _profile_row(env, tmp_path)
    dispatcher = RecordingDispatcher()
    engine = _engine(tmp_path, _two_way_rclone(bisync), env.factory, dispatcher, sync_mode="two_way")
    _synced_before(engine, tmp_path)

    await engine.two_way_sync()

    assert dispatcher.types() == [NotificationEventType.SYNC_FAILED]
    assert dispatcher.events[0].title == "Two-way sync failed — Docs"
    assert dispatcher.events[0].body == "Failed after 2 attempt(s): rclone failed (exit 1): quota exceeded"


async def test_two_way_refusal_is_not_called_retries(tmp_path, env: Env, bisync_dir):
    await _profile_row(env, tmp_path)
    dispatcher = RecordingDispatcher()
    rclone = _two_way_rclone(None)
    rclone.list_top_level = AsyncMock(return_value=["a.txt"])  # the marker is gone on the remote
    engine = _engine(tmp_path, rclone, env.factory, dispatcher, sync_mode="two_way")
    _synced_before(engine, tmp_path)

    await engine.two_way_sync()

    assert dispatcher.types() == [NotificationEventType.SYNC_FAILED]
    assert dispatcher.events[0].body.startswith("The sync marker")


# --- backup paths ---


async def _backup_env(factory, tmp_path: Path, rclone: AsyncMock | None = None):
    from backend.db.models import BackupTarget
    from backend.services.backup_service import BackupService

    now = datetime.now(timezone.utc)
    local = tmp_path / "local"
    local.mkdir(exist_ok=True)
    async with factory() as session:
        profile = SyncProfile(slug="docs", name="Docs", local_dir=str(local), remote_dir="gdrive:Docs",
                              created_at=now, updated_at=now)
        session.add(profile)
        await session.flush()
        target = BackupTarget(profile_id=profile.id, name="Daily", target_path=str(tmp_path / "backup"),
                              target_type="local", backup_mode="mirror", created_at=now, updated_at=now)
        session.add(target)
        await session.commit()
        target_id = target.id
    engine = MagicMock()
    engine.sync_lock = asyncio.Lock()
    manager = MagicMock()
    manager.get_engine.return_value = engine
    manager.sync_lock.return_value = engine.sync_lock
    dispatcher = RecordingDispatcher()
    (tmp_path / "backup").mkdir(exist_ok=True)
    return BackupService(rclone or AsyncMock(), dispatcher, factory, manager), dispatcher, target_id, engine


async def test_backup_auth_error_is_an_auth_error(tmp_path, factory):
    rclone = AsyncMock()
    rclone.sync_with_backup_dir = AsyncMock(side_effect=RcloneAuthError("rclone authentication error: expired"))
    service, dispatcher, target_id, _ = await _backup_env(factory, tmp_path, rclone)

    job = await service.run_backup(target_id)

    assert job.status == "failed"
    assert dispatcher.types() == [NotificationEventType.AUTH_ERROR]
    assert "backup to 'Daily'" in dispatcher.events[0].body
    assert dispatcher.events[0].profile_slug == "docs"


async def test_backup_waiting_too_long_for_the_sync_is_reported(tmp_path, factory, monkeypatch):
    from backend.services.backup_service import common as backup_common

    monkeypatch.setattr(backup_common, "LOCK_TIMEOUT", 0.05)
    service, dispatcher, target_id, engine = await _backup_env(factory, tmp_path)
    await engine.sync_lock.acquire()  # a sync that does not finish
    try:
        job = await service.run_backup(target_id)
    finally:
        engine.sync_lock.release()

    assert job.status == "failed"
    assert dispatcher.types() == [NotificationEventType.BACKUP_FAILED]
    assert "Timed out waiting" in dispatcher.events[0].body


async def test_backup_crash_is_reported_and_ends_the_job(tmp_path, factory, caplog):
    service, dispatcher, target_id, _ = await _backup_env(factory, tmp_path)
    service._backup = AsyncMock(side_effect=OSError("disk I/O error"))

    job = await service.run_backup(target_id)

    assert dispatcher.types() == [NotificationEventType.ENGINE_CRASH]
    assert dispatcher.events[0].title == "Backup crash"
    # The job ends (it is not left running) with a fixed code; the traceback is in the log.
    assert (job.status, job.error_code) == ("failed", "crashed")
    assert job.finished_at is not None
    assert any(r.exc_info and "failed unexpectedly" in r.getMessage() for r in caplog.records)


async def test_backup_of_an_unknown_target_is_not_a_crash(tmp_path, factory):
    service, dispatcher, _, _ = await _backup_env(factory, tmp_path)
    with pytest.raises(ValueError):
        await service.run_backup(999)
    assert dispatcher.events == []
