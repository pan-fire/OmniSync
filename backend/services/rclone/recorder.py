"""Reading rclone's JSON log: live transfer progress and what a sync or bisync changed."""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field

from backend.services.rclone.common import MAX_RECORDED_CHANGES

# Files listed in a sync's live progress (rclone's "transferring" list).
MAX_PROGRESS_FILES = 5


@dataclass(frozen=True)
class ProgressFile:
    """A file rclone is transferring right now."""

    name: str
    size: int | None = None
    bytes: int = 0
    percentage: int | None = None


@dataclass(frozen=True)
class TransferProgress:
    """rclone's latest stats of a running transfer (--stats 1s, JSON log).

    Verified against rclone 1.75.1: with ``--use-json-log --stats 1s
    --stats-log-level NOTICE`` rclone logs one NOTICE line per second whose
    ``stats`` object holds ``bytes``, ``totalBytes``, ``speed`` (bytes/s),
    ``eta`` (seconds or null), ``transfers``/``totalTransfers``,
    ``checks``/``totalChecks`` and ``transferring`` (one object per file in
    flight: name, size, bytes, percentage). Totals grow while rclone is
    still listing. Only the latest stats are kept.
    """

    bytes: int = 0
    total_bytes: int = 0
    speed: float = 0.0
    eta_seconds: int | None = None
    files_done: int = 0
    files_total: int = 0
    checks: int = 0
    total_checks: int = 0
    current: tuple[ProgressFile, ...] = ()


def _int(value: object, default: int = 0) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else default


def parse_stats(stats: object) -> TransferProgress | None:
    """A TransferProgress from the ``stats`` object of an rclone JSON log line."""
    if not isinstance(stats, dict):
        return None
    current: list[ProgressFile] = []
    transferring = stats.get("transferring")
    for item in transferring if isinstance(transferring, list) else []:
        if len(current) >= MAX_PROGRESS_FILES:
            break
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        size = item.get("size")
        pct = item.get("percentage")
        current.append(ProgressFile(
            name=item["name"],
            size=_int(size) if isinstance(size, (int, float)) and size >= 0 else None,
            bytes=_int(item.get("bytes")),
            percentage=max(0, min(100, int(pct))) if isinstance(pct, (int, float)) else None,
        ))
    eta = stats.get("eta")
    speed = stats.get("speed")
    return TransferProgress(
        bytes=_int(stats.get("bytes")),
        total_bytes=_int(stats.get("totalBytes")),
        speed=float(speed) if isinstance(speed, (int, float)) and speed >= 0 else 0.0,
        eta_seconds=_int(eta) if isinstance(eta, (int, float)) and eta >= 0 else None,
        files_done=_int(stats.get("transfers")),
        files_total=_int(stats.get("totalTransfers")),
        checks=_int(stats.get("checks")),
        total_checks=_int(stats.get("totalChecks")),
        current=tuple(current),
    )


@dataclass
class FileChangeRecord:
    """One file rclone created, modified or deleted on the destination."""

    path: str
    action: str  # a FileChangeAction value: created / modified / deleted
    size_bytes: int | None = None
    side: str | None = None  # a FileSide value: the folder that changed


