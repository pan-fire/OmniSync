"""RcloneService against a scripted stand-in for the rclone binary.

The stand-in (a small Python script named ``rclone`` put first on PATH)
records its argv and answers as each test tells it to, so these tests run
the real subprocess, streaming and parsing code and check the exact command
lines. Provider HTTP calls (token refresh, remote checks, the sync test)
go to an in-process fake provider through httpx.MockTransport.

The same code paths against the real rclone binary are covered by the
*_integration.py suites.
"""

from __future__ import annotations

import asyncio
import configparser
import json
import os
import stat
import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from backend.api.schemas import SyncDirection
from backend.exceptions import RcloneAuthError, RcloneError, RcloneRateLimitError
from backend.services.rclone import (
    SENTINEL_FILE,
    TRASH_FILTER,
    BisyncRecorder,
    ChangeRecorder,
    RcloneService,
    parse_auth_token,
    parse_auth_url,
    readable_stderr,
)

FAKE_RCLONE = f"""#!{sys.executable}
import configparser, json, os, signal, sys, time

args = sys.argv[1:]
with open(os.environ["FAKE_RCLONE_LOG"], "a") as log:
    log.write(json.dumps({{"argv": args, "pid": os.getpid()}}) + "\\n")
spec = json.loads(os.environ.get("FAKE_RCLONE", "{{}}"))
if spec.get("ignore_term"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
config_path = args[args.index("--config") + 1] if "--config" in args else None
sub = [a for a in args[2:] if not a.startswith("-")][:1] if config_path else args[:1]
if sub == ["listremotes"]:
    cfg = configparser.RawConfigParser()
    cfg.read(config_path)
    sys.stdout.write("".join(s + ":\\n" for s in cfg.sections()))
if "--files-from-raw" in args:
    with open(args[args.index("--files-from-raw") + 1]) as fh:
        with open(os.environ["FAKE_RCLONE_LOG"], "a") as log:
            log.write(json.dumps({{"files_from": fh.read()}}) + "\\n")
if "obscure" in args:
    sys.stdout.write("OBSCURED-" + sys.stdin.read()[::-1] + "\\n")
sys.stdout.write(spec.get("stdout", ""))
sys.stdout.flush()
sys.stderr.write(spec.get("stderr", ""))
sys.stderr.flush()
if spec.get("sleep"):
    time.sleep(spec["sleep"])
sys.exit(spec.get("rc", 0))
"""


