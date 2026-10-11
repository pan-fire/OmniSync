"""Edge cases of pausing and resuming automatic syncing (sync_engine/pause.py).

A pause is what keeps an unreviewed difference from being pushed or pulled
over. These tests use a real, migrated SQLite database (the pause is stored
with the profile) and the real rclone binary with a `local` remote for the
comparison a resume runs. Skipped when rclone is not installed.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from backend.db import database
from backend.db.database import init_database
from backend.db.models import SyncProfile
from backend.exceptions import IntervalsNotResumableError
from backend.models.profile_config import ProfileConfig
from backend.services.notification_events import NotificationEventType
from backend.services.rclone import SENTINEL_FILE, RcloneService
from backend.services.sync_engine import SyncEngine
from backend.services.sync_engine.pause import store_pause_reason, store_user_pause

if shutil.which("rclone") is None and os.environ.get("OMNISYNC_REQUIRE_RCLONE") == "1":
    raise RuntimeError("OMNISYNC_REQUIRE_RCLONE=1 but rclone is not on PATH")
pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")


class FakeTimer:
    """Stands in for the debounce timer: records that a push was armed, never fires."""

    armed: list[FakeTimer] = []

    def __init__(self, delay: float, fn) -> None:
        self.delay, self.fn, self.cancelled = delay, fn, False

    def start(self) -> None:
        FakeTimer.armed.append(self)

    def cancel(self) -> None:
        self.cancelled = True


class FakeJob:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def pause(self) -> None:
        self.calls.append("pause")

    def resume(self) -> None:
        self.calls.append("resume")


@dataclass
class Env:
    local: Path
    remote: Path
    factory: object
    conf: Path
    profile_id: int

    def engine(self, **changes) -> SyncEngine:
        config = ProfileConfig(profile_id=self.profile_id, slug="docs", name="Docs", local_dir=str(self.local),
                               remote_dir=f"testremote:{self.remote}", max_retries=1, debounce_seconds=5)
        engine = SyncEngine(dataclasses.replace(config, **changes),
                            RcloneService(rclone_config_path=str(self.conf)), self.factory)
        engine.timer_factory = FakeTimer  # type: ignore[assignment]
        return engine

    async def stored(self) -> tuple[str | None, bool]:
        async with self.factory() as session:
            profile = await session.get(SyncProfile, self.profile_id)
            return profile.pause_reason, profile.user_paused


@pytest_asyncio.fixture
async def env(tmp_path: Path):
    local = tmp_path / "local"
    remote = tmp_path / "remote"
    for side in (local, remote):
        side.mkdir()
        (side / SENTINEL_FILE).write_text("marker")
        (side / "same.md").write_text("same")
    conf = tmp_path / "rclone.conf"
    conf.write_text("[testremote]\ntype = local\n")

    saved = database._engine, database._async_session_factory
    database._engine = None
    await init_database(str(tmp_path / "pause.db"))
    factory = database._async_session_factory
    now = datetime.now(timezone.utc)
    async with factory() as session:
        profile = SyncProfile(
            slug="docs", name="Docs", local_dir=str(local), remote_dir=f"testremote:{remote}",
            debounce_seconds=5, pull_interval_minutes=5, rclone_filter="[]", rclone_args="[]",
            max_retries=1, enabled=True, created_at=now, updated_at=now,
        )
        session.add(profile)
        await session.commit()
        profile_id = profile.id
    FakeTimer.armed = []
    yield Env(local, remote, factory, conf, profile_id)

    FakeTimer.armed = []
    await database._engine.dispose()
    database._engine, database._async_session_factory = saved


def save_while_paused(engine: SyncEngine, name: str = "notes.md") -> None:
    """What the watcher reports for a local save."""
    engine._on_file_change(SimpleNamespace(src_path=f"{engine._profile.local_dir}/{name}", event_type="modified"))


# --- the stored pause ---


async def test_storing_a_pause_for_a_deleted_profile_is_a_no_op(env):
    """The profile was deleted while its engine stopped: nothing is created or raised."""
    await store_pause_reason(env.factory, 999, "restore running")
    await store_user_pause(env.factory, 999, True)
    assert await env.stored() == (None, False)


async def test_a_stored_pause_holds_after_a_restart(env):
    """A restore's hold and the user's pause survive a new engine for the profile."""
    first = env.engine()
    await first.hold("Restored from a backup; review before syncing")
    await first.pause_by_user()

    engine = env.engine()
    await engine._load_hold()

    assert engine._state.intervals_paused and engine._state.user_paused
    assert engine._state.last_error == "Restored from a backup; review before syncing"
    assert engine._held is True


async def test_an_unreadable_stored_pause_is_logged_and_not_guessed(env, caplog):
    """The database failing at start must not crash the engine; it is logged."""
    engine = env.engine()

    def broken():
        raise RuntimeError("database is locked")
    engine._db_session_factory = broken  # type: ignore[assignment]

    await engine._load_hold()

    assert "could not read a stored pause" in caplog.text
    assert not engine._state.auto_paused


# --- pausing ---


async def test_pausing_for_differences_notifies(env):
    """The user learns that automatic syncing stopped, with the count of differences."""
    events: list[object] = []
    sent = asyncio.Event()

    async def dispatch(event) -> None:
        events.append(event)
        sent.set()
    engine = env.engine()
    engine._dispatcher = SimpleNamespace(dispatch=dispatch)  # type: ignore[assignment]
    engine._loop = asyncio.get_running_loop()

    engine._pause_if_needed(3)
    await asyncio.wait_for(sent.wait(), timeout=10)

    assert engine._state.intervals_paused
    (event,) = events
    assert event.event_type == NotificationEventType.INTERVALS_PAUSED  # type: ignore[attr-defined]
    assert "3 unresolved" in event.body  # type: ignore[attr-defined]


async def test_a_two_way_profile_is_not_paused_by_differences(env):
    """Two-way carries differences both ways; pausing it would stop syncing for nothing."""
    engine = env.engine(sync_mode="two_way")
    engine._pause_if_needed(5)
    assert not engine._state.auto_paused


async def test_the_scheduler_follows_the_pause_and_tolerates_a_missing_job(env):
    engine = env.engine()
    job = FakeJob()
    engine._scheduler = SimpleNamespace(get_job=lambda name: job)  # type: ignore[assignment]
    engine._pause("test")
    engine._resume("test")
    assert job.calls == ["pause", "resume"]

    engine._scheduler = SimpleNamespace(get_job=lambda name: None)  # type: ignore[assignment]
    engine._pause("test")  # no periodic job (e.g. pull interval off): no error
    assert engine._state.intervals_paused


# --- resuming ---


async def test_resume_all_without_a_user_pause_changes_nothing(env):
    engine = env.engine()
    engine._pause("3 unresolved differences")

    assert await engine.resume_user_pause() is None
    assert engine._state.intervals_paused


async def test_resume_all_lifts_only_the_users_pause(env):
    """An engine pause (pending differences) stays; the answer says why."""
    engine = env.engine()
    await engine.pause_by_user()
    engine.pause_for("3 unresolved differences")

    assert await engine.resume_user_pause() == "3 unresolved differences"

    assert not engine._state.user_paused
    assert engine._state.intervals_paused
    assert await env.stored() == (None, False)


async def test_a_two_way_profile_needing_a_resync_cannot_be_resumed(env):
    engine = env.engine(sync_mode="two_way")
    await engine.pause_by_user()
    engine._state.resync_required = True

    with pytest.raises(IntervalsNotResumableError, match="needs a resync"):
        await engine.resume_intervals()
    assert engine._state.user_paused


async def test_a_two_way_profile_resumes_despite_differences(env):
    """Two-way carries local edits both ways on its next run: no compare, no refusal."""
    engine = env.engine(sync_mode="two_way")
    await engine.pause_by_user()
    engine._state.pending_changes = 4
    (env.local / "notes.md").write_text("edited while paused")
    save_while_paused(engine)

    assert await engine.resume_intervals() == "Intervals resumed successfully."
    assert not engine._state.auto_paused
    assert FakeTimer.armed == []  # the two-way run picks it up, not a push


async def test_local_edits_while_paused_that_now_differ_block_the_resume(env):
    """Resuming would let the next pull overwrite the local edit: refused, still paused."""
    engine = env.engine()
    await engine.pause_by_user()
    (env.local / "notes.md").write_text("edited while paused")
    save_while_paused(engine)

    with pytest.raises(IntervalsNotResumableError, match="changed locally while syncing was paused"):
        await engine.resume_intervals()

    assert engine._state.auto_paused
    assert (env.local / "notes.md").read_text() == "edited while paused"
    assert not (env.remote / "notes.md").exists()
    assert FakeTimer.armed == []


async def test_a_failed_comparison_blocks_the_resume(env):
    """Unable to tell whether the local edits differ: stay paused rather than risk them."""
    engine = env.engine(remote_dir="nosuchremote:somewhere")
    await engine.pause_by_user()
    save_while_paused(engine)

    with pytest.raises(IntervalsNotResumableError, match="comparing the folders failed"):
        await engine.resume_intervals()
    assert engine._state.user_paused


async def test_edits_with_no_difference_resume_and_a_save_meanwhile_is_pushed(env, monkeypatch):
    """The folders match, so resuming is safe; a save during the comparison is pushed after."""
    engine = env.engine()
    await engine.pause_by_user()
    save_while_paused(engine)
    compare = engine._rclone.check_diff

    async def save_during_compare(*args, **kwargs):
        result = await compare(*args, **kwargs)
        save_while_paused(engine, "later.md")
        return result
    monkeypatch.setattr(engine._rclone, "check_diff", save_during_compare)

    assert await engine.resume_intervals() == "Intervals resumed successfully."

    assert not engine._state.auto_paused
    assert engine._paused_edits == 0
    assert await env.stored() == (None, False)
    assert len(FakeTimer.armed) == 1 and FakeTimer.armed[0].delay == 5


async def test_edits_with_no_difference_resume_without_a_push(env):
    engine = env.engine()
    await engine.pause_by_user()
    save_while_paused(engine)

    await engine.resume_intervals()

    assert not engine._state.auto_paused
    assert FakeTimer.armed == []
