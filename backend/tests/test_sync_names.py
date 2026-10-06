"""Local names rclone cannot sync as they are: finding, showing and reporting them.

Without rclone: the folder scan (names.py), the engine's checks around it
with a stand-in rclone, the job's stored warnings and the notification.
The real rclone runs are in test_hostile_names_integration.py.
"""

from __future__ import annotations

import json
import os
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.api.routes.jobs import job_warnings
from backend.api.schemas import SyncWarning, SyncWarningCode
from backend.db.models import SyncJob
from backend.exceptions import RcloneError
from backend.services.notification_events import NotificationEventType, NotificationSeverity, sync_completed_event
from backend.services.rclone import SENTINEL_FILE, TRASH_DIR
from backend.services.sync_engine.names import (
    WARNING_PATHS_SHOWN,
    LocalNames,
    describe,
    display_path,
    link_filter_rules,
    name_warnings,
    only_listed,
    rclone_spelling,
    scan_local,
)
from backend.tests import test_profiles
from backend.tests.test_engine_notifications import RecordingDispatcher, _engine, _profile_row
from backend.tests.test_profiles import Env

env = test_profiles.env

NFC, NFD = (lambda s: unicodedata.normalize("NFC", s)), (lambda s: unicodedata.normalize("NFD", s))
BAD = os.fsdecode(b"bad\xff\xfe.txt")


