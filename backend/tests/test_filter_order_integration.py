"""OmniSync's own excludes beat the profile's filters, against the real rclone binary.

A push or pull leaves out manually flagged files, unresolved conflicts and
(a pull) local symbolic links. rclone applies rules by kind, not in the
order given: --include flags first, then --exclude flags, then --filter
rules, then --filter-from files. The excludes used to travel in a
--filter-from file, so an include-style profile filter (``+ /Docs/**``,
``- **``) or an --include flag matched a flagged file first and the sync
overwrote it. Each case below has a file inside the included folder that
differs on both sides and must come through untouched, next to files that
show the profile's filter still selects what it selected before.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.db.models import ManualFlag
from backend.tests.real_rclone import Env, make_env, needs_rclone, write

pytestmark = needs_rclone

INCLUDE_RULES = {"rclone_filter": ["+ /Docs/**", "- **"]}
INCLUDE_FLAG = {"rclone_args": ["--include", "/Docs/**"]}
# The profile's own "!" would have cleared every rule before it.
CLEARED = {"rclone_filter": ["- *.tmp", "!", "+ /Docs/**", "- **"]}
FILTER_FLAG = {"rclone_args": ["--filter", "+ /Docs/**", "--filter=- **"]}
SELECTIONS = [INCLUDE_RULES, INCLUDE_FLAG, CLEARED, FILTER_FLAG]
IDS = ["include-rules", "include-flag", "clear-rule", "filter-flags"]


@pytest.fixture
async def env(tmp_path: Path, monkeypatch):
    env, db = await make_env(tmp_path, monkeypatch)
    yield env
    for engine in env.engines:
        await engine.stop()
    await db.dispose()


async def flag(env: Env, *paths: str) -> None:
    async with env.factory() as session:
        for path in paths:
            session.add(ManualFlag(profile_id=1, file_path=path, created_at=datetime.now(timezone.utc)))
        await session.commit()


def differing(env: Env, rel: str) -> None:
    """``rel`` on both sides with different content (sizes too) and times."""
    write(env.local, rel, "the local version", mtime=time.time() - 60)
    write(env.remote, rel, "remote", mtime=time.time() - 7200)


def selection(env: Env, source: Path) -> None:
    """A file the profile's filter selects and one it leaves out, on the source side."""
    write(source, "Docs/new.txt", "new")
    write(source, "outside.txt", "not selected")


def selected_as_before(env: Env, source: Path) -> None:
    dest = env.remote if source == env.local else env.local
    assert (dest / "Docs" / "new.txt").read_text() == "new"
    assert not (dest / "outside.txt").exists()


@pytest.mark.parametrize("profile", SELECTIONS, ids=IDS)
@pytest.mark.parametrize("direction", ["push", "pull"])
async def test_a_flagged_file_is_never_overwritten_whatever_the_profile_selects(
        env: Env, profile: dict, direction: str):
    differing(env, "Docs/flagged.txt")
    differing(env, "Docs/flagged with space.txt ")   # the filter file strips line ends
    await flag(env, "Docs/flagged.txt", "Docs/flagged with space.txt ")
    source = env.local if direction == "push" else env.remote
    selection(env, source)
    engine = env.engine(**profile)

    await env.completed(await (engine.push() if direction == "push" else engine.pull()))

    for name in ("flagged.txt", "flagged with space.txt "):
        assert (env.local / "Docs" / name).read_text() == "the local version"
        assert (env.remote / "Docs" / name).read_text() == "remote"
    selected_as_before(env, source)


@pytest.mark.parametrize("profile", [INCLUDE_RULES, INCLUDE_FLAG], ids=IDS[:2])
async def test_an_unresolved_conflict_is_never_overwritten(env: Env, profile: dict):
    """Changed on both sides since the diff (same time, different content): a conflict."""
    same = time.time() - 600
    write(env.local, "Docs/both.txt", "the local version", mtime=same)
    write(env.remote, "Docs/both.txt", "remote", mtime=same)
    engine = env.engine(**profile)
    diff = await engine.enhanced_diff()
    assert [(f.path, f.is_conflict) for f in diff.files] == [("Docs/both.txt", True)]

    await env.completed(await engine.push())
    await env.completed(await engine.pull())

    assert (env.local / "Docs" / "both.txt").read_text() == "the local version"
    assert (env.remote / "Docs" / "both.txt").read_text() == "remote"


async def test_a_pull_keeps_a_local_link_whatever_the_include_flags(env: Env, tmp_path: Path):
    write(tmp_path / "outside", "t.txt", "target")
    (env.local / "Docs").mkdir()
    os.symlink(tmp_path / "outside" / "t.txt", env.local / "Docs" / "clash.txt")
    write(env.remote, "Docs/clash.txt", "remote file")
    engine = env.engine(**INCLUDE_FLAG)

    await env.completed(await engine.pull())

    assert (env.local / "Docs" / "clash.txt").is_symlink()
    assert (tmp_path / "outside" / "t.txt").read_text() == "target"
    assert (env.remote / "Docs" / "clash.txt").read_text() == "remote file"


async def test_two_way_keeps_its_excludes_after_a_clear_rule_of_the_profile(env: Env):
    """bisync reads OmniSync's excludes and the profile's rules from one filters
    file; a "!" of the profile's would clear the excludes (and the trash's)."""
    write(env.local, "Docs/a.txt", "a")
    # Flagged before the first run, so the filters stay the same from then on
    # (a change of them makes the next run a flush, see two_way.py).
    await flag(env, "Docs/flagged.txt")
    engine = env.engine(sync_mode="two_way", **CLEARED)
    await env.completed(await engine.two_way_sync())
    differing(env, "Docs/flagged.txt")
    write(env.local, "Docs/new.txt", "new")

    await env.completed(await engine.two_way_sync())

    assert (env.local / "Docs" / "flagged.txt").read_text() == "the local version"
    assert (env.remote / "Docs" / "flagged.txt").read_text() == "remote"
    assert (env.remote / "Docs" / "new.txt").read_text() == "new"
