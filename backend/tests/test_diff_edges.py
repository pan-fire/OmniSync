"""The diff's classification when rclone's listing is incomplete or odd.

Which side a differing file counts as changed on decides what a selective
sync may overwrite, so the cases where a modification time is missing or
unreadable must fall back to something that never hides a change: a file
whose time is unknown on either side is a conflict, not a one-sided change.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.api.schemas import ChangeCategory
from backend.models.profile_config import ProfileConfig
from backend.services.sync_engine import SyncEngine

T1 = "2026-03-01T10:00:00.123456789Z"
T2 = "2026-03-01T12:00:00.5+01:00"


def engine_with(tmp_path: Path, factory, check: dict, local: list[dict], remote: list[dict]) -> SyncEngine:
    rclone = AsyncMock()
    rclone.check_diff = AsyncMock(return_value={"in_sync": False, **check})
    rclone.lsjson = AsyncMock(side_effect=[local, remote])
    config = ProfileConfig(profile_id=1, slug="docs", name="Docs", local_dir=str(tmp_path),
                           remote_dir="gdrive:Docs")
    return SyncEngine(config, rclone, factory)


def by_path(diff) -> dict[str, ChangeCategory]:
    return {f.path: f.category for f in diff.files}


async def test_unreadable_times_never_hide_a_change(tmp_path, test_db_factory):
    """Garbled ModTimes: one-sided files keep their category, a differing file becomes a conflict."""
    odd = [
        {"Path": "only-local.txt", "Size": 1, "ModTime": "yesterday"},
        {"Path": "both.txt", "Size": 1, "ModTime": None},
    ]
    remote = [
        {"Path": "only-remote.txt", "Size": 2, "ModTime": "not a time"},
        {"Path": "both.txt", "Size": 2, "ModTime": "2026-13-45T99:00:00Z"},
    ]
    engine = engine_with(tmp_path, test_db_factory,
                         {"local_only": ["only-local.txt"], "remote_only": ["only-remote.txt"], "differ": ["both.txt"]},
                         odd, remote)

    diff = await engine.enhanced_diff()

    assert diff.error is None
    assert by_path(diff) == {
        "only-local.txt": ChangeCategory.LOCAL_ONLY,
        "only-remote.txt": ChangeCategory.REMOTE_ONLY,
        "both.txt": ChangeCategory.MODIFIED_BOTH,
    }
    (both,) = [f for f in diff.files if f.path == "both.txt"]
    assert both.is_conflict and both.local_mod_time is None and both.remote_mod_time is None


@pytest.mark.parametrize("local_time, remote_time", [
    (T1, None),
    (None, T1),
    (T1, "not a time"),
    ("garbled", T2),
])
@pytest.mark.parametrize("last_sync", [None, datetime(2026, 1, 1, tzinfo=timezone.utc)])
async def test_a_time_on_one_side_only(tmp_path, test_db_factory, local_time, remote_time, last_sync):
    """With a time missing or unreadable on one side, nothing shows that side is unchanged:
    the file is a conflict to review, never a one-sided change a push or pull would apply."""
    def entry(t):
        return {"Path": "f.txt", "Size": 1, **({"ModTime": t} if t else {})}

    engine = engine_with(tmp_path, test_db_factory, {"local_only": [], "remote_only": [], "differ": ["f.txt"]},
                         [entry(local_time)], [entry(remote_time)])
    engine._state.last_sync = last_sync

    diff = await engine.enhanced_diff()
    assert by_path(diff) == {"f.txt": ChangeCategory.MODIFIED_BOTH}
    assert diff.files[0].is_conflict and diff.summary.modified_both == 1


async def test_directories_in_the_listing_are_not_files(tmp_path, test_db_factory):
    engine = engine_with(tmp_path, test_db_factory, {"local_only": ["d"], "remote_only": [], "differ": []},
                         [{"Path": "d", "IsDir": True, "ModTime": T1}], [])

    (only,) = (await engine.enhanced_diff()).files
    assert only.local_size is None and only.local_mod_time is None


@pytest.mark.parametrize("text, expected", [
    ("2026-03-01T10:00:00.123456789Z", datetime(2026, 3, 1, 10, 0, 0, 123456, timezone.utc)),
    ("2026-03-01T12:00:00.5+01:00", datetime(2026, 3, 1, 11, 0, 0, 500000, timezone.utc)),
    ("2026-03-01T12:00:00.25-02:00", datetime(2026, 3, 1, 14, 0, 0, 250000, timezone.utc)),
    ("2026-03-01T10:00:00", datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)),
])
def test_rclone_times_parse_to_utc(text, expected):
    """Nanoseconds are cut to microseconds and every offset is honoured (or UTC assumed)."""
    parsed = SyncEngine._parse_rclone_modtime(text)
    assert parsed == expected and parsed.tzinfo is not None
