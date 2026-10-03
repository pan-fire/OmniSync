"""rclone bisync's file names, and the short names a two-way sync of long paths runs on.

bisync (rclone 1.75.1) names every file in its workdir after the two paths
(cmd/bisync/bilib/canonical.go): the session name is each path with its
remote name (``gdrive:Docs`` -> ``gdrive_Docs``; a local path as it is),
whitespace, ``\\ / : ? *`` replaced by ``_``, the two joined by ``..``; the
files are the session name plus a suffix, the longest being
``.path1.lst-dry-new`` (a --dry-run's new listing). Once session name and
suffix exceed the file system's 255-byte name limit, bisync fails ("Lock
file exists, but contents are unreadable", "file name too long"): with
rclone 1.75.1 a dry run from 238 bytes on, every run from 242 on.

Such a profile runs bisync on two remotes OmniSync defines only in the
environment of the rclone process (see process.py):
``omnisync_bisync_<profile id>_local`` and ``..._remote``, each a
``combine`` remote with one upstream, ``root``, that is the real path. An
``alias`` remote would not help: rclone resolves an alias to the Fs of its
target, so bisync would still see (and name its files after) the real
path. ``combine`` keeps its own name and passes everything else (hashes,
modification times, server-side moves within the upstream) through, so
bisync sees ``omnisync_bisync_7_local:root`` and its files are named
``omnisync_bisync_7_local_root..omnisync_bisync_7_remote_root.*``. Paths
inside the sync (listings, conflicts, the recorder's rows) are relative to
the root and the same in both forms.
"""

from __future__ import annotations

import os
import re

from backend.services.rclone.process import BISYNC_REMOTE_PREFIX, define_env_remote

NAME_MAX = 255
# The longest suffix rclone 1.75.1 appends to the session name
# (also .path1.lst-dry-old and .path1.lst-dry-err).
LONGEST_SUFFIX = ".path1.lst-dry-new"
# Short names are used once the session name leaves less than this to
# spare beyond the longest suffix: room for rclone's root normalisation,
# which bisync_session_name only approximates, and for longer suffixes in
# later rclone versions.
SPARE_BYTES = 14
SHORT_ROOT_DIR = "root"

# Go's \s (RE2) is ASCII whitespace without \v.
_NON_CANONICAL_RE = re.compile(r"[\t\n\f\r \\/:?*]")
_HEX_SUFFIX_RE = re.compile(r"\{[^}]*\}")


def _is_remote_path(path: str) -> bool:
    return ":" in path and not path.startswith(("/", "."))


def _fs_path(path: str) -> str:
    """bilib.FsPath: a local path made absolute, a remote's ``name:root``, with a trailing slash."""
    if _is_remote_path(path):
        # Backends differ in how they normalise the root (drive drops a
        # leading slash, a local-type remote keeps it): kept as given.
        out = path.rstrip("/")
    else:
        out = os.path.normpath(os.path.abspath(path))
    return out if out.endswith("/") else out + "/"


def _canonical(path: str) -> str:
    """bilib.CanonicalPath + StripHexString of one side."""
    canonical = _NON_CANONICAL_RE.sub("_", _fs_path(path).strip("\\/"))
    return _HEX_SUFFIX_RE.sub("", canonical, count=1)


def bisync_session_name(path1: str, path2: str) -> str:
    """The session name bisync gives the files of a sync of ``path1`` and ``path2`` (approximated)."""
    return f"{_canonical(path1)}..{_canonical(path2)}"


def bisync_names_fit(path1: str, path2: str, spare: int = 0) -> bool:
    """Whether every workdir file name of ``path1`` <-> ``path2`` stays within NAME_MAX, ``spare`` bytes to spare."""
    return len(bisync_session_name(path1, path2).encode("utf-8")) + len(LONGEST_SUFFIX) + spare <= NAME_MAX


def needs_short_names(local: str, remote: str) -> bool:
    """Whether a new two-way sync of ``local`` and ``remote`` should run on short names."""
    return not bisync_names_fit(local, remote, spare=SPARE_BYTES)


def _quote_upstream(value: str) -> str:
    """One entry of combine's space-separated ``upstreams`` list (rclone parses it as CSV)."""
    return '"' + value.replace('"', '""') + '"'


def short_bisync_roots(profile_id: int, local: str, remote: str) -> tuple[str, str]:
    """Define the short-name remotes of a profile; return the roots bisync runs on."""
    roots = []
    for side, path in (("local", local), ("remote", remote)):
        name = define_env_remote(
            f"{profile_id}_{side}",
            {"type": "combine", "upstreams": _quote_upstream(f"{SHORT_ROOT_DIR}={path}")},
            prefix=BISYNC_REMOTE_PREFIX,
        )
        roots.append(f"{name}{SHORT_ROOT_DIR}")
    return roots[0], roots[1]


def rebase(path: str, base: str, new_base: str) -> str:
    """``path`` (``base`` or inside it) relative to ``new_base`` (a short root) instead."""
    if path == base:
        return new_base
    if base.endswith((":", "/")):
        prefix = base
    else:
        prefix = base + "/"
    if not path.startswith(prefix):
        raise ValueError(f"{path!r} is not inside {base!r}")
    return f"{new_base}/{path[len(prefix):].lstrip('/')}"


def short_root_pattern(root: str) -> re.Pattern[str]:
    """How rclone may print short root ``root`` in a message (with a {hash} after the name, a trailing slash)."""
    name, _, rest = root.partition(":")
    return re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(name)}(?:\{{[^}}]*\}})?:{re.escape(rest)}(/|\b)", re.IGNORECASE)
