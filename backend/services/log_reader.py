"""Log file reader service for OmniSync (GET /logs)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Literal

from backend.api.schemas import LogEntryResponse
from backend.logging_setup import AUDIT_LOGGER, DEFAULT_LOG_PATH

logger = logging.getLogger(__name__)

# The text format (backend/logging_setup.py):
#   2024-01-15 10:30:45,123 - INFO - backend.services.sync_engine - [req:3f9c...] Some message
# The logger name and the request id are optional, so the simpler
# "timestamp - LEVEL - message" lines of older files parse too.
LOG_LINE_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:[,\.]\d+)?)\s*[-–]\s*(\w+)\s*[-–]\s*"
    r"(?:([\w.]+)\s*[-–]\s*)?(?:\[req:([A-Za-z0-9._-]{1,64})\]\s)?(.+)$"
)

Category = Literal["audit", "errors"]

# Bytes read per step when walking the file backwards.
TAIL_CHUNK = 64 * 1024
# Lines kept of what follows an entry (a traceback, a multi-line message).
MAX_CONTINUATION_LINES = 400
# Rotated files looked at at most (omnisync.log.1 ... .N), whatever
# OMNISYNC_LOG_BACKUPS says: they are found by name, in order, until one is
# missing.
MAX_ROTATED_FILES = 100
_ERROR_LEVELS = frozenset({"ERROR", "CRITICAL"})


class LogReader:
    """Parses and serves log entries from a log file, text or JSON lines.

    Reads backwards from the end of the file and stops as soon as the
    requested page is complete, so a request costs about the size of the
    entries it returns, not a parse of the whole (up to 5 MB) file. Past the
    start of the file it goes on in the rotated files the logging handler
    keeps (``omnisync.log.1``, then ``.2``, ...), the same way, so paging
    reaches every entry still on disk.

    Lines that are not an entry of their own (the traceback after an
    exception, the rest of a multi-line message) are attached to the entry
    above them as ``exc``.
    """

    def __init__(self, log_path: Path = DEFAULT_LOG_PATH) -> None:
        self.log_path = log_path

    def read(
        self, skip: int = 0, limit: int = 50, level: str | None = None, category: Category | None = None,
    ) -> list[LogEntryResponse]:
        """Read log entries from the file, most recent first.

        With `level`, only entries of that level count, so skip/limit page
        through that level's entries across the whole file; `category`
        "audit" keeps the audit trail (backend.audit), "errors" the ERROR
        and CRITICAL entries.
        """
        wanted = skip + limit
        entries: list[LogEntryResponse] = []
        try:
            with ExitStack() as stack:
                # Every file is opened before the first is read: a rotation
                # meanwhile renames them, but the open files stay the same,
                # so no entry is read twice or skipped.
                files = []
                for path in self.files():
                    try:
                        files.append(stack.enter_context(open(path, "rb")))
                    except FileNotFoundError:
                        break  # rotated away since it was listed
                for fh in files:
                    for entry in self._entries_from_end(fh):
                        if not _matches(entry, level, category):
                            continue
                        entries.append(entry)
                        if len(entries) >= wanted:
                            return entries[skip : skip + limit]
        except OSError as exc:
            logger.warning("Could not read log file %s: %s", self.log_path, exc)
            return []

        return entries[skip : skip + limit]

    def files(self) -> list[Path]:
        """The log file and its rotated copies that exist, newest first."""
        paths = [self.log_path] if self.log_path.is_file() else []
        for n in range(1, MAX_ROTATED_FILES + 1):
            rotated = self.log_path.with_name(f"{self.log_path.name}.{n}")
            if not rotated.is_file():
                break
            paths.append(rotated)
        return paths

    def _entries_from_end(self, fh: BinaryIO) -> Iterator[LogEntryResponse]:
        """The file's entries, last first, each with the lines that follow it.

        Lines above the file's first entry are dropped: the handler rotates
        between records, so they never belong to an entry of the next file.
        """
        pending: list[str] = []  # lines below the entry not yet seen, last first
        omitted = 0
        for line in self._lines_from_end(fh):
            entry = self._parse(line)
            if entry is None:
                if not line.strip():
                    continue
                if len(pending) < MAX_CONTINUATION_LINES:
                    pending.append(line.rstrip("\r"))
                else:
                    omitted += 1
                continue
            if pending:
                lines = list(reversed(pending))
                if omitted:
                    lines.insert(0, f"... ({omitted} more lines)")
                extra = "\n".join(lines)
                entry.exc = f"{entry.exc}\n{extra}" if entry.exc else extra
                pending, omitted = [], 0
            yield entry

    @staticmethod
    def _lines_from_end(fh: BinaryIO) -> Iterator[str]:
        """The file's lines, last line first, read in chunks from the end."""
        fh.seek(0, 2)
        position = fh.tell()
        partial = b""
        while position > 0:
            size = min(TAIL_CHUNK, position)
            position -= size
            fh.seek(position)
            block = fh.read(size) + partial
            lines = block.split(b"\n")
            partial = lines[0]  # may continue in the previous chunk
            for raw in reversed(lines[1:]):
                yield raw.decode("utf-8", errors="replace")
        if partial:
            yield partial.decode("utf-8", errors="replace")

    @staticmethod
    def _parse(line: str) -> LogEntryResponse | None:
        """The entry a line starts, or None for a line that continues one."""
        if line.startswith("{"):
            return _parse_json(line)
        if not line[:1].isdigit():
            return None
        match = LOG_LINE_PATTERN.match(line.strip())
        if not match:
            return None
        timestamp_str, level, logger_name, request_id, message = match.groups()
        try:
            # Handle both comma and dot separators for milliseconds
            timestamp = datetime.fromisoformat(timestamp_str.replace(",", "."))
        except ValueError:
            return None
        return LogEntryResponse(
            timestamp=timestamp, level=level.upper(), message=message.strip(),
            logger=logger_name, request_id=request_id,
        )


def _parse_json(line: str) -> LogEntryResponse | None:
    """An entry of the JSON format (OMNISYNC_LOG_FORMAT=json)."""
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    ts, level, msg = data.get("ts"), data.get("level"), data.get("msg")
    if not isinstance(ts, str) or not isinstance(level, str) or not isinstance(msg, str):
        return None
    try:
        timestamp = datetime.fromisoformat(ts)
    except ValueError:
        return None

    def text(key: str) -> str | None:
        value = data.get(key)
        return value if isinstance(value, str) and value else None

    return LogEntryResponse(
        timestamp=timestamp, level=level.upper(), message=msg,
        logger=text("logger"), request_id=text("request_id"), exc=text("exc"),
    )


def _matches(entry: LogEntryResponse, level: str | None, category: Category | None) -> bool:
    if level is not None and entry.level != level:
        return False
    if category == "audit":
        return entry.logger == AUDIT_LOGGER
    if category == "errors":
        return entry.level in _ERROR_LEVELS
    return True
