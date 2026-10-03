"""Strict parsing of rclone output (audit finding DS-15) and change recording (DS-11).

Unit tests on recorded rclone 1.75 output; test_engine_hardening_integration.py
runs the same paths against the real binary.
"""

from __future__ import annotations

import json

import pytest

from backend.exceptions import RcloneAuthError, RcloneError, RcloneRateLimitError
from backend.services.rclone import (
    ChangeRecorder,
    CheckOutput,
    _is_auth_error,
    _is_rate_limit_error,
    classify_failure,
    json_log_args,
)


def check(lines: list[str], rc: int, stderr: str = "") -> tuple[CheckOutput, str | None]:
    parsed = CheckOutput()
    for line in lines:
        parsed.feed(line)
    return parsed, parsed.error(rc, stderr)


class TestCheckOutput:
    def test_differences_with_exit_1_are_trusted(self) -> None:
        parsed, error = check(["+ a.txt", "- b.txt", "* c.txt", "= d.txt"], 1)
        assert error is None
        assert (parsed.local_only, parsed.remote_only, parsed.differ) == (["a.txt"], ["b.txt"], ["c.txt"])

    def test_error_records_are_errors_not_in_sync(self) -> None:
        _, error = check(["= a.txt", "! unreadable.txt"], 1)
        assert error is not None and "unreadable.txt" in error

    def test_exit_1_without_differences_is_an_error(self) -> None:
        # e.g. a remote that is not configured: exit 1, empty stdout
        _, error = check([], 1, 'CRITICAL: Failed to create file system for "zz:": didn\'t find section')
        assert error is not None and "exit 1" in error

    @pytest.mark.parametrize("rc", [2, 3, 6, 7])
    def test_other_exit_codes_are_errors(self, rc: int) -> None:
        _, error = check(["= a.txt"], rc, "ERROR : dir: permission denied")
        assert error is not None and f"exit {rc}" in error

    def test_names_keep_leading_and_trailing_spaces(self) -> None:
        parsed, error = check(["+  lead.txt ", "*  both  "], 1)
        assert error is None
        assert parsed.local_only == [" lead.txt "]
        assert parsed.differ == [" both  "]

    def test_unparseable_line_fails_the_check(self) -> None:
        # a name with a line break arrives as two lines
        _, error = check(["+ first part", "second part.txt"], 1)
        assert error is not None and "line breaks" in error

    def test_differences_with_exit_0_are_not_trusted(self) -> None:
        _, error = check(["+ a.txt"], 0)
        assert error is not None

    def test_unexplained_errors_fail_the_check(self) -> None:
        stderr = "NOTICE: remote: 1 differences found\nNOTICE: remote: 4 errors while checking"
        _, error = check(["+ a.txt"], 1, stderr)
        assert error is not None

    def test_match_lines_are_only_counted(self) -> None:
        parsed, error = check([f"= f{i}" for i in range(1000)], 0)
        assert error is None and parsed.matched == 1000 and not parsed.has_differences


class TestFailureClassification:
    @pytest.mark.parametrize("stderr", [
        "googleapi: Error 403: User Rate Limit Exceeded. Rate of requests for user exceed configured "
        "project quota., userRateLimitExceeded",
        "googleapi: Error 403: Rate Limit Exceeded, rateLimitExceeded",
        "HTTP error 403 (403 Forbidden) returned body: {\"reason\": \"rateLimitExceeded\"}",
        "HTTP error 429 (429 Too Many Requests)",
    ])
    def test_drive_rate_limits_are_not_auth_errors(self, stderr: str) -> None:
        assert _is_rate_limit_error(stderr)
        assert not _is_auth_error(stderr)
        assert isinstance(classify_failure(1, stderr), RcloneRateLimitError)

    def test_real_auth_errors_stay_auth_errors(self) -> None:
        stderr = 'oauth2: cannot fetch token: 400 Bad Request Response: {"error": "invalid_grant"}'
        exc = classify_failure(1, stderr)
        assert isinstance(exc, RcloneAuthError)
        assert not isinstance(exc, RcloneRateLimitError)

    def test_generic_failure(self) -> None:
        exc = classify_failure(3, "ERROR : directory not found")
        assert type(exc) is RcloneError and "exit 3" in str(exc)

    def test_json_log_stderr_becomes_readable(self) -> None:
        stderr = "\n".join(json.dumps(e) for e in [
            {"level": "info", "msg": "Copied (new)", "object": "a.txt"},
            {"level": "error", "msg": "Got fatal error on delete: --max-delete threshold reached", "object": "x"},
        ])
        text = str(classify_failure(7, stderr))
        assert "x: Got fatal error on delete: --max-delete threshold reached" in text
        assert "Copied" not in text


