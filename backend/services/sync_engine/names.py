"""Local names rclone cannot sync as they are, found by OmniSync itself.

rclone handles three kinds of local names in a way the user would not
expect, and says so only in its own log:

- names in one folder that are equal after Unicode normalisation (``café``
  written composed, NFC, and decomposed, NFD, as macOS writes it): rclone
  matches names after normalising them, so it treats the two as one file,
  syncs only one of them ("Duplicate object found in source - ignoring") and
  leaves the other, or a whole folder of the other, behind;
- names that are not valid UTF-8: rclone shows them with U+FFFD in place of
  the bad bytes. Whole-folder syncs still carry them byte for byte, but a
  per-file action names no file, and two-way sync matches such a name with
  its record only one run late;
- symbolic links: rclone skips them (they are neither followed nor copied),
  so to a sync the link is no file. A remote file or folder of the same
  name is then deleted by a push (into the remote trash) and, without
  OmniSync's exclusion below, copied over the link by a pull or a two-way
  sync, replacing the link (its target stays untouched).

Here the local folder is walked once (no link is followed) to find them. The
engine turns the result into SyncWarning entries for the preview, the diff
and the job, and excludes every local link from pulls and two-way syncs, so
the link and the remote item both stay as they are.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field

from backend.api.schemas import SyncWarning, SyncWarningCode
from backend.services.rclone import SENTINEL_FILE, TRASH_DIR
from backend.services.sync_engine.common import filter_escape

# Paths shown per kind of warning; the count says how many there are.
WARNING_PATHS_SHOWN = 20

# Characters that would make a displayed path lie about itself: bidi
# controls (e.g. U+202E turns "invoice‮gpj.exe" into "invoiceexe.jpg").
_BIDI_CONTROLS = frozenset("‎‏‪‫‬‭‮⁦⁧⁨⁩")
_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def display_path(path: str) -> str:
    """``path`` safe to show: each byte that is not UTF-8 as ``\\xNN``,
    control characters as ``\\n``, ``\\xNN`` or ``\\uNNNN``, bidi controls as
    ``\\uNNNN``. Everything else (accents, emoji, right-to-left text) stays."""
    out: list[str] = []
    for ch in path:
        code = ord(ch)
        if 0xDC80 <= code <= 0xDCFF:  # a byte os.fsdecode could not decode
            out.append(f"\\x{code - 0xDC00:02x}")
        elif ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif unicodedata.category(ch) == "Cc":
            out.append(f"\\x{code:02x}")
        elif ch in _BIDI_CONTROLS:
            out.append(f"\\u{code:04x}")
        else:
            out.append(ch)
    return "".join(out)


def is_utf8(name: str) -> bool:
    """Whether a name from the file system (os.fsdecode) is valid UTF-8."""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _replaced(text: str) -> str:
    """``text`` with each run of U+FFFD as one, to compare names whatever
    number of U+FFFD a decoder put in place of a run of bad bytes."""
    return re.sub("\ufffd+", "\ufffd", text)


def rclone_spelling(path: str) -> str:
    """How rclone lists a path, as far as compared here: bytes that are not
    UTF-8 as U+FFFD (each run as one, see _replaced)."""
    return _replaced(os.fsencode(path).decode("utf-8", errors="replace"))


@dataclass
class LocalNames:
    """What scan_local() found. Paths are relative, as the file system spells
    them (os.fsdecode); a folder's path ends with ``/``."""

    # Per group of names equal after normalisation: its paths, in name order.
    collisions: list[list[str]] = field(default_factory=list)
    not_utf8: list[str] = field(default_factory=list)
    # Symbolic links (to files, folders or nothing), outside the trash.
    symlinks: list[str] = field(default_factory=list)