class FakeRclone:
    """Controls the stand-in binary and reads back what it was called with."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        exe = bin_dir / "rclone"
        exe.write_text(FAKE_RCLONE)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
        self.log = tmp_path / "rclone-calls.jsonl"
        self.log.touch()
        self.config = tmp_path / "rclone.conf"
        self._monkeypatch = monkeypatch
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.setenv("FAKE_RCLONE_LOG", str(self.log))
        self.answer()

    def answer(self, stdout: str = "", stderr: str = "", rc: int = 0, **extra: object) -> None:
        self._monkeypatch.setenv("FAKE_RCLONE", json.dumps({"stdout": stdout, "stderr": stderr, "rc": rc, **extra}))

    def _records(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    @property
    def calls(self) -> list[list[str]]:
        return [r["argv"] for r in self._records() if "argv" in r]

    @property
    def pids(self) -> list[int]:
        return [r["pid"] for r in self._records() if "pid" in r]

    @property
    def files_from(self) -> list[str]:
        return [r["files_from"] for r in self._records() if "files_from" in r]

    def service(self, **kw) -> RcloneService:
        return RcloneService(rclone_config_path=str(self.config), **kw)

    def write_config(self, sections: dict[str, dict[str, str]]) -> None:
        cfg = configparser.RawConfigParser()
        cfg.optionxform = str  # type: ignore[assignment]
        for name, values in sections.items():
            cfg[name] = values
        with open(self.config, "w") as fh:
            cfg.write(fh)

    def read_config(self) -> configparser.RawConfigParser:
        cfg = configparser.RawConfigParser()
        cfg.optionxform = str  # type: ignore[assignment]
        cfg.read(self.config)
        return cfg


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRclone:
    return FakeRclone(tmp_path, monkeypatch)


def json_log(msg: str, obj: str | None = None, level: str = "info", **extra: object) -> str:
    entry: dict[str, object] = {"time": "2026-09-28T10:00:00+02:00", "level": level, "msg": msg}
    if obj is not None:
        entry.update(object=obj, objectType="*local.Object")
    entry.update(extra)
    return json.dumps(entry) + "\n"


def after_double_dash(argv: list[str]) -> list[str]:
    return argv[argv.index("--") + 1:]


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # a zombie still answers signal 0: check its state
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split(") ", 1)[1][0] != "Z"
    except FileNotFoundError:
        return False


# ---------------------------------------------------------------------------
# Command lines
# ---------------------------------------------------------------------------


class TestSyncCommand:
    async def test_push_command_line(self, fake: FakeRclone) -> None:
        await fake.service().sync(
            "/data/docs", "gd:backup/docs", SyncDirection.PUSH,
            exclude_filter_path="/tmp/exclude.txt",
            rclone_filter=["- *.tmp", "+ /keep/**"],
            rclone_args=["--transfers", "8"],
            backup_dir="gd:backup/docs/.omnisync-trash/2026",
            max_delete=25,
        )
        (argv,) = fake.calls
        assert argv[:3] == ["--config", str(fake.config), "sync"]
        assert f"--filter={TRASH_FILTER}" in argv
        assert argv[argv.index("--filter-from") + 1] == "/tmp/exclude.txt"
        assert argv[argv.index("--backup-dir") + 1] == "gd:backup/docs/.omnisync-trash/2026"
        assert argv[argv.index("--max-delete") + 1] == "25"
        # each profile rule is one token, so "- *.tmp" can never read as a flag
        assert "--filter=- *.tmp" in argv and "--filter=+ /keep/**" in argv
        assert argv[argv.index("--transfers") + 1] == "8"
        assert {"--use-json-log", "-v"} <= set(argv)
        assert after_double_dash(argv) == ["/data/docs", "gd:backup/docs"]

    async def test_paths_that_look_like_flags_stay_paths(self, fake: FakeRclone) -> None:
        await fake.service().sync("--dry-run", "r:--delete-excluded", SyncDirection.PUSH)
        (argv,) = fake.calls
        assert after_double_dash(argv) == ["--dry-run", "r:--delete-excluded"]
        assert argv.index("--dry-run") > argv.index("--")

    async def test_profile_log_level_replaces_the_default_verbosity(self, fake: FakeRclone) -> None:
        await fake.service().sync("/a", "r:a", SyncDirection.PULL, rclone_args=["--log-level", "DEBUG"])
        (argv,) = fake.calls
        assert "-v" not in argv and "--use-json-log" in argv

    async def test_changes_are_recorded_from_the_json_log(self, fake: FakeRclone) -> None:
        fake.answer(stderr="".join([
            json_log("Copied (new)", "new.txt", size=3),
            json_log("Copied (replaced existing)", "old.txt", size=5),
            json_log("Deleted", "gone.txt"),
            json_log("There was nothing to transfer", level="notice"),
        ]))
        recorder = ChangeRecorder(side="remote")
        await fake.service().sync("/a", "r:a", SyncDirection.PUSH, recorder=recorder)
        assert [(r.path, r.action, r.size_bytes, r.side) for r in recorder.rows] == [
            ("new.txt", "created", 3, "remote"),
            ("old.txt", "modified", 5, "remote"),
            ("gone.txt", "deleted", None, "remote"),
        ]

    async def test_partial_failure_keeps_what_was_recorded(self, fake: FakeRclone) -> None:
        fake.answer(rc=7, stderr=json_log("Copied (new)", "a.txt", size=1)
                    + json_log("Failed to copy: permission denied", "b.txt", level="error"))
        recorder = ChangeRecorder()
        with pytest.raises(RcloneError) as info:
            await fake.service().sync("/a", "r:a", SyncDirection.PUSH, recorder=recorder)
        assert "exit 7" in str(info.value) and "b.txt: Failed to copy: permission denied" in str(info.value)
        assert [r.path for r in recorder.rows] == ["a.txt"]


class TestBisyncCommand:
    async def test_command_line(self, fake: FakeRclone) -> None:
        await fake.service().bisync(
            "/data/docs", "r:docs", workdir="/wd", filters_file="/wd/filters.txt",
            recorder=BisyncRecorder(), rclone_args=["--max-delete", "5", "--bwlimit", "1M"],
            backup_dirs=("/data/docs/.omnisync-trash/t", "r:docs/.omnisync-trash/t"),
            resync=True, dry_run=True,
        )
        (argv,) = fake.calls
        assert argv[2] == "bisync"
        assert argv[argv.index("--workdir") + 1] == "/wd"
        assert argv[argv.index("--filters-file") + 1] == "/wd/filters.txt"
        assert {"--resilient", "--recover", "--dry-run", "--check-access"} <= set(argv)
        assert argv[argv.index("--check-filename") + 1] == SENTINEL_FILE
        assert argv[argv.index("--resync-mode") + 1] == "newer"
        assert argv[argv.index("--backup-dir1") + 1] == "/data/docs/.omnisync-trash/t"
        assert argv[argv.index("--backup-dir2") + 1] == "r:docs/.omnisync-trash/t"
        # the profile's absolute --max-delete is applied by OmniSync, not passed on
        assert [argv[i + 1] for i, a in enumerate(argv) if a == "--max-delete"] == ["100"]
        assert argv[argv.index("--bwlimit") + 1] == "1M"
        assert after_double_dash(argv) == ["/data/docs", "r:docs"]

    async def test_exclude_rules_are_filter_flags_outside_the_filters_file(self, fake: FakeRclone) -> None:
        """rclone applies --filter rules before the filters file (FilterFrom), and
        bisync hashes only the file: the rules win and need no resync."""
        await fake.service().bisync("/l", "r:x", workdir="/wd", filters_file="/f", recorder=BisyncRecorder(),
                                    exclude_rules=["- /a\\[1\\].txt", "- /a\\[1\\].txt/**"])
        (argv,) = fake.calls
        assert [a for a in argv if a.startswith("--filter=")] == ["--filter=- /a\\[1\\].txt",
                                                                   "--filter=- /a\\[1\\].txt/**"]
        assert argv.index("--filter=- /a\\[1\\].txt") < argv.index("--")

    async def test_minimal_run_has_no_optional_flags(self, fake: FakeRclone) -> None:
        await fake.service().bisync("/l", "r:x", workdir="/wd", filters_file="/f", recorder=BisyncRecorder(),
                                    check_access=False)
        (argv,) = fake.calls
        for flag in ("--check-access", "--resync", "--dry-run", "--backup-dir1"):
            assert flag not in argv

    async def test_failure_carries_the_critical_error(self, fake: FakeRclone) -> None:
        fake.answer(rc=2, stderr=json_log("Bisync critical error: path1 and path2 are the same", level="error"))
        recorder = BisyncRecorder()
        with pytest.raises(RcloneError, match="path1 and path2 are the same"):
            await fake.service().bisync("/l", "r:x", workdir="/wd", filters_file="/f", recorder=recorder)
        assert recorder.critical == "path1 and path2 are the same"


class TestCheckDiff:
    async def test_differences(self, fake: FakeRclone) -> None:
        fake.answer(rc=1, stdout="+ new.txt\n- only remote.txt\n* changed.txt\n=  same with spaces \n",
                    stderr="ERROR : 3 differences found\n")
        result = await fake.service().check_diff("/l", "r:x", rclone_filter=["- *.bak"])
        assert result == {
            "has_changes": True, "local_only": ["new.txt"], "remote_only": ["only remote.txt"],
            "differ": ["changed.txt"], "error": None,
        }
        (argv,) = fake.calls
        assert argv[2:5] == ["check", "--combined", "-"]
        assert f"--filter={TRASH_FILTER}" in argv and "--filter=- *.bak" in argv
        assert after_double_dash(argv) == ["/l", "r:x"]

    async def test_in_sync(self, fake: FakeRclone) -> None:
        fake.answer(stdout="= a.txt\n")
        result = await fake.service().check_diff("/l", "r:x")
        assert result["has_changes"] is False and result["error"] is None

    async def test_failure_is_an_error_not_an_empty_diff(self, fake: FakeRclone) -> None:
        fake.answer(rc=2, stderr="CRITICAL: Failed to create file system: didn't find section in config file\n")
        result = await fake.service().check_diff("/l", "r:x")
        assert result["has_changes"] is False
        assert result["error"] is not None and "exit 2" in result["error"]

    async def test_auth_and_rate_limit_failures_are_named(self, fake: FakeRclone) -> None:
        fake.answer(rc=3, stderr="oauth2: cannot fetch token: invalid_grant\n")
        assert (await fake.service().check_diff("/l", "r:x"))["error"].startswith("Authentication error")
        fake.answer(rc=3, stderr="googleapi: Error 403: Rate Limit Exceeded, rateLimitExceeded\n")
        assert (await fake.service().check_diff("/l", "r:x"))["error"].startswith("Rate-limited")

    async def test_listing_errors_besides_differences_are_errors(self, fake: FakeRclone) -> None:
        fake.answer(rc=1, stdout="+ a.txt\n", stderr="ERROR : 1 differences found\nERROR : 3 errors while checking\n")
        error = (await fake.service().check_diff("/l", "r:x"))["error"]
        assert error is not None and "errors besides the differences" in error

    async def test_differences_with_exit_0_are_not_trusted(self, fake: FakeRclone) -> None:
        fake.answer(rc=0, stdout="+ a.txt\n")
        assert "not trusting" in (await fake.service().check_diff("/l", "r:x"))["error"]

    async def test_timeout_is_an_error(self, fake: FakeRclone) -> None:
        fake.answer(sleep=30)
        started = time.monotonic()
        result = await fake.service().check_diff("/l", "r:x", timeout=0.5)
        assert time.monotonic() - started < 15
        assert "timed out" in result["error"]
        assert not pid_alive(fake.pids[0])

    async def test_missing_binary_is_an_error(self, fake: FakeRclone, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATH", "/nonexistent")
        result = await fake.service().check_diff("/l", "r:x")
        assert result["error"].startswith("Diff check failed")


class TestListings:
    async def test_lsjson(self, fake: FakeRclone) -> None:
        entries = [{"Path": "a.txt", "Size": 3, "IsDir": False}, {"Path": "d", "Size": -1, "IsDir": True}]
        fake.answer(stdout=json.dumps(entries))
        assert await fake.service().lsjson("r:x", rclone_filter=["- *.tmp"]) == entries
        (argv,) = fake.calls
        assert argv[2:4] == ["lsjson", "--recursive"]
        assert f"--filter={TRASH_FILTER}" in argv and "--filter=- *.tmp" in argv
        assert after_double_dash(argv) == ["r:x"]

    async def test_lsjson_garbage_is_an_error(self, fake: FakeRclone) -> None:
        fake.answer(stdout="not json")
        with pytest.raises(RcloneError, match="Failed to parse lsjson"):
            await fake.service().lsjson("r:x")

    async def test_lsjson_paths_lists_exactly_the_given_files(self, fake: FakeRclone) -> None:
        fake.answer(stdout=json.dumps([
            {"Path": "a b.txt", "Size": 1, "IsDir": False},
            {"Path": "sub", "Size": -1, "IsDir": True},
            {"Path": "sub/[x].md", "Size": 2, "IsDir": False},
        ]))
        result = await fake.service().lsjson_paths("r:x", ["/a b.txt", "sub/[x].md", "missing.txt"])
        assert set(result) == {"a b.txt", "sub/[x].md"}
        # one literal path per line, leading slash removed (no filter syntax)
        assert fake.files_from == ["a b.txt\nsub/[x].md\nmissing.txt\n"]

    async def test_lsjson_paths_of_a_missing_root_is_empty(self, fake: FakeRclone) -> None:
        fake.answer(rc=3, stderr="ERROR : error listing: directory not found\n")
        assert await fake.service().lsjson_paths("r:gone", ["a.txt"]) == {}
        assert await fake.service().lsjson_paths("r:gone", []) == {}

    async def test_lsjson_paths_auth_error_propagates(self, fake: FakeRclone) -> None:
        fake.answer(rc=1, stderr="Failed: 401 Unauthorized, directory not found\n")
        with pytest.raises(RcloneAuthError):
            await fake.service().lsjson_paths("r:x", ["a.txt"])

    async def test_lsjson_paths_other_errors_propagate(self, fake: FakeRclone) -> None:
        fake.answer(rc=1, stderr="ERROR : permission denied\n")
        with pytest.raises(RcloneError):
            await fake.service().lsjson_paths("r:x", ["a.txt"])
        fake.answer(stdout="{broken")
        with pytest.raises(RcloneError, match="Failed to parse"):
            await fake.service().lsjson_paths("r:x", ["a.txt"])

    async def test_list_dirs_and_top_level(self, fake: FakeRclone) -> None:
        fake.answer(stdout="docs/\nphotos/\nnote.txt\n")
        assert await fake.service().list_dirs("r:x") == ["docs", "photos"]
        assert await fake.service().list_top_level("r:x") == ["docs", "photos", "note.txt"]
        fake.answer(rc=3, stderr="ERROR : directory not found\n")
        assert await fake.service().list_dirs("r:gone") == []
        assert await fake.service().list_top_level("r:gone") == []
        fake.answer(rc=3, stderr="ERROR : permission denied\n")
        with pytest.raises(RcloneError):
            await fake.service().list_dirs("r:x")
        with pytest.raises(RcloneError):
            await fake.service().list_top_level("r:x")
        fake.answer(rc=1, stderr="invalid_grant\n")
        with pytest.raises(RcloneAuthError):
            await fake.service().list_dirs("r:x")
        with pytest.raises(RcloneAuthError):
            await fake.service().list_top_level("r:x")

    async def test_existing_items_lists_only_the_ruled_paths(self, fake: FakeRclone) -> None:
        fake.answer(stdout="clash.txt\nsub/\nsub/dir/\nsub/dir/x.txt\n")
        assert await fake.service().existing_items("r:x", ["+ /clash.txt", "+ /sub/dir/"]) == {
            "clash.txt", "sub/", "sub/dir/", "sub/dir/x.txt",
        }
        (argv,) = fake.calls
        assert argv[2:5] == ["lsf", "-R", "--filter-from"]
        assert after_double_dash(argv) == ["r:x"]
        # No rules: nothing to look for, no rclone call.
        assert await fake.service().existing_items("r:x", []) == set()
        assert len(fake.calls) == 1

    async def test_existing_items_of_a_missing_root_and_failures(self, fake: FakeRclone) -> None:
        fake.answer(rc=3, stderr="ERROR : error listing: directory not found\n")
        assert await fake.service().existing_items("r:gone", ["+ /a"]) == set()
        fake.answer(rc=1, stderr="Failed: 401 Unauthorized, directory not found\n")
        with pytest.raises(RcloneAuthError):
            await fake.service().existing_items("r:x", ["+ /a"])
        fake.answer(rc=1, stderr="ERROR : permission denied\n")
        with pytest.raises(RcloneError):
            await fake.service().existing_items("r:x", ["+ /a"])

    async def test_lsjson_passes_filter_flags(self, fake: FakeRclone) -> None:
        fake.answer(stdout="[]")
        assert await fake.service().lsjson("/l", rclone_filter=["- *.tmp"], rclone_args=["--exclude", "x"]) == []
        (argv,) = fake.calls
        assert "--filter=- *.tmp" in argv and argv[argv.index("--exclude") + 1] == "x"

    async def test_existing_paths_keeps_unusual_names(self, fake: FakeRclone) -> None:
        fake.answer(stdout="a.txt\nweird\rname.txt\n")
        assert await fake.service().existing_paths("r:x", ["a.txt", "weird\rname.txt", "b.txt"]) == {
            "a.txt", "weird\rname.txt",
        }

    async def test_copy_files_uses_a_raw_file_list(self, fake: FakeRclone) -> None:
        await fake.service().copy_files("/l", "r:x", ["/a.txt", "*.md"], backup_dir="r:x/.omnisync-trash/t",
                                        ignore_times=True)
        (argv,) = fake.calls
        assert argv[2:4] == ["copy", "--files-from-raw"]
        assert argv[argv.index("--backup-dir") + 1] == "r:x/.omnisync-trash/t"
        assert "--ignore-times" in argv
        assert after_double_dash(argv) == ["/l", "r:x"]
        assert fake.files_from == ["a.txt\n*.md\n"]

    async def test_copy_files_with_a_recorder_logs_as_json(self, fake: FakeRclone) -> None:
        fake.answer(stderr=json_log("Copied (new)", "a.txt", size=4))
        recorder = ChangeRecorder()
        await fake.service().copy_files("/l", "r:x", ["a.txt"], recorder=recorder)
        assert "--use-json-log" in fake.calls[0]
        assert [(r.path, r.action) for r in recorder.rows] == [("a.txt", "created")]

    async def test_about(self, fake: FakeRclone) -> None:
        fake.answer(stdout=json.dumps({"total": 100, "used": 40, "free": 60}))
        assert await fake.service().about("r") == {"total": 100, "used": 40, "free": 60, "trashed": None}
        assert fake.calls[0][2:] == ["about", "--json", "--", "r:"]


# ---------------------------------------------------------------------------
# Running rclone: failures, timeouts, stops
# ---------------------------------------------------------------------------


class TestRun:
    @pytest.mark.parametrize(("stderr", "error"), [
        ("oauth2: cannot fetch token: 400 Bad Request {\"error\": \"invalid_grant\"}", RcloneAuthError),
        ("HTTP error 429 (429 Too Many Requests)", RcloneRateLimitError),
        ("ERROR : something else", RcloneError),
    ])
    async def test_failures_are_classified(self, fake: FakeRclone, stderr: str, error: type) -> None:
        fake.answer(rc=1, stderr=stderr)
        with pytest.raises(error) as info:
            await fake.service().copyto("/l/a", "r:a")
        assert type(info.value) is error

    async def test_timeout_terminates_rclone(self, fake: FakeRclone) -> None:
        fake.answer(sleep=30)
        started = time.monotonic()
        with pytest.raises(RcloneError, match="timed out"):
            await fake.service(timeout=1).list_dirs("r:x")
        assert time.monotonic() - started < 15
        assert not pid_alive(fake.pids[0])

    async def test_cancel_terminates_rclone(self, fake: FakeRclone) -> None:
        fake.answer(sleep=30)
        task = asyncio.create_task(fake.service().copyto("/l/a", "r:a"))
        while not fake.pids:
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not pid_alive(fake.pids[0])

    async def test_cancelled_stream_terminates_rclone(self, fake: FakeRclone) -> None:
        fake.answer(sleep=30, stderr=json_log("Copied (new)", "a.txt"))
        recorder = ChangeRecorder()
        task = asyncio.create_task(fake.service().sync("/l", "r:x", SyncDirection.PUSH, recorder=recorder))
        while not fake.pids:
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not pid_alive(fake.pids[0])

    async def test_rclone_that_ignores_the_stop_signal_is_killed(self, fake: FakeRclone) -> None:
        fake.answer(sleep=30, ignore_term=True)
        service = fake.service()
        started = time.monotonic()
        with pytest.raises(RcloneError, match="timed out"):
            await service._stream(
                service._base_cmd() + ["lsf"], lambda _l: None, lambda _l: None, timeout=0.5, stop_grace=0.3,
            )
        assert time.monotonic() - started < 15
        assert not pid_alive(fake.pids[0])

    async def test_terminate_of_a_finished_process_is_a_no_op(self) -> None:
        await RcloneService._terminate(None)

    async def test_check_installed(self, fake: FakeRclone, monkeypatch: pytest.MonkeyPatch) -> None:
        assert await fake.service().check_installed() is True
        fake.answer(rc=1)
        assert await fake.service().check_installed() is False
        monkeypatch.setenv("PATH", "/nonexistent")
        assert await fake.service().check_installed() is False

    async def test_obscure_passes_the_secret_on_stdin(self, fake: FakeRclone) -> None:
        assert await fake.service().obscure("s3cret") == "OBSCURED-terc3s"
        assert all("s3cret" not in arg for argv in fake.calls for arg in argv)
        fake.answer(rc=1)
        with pytest.raises(RcloneError, match="obscure failed"):
            await fake.service().obscure("x")


# ---------------------------------------------------------------------------
# Remote configuration
# ---------------------------------------------------------------------------


class TestRemoteConfig:
    async def test_list_remotes_reads_types_from_the_config(self, fake: FakeRclone) -> None:
        fake.write_config({"gd": {"type": "drive"}, "box": {"type": "dropbox"}})
        remotes = await fake.service().list_remotes()
        assert [(r.name, r.type) for r in remotes] == [("gd", "drive"), ("box", "dropbox")]

    async def test_create_remote_writes_an_owner_only_config(self, fake: FakeRclone) -> None:
        token = '{"access_token":"a%b{c}","expiry":"2030-01-01T00:00:00Z"}'
        await fake.service().create_remote("gd", "drive", {"token": token, "scope": "drive"})
        cfg = fake.read_config()
        assert cfg.get("gd", "type") == "drive" and cfg.get("gd", "token") == token  # no %-interpolation
        assert stat.S_IMODE(fake.config.stat().st_mode) == 0o600
        assert fake.calls[-1][2:] == ["config", "show", "gd"]

    async def test_create_remote_rejects_bad_and_duplicate_names(self, fake: FakeRclone) -> None:
        fake.write_config({"gd": {"type": "drive"}})
        with pytest.raises(ValueError, match="alphanumeric"):
            await fake.service().create_remote("bad name;rm", "drive", {})
        with pytest.raises(ValueError, match="already exists"):
            await fake.service().create_remote("gd", "drive", {})

    async def test_create_remote_survives_an_unreadable_result(self, fake: FakeRclone) -> None:
        fake.answer(rc=1, stderr="Failed to load config")
        # listremotes fails too: the config shows no remotes, so only the check fails
        with pytest.raises(RcloneError):
            await fake.service().create_remote("gd", "drive", {})

    async def test_delete_remote(self, fake: FakeRclone) -> None:
        fake.write_config({"gd": {"type": "drive"}})
        with pytest.raises(ValueError, match="not found"):
            await fake.service().delete_remote("other")
        await fake.service().delete_remote("gd")
        assert not fake.read_config().has_section("gd")
        assert "gd" in (fake.config.parent / "rclone.conf.bak").read_text()  # the previous file

    async def test_config_changes_keep_one_backup(self, fake: FakeRclone) -> None:
        fake.write_config({"gd": {"type": "drive"}})
        await fake.service().create_remote("box", "dropbox", {})
        await fake.service().create_remote("one", "onedrive", {})
        backup = configparser.RawConfigParser()
        backup.read(fake.config.parent / "rclone.conf.bak")
        assert backup.sections() == ["gd", "box"]
        assert stat.S_IMODE((fake.config.parent / "rclone.conf.bak").stat().st_mode) == 0o600
        assert sorted(p.name for p in fake.config.parent.glob("*rclone.conf*")) == ["rclone.conf", "rclone.conf.bak"]

    async def test_a_failed_write_leaves_the_config_intact(
        self, fake: FakeRclone, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A full disk halfway through the write: rclone.conf keeps every remote."""
        fake.write_config({"gd": oauth_remote("drive"), "box": {"type": "dropbox"}})
        before = fake.config.read_bytes()
        real_write = os.write
        writes: list[int] = []

        def disk_full(fd: int, data: bytes) -> int:
            writes.append(fd)
            if len(writes) == 1:
                return real_write(fd, bytes(data[: len(data) // 2]))
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(os, "write", disk_full)
        with pytest.raises(OSError, match="No space left"):
            await fake.service().create_remote("new", "drive", {"token": "x" * 1000})
        monkeypatch.undo()

        assert len(writes) >= 2
        assert fake.config.read_bytes() == before
        assert not list(fake.config.parent.glob(".rclone.conf*"))  # no temporary file left behind

    async def test_a_failed_token_save_leaves_the_config_intact(
        self, fake: FakeRclone, provider: FakeProvider, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake.write_config({"gd": oauth_remote("drive"), "box": {"type": "dropbox"}})
        before = fake.config.read_bytes()
        provider.on("POST", "https://oauth2.googleapis.com/token",
                    lambda req: httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600}))

        def crash(fd: int, data: bytes) -> int:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(os, "write", crash)
        service = fake.service()
        await service._refresh_token("gd", "drive", json.loads(oauth_remote("drive")["token"]), service._read_config())
        monkeypatch.undo()
        assert fake.config.read_bytes() == before
        assert not list(fake.config.parent.glob(".rclone.conf*"))  # no temporary file left behind

    async def test_concurrent_changes_do_not_lose_each_other(
        self, fake: FakeRclone, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Token refreshes and remote changes at once: every change survives."""
        fake.write_config({f"r{i}": {"type": "drive", "token": "old"} for i in range(4)})
        service = fake.service()
        real_read = service._read_config

        def slow_read() -> configparser.RawConfigParser:
            config = real_read()
            time.sleep(0.05)  # widen the read-modify-write window
            return config

        monkeypatch.setattr(service, "_read_config", slow_read)
        await asyncio.gather(
            *(service._update_config(lambda cfg, i=i: cfg.set(f"r{i}", "token", f"new{i}")) for i in range(4)),
            *(service._update_config(lambda cfg, i=i: cfg.__setitem__(f"n{i}", {"type": "local"})) for i in range(4)),
            service._update_config(lambda cfg: cfg.remove_section("r0")),
        )
        cfg = fake.read_config()
        assert sorted(cfg.sections()) == ["n0", "n1", "n2", "n3", "r1", "r2", "r3"]
        assert [cfg.get(f"r{i}", "token") for i in (1, 2, 3)] == ["new1", "new2", "new3"]

    async def test_a_refresh_does_not_bring_back_a_deleted_remote(self, fake: FakeRclone) -> None:
        fake.write_config({"gd": oauth_remote("drive")})
        service = fake.service()
        await service.delete_remote("gd")
        # What _refresh_token saves after its (slow) provider call returns:
        with pytest.raises(configparser.NoSectionError):
            await service._update_config(lambda cfg: cfg.set("gd", "token", "{}"))
        assert not fake.read_config().has_section("gd")


# ---------------------------------------------------------------------------
# Provider API: remote checks and token refresh
# ---------------------------------------------------------------------------


class FakeProvider:
    """Answers the provider HTTP calls RcloneService makes (httpx.MockTransport)."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.handlers: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = {}
        self.files: dict[str, bytes] = {}  # path -> content, for the upload tests
        self.folders: dict[tuple[str, str], str] = {}  # (parent id, name) -> id

    def on(self, method: str, url_prefix: str, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handlers[(method, url_prefix)] = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        for (method, prefix), handler in self.handlers.items():
            if request.method == method and url.startswith(prefix):
                return handler(request)
        return httpx.Response(404, json={"error": "unexpected request"})

    def urls(self) -> list[str]:
        return [f"{r.method} {r.url.host}{r.url.path}" for r in self.requests]


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    fake_provider = FakeProvider()
    real_client = httpx.AsyncClient

    def client(**kwargs) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(fake_provider), **kwargs)

    monkeypatch.setattr("backend.services.rclone.auth.httpx.AsyncClient", client)
    return fake_provider


def oauth_remote(remote_type: str, **token: object) -> dict[str, str]:
    """An OAuth remote with the user's own (public client) app."""
    base = {"access_token": "old-access", "refresh_token": "the-refresh", "token_type": "Bearer"}
    return {"type": remote_type, "client_id": "own-app", "token": json.dumps({**base, **token})}


def builtin_app_remote(remote_type: str, **token: object) -> dict[str, str]:
    """A remote made with rclone's built-in app: no client_id in rclone.conf."""
    section = oauth_remote(remote_type, **token)
    del section["client_id"]
    return section


def expiry(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class TestCheckRemote:
    async def test_unknown_remote(self, fake: FakeRclone) -> None:
        fake.write_config({"gd": {"type": "drive"}})
        assert await fake.service().check_remote("nope") is False

    @pytest.mark.parametrize(("remote_type", "url", "method"), [
        ("drive", "https://www.googleapis.com/drive/v3/about", "GET"),
        ("dropbox", "https://api.dropboxapi.com/2/users/get_current_account", "POST"),
        ("onedrive", "https://graph.microsoft.com/v1.0/me/drive", "GET"),
    ])
    async def test_oauth_remotes_are_checked_through_the_provider_api(
        self, fake: FakeRclone, provider: FakeProvider, remote_type: str, url: str, method: str,
    ) -> None:
        fake.write_config({"r": oauth_remote(remote_type)})
        provider.on(method, url, lambda req: httpx.Response(200, json={}))
        assert await fake.service().check_remote("r") is True
        (request,) = provider.requests
        assert request.headers["Authorization"] == "Bearer old-access"
        assert fake.calls == []  # rclone itself never ran

    async def test_expired_access_token_is_refreshed_and_saved(self, fake: FakeRclone, provider: FakeProvider) -> None:
        fake.write_config({"gd": {**oauth_remote("drive"), "client_id": "my-id", "client_secret": "my-secret"}})
        provider.on("GET", "https://www.googleapis.com/drive/v3/about", lambda req: httpx.Response(401))
        provider.on("POST", "https://oauth2.googleapis.com/token",
                    lambda req: httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600}))

        assert await fake.service().check_remote("gd") is True

        refresh = provider.requests[-1]
        form = dict(x.split("=", 1) for x in refresh.content.decode().split("&"))
        assert form["grant_type"] == "refresh_token" and form["refresh_token"] == "the-refresh"
        assert form["client_id"] == "my-id" and form["client_secret"] == "my-secret"
        saved = json.loads(fake.read_config().get("gd", "token"))
        assert saved["access_token"] == "new-access"
        assert saved["refresh_token"] == "the-refresh"  # kept when the provider sends none
        assert saved["expiry"]
        assert stat.S_IMODE(fake.config.stat().st_mode) == 0o600

    async def test_public_client_refresh_sends_no_secret(self, fake: FakeRclone, provider: FakeProvider) -> None:
        fake.write_config({"box": oauth_remote("dropbox")})
        provider.on("POST", "https://api.dropboxapi.com/2/users/get_current_account", lambda req: httpx.Response(401))
        provider.on("POST", "https://api.dropboxapi.com/oauth2/token",
                    lambda req: httpx.Response(200, json={"access_token": "new-access"}))
        assert await fake.service().check_remote("box") is True
        form = dict(x.split("=", 1) for x in provider.requests[-1].content.decode().split("&"))
        assert form["client_id"] == "own-app" and "client_secret" not in form

    async def test_builtin_app_remote_is_left_to_rclone(self, fake: FakeRclone, provider: FakeProvider) -> None:
        """An expired token of rclone's built-in app: OmniSync has no
        credentials to refresh it, so rclone checks (and refreshes) it."""
        fake.write_config({"gd": builtin_app_remote("drive")})
        provider.on("GET", "https://www.googleapis.com/drive/v3/about", lambda req: httpx.Response(401))
        service = fake.service()
        assert await service.check_remote("gd") is True
        assert provider.urls() == ["GET www.googleapis.com/drive/v3/about"]  # no refresh attempted
        (argv,) = fake.calls
        assert argv[2:5] == ["lsd", "--max-depth", "0"] and argv[-1] == "gd:"
        cfg = fake.read_config()
        assert not cfg.has_option("gd", "client_id")  # no built-in ID was added
        assert json.loads(cfg.get("gd", "token"))["access_token"] == "old-access"

    async def test_failed_refresh_means_unreachable(self, fake: FakeRclone, provider: FakeProvider) -> None:
        fake.write_config({"gd": oauth_remote("drive")})
        provider.on("GET", "https://www.googleapis.com/drive/v3/about", lambda req: httpx.Response(401))
        provider.on("POST", "https://oauth2.googleapis.com/token", lambda req: httpx.Response(400, json={}))
        assert await fake.service().check_remote("gd") is False
        assert json.loads(fake.read_config().get("gd", "token"))["access_token"] == "old-access"

    async def test_provider_errors_mean_unreachable(self, fake: FakeRclone, provider: FakeProvider) -> None:
        fake.write_config({"gd": oauth_remote("drive")})
        provider.on("GET", "https://www.googleapis.com/drive/v3/about", lambda req: httpx.Response(503))
        assert await fake.service().check_remote("gd") is False

        def timeout(req: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=req)

        provider.on("GET", "https://www.googleapis.com/drive/v3/about", timeout)
        assert await fake.service().check_remote("gd") is False

        def broken(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route", request=req)

        provider.on("GET", "https://www.googleapis.com/drive/v3/about", broken)
        assert await fake.service().check_remote("gd") is False

    @pytest.mark.parametrize("token", ["not json", json.dumps({"refresh_token": "r"})])
    async def test_unusable_tokens_mean_unreachable(self, fake: FakeRclone, provider: FakeProvider, token: str) -> None:
        fake.write_config({"gd": {"type": "drive", "token": token}})
        assert await fake.service().check_remote("gd") is False
        assert provider.requests == []

    async def test_other_remotes_are_checked_with_rclone(self, fake: FakeRclone) -> None:
        fake.write_config({"s3": {"type": "s3", "provider": "Minio"}})
        assert await fake.service().check_remote("s3") is True
        (argv,) = fake.calls
        assert argv[2:5] == ["lsd", "--max-depth", "0"]
        assert argv[-2:] == ["--", "s3:"]  # the remote can never be read as an option
        fake.answer(rc=1, stderr="ERROR : bucket not found")
        assert await fake.service().check_remote("s3") is False


class TestTokenRefreshBeforeRclone:
    async def test_expired_token_is_refreshed_before_the_sync(self, fake: FakeRclone, provider: FakeProvider) -> None:
        fake.write_config({"box": oauth_remote("dropbox", expiry=expiry(-timedelta(hours=1)))})
        provider.on("POST", "https://api.dropboxapi.com/oauth2/token",
                    lambda req: httpx.Response(200, json={"access_token": "fresh", "refresh_token": "r2"}))

        await fake.service().sync("/l", "box:docs", SyncDirection.PUSH)

        assert provider.urls() == ["POST api.dropboxapi.com/oauth2/token"]
        saved = json.loads(fake.read_config().get("box", "token"))
        assert (saved["access_token"], saved["refresh_token"]) == ("fresh", "r2")
        assert len(fake.calls) == 1  # then rclone ran

    async def test_token_expiring_soon_is_refreshed(self, fake: FakeRclone, provider: FakeProvider) -> None:
        fake.write_config({"od": oauth_remote("onedrive", expiry=expiry(timedelta(minutes=2)))})
        provider.on("POST", "https://login.microsoftonline.com/", lambda req: httpx.Response(200, json={"access_token": "n"}))
        await fake.service().check_diff("/l", "od:x")
        assert provider.urls() == ["POST login.microsoftonline.com/common/oauth2/v2.0/token"]

    @pytest.mark.parametrize("section", [
        oauth_remote("drive", expiry=expiry(timedelta(days=1))),  # still valid
        {"type": "s3"},  # no OAuth
        {"type": "drive", "token": "garbage"},
    ])
    async def test_no_refresh_needed(self, fake: FakeRclone, provider: FakeProvider, section: dict) -> None:
        fake.write_config({"r": section})
        await fake.service().bisync("/l", "r:x", workdir="/wd", filters_file="/f", recorder=BisyncRecorder())
        assert provider.requests == []

    @pytest.mark.parametrize("remote_type", ["drive", "dropbox", "onedrive"])
    async def test_builtin_app_remote_is_not_refreshed(
        self, fake: FakeRclone, provider: FakeProvider, remote_type: str,
    ) -> None:
        """rclone refreshes a built-in-app remote itself when the sync runs."""
        fake.write_config({"r": builtin_app_remote(remote_type, expiry=expiry(-timedelta(hours=1)))})
        before = fake.config.read_bytes()
        await fake.service().sync("/l", "r:docs", SyncDirection.PUSH)
        assert provider.requests == []
        assert fake.config.read_bytes() == before
        assert len(fake.calls) == 1  # rclone ran

    async def test_unparseable_expiry_is_refreshed(self, fake: FakeRclone, provider: FakeProvider) -> None:
        fake.write_config({"gd": oauth_remote("drive", expiry="yesterday-ish")})
        provider.on("POST", "https://oauth2.googleapis.com/token", lambda req: httpx.Response(200, json={"access_token": "n"}))
        await fake.service().sync("gd:x", "/l", SyncDirection.PULL)
        assert provider.urls() == ["POST oauth2.googleapis.com/token"]


# ---------------------------------------------------------------------------
# The sync test (write, upload, verify, clean up)
# ---------------------------------------------------------------------------


def serve_dropbox(provider: FakeProvider) -> None:
    def upload(req: httpx.Request) -> httpx.Response:
        provider.files[json.loads(req.headers["Dropbox-API-Arg"])["path"]] = req.content
        return httpx.Response(200, json={})

    def metadata(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if json.loads(req.content)["path"] in provider.files else 409, json={})

    def delete(req: httpx.Request) -> httpx.Response:
        provider.files.pop(json.loads(req.content)["path"], None)
        return httpx.Response(200, json={})

    provider.on("POST", "https://content.dropboxapi.com/2/files/upload", upload)
    provider.on("POST", "https://api.dropboxapi.com/2/files/get_metadata", metadata)
    provider.on("POST", "https://api.dropboxapi.com/2/files/delete_v2", delete)


def serve_onedrive(provider: FakeProvider) -> None:
    prefix = "https://graph.microsoft.com/v1.0/me/drive/root:/"

    def path_of(req: httpx.Request) -> str:
        return str(req.url)[len(prefix):].removesuffix(":/content")

    def upload(req: httpx.Request) -> httpx.Response:
        provider.files[path_of(req)] = req.content
        return httpx.Response(201, json={})

    provider.on("PUT", prefix, upload)
    provider.on("GET", prefix, lambda req: httpx.Response(200 if path_of(req) in provider.files else 404))
    provider.on("DELETE", prefix, lambda req: (provider.files.pop(path_of(req), None), httpx.Response(204))[1])


def serve_drive(provider: FakeProvider) -> None:
    """Folders by (parent, name); files by (folder, name); ids are counters."""
    ids = iter(range(1, 1000))

    def query(req: httpx.Request) -> tuple[str, str]:
        q = req.url.params["q"]
        name = q.split("name='", 1)[1].split("'", 1)[0]
        parent = q.split("and '", 1)[1].split("'", 1)[0]
        return parent, name

    def search(req: httpx.Request) -> httpx.Response:
        parent, name = query(req)
        if "mimeType='application/vnd.google-apps.folder'" in req.url.params["q"]:
            found = provider.folders.get((parent, name))
        else:
            found = next((k for k, v in provider.files.items() if k == f"{parent}/{name}"), None)
        return httpx.Response(200, json={"files": [{"id": found}] if found else []})

    def create_folder(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        folder_id = f"folder{next(ids)}"
        provider.folders[(body["parents"][0], body["name"])] = folder_id
        return httpx.Response(200, json={"id": folder_id})

    def upload(req: httpx.Request) -> httpx.Response:
        content = req.content.decode(errors="replace")
        metadata = json.loads(content.split("application/json\r\n\r\n", 1)[1].split("\r\n", 1)[0])
        key = f"{metadata['parents'][0]}/{metadata['name']}"
        provider.files[key] = b"x"
        return httpx.Response(200, json={"id": key})

    def delete(req: httpx.Request) -> httpx.Response:
        provider.files.pop(req.url.path.removeprefix("/drive/v3/files/"), None)
        return httpx.Response(204)

    provider.on("GET", "https://www.googleapis.com/drive/v3/files?", search)
    provider.on("POST", "https://www.googleapis.com/drive/v3/files", create_folder)
    provider.on("POST", "https://www.googleapis.com/upload/drive/v3/files", upload)
    provider.on("DELETE", "https://www.googleapis.com/drive/v3/files/", delete)


class TestSyncTest:
    @pytest.mark.parametrize(("remote_type", "serve"), [
        ("dropbox", serve_dropbox), ("onedrive", serve_onedrive), ("drive", serve_drive),
    ])
    async def test_round_trip_through_the_provider_api(
        self, fake: FakeRclone, provider: FakeProvider, tmp_path: Path, remote_type: str, serve,
    ) -> None:
        # the drive handlers match the upload URL before the plain files URL
        provider.handlers.clear()
        serve(provider)
        fake.write_config({"r": oauth_remote(remote_type)})
        local = tmp_path / "local"

        result = await fake.service().test_sync(str(local), "r:backup/docs")

        assert result == {"success": True, "error": None, "steps": [
            {"step": "local_write", "ok": True}, {"step": "remote_upload", "ok": True},
            {"step": "remote_verify", "ok": True}, {"step": "remote_cleanup", "ok": True},
            {"step": "local_cleanup", "ok": True},
        ]}
        assert provider.files == {}  # cleaned up on the remote ...
        assert list(local.iterdir()) == []  # ... and locally
        assert fake.calls == []

    async def test_drive_folders_are_reused(self, fake: FakeRclone, provider: FakeProvider, tmp_path: Path) -> None:
        serve_drive(provider)
        fake.write_config({"gd": oauth_remote("drive")})
        await fake.service().test_sync(str(tmp_path), "gd:a/b")
        await fake.service().test_sync(str(tmp_path), "gd:a/b")
        assert sorted(provider.folders) == [("folder1", "b"), ("root", "a")]

    async def test_failed_upload_stops_and_cleans_up(self, fake: FakeRclone, provider: FakeProvider, tmp_path: Path) -> None:
        provider.on("POST", "https://content.dropboxapi.com/2/files/upload", lambda req: httpx.Response(507))
        fake.write_config({"box": oauth_remote("dropbox")})
        local = tmp_path / "local"
        result = await fake.service().test_sync(str(local), "box:docs")
        assert result["success"] is False and result["error"] == "Failed to upload test file to remote"
        assert list(local.iterdir()) == []

    async def test_drive_folder_errors_fail_the_upload(self, fake: FakeRclone, provider: FakeProvider, tmp_path: Path) -> None:
        provider.on("GET", "https://www.googleapis.com/drive/v3/files?", lambda req: httpx.Response(403))
        fake.write_config({"gd": oauth_remote("drive")})
        result = await fake.service().test_sync(str(tmp_path), "gd:docs")
        assert result["success"] is False

    async def test_other_remotes_use_rclone(self, fake: FakeRclone, tmp_path: Path) -> None:
        fake.write_config({"s3": {"type": "s3"}})
        result = await fake.service().test_sync(str(tmp_path), "s3:bucket/docs")
        assert result["success"] is True
        subcommands = [argv[2] for argv in fake.calls]
        assert subcommands == ["copyto", "lsf", "deletefile"]
        assert all(argv[3] == "--" for argv in fake.calls)  # remote specs after "--"
        assert fake.calls[0][-1].startswith("s3:bucket/docs/.omnisync-test-")

    async def test_rclone_upload_failure(self, fake: FakeRclone, tmp_path: Path) -> None:
        fake.write_config({"s3": {"type": "s3"}})
        fake.answer(rc=1, stderr="ERROR : AccessDenied")
        local = tmp_path / "local"
        result = await fake.service().test_sync(str(local), "s3:bucket")
        assert result["success"] is False
        assert [s["step"] for s in result["steps"]] == ["local_write", "remote_upload"]
        assert list(local.iterdir()) == []

    async def test_invalid_remote_and_unwritable_folder(self, fake: FakeRclone, tmp_path: Path) -> None:
        result = await fake.service().test_sync(str(tmp_path), "no-colon")
        assert result["success"] is False and "expected 'name:path'" in result["error"]
        blocker = tmp_path / "file"
        blocker.write_text("x")
        result = await fake.service().test_sync(str(blocker / "sub"), "r:x")
        assert result["success"] is False and result["error"].startswith("Cannot write to local directory")


# ---------------------------------------------------------------------------
# Output parsing edge cases
# ---------------------------------------------------------------------------


class TestParsing:
    def test_auth_url_and_token(self) -> None:
        stderr = "NOTICE: Please go to the following link: http://127.0.0.1:53682/auth?state=x.\n"
        assert parse_auth_url(stderr) == "http://127.0.0.1:53682/auth?state=x"
        assert parse_auth_url("no link here") is None
        stdout = 'Paste the following\n{"access_token":"a","token_type":"Bearer"}\n<---End paste\n'
        assert parse_auth_token(stdout) == '{"access_token":"a","token_type":"Bearer"}'
        assert parse_auth_token('{"access_token": broken\n') is None

    def test_readable_stderr(self) -> None:
        stderr = "\n".join([
            "{not json",
            json.dumps({"level": "info", "msg": "Copied (new)", "object": "a"}),
            json.dumps({"level": "error", "msg": " failed ", "object": "b"}),
            json.dumps({"level": "notice", "msg": "no object"}),
            "",
            "plain line",
        ])
        assert readable_stderr(stderr) == "{not json\nb: failed\nno object\nplain line"
        assert readable_stderr("\n".join(f"l{i}" for i in range(30)), max_lines=2) == "l28\nl29"

    def test_change_recorder_ignores_what_is_not_a_change(self) -> None:
        rec = ChangeRecorder()
        for raw in ["", "   ", "{broken", "NOTICE: plain", json.dumps({"level": "info", "msg": "Copied (new)"})]:
            rec.feed(raw)
        assert rec.total == 0 and rec.rows == []
        assert list(rec.other_lines) == ["{broken", "NOTICE: plain"]

    def test_bisync_recorder_non_json_and_odd_lines(self) -> None:
        rec = BisyncRecorder()
        for raw in ["2026/09/28 NOTICE: plain text", "{broken"]:
            rec.feed(raw)
        assert list(rec.other_lines) == ["2026/09/28 NOTICE: plain text", "{broken"]
        assert rec.total == 0

    def test_bisync_critical_error_alone_is_the_failure_text(self) -> None:
        rec = BisyncRecorder()
        rec.feed(json.dumps({"level": "notice", "msg": "Bisync critical error: all files were changed"}))
        assert rec.failure_text() == "all files were changed"
