"""Scale tests: about 5,000 files in 200 folders, plus large files, through the real rclone.

The other real-rclone tests use a handful of files; these run the same
preview, diff, push, two-way and per-file flows on a tree the size of a
real document folder. Each asserts correctness (both sides identical, the
job's counts, no rclone .partial files left) and budgets for the backend
process (see ``Budget``).

Marked ``slow``: the normal suite deselects them (backend/pytest.ini); CI
runs them in a step of their own (``pytest -m slow``).
"""

from __future__ import annotations

import json
import os
import random
import time
import tracemalloc
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from backend.api.schemas import ChangeCategory, FileAction, SelectiveSyncItem
from backend.db.models import Base, SyncJob
from backend.models.profile_config import ProfileConfig
from backend.services.rclone import SENTINEL_FILE, TRASH_DIR
from backend.services.sync_engine import SyncEngine
from backend.tests.real_rclone import Env, make_env, needs_rclone, partials, tree, write

pytestmark = [pytest.mark.slow, needs_rclone]

DIRS = 200          # 20 top-level folders with 10 subfolders each
FULL = 5000         # files, spread evenly over the subfolders
SMALL = 600         # the same flows on a smaller tree, for the growth check
LARGE = {           # name -> size; sparse, written in an instant
    "media/video.bin": 300 << 20,   # above rclone's 256 MiB multi-thread cutoff
    "media/disk.img": 64 << 20,
}

# On a developer machine (rclone 1.75.1) every flow on the full tree took at
# most 2.2 s of wall time when idle (22 s under a load average of 50),
# 0.8 s of backend CPU time and 10.6 MiB of traced Python allocations at
# peak, and at most 6.4 times the CPU time of the same flow on the small
# tree. CPU and memory get about five times that; wall time, which counts
# rclone and the disk and so varies most, only stops a hang; growth: see
# Budget.
TIME_BUDGET = 60.0          # s of wall time per flow (rclone and the disk included)
CPU_BUDGET = 4.0            # s of backend CPU time per flow
MEMORY_BUDGET_MIB = 50      # traced Python allocations at peak, per flow
GROWTH_BUDGET = 16.0        # CPU time on FULL / CPU time on SMALL, per flow
CPU_FLOOR = 0.1             # s: a smaller SMALL measurement counts as this much


class Budget:
    """Measures each flow: wall time, CPU time of this process, peak traced memory.

    Wall time includes rclone and the disk, which vary most between
    machines, so its budget is the loosest. CPU time counts only the backend
    process (rclone runs in child processes, which process_time leaves
    out). A flow that grows linearly takes at most FULL / SMALL (8.3) times
    as long on the full tree, one that grows with the square of the file
    count up to 70 times: the growth check catches that whatever the
    machine's speed, where an absolute budget with headroom for a slow
    runner would not.
    """

    def __init__(self) -> None:
        self.cpu: dict[str, float] = {}

    @contextmanager
    def flow(self, label: str):
        tracemalloc.start()
        start, cpu_start = time.monotonic(), time.process_time()
        try:
            yield
        finally:
            elapsed, cpu = time.monotonic() - start, time.process_time() - cpu_start
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        peak_mib = peak / (1 << 20)
        print(f"\n[scale] {label}: {elapsed:.1f}s, cpu {cpu:.2f}s, peak {peak_mib:.1f} MiB")
        self.cpu[label] = cpu
        assert elapsed < TIME_BUDGET, f"{label} took {elapsed:.1f}s (budget {TIME_BUDGET}s)"
        assert cpu < CPU_BUDGET, f"{label} used {cpu:.2f}s of backend CPU time (budget {CPU_BUDGET}s)"
        assert peak_mib < MEMORY_BUDGET_MIB, f"{label} peaked at {peak_mib:.1f} MiB (budget {MEMORY_BUDGET_MIB})"

    @staticmethod
    def check_growth(small: Budget, full: Budget) -> None:
        assert small.cpu.keys() == full.cpu.keys()
        for label, cpu in full.cpu.items():
            growth = cpu / max(small.cpu[label], CPU_FLOOR)
            print(f"[scale] {label}: {growth:.1f}x the CPU time for {FULL / SMALL:.1f}x the files")
            assert growth < GROWTH_BUDGET, (
                f"{label}: {growth:.1f}x the CPU time for {FULL / SMALL:.1f}x the files; "
                f"a step grows faster than linearly (budget {GROWTH_BUDGET}x)")


