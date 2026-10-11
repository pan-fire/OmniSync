"""Paths inside a profile's local folder that never lead through a symbolic link.

rclone skips local symbolic links when it lists a folder, but when it is
told to write one path (a per-file pull, ``copyto``, a restore) the
operating system follows every link on the way: a pull of ``ld/f``, where
``ld`` is a link to a folder elsewhere, writes ``f`` into that folder,
outside the synced one. OmniSync's own file moves (conflict resolution,
trash restore) would do the same. So every place that reads or writes a
single local path checks it first:

- link_on_path() looks at each part of the path with ``lstat`` (which never
  follows a link), from the real path of the folder down to the last part
  that exists, and then checks that the deepest existing folder resolves
  inside the folder (``realpath``), in case a part was swapped meanwhile.
- move_within() moves a file inside the folder through directory file
  descriptors opened with ``O_NOFOLLOW``: a link swapped in for a folder
  on the way after the check makes the move fail instead of following it.

rclone itself cannot be told not to follow a link on the way, so a check
before an rclone command leaves a window between the check and rclone
opening the path; callers make the check right before the command, after
any slower listing. The folder itself may be a link (that is where the
user put it); only what lies below it is checked.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
from collections.abc import Iterable


class LinkedPathError(ValueError):
    """A path that leads through a symbolic link (``link``: the part that is one, relative)."""

    def __init__(self, rel: str, link: str) -> None:
        super().__init__(f"'{link}' in the local folder is a symbolic link; OmniSync does not read or write "
                         f"'{rel}' through it")
        self.rel = rel
        self.link = link


def _parts(rel: str) -> list[str] | None:
    """The parts of a relative ``/``-separated path, or None for one that is no plain path below the folder."""
    parts = [p for p in rel.split("/") if p]
    if not parts or "\0" in rel or any(p in (".", "..") for p in parts):
        return None
    return parts


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def link_on_path(root: str, rel: str) -> str | None:
    """The first part of ``rel`` (below ``root``) that is a symbolic link, as a relative path; None if none is.

    The last part counts too (a link to a file or a folder). A path that is
    no plain path below the folder (empty, ``..``, NUL) is returned whole,
    as is one whose deepest existing folder does not resolve inside it.
    """
    parts = _parts(rel)
    if parts is None:
        return rel
    real_root = os.path.realpath(root)
    current = real_root
    deepest = real_root
    for i, part in enumerate(parts):
        current = os.path.join(current, part)
        try:
            mode = os.lstat(current).st_mode
        except OSError:
            break  # nothing below a missing (or unreadable) part exists, so no link either
        if stat.S_ISLNK(mode):
            return "/".join(parts[:i + 1])
        if stat.S_ISDIR(mode):
            deepest = current
    # Defence in depth: the folder that holds the path resolves inside the root.
    if not _inside(os.path.realpath(deepest), real_root):
        return rel
    return None


def links_on_paths(root: str, rels: Iterable[str]) -> list[str]:
    """link_on_path() for many paths at once: each link (or bad path) found, once, sorted.

    Each folder on the way is looked at once, however many paths share it.
    """
    real_root = os.path.realpath(root)
    seen: dict[str, str] = {}  # relative prefix -> "dir", "link", "missing" or "file"
    found: set[str] = set()
    for rel in rels:
        parts = _parts(rel)
        if parts is None:
            found.add(rel)
            continue
        for i in range(len(parts)):
            prefix = "/".join(parts[:i + 1])
            kind = seen.get(prefix)
            if kind is None:
                full = os.path.join(real_root, *parts[:i + 1])
                try:
                    mode = os.lstat(full).st_mode
                    kind = "link" if stat.S_ISLNK(mode) else "dir" if stat.S_ISDIR(mode) else "file"
                except OSError:
                    kind = "missing"
                if kind == "dir" and not _inside(os.path.realpath(full), real_root):
                    kind = "outside"  # swapped meanwhile: the defence in depth of link_on_path()
                seen[prefix] = kind
            if kind == "link":
                found.add(prefix)
            if kind == "outside":
                found.add(rel)
            if kind != "dir":
                break
    return sorted(found)


def check_no_link(root: str, rel: str) -> str:
    """The full path of ``rel`` below ``root``; LinkedPathError if a link (or a way out) is on it."""
    link = link_on_path(root, rel)
    if link is not None:
        raise LinkedPathError(rel, link)
    return os.path.join(os.path.realpath(root), *rel.split("/"))


_DIR_FD_OPS = {os.open, os.mkdir, os.rename, os.stat}


def _open_dir(real_root: str, parts: list[str], rel: str, create: bool) -> int:
    """A file descriptor of the folder ``parts`` below ``real_root``, no link followed on the way."""
    fd = os.open(real_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for i, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass  # made meanwhile: opened (and checked) below
                child = os.open(part, flags, dir_fd=fd)
            except OSError as exc:
                # ELOOP: the part is a link; ENOTDIR: a link to a file, or a file.
                if exc.errno in (errno.ELOOP, errno.ENOTDIR) and \
                        stat.S_ISLNK(os.stat(part, dir_fd=fd, follow_symlinks=False).st_mode):
                    raise LinkedPathError(rel, "/".join(parts[:i + 1])) from None
                raise
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    return fd


def move_within(root: str, src_rel: str, dst_rel: str) -> None:
    """Move the file (or link) ``src_rel`` to ``dst_rel``, both below ``root``, replacing what is there.

    The folders of ``dst_rel`` are made as needed. Raises LinkedPathError
    when a folder on either way is a symbolic link (also one swapped in
    after a check), and OSError as os.replace does. A move onto another
    file system (a mount inside the folder) copies and deletes instead,
    after checking both paths.
    """
    src_parts, dst_parts = _parts(src_rel), _parts(dst_rel)
    if src_parts is None or dst_parts is None:
        raise LinkedPathError(src_rel if src_parts is None else dst_rel,
                              src_rel if src_parts is None else dst_rel)
    real_root = os.path.realpath(root)
    if not _DIR_FD_OPS <= os.supports_dir_fd:  # pragma: no cover - not on Linux or macOS
        _move_by_path(root, src_rel, dst_rel)
        return
    src_dir = _open_dir(real_root, src_parts[:-1], src_rel, create=False)
    try:
        dst_dir = _open_dir(real_root, dst_parts[:-1], dst_rel, create=True)
        try:
            os.replace(src_parts[-1], dst_parts[-1], src_dir_fd=src_dir, dst_dir_fd=dst_dir)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            _move_by_path(root, src_rel, dst_rel)
        finally:
            os.close(dst_dir)
    finally:
        os.close(src_dir)


def _move_by_path(root: str, src_rel: str, dst_rel: str) -> None:
    """move_within() by path names: checked right before, then shutil.move (copies across file systems)."""
    for rel in (src_rel, dst_rel):
        parent = rel.rsplit("/", 1)[0] if "/" in rel.strip("/") else ""
        if parent:
            check_no_link(root, parent)
    src = os.path.join(os.path.realpath(root), *src_rel.split("/"))
    dst = os.path.join(os.path.realpath(root), *dst_rel.split("/"))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
