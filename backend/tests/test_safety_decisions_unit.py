"""The small decisions the data-safety paths rest on, tested one by one.

A mutation run (see the commit that added this file) found these
decisions changeable without any test noticing: whether a destination
is still as the diff saw it, whether a conflict's file changed since it
was recorded, how conflict records follow a diff, which keep-both files
are stale, and which trash folders the retention may delete.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.schemas import ChangeCategory, ConflictResolution, DiffResponse, FileAction, FileDiff
from backend.db.models import Base, Conflict
from backend.models.profile_config import ProfileConfig
from backend.services.rclone import TRASH_DIR
from backend.services.sync_engine import TRASH_STAMP_FORMAT, SyncEngine, expired_trash_folders

T = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
T_RCLONE = "2026-05-01T12:00:00.000000000Z"
LATER_RCLONE = "2026-05-01T12:00:01.000000000Z"


@pytest.fixture
async def factory():
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(db, expire_on_commit=False)
    await db.dispose()


def make_engine(tmp_path: Path, factory=None, rclone=None) -> SyncEngine:
    local = tmp_path / "local"
    local.mkdir(exist_ok=True)
    config = ProfileConfig(profile_id=1, slug="docs", name="Docs", local_dir=str(local), remote_dir="gdrive:Docs")
    return SyncEngine(config, rclone or AsyncMock(), factory)


# --- Is the destination still as the diff saw it? ---


@pytest.mark.parametrize(("entry", "expected", "size", "mod", "as_diffed"), [
    (None, False, None, None, True),                                   # absent, as expected
    ({"Size": 1, "ModTime": T_RCLONE}, False, None, None, False),      # appeared since
    (None, True, 1, T, False),                                         # gone since
    ({"Size": 1, "ModTime": T_RCLONE}, True, 1, T, True),
    ({"Size": 2, "ModTime": T_RCLONE}, True, 1, T, False),             # size changed
    ({"Size": 1, "ModTime": LATER_RCLONE}, True, 1, T, False),         # time changed
    ({"Size": 1}, True, 1, T, False),                                  # time unknown now
    ({"Size": 1, "ModTime": "yesterday"}, True, 1, T, False),          # time unreadable
    ({"Size": 9, "ModTime": LATER_RCLONE}, True, None, None, True),    # nothing recorded to compare
    ({"Size": 1, "ModTime": LATER_RCLONE}, True, 1, None, True),
])
def test_as_diffed(entry, expected, size, mod, as_diffed):
    assert SyncEngine._as_diffed(entry, expected, size, mod) is as_diffed


# --- Did a conflict's file change since it was recorded? ---


@pytest.mark.parametrize(("entry", "seen", "unchanged"), [
    (None, T, True),                                      # gone: replaces nothing
    ({"ModTime": T_RCLONE}, None, True),                  # nothing recorded to compare
    ({"ModTime": T_RCLONE}, T, True),
    ({"ModTime": T_RCLONE}, T.replace(tzinfo=None), True),  # SQLite returns naive UTC
    ({"ModTime": LATER_RCLONE}, T, False),
    ({}, T, False),                                       # time unknown now
    ({"ModTime": "garbage"}, T, False),                   # time unreadable
])
def test_unchanged_since(entry, seen, unchanged):
    assert SyncEngine._unchanged_since(entry, seen) is unchanged


# --- Conflict records follow the diff ---


def both(path: str, local: datetime, remote: datetime) -> FileDiff:
    return FileDiff(path=path, category=ChangeCategory.MODIFIED_BOTH, local_mod_time=local,
                    remote_mod_time=remote, is_conflict=True)


async def rows(factory) -> list[Conflict]:
    async with factory() as session:
        return list((await session.execute(select(Conflict).order_by(Conflict.id))).scalars().all())


async def test_conflict_records_follow_the_diff(tmp_path, factory):
    engine = make_engine(tmp_path, factory)
    engine._emit_notification = AsyncMock()  # type: ignore[method-assign]
    async with factory() as session:  # two open records of one file (an older bug's leftover)
        session.add_all([Conflict(profile_id=1, file_path="a.txt", resolved=False) for _ in range(2)])
        await session.commit()

    await engine._record_conflicts([both("a.txt", T, T), both("b.txt", T, T)])

    a1, a2, b = await rows(factory)
    assert (a1.resolved, a2.resolved, a2.resolution) == (False, True, ConflictResolution.DISMISS.value)
    assert a1.local_modified is not None and a1.local_modified.replace(tzinfo=timezone.utc) == T
    assert (b.file_path, b.resolved) == ("b.txt", False)
    engine._emit_notification.assert_awaited_once_with("conflict_detected", count=1)

    # A repeated diff refreshes the times and notifies no one; a file that
    # no longer differs is closed with no resolution.
    later = T + timedelta(hours=1)
    await engine._record_conflicts([both("a.txt", later, later)])
    a1, a2, b = await rows(factory)
    assert a1.local_modified.replace(tzinfo=timezone.utc) == later and not a1.resolved
    assert (b.resolved, b.resolution) == (True, None)
    assert engine._emit_notification.await_count == 1


# --- Keep both needs both files ---


async def test_keep_both_is_stale_once_either_file_is_gone(tmp_path):
    rclone = AsyncMock()
    rclone.lsjson_paths = AsyncMock(side_effect=lambda root, paths: {
        p: {"Size": 1, "ModTime": T_RCLONE} for p in paths
        if not (p == "gone-local.txt" and root.endswith("local")) and not (p == "gone-remote.txt" and root == "gdrive:Docs")
    })
    engine = make_engine(tmp_path, rclone=rclone)
    files = ["both.txt", "gone-local.txt", "gone-remote.txt", "also-both.txt"]
    engine._state.cache_diff(DiffResponse(files=[both(p, T, T) for p in files]))

    stale = await engine._stale_paths(files, FileAction.KEEP_BOTH)

    assert stale == {
        "gone-local.txt": "The local file no longer exists. Run the diff again.",
        "gone-remote.txt": "The remote file no longer exists. Run the diff again.",
    }


# --- Which trash folders retention may delete ---


def stamp(at: datetime) -> str:
    return at.strftime(TRASH_STAMP_FORMAT)


def test_expired_trash_folders_boundaries():
    cutoff = T
    names = [
        stamp(cutoff),                       # exactly at the cutoff: kept
        "2026-13-45T00-00-00Z",              # the format, but no date: skipped, not the end
        stamp(cutoff - timedelta(seconds=1)),
        "pre-restore", "2026-01-01",         # not engine stamps
    ]
    assert expired_trash_folders(names, cutoff) == [stamp(cutoff - timedelta(seconds=1))]


async def test_local_trash_pruning(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.services.sync_engine.trash.TRASH_RETENTION_DAYS", 1)
    engine = make_engine(tmp_path)
    trash = tmp_path / "local" / TRASH_DIR
    assert await engine.prune_trash("local", now=T) == []  # no trash folder yet

    old, recent = stamp(T - timedelta(days=2)), stamp(T - timedelta(hours=1))
    for name in (old, recent):
        (trash / name).mkdir(parents=True)
        (trash / name / "f.txt").write_text("x")

    assert await engine.prune_trash("local", now=T) == [old]
    assert sorted(os.listdir(trash)) == [recent]


async def test_a_symlinked_local_trash_is_never_pruned(tmp_path, monkeypatch):
    """The trash folder itself a link elsewhere: nothing there is deleted."""
    monkeypatch.setattr("backend.services.sync_engine.trash.TRASH_RETENTION_DAYS", 1)
    elsewhere = tmp_path / "elsewhere"
    old = stamp(T - timedelta(days=30))
    (elsewhere / old).mkdir(parents=True)
    (elsewhere / old / "precious.txt").write_text("never delete")
    engine = make_engine(tmp_path)
    (tmp_path / "local" / TRASH_DIR).symlink_to(elsewhere, target_is_directory=True)

    assert await engine.prune_trash("local", now=T) == []
    assert (elsewhere / old / "precious.txt").read_text() == "never delete"