def small_paths(n: int) -> list[str]:
    per_dir = n // DIRS
    return [f"top{d // 10:02d}/sub{d % 10}/file{i:03d}.txt" for d in range(DIRS) for i in range(per_dir)]


def fill(root: Path, paths: list[str], seed: int, mtime: float) -> None:
    rnd = random.Random(seed)
    for rel in paths:
        write(root, rel, rnd.randbytes(rnd.randint(0, 2048)), mtime=mtime)


def make_large(root: Path, mtime: float, n: int) -> int:
    """Sparse files with real bytes at both ends, so a truncated copy shows; how many.

    Only in the full tree: the growth check compares backend CPU time, which
    a large file barely adds to, and copying them twice would only cost time.
    """
    if n != FULL:
        return 0
    for rel, size in LARGE.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(rel.encode())
            fh.truncate(size)
            fh.seek(size - 16)
            fh.write(b"end-of-" + rel[-9:].encode())
        os.utime(path, (mtime, mtime))
    return len(LARGE)


def assert_identical(env: Env, expected_files: int) -> None:
    local, remote = tree(env.local), tree(env.remote)
    assert len(local) == expected_files
    assert local == remote, sorted(set(local) ^ set(remote))[:10]
    assert partials(env.local, env.remote) == []


def trashed(root: Path) -> list[str]:
    return [k for k in tree(root, trash=True) if k.startswith(TRASH_DIR + os.sep)]


@pytest.fixture
async def at_scale(tmp_path: Path, monkeypatch):
    """Runs a flow on the small tree, then on the full one, then checks the growth."""
    envs: list[tuple[Env, AsyncEngine]] = []

    async def run(flow) -> None:
        budgets = []
        for n in (SMALL, FULL):
            env, db = await make_env(tmp_path / str(n), monkeypatch)
            envs.append((env, db))
            budget = Budget()
            await flow(env, n, budget)
            budgets.append(budget)
        Budget.check_growth(*budgets)

    yield run
    for env, db in envs:
        for engine in env.engines:
            await engine.stop()
        await db.dispose()


async def mirror_flow(env: Env, n: int, budget: Budget) -> None:
    """A first push of the whole tree, then a push of a day's edits (edits, deletions, new files)."""
    old = time.time() - 86400
    paths = small_paths(n)
    fill(env.local, paths, seed=1, mtime=old)
    total = n + make_large(env.local, old, n)
    engine = env.engine()

    with budget.flow("mirror: preview + diff of a new tree"):
        preview = await engine.preview_sync()
        diff = await engine.enhanced_diff()
    assert preview.error is None and diff.error is None
    assert (preview.push.creates, preview.push.deletes, preview.push.replaces) == (total, 0, 0)
    assert diff.summary.local_only == total and diff.summary.total == total

    with budget.flow("mirror: first push"):
        job = await env.completed(await engine.push())
    assert job.files_changed == total
    assert_identical(env, total)

    # A day's work: edits, deletions (under the limit of 50) and new files.
    edited, deleted = paths[::50], paths[1::125]
    new = [f"new/sub{i % 3}/n{i:02d}.txt" for i in range(60)]
    assert not set(edited) & set(deleted) and len(deleted) < 50
    rnd = random.Random(2)
    for rel in edited:
        write(env.local, rel, b"edited " + rnd.randbytes(64))
    for rel in deleted:
        (env.local / rel).unlink()
    fill(env.local, new, seed=3, mtime=time.time())
    changes = len(edited) + len(deleted) + len(new)

    with budget.flow("mirror: preview + diff of a day's edits"):
        preview = await engine.preview_sync()
        diff = await engine.enhanced_diff()
    assert (preview.push.creates, preview.push.replaces, preview.push.deletes) == (len(new), len(edited), len(deleted))
    assert (diff.summary.local_only, diff.summary.remote_only, diff.summary.total) == (len(new), len(deleted), changes)

    with budget.flow("mirror: push of a day's edits"):
        job = await env.completed(await engine.push())
    assert job.files_changed == changes
    assert_identical(env, total - len(deleted) + len(new))
    # Every replaced and deleted version is recoverable from the remote's trash.
    assert len(trashed(env.remote)) == len(edited) + len(deleted)