def log(msg: str, obj: str | None = None, level: str = "info", **extra: object) -> str:
    entry: dict[str, object] = {"time": "2026-09-27T19:36:52+02:00", "level": level, "msg": msg}
    if obj is not None:
        entry.update(object=obj, objectType="*local.Object")
    entry.update(extra)
    return json.dumps(entry)


class TestChangeRecorder:
    def test_backup_dir_sequence(self) -> None:
        """rclone 1.75 sync with --backup-dir, as captured from the real binary."""
        rec = ChangeRecorder()
        for line in [
            log("Copied (new)", "new.txt", size=1),
            log("Moved (server-side)", "mod.txt"),
            log("Copied (new)", "mod.txt", size=2),
            log("Moved (server-side)", "del.txt"),
            log("Moved into backup dir", "del.txt"),
            log("Updated modification time in destination", "same.txt"),
            json.dumps({"level": "info", "msg": "Set directory modification time", "object": "sub",
                        "objectType": "string"}),
            json.dumps({"level": "info", "msg": "\nTransferred: ...", "stats": {"transfers": 2}}),
        ]:
            rec.feed(line)
        assert [(r.path, r.action, r.size_bytes) for r in rec.rows] == [
            ("new.txt", "created", 1), ("mod.txt", "modified", 2), ("del.txt", "deleted", None),
        ]
        assert rec.total == 3

    def test_plain_sequence(self) -> None:
        rec = ChangeRecorder()
        rec.feed(log("Copied (replaced existing)", "mod.txt", size=2))
        rec.feed(log("Deleted", "del.txt"))
        assert [(r.path, r.action) for r in rec.rows] == [("mod.txt", "modified"), ("del.txt", "deleted")]

    def test_copy_and_delete_backup_fallback(self) -> None:
        rec = ChangeRecorder()
        for line in [
            log("Copied (server-side copy)", "a.txt"), log("Deleted", "a.txt"), log("Copied (new)", "a.txt"),
            log("Copied (server-side copy)", "b.txt"), log("Deleted", "b.txt"), log("Moved into backup dir", "b.txt"),
        ]:
            rec.feed(line)
        assert [(r.path, r.action) for r in rec.rows] == [("a.txt", "modified"), ("b.txt", "deleted")]

    def test_rows_are_capped_but_all_changes_counted(self) -> None:
        rec = ChangeRecorder(max_rows=10)
        for i in range(25_000):
            rec.feed(log("Copied (new)", f"f{i}", size=i))
        assert len(rec.rows) == 10 and rec.total == 25_000 and rec.truncated

    def test_errors_are_kept_bounded(self) -> None:
        rec = ChangeRecorder()
        for i in range(100):
            rec.feed(log("Failed to copy: permission denied", f"f{i}", level="error"))
        rec.feed("CRITICAL: Can't set -v and --log-level")
        assert len(rec.errors) == 20
        assert "f99: Failed to copy: permission denied" in rec.failure_text()
        assert "Can't set -v" in rec.failure_text()
        assert rec.total == 0


class TestJsonLogArgs:
    def test_adds_verbose_by_default(self) -> None:
        assert json_log_args([]) == ["--use-json-log", "--stats", "1s", "--stats-log-level", "NOTICE", "-v"]

    @pytest.mark.parametrize("own", [["-v"], ["-vv"], ["--log-level", "DEBUG"], ["--log-level=ERROR"], ["-q"]])
    def test_never_conflicts_with_the_profile_log_level(self, own: list[str]) -> None:
        assert "-v" not in json_log_args(own)
