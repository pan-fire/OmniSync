"""Comparing two folders with rclone check: the strict output parser and the commands."""

from __future__ import annotations

import re
from collections import deque

from backend.exceptions import RcloneError
from backend.services.rclone.common import CHECK_TIMEOUT, TRASH_FILTER, logger
from backend.services.rclone.errors import _is_auth_error, _is_rate_limit_error, readable_stderr, redact_secrets
from backend.services.rclone.process import RcloneBase

_CHECK_ERRORS_RE = re.compile(r"(\d+) errors while checking")
_CHECK_DIFFERENCES_RE = re.compile(r"(\d+) differences found")


class CheckOutput:
    """Strict, streaming parser for ``rclone check --combined -`` output.

    Each record is ``<marker> <path>``: ``+`` only in the source (local),
    ``-`` only in the destination (remote), ``*`` differs, ``=`` matches,
    ``!`` could not be compared (an error). The path is taken verbatim:
    leading and trailing spaces are legal in file names.

    rclone check exits 0 when everything matches and 1 both when it found
    differences and for many errors, so ``error()`` accepts exit 1 as
    "differences" only when every line parsed, no ``!`` record appeared and
    at least one difference was listed.
    """

    _KEEP = 10  # sample paths/lines kept for messages

    def __init__(self) -> None:
        self.local_only: list[str] = []
        self.remote_only: list[str] = []
        self.differ: list[str] = []
        self.matched = 0
        self.errored: list[str] = []
        self.error_count = 0
        self.bad_lines: list[str] = []
        self.bad_count = 0

    def feed(self, line: str) -> None:
        if len(line) >= 3 and line[1] == " " and line[0] in "+-*=!":
            marker, path = line[0], line[2:]
            if marker == "+":
                self.local_only.append(path)
            elif marker == "-":
                self.remote_only.append(path)
            elif marker == "*":
                self.differ.append(path)
            elif marker == "=":
                self.matched += 1
            else:
                self.error_count += 1
                if len(self.errored) < self._KEEP:
                    self.errored.append(path)
            return
        # Anything else (e.g. the tail of a file name containing a line break)
        self.bad_count += 1
        if len(self.bad_lines) < self._KEEP:
            self.bad_lines.append(line)

    @property
    def has_differences(self) -> bool:
        return bool(self.local_only or self.remote_only or self.differ)

    def error(self, return_code: int, stderr: str) -> str | None:
        """None when the output is a trustworthy comparison, else why not."""
        if self.error_count:
            sample = ", ".join(repr(p) for p in self.errored)
            return (
                f"rclone could not compare {self.error_count} file(s) ({sample}), e.g. unreadable "
                "files or failed hashing. Not treating them as in sync."
            )
        if self.bad_count:
            return (
                f"Unexpected rclone check output ({self.bad_count} line(s), e.g. {self.bad_lines[0]!r}). "
                "File names containing line breaks cannot be compared."
            )
        detail = readable_stderr(stderr)
        if return_code not in (0, 1) or (return_code == 1 and not self.has_differences):
            if _is_rate_limit_error(stderr):
                return f"Rate-limited by the provider (exit {return_code}): {detail[:500]}"
            if _is_auth_error(stderr):
                return f"Authentication error: {detail[:500]}"
            return f"rclone check failed (exit {return_code}): {detail[:500]}"
        if return_code == 0 and self.has_differences:
            return "rclone check reported differences but exited 0; not trusting the result."
        # Errors rclone counted that no record explains (e.g. a listing error).
        errors = _CHECK_ERRORS_RE.search(stderr)
        differences = _CHECK_DIFFERENCES_RE.search(stderr)
        if errors and differences and int(errors.group(1)) > int(differences.group(1)):
            return f"rclone check reported errors besides the differences: {detail[:500]}"
        return None


class CheckMixin(RcloneBase):
    """rclone check: the diff of a profile and the verification of a backup."""

    async def check_diff(self, local_dir: str, remote_dir: str, rclone_filter: list[str] | None = None, rclone_args: list[str] | None = None, timeout: float | None = None) -> dict:
        """Compare local and remote directories using rclone check --combined.

        Returns a dict with local_only, remote_only, differ lists and has_changes bool.
        Does NOT transfer any files — metadata-only comparison (size + hash).

        For a 2GB directory with thousands of files this typically takes 5-15s
        because rclone only lists files and compares metadata, never downloads
        actual file content.
        """
        # Pre-refresh token so rclone doesn't hang
        remote_name = remote_dir.split(":")[0] if ":" in remote_dir else ""
        if remote_name:
            await self._ensure_fresh_token(remote_name)

        result_data: dict = {
            "has_changes": False,
            "local_only": [],
            "remote_only": [],
            "differ": [],
            "error": None,
        }

        # rclone check exits 1 both for "differences found" and for errors,
        # so the exit code alone is never trusted: see CheckOutput.error().
        # The output is read line by line; '=' (match) lines are only counted.
        cmd = self._build_command(
            ["check", "--combined", "-"],
            rclone_filter=[TRASH_FILTER, *(rclone_filter or [])],
            rclone_args=rclone_args,
            positional=[local_dir, remote_dir],
        )
        parsed = CheckOutput()
        stderr_tail: deque[str] = deque(maxlen=40)

        try:
            rc = await self._stream(cmd, parsed.feed, stderr_tail.append, timeout=timeout or CHECK_TIMEOUT)
        except RcloneError as exc:  # timed out
            rc, error = -1, f"Diff check failed: {exc}"
        except Exception as exc:  # rclone missing, cannot start
            rc, error = -1, f"Diff check failed: {exc}"
        else:
            error = parsed.error(rc, "\n".join(stderr_tail))

        if error is None:
            result_data["local_only"] = parsed.local_only
            result_data["remote_only"] = parsed.remote_only
            result_data["differ"] = parsed.differ
        else:
            result_data["error"] = error
            logger.warning("check_diff failed (exit %d): %s", rc, error[:500])

        result_data["has_changes"] = bool(
            result_data["local_only"] or result_data["remote_only"] or result_data["differ"]
        )
        total = len(result_data["local_only"]) + len(result_data["remote_only"]) + len(result_data["differ"])
        logger.info("check_diff complete: %d differences found", total)
        return result_data

    async def verify_copy(
        self, source: str, dest: str, rclone_filter: list[str] | None = None, *, encrypted: bool = False,
    ) -> tuple[CheckOutput, str | None]:
        """Whether every file of ``source`` is in ``dest`` with the same content (one way).

        ``rclone check --one-way`` (``cryptcheck`` for a crypt ``dest``):
        sizes plus a hash both sides support. Neither downloads file
        content; check lists ``dest`` and hashes the local files, cryptcheck
        also reads the 32-byte header of each encrypted file. Files only in
        ``dest`` are not reported. Returns the parsed output and None, or
        why the comparison cannot be trusted.
        """
        cmd = self._build_command(
            ["cryptcheck" if encrypted else "check", "--one-way", "--combined", "-"],
            rclone_filter=rclone_filter, positional=[source, dest],
        )
        parsed = CheckOutput()
        stderr_tail: deque[str] = deque(maxlen=40)
        try:
            rc = await self._stream(cmd, parsed.feed, stderr_tail.append, timeout=None)
        except RcloneError as exc:
            return parsed, f"Verification failed: {exc}"
        return parsed, parsed.error(rc, redact_secrets("\n".join(stderr_tail)))