def touch(root: Path, rel: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")


# --- Showing a name ---


@pytest.mark.parametrize(("raw", "shown"), [
    ("résumé 📁/تقرير.txt", "résumé 📁/تقرير.txt"),          # readable names stay as they are
    ("👨‍👩‍👧.txt", "👨‍👩‍👧.txt"),     # joiners in emoji too
    (BAD, "bad\\xff\\xfe.txt"),                                # each byte that is not UTF-8
    ("line\nbreak\ttab\rcr", "line\\nbreak\\ttab\\rcr"),
    ("ctrl\x01\x1b\x7f\x85", "ctrl\\x01\\x1b\\x7f\\x85"),
    ("invoice‮gpj.exe", "invoice\\u202egpj.exe"),        # bidi override cannot disguise it
    ("⁦x⁩", "\\u2066x\\u2069"),
])
def test_display_path_escapes_what_would_hide_or_disguise_a_name(raw: str, shown: str):
    assert display_path(raw) == shown


def test_rclone_spelling_counts_a_run_of_bad_bytes_once():
    """rclone puts U+FFFD in place of bad bytes; compared per run, the count does not matter."""
    assert rclone_spelling(BAD) == "bad�.txt"
    assert rclone_spelling("ok/é.txt") == "ok/é.txt"


# --- Scanning the local folder ---


def test_scan_finds_collisions_of_files_and_folders(tmp_path: Path):
    touch(tmp_path, NFC("café.txt"))
    touch(tmp_path, NFD("café.txt"))
    touch(tmp_path, NFC("dé") + "/a")
    touch(tmp_path, NFD("dé") + "/b")
    touch(tmp_path, "sub/" + NFC("ü"))
    touch(tmp_path, "sub/" + NFD("ü"))
    touch(tmp_path, "plain.txt")
    # Case differs, normalisation does not: two files to rclone.
    touch(tmp_path, "Readme")
    touch(tmp_path, "README")

    found = scan_local(str(tmp_path))

    assert sorted(map(sorted, found.collisions)) == sorted([
        sorted([NFC("café.txt"), NFD("café.txt")]),
        sorted([NFC("dé") + "/", NFD("dé") + "/"]),
        sorted(["sub/" + NFC("ü"), "sub/" + NFD("ü")]),
    ])
    assert found.not_utf8 == [] and found.symlinks == []


def test_scan_reports_a_bad_folder_once_and_finds_links(tmp_path: Path):
    touch(tmp_path, BAD)
    touch(tmp_path, os.fsdecode(b"dir\xff") + "/" + os.fsdecode(b"inner\xfe.txt"))
    os.symlink("nowhere", tmp_path / "dangling")
    os.symlink(tmp_path, tmp_path / "loop")              # never followed
    os.symlink("x", tmp_path / NFD("é"))                 # a link next to a file of
    touch(tmp_path, NFC("é"))                            # the same name: no collision
    (tmp_path / TRASH_DIR / "t").mkdir(parents=True)     # the trash and the marker
    os.symlink("x", tmp_path / TRASH_DIR / "t" / "link")  # are never looked at
    touch(tmp_path, TRASH_DIR + "/t/" + os.fsdecode(b"\xff"))
    touch(tmp_path, SENTINEL_FILE)

    found = scan_local(str(tmp_path))

    assert found.not_utf8 == sorted([BAD, os.fsdecode(b"dir\xff") + "/"])
    assert found.symlinks == sorted(["dangling", "loop", NFD("é")])
    assert found.collisions == []


def test_scan_of_a_missing_or_unreadable_folder_finds_nothing(tmp_path: Path):
    assert scan_local(str(tmp_path / "missing")) == LocalNames()
    locked = tmp_path / "locked"
    touch(locked, "inner/" + NFC("é"))
    touch(locked, "inner/" + NFD("é"))
    (locked / "inner").chmod(0)
    try:
        if os.access(locked / "inner", os.R_OK):
            pytest.skip("running as root: permissions do not lock the folder")
        assert scan_local(str(locked)) == LocalNames()
    finally:
        (locked / "inner").chmod(0o755)


def test_only_listed_keeps_what_the_sync_sees():
    found = LocalNames(
        collisions=[[NFC("á.txt"), NFD("á.txt")], ["x/" + NFC("é"), "x/" + NFD("é"), "x/é̀"],
                    [NFC("ö/"), NFD("ö/")]],
        not_utf8=[BAD, os.fsdecode(b"gone\xff")],
        symlinks=["link"],
    )
    # rclone listed the bad name with one U+FFFD per byte, a folder without "/".
    listed = {NFC("á.txt"), "x/" + NFC("é"), "x/" + NFD("é"), NFC("ö"), NFD("ö"), "bad��.txt"}

    narrowed = only_listed(found, listed)

    assert narrowed.collisions == [["x/" + NFC("é"), "x/" + NFD("é")], [NFC("ö/"), NFD("ö/")]]
    assert narrowed.not_utf8 == [BAD]
    assert narrowed.symlinks == ["link"]


def test_link_rules_name_each_link_and_what_is_below_it():
    assert link_filter_rules(["a[1].txt", "dir/l*nk", BAD, "line\nbreak", "cr\r"]) == [
        "- /a\\[1\\].txt", "- /a\\[1\\].txt/**", "- /dir/l\\*nk", "- /dir/l\\*nk/**",
    ]


# --- The warnings ---


def test_warnings_show_some_paths_and_count_all():
    many = [f"f{i:02d}" + os.fsdecode(b"\xff") for i in range(WARNING_PATHS_SHOWN + 5)]
    found = LocalNames(collisions=[[NFD("é.txt"), NFC("é.txt")]], not_utf8=many)

    warnings = name_warnings(found, ["clash"], SyncWarningCode.SYMLINK_TRASHED)

    assert warnings == [
        SyncWarning(code=SyncWarningCode.NAME_COLLISION, count=1, paths=[NFC("é.txt")]),
        SyncWarning(code=SyncWarningCode.NAME_NOT_UTF8, count=len(many),
                    paths=[display_path(p) for p in many[:WARNING_PATHS_SHOWN]]),
        SyncWarning(code=SyncWarningCode.SYMLINK_TRASHED, count=1, paths=["clash"]),
    ]
    lines = describe(warnings)
    assert lines[0] == ("1 local name(s) exist in spellings that are equal after Unicode normalisation; "
                        f"rclone synced only one spelling of each: {NFC('é.txt')}")
    assert lines[1].endswith("f19\\xff, ...") and lines[1].startswith("25 local name(s) are not valid UTF-8")
    assert "moved to the remote trash" in lines[2]
    assert name_warnings(LocalNames(), [], SyncWarningCode.SYMLINK_KEPT) == []
    assert all(describe([SyncWarning(code=c, count=1, paths=["p"])]) for c in SyncWarningCode)


def test_job_warnings_survive_bad_stored_values():
    good = SyncWarning(code=SyncWarningCode.SYMLINK_KEPT, count=2, paths=["a", "b"])
    job = SyncJob(id=1, warnings=json.dumps([good.model_dump(mode="json"), {"code": "nope"}, 3]))
    assert job_warnings(job) == [good]
    assert job_warnings(SyncJob(id=2, warnings="{not json")) == []
    assert job_warnings(SyncJob(id=3, warnings=json.dumps({"code": "x"}))) == []
    assert job_warnings(SyncJob(id=4, warnings="[]")) == []


def test_completed_event_with_warnings_is_a_warning():
    plain = sync_completed_event("push", 3)
    assert plain.severity == NotificationSeverity.INFO and "warnings" not in plain.title
    event = sync_completed_event("two-way", 3, warnings=["1 local name(s) are not valid UTF-8: x"],
                                 profile_name="Docs")
    assert event.severity == NotificationSeverity.WARNING
    assert event.title == "Two-way sync completed with warnings — Docs"
    assert event.body.endswith("see the job's warnings: 1 local name(s) are not valid UTF-8: x.")
    long = sync_completed_event("push", 1, warnings=["x" * 3000])
    assert len(long.body) == 2000


# --- The engine's checks, with a stand-in rclone ---


def rclone_stub() -> AsyncMock:
    rclone = AsyncMock()
    rclone.list_top_level = AsyncMock(return_value=[])
    rclone.existing_items = AsyncMock(return_value=set())
    return rclone


async def test_a_push_with_a_collision_is_completed_with_its_warning_stored_and_sent(tmp_path, env: Env):
    await _profile_row(env, tmp_path)
    rclone = rclone_stub()
    dispatcher = RecordingDispatcher()
    engine = _engine(tmp_path, rclone, env.factory, dispatcher, sync_mode="mirror")
    touch(tmp_path / "local", NFC("café.txt"))
    touch(tmp_path / "local", NFD("café.txt"))

    job_id = await engine.push()

    async with env.factory() as session:
        job = await session.get(SyncJob, job_id)
    assert job is not None and job.status == "completed"
    assert job_warnings(job) == [SyncWarning(code=SyncWarningCode.NAME_COLLISION, count=1, paths=[NFC("café.txt")])]
    (event,) = dispatcher.events
    assert event.event_type == NotificationEventType.SYNC_COMPLETED
    assert event.severity == NotificationSeverity.WARNING and NFC("café.txt") in event.body
    # No profile filters: the scan alone decides, nothing else is listed.
    rclone.lsjson.assert_not_called()


async def test_profile_filters_narrow_the_warnings(tmp_path, env: Env):
    rclone = rclone_stub()
    rclone.lsjson = AsyncMock(return_value=[{"Path": "keep", "IsDir": True}, {"Path": "keep/" + NFC("é")},
                                            {"Path": "keep/" + NFD("é")}, {"no path": 1}, "junk"])
    engine = _engine(tmp_path, rclone, env.factory, None, sync_mode="mirror",
                     rclone_filter=["- /skip/**"], rclone_args=["--exclude", "*.tmp", "--transfers", "4"])
    for folder in ("keep", "skip"):
        touch(tmp_path / "local", f"{folder}/" + NFC("é"))
        touch(tmp_path / "local", f"{folder}/" + NFD("é"))

    warnings, links = await engine._name_warnings(SyncWarningCode.SYMLINK_SHADOW)

    assert warnings == [SyncWarning(code=SyncWarningCode.NAME_COLLISION, count=1, paths=["keep/" + NFC("é")])]
    assert links == []
    rclone.lsjson.assert_awaited_once_with(str(tmp_path / "local"), rclone_filter=["- /skip/**"],
                                           rclone_args=["--exclude", "*.tmp"])

    # A listing that fails keeps everything found: a warning too many, never one too few.
    rclone.lsjson = AsyncMock(side_effect=RcloneError("listing failed"))
    warnings, _ = await engine._name_warnings(SyncWarningCode.SYMLINK_SHADOW)
    assert warnings[0].count == 2


async def test_links_are_checked_against_the_remote(tmp_path, env: Env):
    rclone = rclone_stub()
    rclone.existing_items = AsyncMock(return_value={"file.txt", "folder/", "folder/x", "sub/"})
    engine = _engine(tmp_path, rclone, env.factory, None, sync_mode="mirror")
    local = tmp_path / "local"
    for name in ("file.txt", "folder", "alone", "sub/[x]"):
        (local / name).parent.mkdir(parents=True, exist_ok=True)
        os.symlink("nowhere", local / name)
    os.symlink("nowhere", local / BAD)

    warnings, links = await engine._name_warnings(SyncWarningCode.SYMLINK_KEPT)

    assert warnings == [SyncWarning(code=SyncWarningCode.SYMLINK_KEPT, count=2, paths=["file.txt", "folder"])]
    assert links == sorted([BAD, "alone", "file.txt", "folder", "sub/[x]"])
    (root, rules), _ = rclone.existing_items.call_args
    assert root == "gdrive:Docs"
    assert rules == ["+ /alone", "+ /alone/", "+ /file.txt", "+ /file.txt/", "+ /folder", "+ /folder/",
                     "+ /sub/\\[x\\]", "+ /sub/\\[x\\]/"]

    # The remote cannot be checked: every link is reported.
    rclone.existing_items = AsyncMock(side_effect=RcloneError("unreachable"))
    warnings, _ = await engine._name_warnings(SyncWarningCode.SYMLINK_KEPT)
    assert warnings[0].count == 4


async def test_only_unnameable_links_need_no_remote_check(tmp_path, env: Env):
    rclone = rclone_stub()
    engine = _engine(tmp_path, rclone, env.factory, None, sync_mode="mirror")
    os.symlink("nowhere", tmp_path / "local" / BAD)
    assert await engine._name_warnings(SyncWarningCode.SYMLINK_KEPT) == ([], [BAD])
    rclone.existing_items.assert_not_called()


async def test_a_check_that_crashes_gives_no_warnings(tmp_path, env: Env, monkeypatch):
    """The sync does not depend on the check: a crash in it is logged, and the run goes on."""
    def broken(_root: str) -> LocalNames:
        raise RuntimeError("boom")

    monkeypatch.setattr("backend.services.sync_engine.reporting.scan_local", broken)
    engine = _engine(tmp_path, rclone_stub(), env.factory, None, sync_mode="mirror")
    assert await engine._name_warnings(SyncWarningCode.SYMLINK_KEPT) == ([], [])


async def test_jobs_list_their_warnings(env: Env):
    profile = await env.create("Docs", env.folder("docs"))
    now = datetime.now(timezone.utc)
    stored = SyncWarning(code=SyncWarningCode.NAME_NOT_UTF8, count=1, paths=["bad\\xff.txt"])
    async with env.factory() as session:  # type: ignore[operator]
        session.add(SyncJob(id=5, profile_id=profile["id"], direction="push", started_at=now, status="completed",
                            warnings=json.dumps([stored.model_dump(mode="json")])))
        session.add(SyncJob(id=6, profile_id=profile["id"], direction="pull", started_at=now, status="completed"))
        await session.commit()

    job = (await env.client.get("/jobs/5")).json()
    assert job["status"] == "completed"
    assert job["warnings"] == [{"code": "name_not_utf8", "count": 1, "paths": ["bad\\xff.txt"]}]
    assert (await env.client.get("/jobs/6")).json()["warnings"] == []
    by_profile = {j["id"]: j["warnings"] for j in (await env.client.get(f"/profiles/{profile['slug']}/jobs")).json()}
    assert by_profile == {5: job["warnings"], 6: []}