@dataclass
class ChangeRecorder:
    """Collects what a transfer actually did from rclone's --use-json-log output.

    rclone 1.75 logs one JSON object per line on stderr; at INFO level:

    - ``Copied (new)``                a file was created on the destination
    - ``Copied (replaced existing)``  a file was overwritten (no --backup-dir)
    - ``Deleted``                     a file was deleted (no --backup-dir)
    - ``Moved (server-side)``         with --backup-dir: the old version went
      into the backup dir. ``Copied (new)`` of the same object follows when
      the file was replaced, ``Moved into backup dir`` when it was deleted.
    - ``Copied (server-side copy)`` followed by ``Deleted`` is the same
      backup move on backends that cannot move server-side.

    ``object`` is the path relative to the destination root, unmodified
    (leading/trailing spaces kept). Memory stays bounded on huge trees: at
    most ``max_rows`` changes are kept, all of them are counted, and only
    the last few error lines are kept.

    ``side`` ("local" / "remote") is the folder the changes happen in; it
    is stored with every row.
    """

    max_rows: int = MAX_RECORDED_CHANGES
    side: str | None = None
    rows: list[FileChangeRecord] = field(default_factory=list)
    created: int = 0
    modified: int = 0
    deleted: int = 0
    errors: deque[str] = field(default_factory=lambda: deque(maxlen=20))
    other_lines: deque[str] = field(default_factory=lambda: deque(maxlen=20))
    # rclone's latest stats line (live progress), replaced every second.
    progress: TransferProgress | None = None
    # (side, action) -> count, for every change (not only the kept rows)
    by_side: dict[tuple[str | None, str], int] = field(default_factory=dict)
    _backed_up: set[tuple[str | None, str]] = field(default_factory=set)

    @property
    def total(self) -> int:
        """Every change seen, including those beyond ``max_rows``."""
        return self.created + self.modified + self.deleted

    @property
    def truncated(self) -> bool:
        return self.total > len(self.rows)

    def count(self, side: str | None, action: str) -> int:
        """Changes of one kind on one side (all of them, not only the kept rows)."""
        return self.by_side.get((side, action), 0)

    def _add(self, path: str, action: str, size: object = None) -> None:
        if action == "created":
            self.created += 1
        elif action == "modified":
            self.modified += 1
        else:
            self.deleted += 1
        key = (self.side, action)
        self.by_side[key] = self.by_side.get(key, 0) + 1
        if len(self.rows) < self.max_rows:
            self.rows.append(FileChangeRecord(path, action, size if isinstance(size, int) else None, self.side))

    def feed(self, line: str) -> None:
        """Process one stderr line."""
        if not line.startswith("{"):
            if line.strip():
                self.other_lines.append(line)
            return
        try:
            entry = json.loads(line)
        except ValueError:
            self.other_lines.append(line)
            return
        if not isinstance(entry, dict) or self._feed_stats(entry):
            return
        self._feed_entry(entry, str(entry.get("msg", "")))

    def _feed_stats(self, entry: dict) -> bool:
        """Keep a stats line as the live progress; True if ``entry`` was one.

        Its ``msg`` is the human-readable stats block, which lists the names
        of the files in flight: it is never parsed as a log message.
        """
        if "stats" not in entry:
            return False
        progress = parse_stats(entry["stats"])
        if progress is not None:
            self.progress = progress
        return True

    def _feed_entry(self, entry: dict, msg: str) -> None:
        obj = entry.get("object")
        if entry.get("level") in ("error", "critical"):
            self.errors.append(f"{obj}: {msg.strip()}" if obj else msg.strip())
            return
        if not isinstance(obj, str) or entry.get("objectType") == "string":
            return  # stats, notices, directories
        key = (self.side, obj)
        if msg in ("Moved (server-side)", "Copied (server-side copy)"):
            self._backed_up.add(key)
        elif msg == "Moved into backup dir":
            self._backed_up.discard(key)
            self._add(obj, "deleted")
        elif msg == "Deleted":
            if key not in self._backed_up:  # else: second half of a copy+delete backup move
                self._add(obj, "deleted")
        elif msg.startswith("Copied (") and "replaced existing" in msg:
            self._add(obj, "modified", entry.get("size"))
        elif msg.startswith("Copied (") and "new" in msg:
            if key in self._backed_up:
                self._backed_up.discard(key)
                self._add(obj, "modified", entry.get("size"))
            else:
                self._add(obj, "created", entry.get("size"))

    def failure_text(self) -> str:
        """The errors rclone reported, for an exception message."""
        return "\n".join([*self.errors, *self.other_lines])


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# "- Path2    Do queued copies to    - Path1": the transfers that follow change Path1.
_BISYNC_PHASE_RE = re.compile(
    r"^-\s+Path[12]\s+(?:Do queued copies to|Resync is copying files to)\s+-\s+Path([12])\s*$"
)
# "- Path1    Renaming Path1 copy    - <full path>": a conflict loser is renamed on Path1.
_BISYNC_RENAME_RE = re.compile(r"^-\s+Path([12])\s+Renaming Path[12] copy\s+-\s")
_BISYNC_BOTH_CHANGED_RE = re.compile(r"^-\s+WARNING\s+New or changed in both paths\s+-\s")
_BISYNC_MOVED_TO = "Moved (server-side) to: "
# rclone --dry-run logs "Skipped <what> as --dry-run is set" with skipped=<what>.
_DRY_RUN_AS = {
    "move": "Moved (server-side)",
    "copy": "Copied (new)",
    "move into backup dir": "Moved into backup dir",
    "delete": "Deleted",
}
BISYNC_SIDES = {"1": "local", "2": "remote"}  # Path1 is always the local folder