async def two_way_flow(env: Env, n: int, budget: Budget) -> None:
    """The first two-way run joins two halves of the tree; the next carries edits and deletions both ways."""
    old = time.time() - 86400
    paths = small_paths(n)
    local_half, remote_half = paths[::2], paths[1::2]
    fill(env.local, local_half, seed=4, mtime=old)
    fill(env.remote, remote_half, seed=5, mtime=old)
    total = n + make_large(env.local, old, n)
    engine = env.engine(sync_mode="two_way")

    with budget.flow("two-way: preview of the first run"):
        preview = await engine.preview_sync()
    assert preview.two_way is not None and preview.two_way.error is None and preview.two_way.resync
    assert preview.two_way.remote.creates == len(local_half) + total - n
    assert preview.two_way.local.creates == len(remote_half)

    with budget.flow("two-way: first run (resync)"):
        job = await env.completed(await engine.two_way_sync())
    assert job.direction == "resync"
    assert job.files_changed == total
    assert_identical(env, total)

    # Edits on both sides (different files) and deletions under the limit.
    local_edits, remote_edits = paths[::50], paths[10::50]
    local_dels, remote_dels = paths[20::200], paths[30::200]
    for rel in local_edits:
        write(env.local, rel, b"local edit")
    for rel in remote_edits:
        write(env.remote, rel, b"remote edit")
    for rel in local_dels:
        (env.local / rel).unlink()
    for rel in remote_dels:
        (env.remote / rel).unlink()
    changes = len(local_edits) + len(remote_edits) + len(local_dels) + len(remote_dels)

    with budget.flow("two-way: preview of a run"):
        preview = await engine.preview_sync()
    tw = preview.two_way
    assert tw is not None and tw.error is None and not tw.resync
    assert (tw.remote.replaces, tw.remote.deletes) == (len(local_edits), len(local_dels))
    assert (tw.local.replaces, tw.local.deletes) == (len(remote_edits), len(remote_dels))
    assert not tw.local.exceeds_max_delete and not tw.remote.exceeds_max_delete

    with budget.flow("two-way: run"):
        job = await env.completed(await engine.two_way_sync())
    assert job.direction == "two_way"
    assert job.files_changed == changes
    assert_identical(env, total - len(local_dels) - len(remote_dels))
    synced = tree(env.local)
    assert all(synced[rel] == synced[local_edits[0]] for rel in local_edits)
    assert all(synced[rel] == synced[remote_edits[0]] for rel in remote_edits)


async def per_file_flow(env: Env, n: int, budget: Budget) -> None:
    """Thousands of per-file pushes and pulls chosen from one diff, applied in one run."""
    old = time.time() - 86400
    paths = small_paths(n)
    shared, local_only, remote_only, changed = paths[0::4], paths[1::4], paths[2::4], paths[3::4]
    for side in (env.local, env.remote):
        fill(side, shared, seed=6, mtime=old)
        write(side, SENTINEL_FILE, "marker")
    fill(env.local, local_only, seed=7, mtime=old)
    fill(env.remote, remote_only, seed=8, mtime=old)
    fill(env.remote, changed, seed=9, mtime=old)
    fill(env.local, changed, seed=10, mtime=old + 3600)  # newer here: modified_local
    large = make_large(env.local, old, n)
    engine = env.engine()

    with budget.flow("per-file: diff"):
        diff = await engine.enhanced_diff()
    assert diff.error is None
    assert diff.summary.local_only == len(local_only) + large
    assert diff.summary.remote_only == len(remote_only)
    assert diff.summary.modified_local == len(changed)

    items = [
        SelectiveSyncItem(path=f.path, action=FileAction.PULL if f.category == ChangeCategory.REMOTE_ONLY
                          else FileAction.PUSH)
        for f in diff.files
    ]
    with budget.flow("per-file: apply"):
        result = await engine.selective_sync(items)
    assert (result.failed, result.succeeded) == (0, len(items)), result.errors[:5]
    job = await env.completed(result.job_id)
    assert job.files_changed == len(items)
    assert_identical(env, n + large)
    # The replaced versions of the changed files are in the remote's trash.
    assert len(trashed(env.remote)) == len(changed)


async def test_mirror_preview_diff_and_push_at_scale(at_scale):
    await at_scale(mirror_flow)


async def test_two_way_resync_and_run_at_scale(at_scale):
    await at_scale(two_way_flow)


