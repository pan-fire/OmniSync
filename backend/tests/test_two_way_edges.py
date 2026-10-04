"""Two-way sync refusals and failures, with rclone mocked at the bisync boundary.

The happy paths and real bisync runs are in test_two_way_sync_integration.py;
these are the cases where a two-way run must stop, pause or report instead
of changing files: an auth error, bisync's own delete guard, a remote that
cannot be listed, unreadable records, and the preview of each of them.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from backend.db.models import SyncJob, SyncProfile
from backend.exceptions import RcloneAuthError, RcloneError
from backend.models.profile_config import ProfileConfig
from backend.services.rclone import SENTINEL_FILE, BisyncRecorder
from backend.services.sync_engine import SyncEngine, bisync_workdir
from backend.tests.test_two_way_unit import line


@pytest.fixture
def bisync_dir(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "bisync"
    monkeypatch.setattr("backend.services.sync_engine.two_way.BISYNC_DIR", str(path))
    return path


async def make_engine(tmp_path: Path, factory, rclone: AsyncMock, max_retries: int = 1) -> SyncEngine:
    local = tmp_path / "local"
    local.mkdir(exist_ok=True)
    (local / SENTINEL_FILE).write_text("m")
    (local / "a.txt").write_text("a")
    now = datetime.now(timezone.utc)
    async with factory() as session:
        session.add(SyncProfile(id=1, slug="docs", name="Docs", local_dir=str(local),
                                remote_dir="gdrive:Docs", created_at=now, updated_at=now))
        await session.commit()
    config = ProfileConfig(profile_id=1, slug="docs", name="Docs", local_dir=str(local),
                           remote_dir="gdrive:Docs", max_retries=max_retries, sync_mode="two_way")
    engine = SyncEngine(config, rclone, factory)
    engine._emit_notification = AsyncMock()  # type: ignore[method-assign]
    return engine


def synced_before(engine: SyncEngine) -> Path:
    """The records of an earlier successful run of this pair: state, listings, filters."""
    workdir = Path(bisync_workdir(1))
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "omnisync-state.json").write_text(json.dumps({"pair": engine._pair}))
    (workdir / "x.path1.lst").write_text("# bisync listing\n")
    (workdir / "x.path2.lst").write_text("# bisync listing\n")
    (workdir / "filters.txt").write_text(engine._bisync_filters([]))
    return workdir


def marked_remote() -> AsyncMock:
    rclone = AsyncMock()
    rclone.list_top_level = AsyncMock(return_value=[SENTINEL_FILE, "a.txt"])
    return rclone


async def only_job(factory) -> SyncJob:
    async with factory() as session:
        (job,) = (await session.execute(select(SyncJob))).scalars().all()
        return job


def events(engine: SyncEngine) -> list[str]:
    return [c.args[0] for c in engine._emit_notification.await_args_list]  # type: ignore[attr-defined]


async def test_an_auth_error_fails_the_run_once_and_reports_auth(tmp_path, bisync_dir, test_db_factory):
    """Retrying with an expired token only repeats the failure: one attempt, auth_error, no pause."""
    rclone = marked_remote()
    rclone.bisync = AsyncMock(side_effect=RcloneAuthError("token expired"))
    engine = await make_engine(tmp_path, test_db_factory, rclone, max_retries=3)
    synced_before(engine)

    await engine.two_way_sync()

    assert rclone.bisync.await_count == 1
    job = await only_job(test_db_factory)
    assert (job.direction, job.status) == ("two_way", "failed")
    assert events(engine) == ["auth_error"]
    assert not engine._state.intervals_paused


async def test_bisyncs_own_delete_guard_pauses_the_profile(tmp_path, bisync_dir, test_db_factory):
    """bisync refusing "too many deletes" means a side was emptied: pause, never retry."""
    rclone = marked_remote()

    async def refusing(*args, recorder: BisyncRecorder, **kwargs):
        recorder.feed(line("too many deletes - 100% Path2 deletes", "error"))
        raise RcloneError("rclone failed (exit 2)")

    rclone.bisync = AsyncMock(side_effect=refusing)
    engine = await make_engine(tmp_path, test_db_factory, rclone, max_retries=3)
    synced_before(engine)

    await engine.two_way_sync()

    assert rclone.bisync.await_count == 1
    assert (await only_job(test_db_factory)).status == "failed"
    assert engine._state.intervals_paused
    assert events(engine) == ["sync_failed"]


async def test_a_remote_that_cannot_be_listed_refuses_before_bisync(tmp_path, bisync_dir, test_db_factory):
    """Without the remote's listing the marker check cannot run, so bisync must not either."""
    rclone = AsyncMock()
    rclone.list_top_level = AsyncMock(side_effect=RcloneError("directory not found"))
    rclone.bisync = AsyncMock()
    engine = await make_engine(tmp_path, test_db_factory, rclone)
    synced_before(engine)

    await engine.two_way_sync()

    rclone.bisync.assert_not_awaited()
    assert (await only_job(test_db_factory)).status == "failed"
    assert "Could not list gdrive:Docs" in (engine._state.last_error or "")


async def test_a_first_run_on_unmarked_folders_writes_the_markers(tmp_path, bisync_dir, test_db_factory):
    """--check-access needs the marker on both sides; a first run on two unmarked folders places it."""
    rclone = AsyncMock()
    rclone.list_top_level = AsyncMock(return_value=[])
    rclone.bisync = AsyncMock()
    engine = await make_engine(tmp_path, test_db_factory, rclone)
    (tmp_path / "local" / SENTINEL_FILE).unlink()
    (tmp_path / "local" / "a.txt").unlink()
    engine._write_sentinels = AsyncMock()  # type: ignore[method-assign]

    await engine.two_way_sync()

    engine._write_sentinels.assert_awaited_once()
    assert rclone.bisync.await_args.kwargs["resync"] is True
    assert (await only_job(test_db_factory)).status == "completed"