@dataclass
class BisyncRecorder(ChangeRecorder):
    """What an ``rclone bisync`` run did (or, with --dry-run, would do), per side.

    Verified against rclone 1.75.1 (--use-json-log, -v, --color NEVER):

    - ``- Path2  Do queued copies to  - Path1`` (``Resync is copying files
      to`` during --resync) starts the phase that changes Path1; the
      transfer lines that follow are those of rclone sync (see
      ChangeRecorder), with ``object`` relative to the root.
    - A file changed on both sides is logged as ``- WARNING  New or changed
      in both paths  - <path>``. When rclone keeps both versions, the loser
      is renamed on its side first: ``- Path1  Renaming Path1 copy - <full
      path>``, then ``Moved (server-side) to: <new name>`` with ``object``
      the old name; the renamed copy is later copied to the other side like
      any new file. Files that turn out equal are not renamed.
    - --dry-run logs ``Skipped copy/move/move into backup dir/delete as
      --dry-run is set`` (field ``skipped``) instead of the transfer lines.
    - A failure that needs --resync says so ("must run --resync", "Must run
      --resync to recover"); the delete guard says "too many deletes".

    Path1 is the local folder, Path2 the remote.
    """

    # original path -> {"local": name the local version has now, "remote": ...}
    conflicts: dict[str, dict[str, str]] = field(default_factory=dict)
    changed_on_both: int = 0
    resync_needed: bool = False
    too_many_deletes: bool = False
    critical: str | None = None
    # A run on short names (bisync_names.py): short root -> the real path,
    # so messages name the folders the user knows.
    real_roots: dict[str, str] = field(default_factory=dict)

    def real_paths(self, text: str) -> str:
        """``text`` with the short roots of this run replaced by the real paths."""
        if not self.real_roots:
            return text
        # (bisync_names -> process -> this module: imported when needed.)
        from backend.services.rclone.bisync_names import short_root_pattern

        for root, real in self.real_roots.items():
            def repl(match: re.Match[str], real: str = real) -> str:
                return real if not match.group(1) or real.endswith((":", "/")) else real + "/"
            text = short_root_pattern(root).sub(repl, text)
        return text

    def feed(self, line: str) -> None:
        if not line.startswith("{"):
            super().feed(line)
            return
        try:
            entry = json.loads(line)
        except ValueError:
            self.other_lines.append(line)
            return
        if not isinstance(entry, dict) or self._feed_stats(entry):
            return
        msg = _ANSI_RE.sub("", str(entry.get("msg", "")))
        lowered = msg.lower()
        if "must run --resync" in lowered:
            self.resync_needed = True
        if "too many deletes" in lowered:
            self.too_many_deletes = True
        if msg.startswith("Bisync critical error: "):
            self.critical = self.real_paths(msg.removeprefix("Bisync critical error: ").strip())
        text = msg.strip()
        if match := _BISYNC_PHASE_RE.match(text):
            self.side = BISYNC_SIDES[match.group(1)]
            return
        if match := _BISYNC_RENAME_RE.match(text):
            self.side = BISYNC_SIDES[match.group(1)]
            return
        if _BISYNC_BOTH_CHANGED_RE.match(text):
            self.changed_on_both += 1
            return
        obj = entry.get("object")
        if msg.startswith(_BISYNC_MOVED_TO) and isinstance(obj, str) and self.side is not None:
            # A conflict loser renamed on self.side: the new name is a new
            # file there, and the old name is about to be replaced.
            new_name = msg[len(_BISYNC_MOVED_TO):]
            self.conflicts.setdefault(obj, {"local": obj, "remote": obj})[self.side] = new_name
            self._add(new_name, "created", entry.get("size"))
            self._backed_up.add((self.side, obj))
            return
        skipped = entry.get("skipped")
        if isinstance(skipped, str) and skipped in _DRY_RUN_AS:
            msg = _DRY_RUN_AS[skipped]
            entry = {**entry, "level": "info"}
        self._feed_entry(entry, msg)

    def failure_text(self) -> str:
        text = self.real_paths(super().failure_text())
        if self.critical and self.critical not in text:
            text = f"{self.critical}\n{text}" if text else self.critical
        return text
