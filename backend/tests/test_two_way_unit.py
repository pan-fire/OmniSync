"""Two-way sync without rclone: log parsing, flag handling, state, API.

The log lines below were captured from rclone 1.75.1 ``bisync
--use-json-log -v --color NEVER`` runs on temp folders (Path1 local, Path2
an rclone `local` remote), shortened to the fields the parser reads.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from backend.api.schemas import ProfileCreateRequest, ProfileUpdateRequest, SyncMode
from backend.db.models import SyncJob, SyncProfile
from backend.exceptions import RcloneError
from backend.models.profile_config import ProfileConfig
from backend.services.rclone import (
    BisyncRecorder,
    bisync_names_fit,
    bisync_session_name,
    define_env_remote,
    is_reserved_remote_name,
    needs_short_names,
    process_env,
    without_flag,
)
from backend.services.rclone.bisync_names import NAME_MAX, rebase, short_bisync_roots
from backend.services.sync_engine import SyncEngine, bisync_workdir, filter_flags
from backend.tests import test_profiles
from backend.tests.test_profiles import Env

env = test_profiles.env


def line(msg: str, level: str = "info", obj: str | None = None, **extra) -> str:
    entry: dict[str, object] = {"time": "2026-09-27T22:08:39+02:00", "level": level, "msg": msg}
    if obj is not None:
        entry.update(object=obj, objectType="*local.Object")
    entry.update(extra)
    return json.dumps(entry)


# A normal run: a.txt changed locally, b.txt deleted remotely, c.txt changed
# on both sides (the remote version newer), d.txt deleted locally, e.txt
# changed remotely, one new file on each side.
NORMAL_RUN = [
    line("- Path1             File changed: size (larger), time (newer)   - a.txt"),
    line("- WARNING           New or changed in both paths                - c.txt", "notice"),
    line("- Path1             Renaming Path1 copy                         - /l/c.local-conflict1.txt", "notice"),
    line("Moved (server-side) to: c.local-conflict1.txt", obj="c.txt"),
    line("- Path1             Queue copy to Path2                         - tr:/r/c.local-conflict1.txt", "notice"),
    line("- Path2             Not renaming Path2 copy, as it was determined the winner - tr:/r/c.txt", "notice"),
    line("- Path2             Queue copy to Path1                         - /l/c.txt", "notice"),
    line("- Path2             Do queued copies to                         - Path1"),
    line("Moved (server-side)", obj="e.txt"),
    line("Copied (new)", obj="c.txt", size=14),
    line("Copied (new)", obj="e.txt", size=12),
    line("Copied (new)", obj="new-remote.txt", size=10),
    line("Moved (server-side)", obj="b.txt"),
    line("Moved into backup dir", obj="b.txt"),
    line("- Path1             Do queued copies to                         - Path2"),
    line("Copied (new)", obj="c.local-conflict1.txt", size=7),
    line("Copied (new)", obj="new-local.txt", size=9),
    line("Moved (server-side)", obj="a.txt"),
    line("Copied (new)", obj="a.txt", size=11),
    line("Moved (server-side)", obj="d.txt"),
    line("Moved into backup dir", obj="d.txt"),
    json.dumps({"level": "info", "msg": "Making directory", "object": "emptydir", "objectType": "string"}),
    line("Bisync successful"),
]


class TestBisyncRecorder:
    def test_changes_are_attributed_to_the_side_they_happen_on(self) -> None:
        rec = BisyncRecorder()
        for entry in NORMAL_RUN:
            rec.feed(entry)
        assert {(r.path, r.action, r.side) for r in rec.rows} == {
            ("c.local-conflict1.txt", "created", "local"),  # the rename on Path1
            ("c.txt", "modified", "local"),  # then the winner copied over
            ("e.txt", "modified", "local"),
            ("new-remote.txt", "created", "local"),
            ("b.txt", "deleted", "local"),
            ("c.local-conflict1.txt", "created", "remote"),
            ("new-local.txt", "created", "remote"),
            ("a.txt", "modified", "remote"),
            ("d.txt", "deleted", "remote"),
        }
        assert rec.count("local", "deleted") == 1 and rec.count("remote", "deleted") == 1
        assert rec.conflicts == {"c.txt": {"local": "c.local-conflict1.txt", "remote": "c.txt"}}
        assert rec.changed_on_both == 1
        assert not rec.resync_needed and not rec.too_many_deletes

    def test_resync_phases(self) -> None:
        rec = BisyncRecorder()
        for entry in [
            line("- \u001b[34mPath2\u001b[0m    \u001b[35mResync is copying files to\u001b[0m         - \u001b[36mPath1\u001b[0m"),
            line("Moved (server-side)", obj="f1.txt"),
            line("Copied (new)", obj="f1.txt", size=13),
            line("Copied (new)", obj="onlyr.txt", size=7),
            line("- Path1    Resync is copying files to         - Path2"),
            line("Copied (new)", obj="onlyl.txt", size=7),
        ]:
            rec.feed(entry)
        assert {(r.path, r.action, r.side) for r in rec.rows} == {
            ("f1.txt", "modified", "local"), ("onlyr.txt", "created", "local"), ("onlyl.txt", "created", "remote"),
        }

    def test_dry_run_is_counted_per_side(self) -> None:
        rec = BisyncRecorder(max_rows=0)
        for entry in [
            line("- Path2             Do queued copies to                         - Path1", "notice"),
            line("Skipped move as --dry-run is set (size 2)", "notice", obj="f4.txt", skipped="move", size=2),
            line("Skipped copy as --dry-run is set (size 8)", "notice", obj="f4.txt", skipped="copy", size=8),
            line("Skipped move into backup dir as --dry-run is set (size 2)", "notice", obj="f3.txt",
                 skipped="move into backup dir", size=2),
            line("- Path1             Do queued copies to                         - Path2", "notice"),
            line("Skipped copy as --dry-run is set (size 4)", "notice", obj="n.txt", skipped="copy", size=4),
            line("Skipped move into backup dir as --dry-run is set (size 2)", "notice", obj="f1.txt",
                 skipped="move into backup dir", size=2),
            line("Skipped move into backup dir as --dry-run is set (size 2)", "notice", obj="f2.txt",
                 skipped="move into backup dir", size=2),
        ]:
            rec.feed(entry)
        assert rec.rows == [] and rec.total == 5
        assert (rec.count("local", "modified"), rec.count("local", "deleted")) == (1, 1)
        assert (rec.count("remote", "created"), rec.count("remote", "deleted")) == (1, 2)

    @pytest.mark.parametrize("msg", [
        "Bisync aborted. Must run --resync to recover.",
        "Bisync critical error: filters file has changed (must run --resync): /wd/filters.txt",
    ])
    def test_resync_needed(self, msg: str) -> None:
        rec = BisyncRecorder()
        rec.feed(line(msg, "error"))
        assert rec.resync_needed

    def test_retryable_error_is_not_a_resync(self) -> None:
        rec = BisyncRecorder()
        rec.feed(line("Bisync critical error: check file check failed", "error"))
        rec.feed(line("Bisync aborted. Error is retryable without --resync due to --resilient mode.", "error"))
        assert not rec.resync_needed
        assert rec.critical == "check file check failed"
        assert "check file check failed" in rec.failure_text()

    def test_percentage_delete_guard(self) -> None:
        rec = BisyncRecorder()
        rec.feed(json.dumps({
            "level": "error", "msg": 'too many deletes (>30%, 5 of 11) on Path1 "/l/". Run with --force if desired.',
            "object": "Safety abort", "objectType": "string",
        }))
        assert rec.too_many_deletes and not rec.resync_needed


@pytest.mark.parametrize(("args", "expected"), [
    (["--max-delete", "10", "--transfers", "4"], ["--transfers", "4"]),
    (["--max-delete=3", "--bwlimit", "1M"], ["--bwlimit", "1M"]),
    ([], []),
])
def test_without_flag(args, expected):
    assert without_flag(args, "--max-delete") == expected


@pytest.mark.parametrize(("args", "expected"), [
    (["--transfers", "4", "--bwlimit", "1M"], []),
    (["--exclude", "*.bak", "--transfers", "4"], ["--exclude", "*.bak"]),
    (["--max-size=10M", "--skip-links", "--max-age", "7d"], ["--max-size=10M", "--skip-links", "--max-age", "7d"]),
])
def test_filter_flags(args, expected):
    assert filter_flags(args) == expected


# --- engine state without rclone ---


def two_way_engine(tmp_path: Path, rclone: AsyncMock, factory) -> SyncEngine:
    local = tmp_path / "local"
    local.mkdir(exist_ok=True)
    config = ProfileConfig(profile_id=1, slug="docs", name="Docs", local_dir=str(local),
                           remote_dir="gdrive:Docs", max_retries=3, sync_mode="two_way")
    return SyncEngine(config, rclone, factory)


@pytest.fixture
def bisync_dir(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "bisync"
    monkeypatch.setattr("backend.services.sync_engine.two_way.BISYNC_DIR", str(path))
    return path


async def test_bisync_error_that_needs_a_resync_pauses_instead_of_resyncing(tmp_path, bisync_dir, env: Env):
    rclone = AsyncMock()
    rclone.list_top_level = AsyncMock(return_value=[".omnisync-check", "a.txt"])

    async def failing_bisync(*args, recorder: BisyncRecorder, resync: bool = False, **kwargs):
        assert not resync  # never a blind resync
        recorder.feed(line("Bisync critical error: chtimes /r/x.partial: no such file or directory", "error"))
        recorder.feed(line("Bisync aborted. Must run --resync to recover.", "error"))
        raise RcloneError("rclone failed (exit 7)")

    rclone.bisync = AsyncMock(side_effect=failing_bisync)
    async with env.factory() as session:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        session.add(SyncProfile(id=1, slug="docs", name="Docs", local_dir=str(tmp_path / "local"),
                                remote_dir="gdrive:Docs", created_at=now, updated_at=now))
        await session.commit()
    engine = two_way_engine(tmp_path, rclone, env.factory)
    (tmp_path / "local" / ".omnisync-check").write_text("m")
    (tmp_path / "local" / "a.txt").write_text("a")
    # A pair that synced before: state, listings and the same filters.
    workdir = Path(bisync_workdir(1))
    workdir.mkdir(parents=True)
    (workdir / "omnisync-state.json").write_text(json.dumps({"pair": engine._pair}))
    (workdir / "x.path1.lst").write_text("# bisync listing\n")
    (workdir / "x.path2.lst").write_text("# bisync listing\n")
    (workdir / "filters.txt").write_text(engine._bisync_filters([]))

    job_id = await engine.two_way_sync()

    assert rclone.bisync.await_count == 1  # no retry, no resync
    async with env.factory() as session:
        job = await session.get(SyncJob, job_id)
        assert (job.direction, job.status) == ("two_way", "failed")
    assert engine._state.resync_required and engine._state.intervals_paused
    assert "chtimes" in (engine._state.last_error or "") and "Resync" in (engine._state.last_error or "")
    assert json.loads((workdir / "omnisync-state.json").read_text())["resync_required"]

    # The next automatic run does not touch rclone either.
    await engine.two_way_sync()
    assert rclone.bisync.await_count == 1


def test_listings_of_a_stopped_run_are_kept(tmp_path, bisync_dir):
    engine = two_way_engine(tmp_path, AsyncMock(), None)
    workdir = Path(bisync_workdir(1))
    workdir.mkdir(parents=True)
    for name in ("x.path1.lst-err", "x.path2.lst-err", "x.path1.lst-new", "x.path2.lst-new"):
        (workdir / name).write_text(name)

    engine._keep_listings_after_stop()

    assert sorted(os.listdir(workdir)) == ["x.path1.lst", "x.path1.lst-new", "x.path2.lst", "x.path2.lst-new"]
    assert (workdir / "x.path1.lst").read_text() == "x.path1.lst-err"
    # Nothing to do when the listings are there.
    engine._keep_listings_after_stop()
    assert engine._listings_exist()


def test_plan(tmp_path, bisync_dir):
    engine = two_way_engine(tmp_path, AsyncMock(), None)
    filters = engine._bisync_filters(["flagged.txt"])
    assert "- /flagged.txt" in filters and "- /.omnisync-trash/**" in filters
    assert engine._two_way_plan(filters) == ("resync", None)  # never synced

    workdir = Path(bisync_workdir(1))
    workdir.mkdir(parents=True)
    (workdir / "omnisync-state.json").write_text(json.dumps({"pair": engine._pair}))
    assert engine._two_way_plan(filters)[0] == "blocked"  # listings gone
    (workdir / "x.path1.lst").write_text("")
    (workdir / "x.path2.lst").write_text("")
    (workdir / "filters.txt").write_text(filters)
    assert engine._two_way_plan(filters) == ("run", None)
    assert engine._two_way_plan(engine._bisync_filters([])) == ("flush", None)
    # Other folders: a first run again.
    (workdir / "omnisync-state.json").write_text(json.dumps({"pair": ["/elsewhere", "gdrive:Docs"]}))
    assert engine._two_way_plan(filters) == ("resync", None)


# --- folder paths too long for bisync's file names (see rclone/bisync_names.py) ---


def test_session_name_is_the_one_rclone_uses():
    # As rclone 1.75.1 names the files (checked against real runs in
    # test_two_way_sync_integration.py, which compares every workdir).
    assert bisync_session_name("/home/me/My Docs/", "gdrive:Work/Q1") == "home_me_My_Docs..gdrive_Work_Q1"
    assert bisync_session_name("/a", "local-disk:/b") == "a..local-disk__b"
    assert bisync_session_name("/a", "gdrive:") == "a..gdrive_"
    # A connection string's {hash} is not part of the name.
    assert bisync_session_name("/a", "gdrive{AbC12}:x") == "a..gdrive_x"


@pytest.mark.parametrize("session_bytes, fits", [(237, True), (238, False)])
def test_names_fit_up_to_the_longest_suffix(session_bytes, fits):
    # Found with rclone 1.75.1: a dry run (.path1.lst-dry-new) fails from 238 bytes on.
    local = "/" + "x" * (session_bytes - len("..r_y"))
    assert len(bisync_session_name(local, "r:y")) == session_bytes
    assert bisync_names_fit(local, "r:y") is fits
    assert needs_short_names(local, "r:y")  # short names leave room to spare
    # Bytes, not characters.
    assert not bisync_names_fit("/" + "\u00e9" * 120, "r:y") and NAME_MAX == 255


def test_short_names_are_reserved_and_defined_only_in_the_environment():
    assert short_bisync_roots(7, "/home/me/Docs", 'gdrive:My "Q1" files') == (
        "omnisync_bisync_7_local:root", "omnisync_bisync_7_remote:root",
    )
    env = process_env(["rclone", "bisync", "--backup-dir2", "omnisync_bisync_7_remote:root/t", "--",
                       "omnisync_bisync_7_local:root", "omnisync_bisync_7_remote:root"])
    assert env is not None
    assert {k: v for k, v in env.items() if k.startswith("RCLONE_CONFIG_OMNISYNC")} == {
        "RCLONE_CONFIG_OMNISYNC_BISYNC_7_LOCAL_TYPE": "combine",
        "RCLONE_CONFIG_OMNISYNC_BISYNC_7_LOCAL_UPSTREAMS": '"root=/home/me/Docs"',
        "RCLONE_CONFIG_OMNISYNC_BISYNC_7_REMOTE_TYPE": "combine",
        # combine's upstreams are a space-separated list, parsed as CSV.
        "RCLONE_CONFIG_OMNISYNC_BISYNC_7_REMOTE_UPSTREAMS": '"root=gdrive:My ""Q1"" files"',
    }
    assert process_env(["rclone", "lsf", "--", "xomnisync_bisync_7_local:root", "omnisync_bisync_8_x:"]) is None
    with pytest.raises(ValueError):
        define_env_remote("7_other", {"type": "combine"}, prefix="omnisync_bisync_")
    with pytest.raises(ValueError):
        define_env_remote("1", {"type": "alias"}, prefix="mine_")
    for name in ("omnisync_bisync_1_local", "OmniSync-Bisync-x", "omnisync_backup_crypt_1"):
        assert is_reserved_remote_name(name)
    assert not is_reserved_remote_name("omnisync_bisyn")


def test_backup_dirs_stay_on_the_remote_of_their_path():
    root = "omnisync_bisync_1_remote:root"
    assert rebase("gdrive:Docs/.omnisync-trash/t", "gdrive:Docs", root) == f"{root}/.omnisync-trash/t"
    assert rebase("gdrive:.omnisync-trash/t", "gdrive:", root) == f"{root}/.omnisync-trash/t"
    assert rebase("/a/b/", "/a/b/", root) == root
    with pytest.raises(ValueError):
        rebase("/a/bc/t", "/a/b", root)


def test_messages_name_the_real_folders():
    recorder = BisyncRecorder(max_rows=0, real_roots={
        "omnisync_bisync_3_local:root": "/home/me/Docs",
        "omnisync_bisync_3_remote:root": "gdrive:",
    })
    recorder.feed(line("Bisync critical error: cannot read omnisync_bisync_3_remote{a1B2c}:root/x.txt", "error"))
    recorder.feed("access denied: omnisync_bisync_3_local:root/a b.txt and omnisync_bisync_3_local:root")
    assert recorder.critical == "cannot read gdrive:x.txt"
    assert recorder.failure_text().splitlines() == [
        "Bisync critical error: cannot read gdrive:x.txt",
        "access denied: /home/me/Docs/a b.txt and /home/me/Docs",
    ]


def test_plan_of_a_pair_synced_on_paths_that_no_longer_fit(tmp_path, bisync_dir):
    engine = two_way_engine(tmp_path, AsyncMock(), None)
    filters = engine._bisync_filters([])
    workdir = Path(bisync_workdir(1))
    workdir.mkdir(parents=True)
    (workdir / "x.path1.lst").write_text("")
    (workdir / "x.path2.lst").write_text("")
    (workdir / "filters.txt").write_text(filters)
    (workdir / "omnisync-state.json").write_text(json.dumps({"pair": engine._pair, "names": "paths"}))
    assert engine._two_way_plan(filters) == ("run", None) and not engine._short_names(resync=False)

    engine._profile = replace(engine._profile, local_dir=str(tmp_path / ("d" * 250)))
    (workdir / "omnisync-state.json").write_text(json.dumps({"pair": engine._pair, "names": "paths"}))
    plan, reason = engine._two_way_plan(filters)
    assert plan == "blocked" and "too long" in (reason or "")
    assert engine._short_names(resync=True)  # the confirmed resync switches
    (workdir / "omnisync-state.json").write_text(json.dumps({"pair": engine._pair, "names": "short"}))
    assert engine._two_way_plan(filters) == ("run", None) and engine._short_names(resync=False)


# --- profile API ---


def test_sync_mode_validation():
    base = {"name": "Docs", "local_dir": "/home/me/Docs", "remote_dir": "gdrive:Docs"}
    assert ProfileCreateRequest(**base).sync_mode == SyncMode.TWO_WAY
    assert ProfileCreateRequest(**base, sync_mode="mirror").sync_mode == SyncMode.MIRROR
    with pytest.raises(ValidationError):
        ProfileCreateRequest(**base, sync_mode="both")
    assert ProfileUpdateRequest().sync_mode is None
    with pytest.raises(ValidationError):
        ProfileUpdateRequest(sync_mode="")


@pytest.mark.parametrize("remote_dir", ["omnisync_bisync_1_local:root", "OmniSync-Backup-Crypt-2:x"])
def test_profiles_cannot_name_omnisyncs_own_remotes(remote_dir):
    with pytest.raises(ValidationError, match="reserved"):
        ProfileCreateRequest(name="Docs", local_dir="/home/me/Docs", remote_dir=remote_dir)
    with pytest.raises(ValidationError, match="reserved"):
        ProfileUpdateRequest(remote_dir=remote_dir)


async def test_new_profiles_are_two_way_and_switching_to_mirror_forgets_the_state(env: Env, bisync_dir):
    created = await env.create("Docs", env.folder("docs"))
    assert created["sync_mode"] == "two_way"
    assert (await env.create("Old", env.folder("old"), mode="mirror"))["sync_mode"] == "mirror"
    assert (await env.client.post("/profiles", json={
        "name": "Bad", "local_dir": env.folder("bad"), "remote_dir": "gdrive:Bad", "sync_mode": "sideways",
    })).status_code == 422
    listed = {p["slug"]: (p["sync_mode"], p["resync_required"]) for p in (await env.client.get("/profiles")).json()}
    assert listed == {"docs": ("two_way", False), "old": ("mirror", False)}

    workdir = Path(bisync_workdir(created["id"]))
    workdir.mkdir(parents=True)
    (workdir / "omnisync-state.json").write_text("{}")
    resp = await env.client.put("/profiles/docs", json={"sync_mode": "mirror"})
    assert resp.status_code == 200 and resp.json()["sync_mode"] == "mirror"
    assert not workdir.exists()
    engine = env.running()[created["id"]]
    assert engine.profile.sync_mode == "mirror"

    resp = await env.client.put("/profiles/docs", json={"sync_mode": "two_way"})
    assert resp.json()["sync_mode"] == "two_way" and env.running()[created["id"]].profile.two_way
    async with env.factory() as session:
        row = (await session.execute(select(SyncProfile).where(SyncProfile.slug == "docs"))).scalar_one()
        assert row.sync_mode == "two_way"

    workdir.mkdir(parents=True)
    assert (await env.client.delete("/profiles/docs?confirm=true")).status_code == 204
    assert not workdir.exists()


# --- The delete limit's dry run ---


def listing(path: Path, files: int) -> None:
    """A bisync listing (header plus one "-" line per file), as rclone 1.75.1 writes it."""
    rows = [f'- 1 - - 2026-01-01T00:00:00.000000000+0000 "f{i}.txt"' for i in range(files)]
    path.write_text("# bisync listing v1 from test\n" + "\n".join(rows) + "\n")


def test_listed_files_is_the_larger_listing(tmp_path, bisync_dir):
    engine = two_way_engine(tmp_path, AsyncMock(), None)
    workdir = Path(bisync_workdir(1))
    workdir.mkdir(parents=True)
    assert engine._listed_files() == 0
    listing(workdir / "x.path1.lst", 3)
    listing(workdir / "x.path2.lst", 5)
    assert engine._listed_files() == 5


@pytest.mark.parametrize(("listed", "dry_run"), [(3, False), (4, True)])
async def test_the_delete_check_dry_runs_only_when_a_listing_exceeds_the_limit(
        tmp_path, bisync_dir, monkeypatch, listed, dry_run):
    """A side whose last listing holds no more files than the limit cannot
    lose more than the limit: no dry run then. Otherwise the dry run counts
    the deletions, with --check-access like the run itself."""
    monkeypatch.setattr("backend.services.sync_engine.safety.DEFAULT_MAX_DELETE", 3)
    rclone = AsyncMock()

    async def plan(*args, recorder: BisyncRecorder, dry_run: bool = False, check_access: bool = False, **kwargs):
        assert dry_run and check_access
        recorder.feed(line("- Path2             Do queued copies to                         - Path1"))
        for i in range(4):
            recorder.feed(line("Skipped delete as --dry-run is set", "notice", obj=f"f{i}.txt", skipped="delete"))

    rclone.bisync = AsyncMock(side_effect=plan)
    engine = two_way_engine(tmp_path, rclone, None)
    workdir = Path(bisync_workdir(1))
    workdir.mkdir(parents=True)
    listing(workdir / "x.path1.lst", listed)
    listing(workdir / "x.path2.lst", 2)

    refusal = await engine._two_way_delete_refusal(BisyncRecorder(max_rows=0), short=False)

    assert rclone.bisync.await_count == int(dry_run)
    assert (refusal is not None) == dry_run
    if refusal:
        assert "4 file(s) in the local folder" in refusal and "limit of 3" in refusal