async def test_an_automatic_retry_stops_once_syncing_is_paused(tmp_path, bisync_dir, test_db_factory, monkeypatch):
    """A pause between attempts (e.g. by the user) ends an automatic run instead of retrying on."""
    monkeypatch.setattr("backend.services.sync_engine.two_way.asyncio.sleep", AsyncMock())
    rclone = marked_remote()
    engine = await make_engine(tmp_path, test_db_factory, rclone, max_retries=3)

    async def failing(*args, **kwargs):
        engine._state.user_paused = True
        raise RcloneError("connection reset")

    rclone.bisync = AsyncMock(side_effect=failing)
    synced_before(engine)

    await engine.two_way_sync(automatic=True)

    assert rclone.bisync.await_count == 1
    assert (await only_job(test_db_factory)).status == "failed"


async def test_an_unreadable_listing_does_not_skip_the_delete_check(tmp_path, bisync_dir, test_db_factory):
    """If the file count of the last listing is unknown, the dry run must count the deletions."""
    rclone = marked_remote()
    rclone.bisync = AsyncMock()
    engine = await make_engine(tmp_path, test_db_factory, rclone)
    workdir = synced_before(engine)
    (workdir / "y.path1.lst").mkdir()  # matches the pattern, cannot be read as a file

    assert engine._listed_files() is None
    await engine.two_way_sync()

    dry, real = rclone.bisync.await_args_list
    assert dry.kwargs.get("dry_run") is True and not real.kwargs.get("dry_run")


def test_half_a_set_of_failed_listings_is_not_restored(tmp_path, bisync_dir):
    """Restoring one side's listing without the other would pair records of different runs."""
    engine = SyncEngine(ProfileConfig(profile_id=1, slug="docs", name="Docs", local_dir=str(tmp_path),
                                      remote_dir="gdrive:Docs", sync_mode="two_way"), AsyncMock(), None)  # type: ignore[arg-type]
    workdir = Path(bisync_workdir(1))
    workdir.mkdir(parents=True)
    (workdir / "x.path1.lst-err").write_text("1")

    engine._keep_listings_after_stop()

    assert sorted(p.name for p in workdir.iterdir()) == ["x.path1.lst-err"]


async def test_a_missing_filters_file_blocks_instead_of_guessing(tmp_path, bisync_dir, test_db_factory):
    """Without the last run's filters OmniSync cannot tell whether they changed: resync required."""
    engine = await make_engine(tmp_path, test_db_factory, marked_remote())
    workdir = synced_before(engine)
    (workdir / "filters.txt").unlink()

    plan, reason = engine._two_way_plan(engine._bisync_filters([]))

    assert plan == "blocked" and "filters file" in (reason or "")


async def test_a_stale_lock_that_cannot_be_removed_is_logged_not_fatal(tmp_path, bisync_dir, test_db_factory, caplog):
    rclone = marked_remote()
    rclone.bisync = AsyncMock()
    engine = await make_engine(tmp_path, test_db_factory, rclone)
    workdir = synced_before(engine)
    (workdir / "x.lck").mkdir()  # os.remove fails on a directory

    await engine.two_way_sync()

    assert "Could not remove" in caplog.text
    assert (await only_job(test_db_factory)).status == "completed"


class TestPreview:
    """The preview (a dry run) reports why a run would not go ahead, and changes nothing."""

    async def test_a_blocked_pair_previews_as_resync_required(self, tmp_path, bisync_dir, test_db_factory):
        engine = await make_engine(tmp_path, test_db_factory, marked_remote())
        workdir = synced_before(engine)
        for p in workdir.glob("*.lst"):
            p.unlink()

        preview = await engine._two_way_preview()

        assert preview.resync_required and preview.resync and preview.error

    async def test_a_missing_local_folder(self, tmp_path, bisync_dir, test_db_factory):
        rclone = marked_remote()
        engine = await make_engine(tmp_path, test_db_factory, rclone)
        engine._profile = replace(engine._profile, local_dir=str(tmp_path / "unmounted"))

        preview = await engine._two_way_preview()

        assert "missing or not mounted" in (preview.error or "")
        rclone.bisync.assert_not_awaited()

    async def test_a_remote_that_cannot_be_listed(self, tmp_path, bisync_dir, test_db_factory):
        rclone = AsyncMock()
        rclone.list_top_level = AsyncMock(side_effect=RcloneError("timeout"))
        engine = await make_engine(tmp_path, test_db_factory, rclone)

        preview = await engine._two_way_preview()

        assert (preview.error or "").startswith("Could not list gdrive:Docs")

    async def test_a_marker_on_one_side_only(self, tmp_path, bisync_dir, test_db_factory):
        rclone = AsyncMock()
        rclone.list_top_level = AsyncMock(return_value=["a.txt"])
        engine = await make_engine(tmp_path, test_db_factory, rclone)

        preview = await engine._two_way_preview()

        assert "missing from the remote folder" in (preview.error or "")
        rclone.bisync.assert_not_awaited()

    async def test_a_failing_dry_run(self, tmp_path, bisync_dir, test_db_factory):
        rclone = marked_remote()
        rclone.bisync = AsyncMock(side_effect=RcloneError("exit 7"))
        engine = await make_engine(tmp_path, test_db_factory, rclone)
        synced_before(engine)

        preview = await engine._two_way_preview()

        assert (preview.error or "").startswith("The two-way preview failed")
        assert rclone.bisync.await_args.kwargs["dry_run"] is True
