"""RcloneService.remove_partials against the real rclone: only rclone's own leftovers go."""

from __future__ import annotations

import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.services.rclone import PARTIAL_NAME, TRASH_DIR, RcloneService

if shutil.which("rclone") is None and os.environ.get("OMNISYNC_REQUIRE_RCLONE") == "1":
    raise RuntimeError("OMNISYNC_REQUIRE_RCLONE=1 but rclone is not on PATH")
pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")

OLD = time.time() - 7200


def _write(root: Path, rel: str, mtime: float = OLD) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rel)
    os.utime(path, (mtime, mtime))


@pytest.fixture
def rclone(tmp_path: Path) -> RcloneService:
    conf = tmp_path / "rclone.conf"
    conf.write_text("[testremote]\ntype = local\n")
    return RcloneService(rclone_config_path=str(conf))


@pytest.mark.parametrize("via_remote", [False, True], ids=["local-path", "remote"])
async def test_only_old_rclone_partials_outside_the_trash_are_removed(tmp_path, rclone, via_remote) -> None:
    root = tmp_path / "folder"
    gone = ["a.txt.0123abcd.partial", "sub/deep/b.bin.89abcdef.partial", "with space.txt.00000000.partial"]
    kept = [
        "notes.partial", "c.0123ABCD.partial", "d.0123abc.partial", "e.0123abcd.partial.txt",
        "f.0123abcd.partial/inside.txt", f"{TRASH_DIR}/2026/x.0123abcd.partial", "user.txt",
    ]
    for rel in gone + kept:
        _write(root, rel)
    _write(root, "fresh.0123abcd.partial", mtime=time.time() + 3600)  # newer than the run: in use
    kept.append("fresh.0123abcd.partial")

    target = f"testremote:{root}" if via_remote else str(root)
    removed = await rclone.remove_partials(target, older_than=datetime.now(timezone.utc))

    assert removed == len(gone)
    left = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
    assert left == sorted(kept)


async def test_nothing_to_remove_and_a_missing_folder(tmp_path, rclone) -> None:
    root = tmp_path / "folder"
    _write(root, "notes.partial")
    assert await rclone.remove_partials(str(root), older_than=datetime.now(timezone.utc)) == 0
    assert await rclone.remove_partials(str(tmp_path / "missing"), older_than=datetime.now(timezone.utc)) == 0
    assert (root / "notes.partial").exists()


def test_the_name_pattern_is_the_filter_pattern() -> None:
    from backend.services.rclone import PARTIAL_FILTER

    assert PARTIAL_FILTER == r"- {{.*\.[0-9a-f]{8}\.partial}}"
    assert PARTIAL_NAME.fullmatch("x.0123abcd.partial")
    assert not PARTIAL_NAME.fullmatch("x.0123abcd.partial.bak")
    assert not PARTIAL_NAME.fullmatch("notes.partial")
