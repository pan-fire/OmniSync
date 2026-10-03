"""Decide whether two sync or backup locations overlap.

Two locations overlap when they are the same folder or one is inside the
other. Syncing or backing up overlapping folders makes one job rewrite or
delete what another one owns, so profiles and backup targets may not
overlap. Comparing raw strings misses "/data/a/" vs "/data/a", "/data/a"
vs "/data/a/b" and symlinked paths; these helpers normalise first.
"""

from __future__ import annotations

import os
import posixpath
import re

# "<remote>:<path>", as rclone writes it (see backend.api.schemas).
_REMOTE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_-]*):(.*)$", re.DOTALL)


def normalize_location(location: str) -> tuple[str | None, str]:
    """Return (remote name, path) with the path normalised.

    Local paths (remote name None) are made absolute with symlinks
    resolved. Remote paths are normalised without leading or trailing
    slashes, so "gdrive:", "gdrive:/" and "gdrive:a/.." are all the root.
    Either way the path has no trailing slash, except the local root "/".
    """
    match = _REMOTE.match(location)
    if match is not None:
        remote, path = match.groups()
        path = posixpath.normpath("/" + path).strip("/")
        return remote, path
    path = os.path.realpath(os.path.expanduser(location))
    return None, path.rstrip("/") or "/"


def _inside(child: str, parent: str) -> bool:
    if parent in ("", "/"):
        return True
    return child == parent or child.startswith(parent + "/")


def locations_overlap(a: str, b: str) -> bool:
    """True if ``a`` and ``b`` are the same folder or one contains the other."""
    remote_a, path_a = normalize_location(a)
    remote_b, path_b = normalize_location(b)
    if remote_a != remote_b:
        return False
    return _inside(path_a, path_b) or _inside(path_b, path_a)