def scan_local(root: str) -> LocalNames:
    """Walk ``root`` (blocking; no link is followed) for the names above.

    The trash and the sync marker at the top are left out, as every sync
    leaves them out. A folder that cannot be read is skipped: rclone
    reports it itself. Inside a folder whose own name is not UTF-8, the
    names are not reported again.
    """
    found = LocalNames()
    stack: list[tuple[str, bool]] = [("", False)]
    while stack:
        rel, in_bad = stack.pop()
        try:
            with os.scandir(os.path.join(root, rel) if rel else root) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        groups: dict[str, list[str]] = {}
        for entry in entries:
            if not rel and entry.name in (TRASH_DIR, SENTINEL_FILE):
                continue
            path = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_symlink():
                found.symlinks.append(path)
                continue
            is_dir = entry.is_dir(follow_symlinks=False)
            shown = path + "/" if is_dir else path
            bad = not is_utf8(entry.name)
            if bad and not in_bad:
                found.not_utf8.append(shown)
            groups.setdefault(unicodedata.normalize("NFC", entry.name), []).append(shown)
            if is_dir:
                stack.append((path, in_bad or bad))
        found.collisions.extend(paths for paths in groups.values() if len(paths) > 1)
    found.collisions.sort()
    found.not_utf8.sort()
    found.symlinks.sort()
    return found


def only_listed(found: LocalNames, listed: set[str]) -> LocalNames:
    """``found`` narrowed to what a sync sees: the paths rclone listed with
    the profile's filters (``listed``, as rclone spells them, folders
    without the trailing ``/``). Links are kept: rclone never lists them."""
    spellings = {_replaced(p) for p in listed}

    def seen(path: str) -> bool:
        return rclone_spelling(path.rstrip("/")) in spellings

    collisions = [kept for paths in found.collisions if len(kept := [p for p in paths if seen(p)]) > 1]
    return LocalNames(collisions=collisions, not_utf8=[p for p in found.not_utf8 if seen(p)],
                      symlinks=found.symlinks)


def link_filter_rules(symlinks: list[str]) -> list[str]:
    """rclone filter rules that leave each link's path (and anything below
    it, for a link to a folder) out of a sync. A link whose name is not
    UTF-8 or holds a line break cannot be named in a rule (rclone skips the
    link itself anyway)."""
    rules: list[str] = []
    for path in symlinks:
        if not is_utf8(path) or "\n" in path or "\r" in path:
            continue
        escaped = filter_escape(path)
        rules += [f"- /{escaped}", f"- /{escaped}/**"]
    return rules


def _warning(code: SyncWarningCode, paths: list[str]) -> SyncWarning:
    return SyncWarning(code=code, count=len(paths), paths=[display_path(p) for p in paths[:WARNING_PATHS_SHOWN]])


def name_warnings(found: LocalNames, shadowing: list[str], link_code: SyncWarningCode) -> list[SyncWarning]:
    """The warnings for ``found``: one per kind that occurs.

    A collision is shown as the name its spellings share (NFC); its count
    is the number of groups. ``shadowing``: the links that have the name of
    a remote file or folder, reported with ``link_code``.
    """
    out: list[SyncWarning] = []
    if found.collisions:
        out.append(_warning(SyncWarningCode.NAME_COLLISION,
                            [unicodedata.normalize("NFC", paths[0]) for paths in found.collisions]))
    if found.not_utf8:
        out.append(_warning(SyncWarningCode.NAME_NOT_UTF8, found.not_utf8))
    if shadowing:
        out.append(_warning(link_code, shadowing))
    return out


_DESCRIPTIONS = {
    SyncWarningCode.NAME_COLLISION: "local name(s) exist in spellings that are equal after Unicode "
                                    "normalisation; rclone synced only one spelling of each",
    SyncWarningCode.NAME_NOT_UTF8: "local name(s) are not valid UTF-8; per-file actions cannot copy them",
    SyncWarningCode.SYMLINK_SHADOW: "local symbolic link(s) have the name of a remote file or folder",
    SyncWarningCode.SYMLINK_KEPT: "remote item(s) were left alone because a local symbolic link has their name",
    SyncWarningCode.SYMLINK_TRASHED: "remote item(s) were moved to the remote trash because a local symbolic "
                                     "link has their name",
}


def describe(warnings: list[SyncWarning]) -> list[str]:
    """One line per warning (English, for the server log and notifications)."""
    return [f"{w.count} {_DESCRIPTIONS[w.code]}: {', '.join(w.paths)}{', ...' if w.count > len(w.paths) else ''}"
            for w in warnings]
