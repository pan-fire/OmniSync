"""Log file reader service for OmniSync."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from backend.api.schemas import LogEntryResponse

logger = logging.getLogger(__name__)

# Pattern: 2024-01-15 10:30:45,123 - INFO - backend.services.sync_engine - Some message
# Also matches the simpler 3-part format: timestamp - LEVEL - message
LOG_LINE_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:[,\.]\d+)?)\s*[-–]\s*(\w+)\s*[-–]\s*(?:[\w.]+\s*[-–]\s*)?(.+)$"
)

DEFAULT_LOG_PATH = Path("/data/omnisync/omnisync.log")

# Bytes read per step when walking the file backwards.
TAIL_CHUNK = 64 * 1024


class LogReader:
    """Parses and serves log entries from a log file.

    Reads backwards from the end of the file and stops as soon as the
    requested page is complete, so a request costs about the size of the
    entries it returns, not a parse of the whole (up to 5 MB) file.
    """

    def __init__(self, log_path: Path = DEFAULT_LOG_PATH) -> None:
        self.log_path = log_path

    def read(self, skip: int = 0, limit: int = 50, level: str | None = None) -> list[LogEntryResponse]:
        """Read log entries from the file, most recent first.

        With `level`, only entries of that level count, so skip/limit page
        through that level's entries across the whole file.
        """
        if not self.log_path.exists():
            return []

        wanted = skip + limit
        entries: list[LogEntryResponse] = []
        try:
            for line in self._lines_from_end():
                entry = self._parse(line)
                if entry is None or (level is not None and entry.level != level):
                    continue
                entries.append(entry)
                if len(entries) >= wanted:
                    break
        except OSError as exc:
            logger.warning("Could not read log file %s: %s", self.log_path, exc)
            return []

        return entries[skip : skip + limit]

    def _lines_from_end(self) -> Iterator[str]:
        """The file's lines, last line first, read in chunks from the end."""
        with open(self.log_path, "rb") as fh:
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
        line = line.strip()
        if not line:
            return None
        match = LOG_LINE_PATTERN.match(line)
        if not match:
            return None
        timestamp_str, level, message = match.groups()
        try:
            # Handle both comma and dot separators for milliseconds
            timestamp = datetime.fromisoformat(timestamp_str.replace(",", "."))
        except ValueError:
            return None
        return LogEntryResponse(timestamp=timestamp, level=level.upper(), message=message.strip())
