"""Unit tests: rclone stats parsing, sync windows, bandwidth limits and the user's pause."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from backend.api.schemas import ProfileCreateRequest, SyncState, SyncWindow, check_bwlimit
from backend.models.profile_config import ProfileConfig
from backend.models.sync_state import SyncStateManager
from backend.services.rclone import (
    MAX_PROGRESS_FILES,
    BisyncRecorder,
    ChangeRecorder,
    classify_failure,
    parse_stats,
    readable_stderr,
)
from backend.services.sync_engine import SyncEngine
from backend.services.sync_window import next_window_start, window_open


def stats_line(**stats) -> str:
    """An rclone 1.75.1 --use-json-log stats line (the msg lists the files in flight)."""
    return json.dumps({
        "time": "2026-10-02T12:36:52Z", "level": "notice",
        "msg": "\nTransferred: 20 MiB / 85 MiB, 24%\nTransferring:\n * must run --resync.bin: 23%\n"
               " * too many deletes.txt: 1%\n",
        "stats": stats, "source": "accounting/stats.go:549",
    })


STATS = {
    "bytes": 21188608, "totalBytes": 90000000, "speed": 21154788.37, "eta": 3, "transfers": 1,
    "totalTransfers": 3, "checks": 2, "totalChecks": 4,
    "transferring": [
        {"name": f"f{i}.bin", "size": 30000000, "bytes": 6942720, "percentage": 23, "speed": 1.0}
        for i in range(8)
    ],
}


class TestStats:
    def test_parse_stats(self) -> None:
        progress = parse_stats(STATS)
        assert progress is not None
        assert (progress.bytes, progress.total_bytes, progress.eta_seconds) == (21188608, 90000000, 3)
        assert (progress.files_done, progress.files_total, progress.checks, progress.total_checks) == (1, 3, 2, 4)
        assert len(progress.current) == MAX_PROGRESS_FILES and progress.current[0].name == "f0.bin"
        assert progress.current[0].percentage == 23 and progress.current[0].size == 30000000

    def test_unknown_eta_and_junk(self) -> None:
        progress = parse_stats({"eta": None, "speed": "fast", "bytes": -1, "transferring": [1, {"name": 2}]})
        assert progress is not None
        assert progress.eta_seconds is None and progress.speed == 0 and progress.bytes == 0 and not progress.current
        assert parse_stats("nope") is None

    def test_recorders_keep_only_the_latest_stats_and_never_parse_their_text(self) -> None:
        for recorder in (ChangeRecorder(), BisyncRecorder()):
            recorder.feed(stats_line(**{**STATS, "bytes": 1}))
            recorder.feed(stats_line(**STATS))
            assert recorder.progress is not None and recorder.progress.bytes == 21188608
            assert recorder.total == 0 and not recorder.errors and not recorder.other_lines
            assert recorder.failure_text() == ""
        bisync = BisyncRecorder()
        bisync.feed(stats_line(**STATS))
        assert not bisync.resync_needed and not bisync.too_many_deletes

    def test_failure_text_leaves_the_stats_out(self) -> None:
        stderr = "\n".join([stats_line(**STATS), json.dumps({"level": "error", "msg": "boom", "object": "x"})])
        assert readable_stderr(stderr) == "x: boom"
        assert "Transferred" not in str(classify_failure(1, stderr))

    def test_status_shows_progress_only_while_syncing(self) -> None:
        state = SyncStateManager()
        recorder = ChangeRecorder()
        recorder.feed(stats_line(**STATS))
        state.set_syncing(SyncState.PUSHING, 1)
        state.recorder = recorder
        progress = state.to_status_response().progress
        assert progress is not None and progress.files_total == 3 and len(progress.current_files) == 5
        state.set_idle()
        assert state.to_status_response().progress is None and state.recorder is None


def at(day: int, hhmm: str) -> datetime:
    """A time on a weekday (2026-10-05 is a Monday)."""
    hours, minutes = map(int, hhmm.split(":"))
    return datetime(2026, 10, 5 + day, hours, minutes, tzinfo=timezone.utc)


class TestSyncWindow:
    def test_same_day_window(self) -> None:
        window = {"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"}
        assert window_open(window, at(0, "09:00")) and window_open(window, at(4, "16:59"))
        assert not window_open(window, at(0, "17:00")) and not window_open(window, at(5, "12:00"))
        assert next_window_start(window, at(4, "18:00")) == at(7, "09:00")  # next Monday
        assert next_window_start(window, at(0, "08:00")) == at(0, "09:00")
        assert next_window_start(window, at(0, "10:00")) is None  # open

    def test_overnight_window_belongs_to_the_day_it_starts(self) -> None:
        window = {"days": [4], "start": "22:00", "end": "06:00"}  # Friday night
        assert window_open(window, at(4, "23:30")) and window_open(window, at(5, "05:59"))
        assert not window_open(window, at(5, "06:00")) and not window_open(window, at(3, "23:30"))
        assert not window_open(window, at(4, "05:00"))  # Thursday's night is not in the window

    def test_no_window_is_always_open(self) -> None:
        assert window_open(None) and next_window_start(None) is None
        assert window_open({"start": "bad"})  # unreadable: never blocks syncing

    @pytest.mark.parametrize("bad", [
        {"start": "9:00", "end": "17:00"}, {"start": "09:00", "end": "09:00"},
        {"days": [7], "start": "09:00", "end": "10:00"}, {"days": [], "start": "09:00", "end": "10:00"},
    ])
    def test_invalid_windows(self, bad: dict) -> None:
        with pytest.raises(ValidationError):
            SyncWindow(**bad)

    def test_days_are_normalised(self) -> None:
        assert SyncWindow(days=[6, 0, 6], start="22:00", end="06:00").days == [0, 6]


class TestBwlimit:
    @pytest.mark.parametrize("value", [
        "10M", "off", "512k", "10M:1M", "1.5M", "10Mi", "10MiB", "0", "10B",
        "08:00,512k 19:00,10M 23:00,off", "Mon-08:00,512k Sun-20:00,off", "08:00,512k:1M", "Monday-08:00,1M",
    ])
    def test_rclone_accepts(self, value: str) -> None:
        assert check_bwlimit(value) == value

    @pytest.mark.parametrize("value", ["10x", "25:00,1M", "8:00,1M", "-1", "10kb", "10M 20M", "--config=/x"])
    def test_rclone_refuses(self, value: str) -> None:
        with pytest.raises(ValueError):
            check_bwlimit(value)

    def test_blank_is_none_and_spaces_are_normalised(self) -> None:
        assert check_bwlimit("  ") is None and check_bwlimit(" 08:00,1M   09:00,off ") == "08:00,1M 09:00,off"

    def test_field_and_flag_conflict(self) -> None:
        base = {"name": "x", "local_dir": "/tmp/x", "remote_dir": "r:x", "sync_mode": "mirror"}
        with pytest.raises(ValidationError, match="twice"):
            ProfileCreateRequest(**base, bwlimit="1M", rclone_args=["--bwlimit=2M"])
        assert ProfileCreateRequest(**base, rclone_args=["--bwlimit", "2M"]).bwlimit is None


def engine(**changes) -> SyncEngine:
    config = ProfileConfig(profile_id=1, slug="t", name="T", local_dir="/nonexistent", remote_dir="r:x", **changes)
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return SyncEngine(config, AsyncMock(), factory)


class TestEngine:
    def test_bwlimit_goes_after_the_profile_flags(self) -> None:
        assert engine(bwlimit="1M", rclone_args=["--transfers", "2"])._transfer_args == [
            "--transfers", "2", "--bwlimit", "1M"]
        assert engine(rclone_args=["--bwlimit=2M"])._transfer_args == ["--bwlimit=2M"]

    async def test_user_pause_cancels_an_armed_push_and_survives_the_engine_resume(self) -> None:
        e = engine()
        timer = MagicMock()
        e._debounce_timer = timer
        assert await e.pause_by_user()
        assert not await e.pause_by_user()
        timer.cancel.assert_called_once()
        assert e._paused_edits == 1 and e._debounce_timer is None
        scheduler = MagicMock()
        e._scheduler = scheduler
        e._pause("3 unresolved differences")
        e._resume("bulk sync succeeded")  # the engine's own resume ...
        assert e._state.auto_paused and e._state.user_paused  # ... leaves the user's pause
        scheduler.get_job.return_value.resume.assert_not_called()
        e._state.pending_changes = 0
        e._paused_edits = 0
        await e.resume_intervals()
        assert not e._state.auto_paused
        scheduler.get_job.return_value.resume.assert_called()

    async def test_window_deferral_is_not_a_paused_edit(self) -> None:
        e = engine(sync_window={"days": [0], "start": "00:00", "end": "00:01"})
        e.window_open = lambda: False  # type: ignore[method-assign]
        e.next_window_start = lambda: None  # type: ignore[method-assign]
        assert await e._auto_sync() is None
        assert e._deferred == {"push"} and e._paused_edits == 0 and e._state.waiting_for_window