async def test_per_file_actions_at_scale(at_scale):
    await at_scale(per_file_flow)


# --- The backend's own per-file work, on ten times as many files ---
#
# At 5,000 files rclone's share hides a mildly quadratic Python step (one
# costing a second or two). Here rclone is a stub that answers instantly,
# so the diff, the per-file checks, the copies' log parsing and the job
# bookkeeping are all that is measured, on 5,000 and on 50,000 files.

class InstantRclone:
    """Answers a diff and per-file copies of ``n`` files without running rclone."""

    def __init__(self, n: int) -> None:
        stamp = "2026-01-01T00:00:00.000000000Z"
        newer = "2026-01-02T00:00:00.000000000Z"
        paths = [f"d{i % 200:03d}/f{i:06d}.txt" for i in range(n)]
        self.local_only, self.remote_only, self.differ = paths[0::3], paths[1::3], paths[2::3]
        self.local = {p: {"Path": p, "Size": 10, "ModTime": newer, "IsDir": False}
                      for p in self.local_only + self.differ}
        self.remote = {p: {"Path": p, "Size": 20, "ModTime": stamp, "IsDir": False}
                       for p in self.remote_only + self.differ}

    def side(self, root: str) -> dict[str, dict]:
        return self.local if root == "/local" else self.remote

    async def check_diff(self, *args, **kwargs) -> dict:
        return {"local_only": self.local_only, "remote_only": self.remote_only, "differ": self.differ,
                "has_changes": True}

    async def lsjson(self, root: str, rclone_filter=None) -> list[dict]:
        return list(self.side(root).values())

    async def lsjson_paths(self, root: str, file_paths: list[str]) -> dict[str, dict]:
        entries = self.side(root)
        return {p: entries[p] for p in file_paths if p in entries}

    async def copy_files(self, source: str, dest: str, file_paths: list[str], recorder=None, **kwargs) -> None:
        src, dst = self.side(source), self.side(dest)
        for p in file_paths:
            msg = "Copied (replaced existing)" if p in dst else "Copied (new)"
            dst[p] = dict(src[p])
            if recorder is not None:
                recorder.feed(json.dumps({"level": "info", "msg": msg, "object": p, "objectType": "*local.Object",
                                          "size": src[p]["Size"]}))

    async def existing_paths(self, root: str, file_paths: list[str]) -> set[str]:
        entries = self.side(root)
        return {p for p in file_paths if p in entries}


async def bookkeeping(n: int, tmp_path: Path) -> float:
    """Backend CPU time of a diff and per-file actions on every file of it, for n files."""
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db, expire_on_commit=False)
    stub = InstantRclone(n)
    engine = SyncEngine(
        ProfileConfig(profile_id=1, slug="docs", name="Docs", local_dir=str(tmp_path), remote_dir="/remote"),
        stub, factory,  # type: ignore[arg-type]
    )
    engine._profile = replace(engine._profile, local_dir="/local")
    try:
        start = time.process_time()
        diff = await engine.enhanced_diff()
        assert diff.summary.total == n and diff.summary.modified_local == len(stub.differ)
        items = [SelectiveSyncItem(path=f.path, action=FileAction.PULL if f.category == ChangeCategory.REMOTE_ONLY
                                   else FileAction.PUSH) for f in diff.files]
        with patch("os.path.isdir", return_value=True):
            result = await engine.selective_sync(items)
        cpu = time.process_time() - start
        assert (result.succeeded, result.failed) == (n, 0)
        assert stub.local == stub.remote
        async with factory() as session:
            job = await session.get(SyncJob, result.job_id)
        assert job is not None and job.files_changed == n
        return cpu
    finally:
        await db.dispose()


async def test_backend_per_file_work_grows_linearly(tmp_path: Path):
    """Ten times the files may cost the backend at most 25 times the CPU time.

    Linear work costs at most 10 times as much (measured: 6 to 7, as the
    recorded change rows are capped), a step that compares every file with
    every other one about 100 times. A list where a set belongs, in the
    per-file actions, measured 48 times.
    """
    small = await bookkeeping(5000, tmp_path)
    full = await bookkeeping(50000, tmp_path)
    print(f"\n[scale] per-file bookkeeping: {small:.2f}s for 5,000 files, {full:.2f}s for 50,000")
    assert full < 25 * max(small, CPU_FLOOR), f"{full / small:.1f}x the CPU time for 10x the files"
