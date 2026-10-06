"""Browse, restore and delete what a profile's syncs put into the trash.

Every push, pull and two-way sync moves the files it replaces or deletes
into ``.omnisync-trash/<timestamp>/<original path>`` inside the folder it
changes (rclone --backup-dir), on the local and on the remote side. An entry
is identified by its path inside the trash (``<timestamp>/<path>``).

Restore moves the file back to its original place. When a file exists
there already, it is moved into the trash first (a new timestamp folder),
so nothing is lost; a target newer than the trashed file is only replaced
when the caller confirms (``overwrite``). Delete removes trash entries for
good.

Paths are checked twice: an id must be a plain relative path (no ``..``,
no absolute path, nothing outside the trash), and on the local side every
folder on the way must be a real folder inside the profile's folder, so a
symlink cannot lead a restore or delete elsewhere.
"""

from __future__ import annotations

import asyncio
import logging
import os
import posixpath
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from backend.api.schemas import TrashEntry, TrashItemError, TrashListResponse, TrashSide
from backend.exceptions import RcloneAuthError, RcloneError
from backend.services.rclone import TRASH_DIR, RcloneService
from backend.services.sync_engine import TRASH_STAMP_FORMAT, SyncEngine, remote_join

logger = logging.getLogger(__name__)

# Entries listed per side; the totals still count every file.
MAX_LISTED = 5000


class TrashPathError(ValueError):
    """An id that is not a plain path inside the trash."""


def check_trash_id(entry_id: str) -> str:
    """``entry_id`` if it is a plain relative path (``<folder>/<path>``), else TrashPathError."""
    if (
        not entry_id or entry_id.startswith("/") or "\x00" in entry_id or "\n" in entry_id
        or posixpath.normpath(entry_id) != entry_id
        or any(part in ("", ".", "..") for part in entry_id.split("/"))
    ):
        raise TrashPathError(f"Not a path inside the trash: {entry_id!r}")
    return entry_id


def split_id(entry_id: str) -> tuple[str, str]:
    """(timestamp folder, original path) of an entry; a file directly in the trash has no folder."""
    folder, sep, path = entry_id.partition("/")
    return (folder, path) if sep else ("", folder)


def trashed_at(folder: str) -> datetime | None:
    """When a sync's trash folder was made (its name), or None for other folders."""
    try:
        return datetime.strptime(folder, TRASH_STAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _entry(entry_id: str, size: int | None, modified: datetime | None) -> TrashEntry:
    folder, path = split_id(entry_id)
    return TrashEntry(id=entry_id, folder=folder, path=path, size=size, modified=modified,
                      trashed_at=trashed_at(folder))


@dataclass
class _Listing:
    entries: list[TrashEntry] = field(default_factory=list)
    total_files: int = 0
    total_bytes: int = 0

    def add(self, entry_id: str, size: int | None, modified: datetime | None) -> None:
        self.total_files += 1
        self.total_bytes += size or 0
        if len(self.entries) < MAX_LISTED:
            self.entries.append(_entry(entry_id, size, modified))

    def response(self, side: TrashSide) -> TrashListResponse:
        return TrashListResponse(side=side, entries=self.entries, total_files=self.total_files,
                                 total_bytes=self.total_bytes, truncated=self.total_files > len(self.entries))


# How far past the restore's own stamp a free trash folder is looked for.
TRASH_SLOT_SECONDS = 3600


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime(TRASH_STAMP_FORMAT)


class NoFreeTrashSlotError(Exception):
    """Every trash folder name within the hour after a restore already holds the file's path."""


def _later_stamps(stamp: str):
    """``stamp``, then the following seconds: trash folder names that retention still prunes.

    Raises NoFreeTrashSlotError when all of them are used up (before anything
    is moved); apply_to_trash reports that as a failure of that one entry.
    """
    start = datetime.strptime(stamp, TRASH_STAMP_FORMAT)
    for seconds in range(TRASH_SLOT_SECONDS):
        yield (start + timedelta(seconds=seconds)).strftime(TRASH_STAMP_FORMAT)
    raise NoFreeTrashSlotError(f"no free trash folder name in the {TRASH_SLOT_SECONDS} s after {stamp}")


def _fail(entry_id: str, code: str, message: str) -> TrashItemError:
    return TrashItemError(id=entry_id, code=code, message=message)


# --- local side ---


class LocalTrash:
    """The trash inside a profile's local folder."""

    def __init__(self, local_dir: str) -> None:
        self.root = os.path.realpath(local_dir)
        self.trash = os.path.join(self.root, TRASH_DIR)

    def _usable(self) -> bool:
        # Never follow a symlinked trash folder somewhere else.
        return os.path.isdir(self.trash) and not os.path.islink(self.trash)

    def list(self) -> TrashListResponse:
        listing = _Listing()
        if not self._usable():
            return listing.response(TrashSide.LOCAL)
        with os.scandir(self.trash) as it:
            tops = sorted(((e.name, e.is_dir(follow_symlinks=False)) for e in it), reverse=True)
        for name, is_dir in tops:  # newest sync first
            if not is_dir:
                self._add_file(listing, name)
                continue
            for dirpath, dirnames, filenames in os.walk(os.path.join(self.trash, name)):
                dirnames.sort()
                for filename in sorted(filenames):
                    rel = os.path.relpath(os.path.join(dirpath, filename), self.trash)
                    self._add_file(listing, rel.replace(os.sep, "/"))
        return listing.response(TrashSide.LOCAL)

    def _add_file(self, listing: _Listing, rel: str) -> None:
        try:
            st = os.lstat(os.path.join(self.trash, rel))
        except OSError:
            return
        modified = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        listing.add(rel, st.st_size, modified)

    def _inside(self, path: str, base: str) -> bool:
        return path == base or path.startswith(base.rstrip(os.sep) + os.sep)

    def _source(self, entry_id: str) -> str:
        """The trashed file of an id: every folder on the way real and inside the trash."""
        check_trash_id(entry_id)
        if not self._usable():
            raise FileNotFoundError(entry_id)
        parent = os.path.dirname(os.path.join(self.trash, entry_id))
        if os.path.realpath(parent) != os.path.normpath(parent):
            raise TrashPathError(f"A folder on the way to {entry_id!r} is a symlink")
        source = os.path.join(self.trash, entry_id)
        if not os.path.lexists(source) or (os.path.isdir(source) and not os.path.islink(source)):
            raise FileNotFoundError(entry_id)
        return source

    def _target(self, path: str) -> str:
        """Where a trashed file goes back to, refusing a way out of the folder or into the trash."""
        if path.split("/", 1)[0] == TRASH_DIR:
            raise TrashPathError(f"Cannot restore into the trash: {path!r}")
        target = os.path.join(self.root, path)
        # The deepest folder of the way that exists must be inside the
        # profile's folder once symlinks are resolved.
        existing = os.path.dirname(target)
        while not os.path.lexists(existing):
            existing = os.path.dirname(existing)
        if not self._inside(os.path.realpath(existing), self.root) or \
                self._inside(os.path.realpath(existing), os.path.realpath(self.trash)):
            raise TrashPathError(f"{path!r} leads outside the synced folder")
        return target

    def restore(self, entry_id: str, overwrite: bool, stamp: str) -> TrashItemError | None:
        try:
            source = self._source(entry_id)
            _folder, path = split_id(entry_id)
            target = self._target(path)
        except TrashPathError as exc:
            return _fail(entry_id, "invalid", str(exc))
        except FileNotFoundError:
            return _fail(entry_id, "not_found", "The file is no longer in the trash.")
        if os.path.lexists(target):
            if os.path.isdir(target) and not os.path.islink(target):
                return _fail(entry_id, "target_is_folder", f"A folder named {path!r} is in the way.")
            if not overwrite and os.lstat(target).st_mtime > os.lstat(source).st_mtime:
                return _fail(entry_id, "target_newer",
                             f"{path!r} exists and is newer than the trashed version. Confirm to replace it "
                             "(the current version is moved to the trash).")
            # Never onto a trashed file (e.g. the one being restored, when
            # its sync ran within the same second).
            keep = next(k for k in (os.path.join(self.trash, st, path) for st in _later_stamps(stamp))
                        if not os.path.lexists(k))
            os.makedirs(os.path.dirname(keep), exist_ok=True)
            os.replace(target, keep)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(source, target)
        self._prune_empty(os.path.dirname(source))
        return None

    def delete(self, entry_id: str) -> TrashItemError | None:
        try:
            source = self._source(entry_id)
        except TrashPathError as exc:
            return _fail(entry_id, "invalid", str(exc))
        except FileNotFoundError:
            return _fail(entry_id, "not_found", "The file is no longer in the trash.")
        os.remove(source)  # a symlink itself, never what it points to
        self._prune_empty(os.path.dirname(source))
        return None

    def _prune_empty(self, folder: str) -> None:
        """Remove folders left empty, up to (not including) the trash folder itself."""
        while folder != self.trash and self._inside(folder, self.trash):
            try:
                os.rmdir(folder)
            except OSError:
                return
            folder = os.path.dirname(folder)


# --- remote side ---


class RemoteTrash:
    """The trash inside a profile's remote folder, through rclone."""

    def __init__(self, rclone: RcloneService, remote_dir: str) -> None:
        self.rclone = rclone
        self.root = remote_dir
        self.trash = remote_join(remote_dir, TRASH_DIR)

    async def list(self) -> TrashListResponse:
        listing = _Listing()
        try:
            items = await self.rclone.lsjson(self.trash)
        except RcloneAuthError:
            raise
        except RcloneError as exc:
            if "directory not found" in str(exc).lower():
                return listing.response(TrashSide.REMOTE)
            raise
        files = [i for i in items if isinstance(i, dict) and not i.get("IsDir") and isinstance(i.get("Path"), str)]
        # Newest sync first (timestamp folders sort by time), then by path.
        files.sort(key=lambda i: split_id(i["Path"])[1])
        files.sort(key=lambda i: split_id(i["Path"])[0], reverse=True)
        for item in files:
            size = item.get("Size")
            listing.add(item["Path"], size if isinstance(size, int) and size >= 0 else None, _modtime(item))
        return listing.response(TrashSide.REMOTE)

    async def _stat(self, root: str, path: str) -> dict | None:
        return (await self.rclone.lsjson_paths(root, [path])).get(path)

    async def restore(self, entry_id: str, overwrite: bool, stamp: str) -> TrashItemError | None:
        try:
            check_trash_id(entry_id)
        except TrashPathError as exc:
            return _fail(entry_id, "invalid", str(exc))
        _folder, path = split_id(entry_id)
        if path.split("/", 1)[0] == TRASH_DIR:
            return _fail(entry_id, "invalid", f"Cannot restore into the trash: {path!r}")
        source = await self._stat(self.trash, entry_id)
        if source is None:
            return _fail(entry_id, "not_found", "The file is no longer in the trash.")
        current = await self._stat(self.root, path)
        if current is not None:
            if not overwrite and (_modtime(current) or datetime.min.replace(tzinfo=timezone.utc)) > \
                    (_modtime(source) or datetime.min.replace(tzinfo=timezone.utc)):
                return _fail(entry_id, "target_newer",
                             f"{path!r} exists and is newer than the trashed version. Confirm to replace it "
                             "(the current version is moved to the trash).")
            keep = stamp
            for keep in _later_stamps(stamp):  # never onto a trashed file
                if await self._stat(self.trash, f"{keep}/{path}") is None:
                    break
            await self.rclone.move_file(remote_join(self.root, path), remote_join(self.trash, f"{keep}/{path}"))
        await self.rclone.move_file(remote_join(self.trash, entry_id), remote_join(self.root, path))
        return None

    async def delete(self, entry_id: str) -> TrashItemError | None:
        try:
            check_trash_id(entry_id)
        except TrashPathError as exc:
            return _fail(entry_id, "invalid", str(exc))
        if await self._stat(self.trash, entry_id) is None:
            return _fail(entry_id, "not_found", "The file is no longer in the trash.")
        await self.rclone.delete_file(remote_join(self.trash, entry_id))
        return None


def _modtime(item: dict) -> datetime | None:
    value = item.get("ModTime")
    if not isinstance(value, str):
        return None
    try:
        return SyncEngine._parse_rclone_modtime(value)
    except (ValueError, TypeError):
        return None


# --- one entry point for the routes ---


async def list_trash(side: TrashSide, local_dir: str, remote_dir: str, rclone: RcloneService) -> TrashListResponse:
    if side == TrashSide.LOCAL:
        return await asyncio.to_thread(LocalTrash(local_dir).list)
    return await RemoteTrash(rclone, remote_dir).list()


async def apply_to_trash(
    action: str, side: TrashSide, ids: list[str], overwrite: bool,
    local_dir: str, remote_dir: str, rclone: RcloneService,
) -> tuple[list[str], list[TrashItemError]]:
    """Restore or delete (``action``) each entry; (done ids, failures). One failure never stops the rest."""
    done: list[str] = []
    failed: list[TrashItemError] = []
    stamp = _now_stamp()
    local = LocalTrash(local_dir) if side == TrashSide.LOCAL else None
    remote = RemoteTrash(rclone, remote_dir) if side == TrashSide.REMOTE else None
    for entry_id in dict.fromkeys(ids):  # each once, in order
        try:
            if local is not None:
                if action == "restore":
                    error = await asyncio.to_thread(local.restore, entry_id, overwrite, stamp)
                else:
                    error = await asyncio.to_thread(local.delete, entry_id)
            else:
                assert remote is not None
                error = await (remote.restore(entry_id, overwrite, stamp) if action == "restore"
                               else remote.delete(entry_id))
        except RcloneAuthError:
            raise
        except (OSError, RcloneError) as exc:
            logger.warning("Trash %s of %r on the %s side failed: %s", action, entry_id, side.value, exc)
            error = _fail(entry_id, "failed", f"The {action} failed. The OmniSync log has the details.")
        except NoFreeTrashSlotError as exc:
            # Nothing was moved: the file in the way has nowhere to go in the trash.
            logger.warning("Trash %s of %r on the %s side refused: %s", action, entry_id, side.value, exc)
            error = _fail(entry_id, "failed",
                          f"The {action} was not done: the trash already holds a version of this file "
                          "for every second of the next hour, so the file in its place cannot be kept. "
                          "Try again later.")
        if error is None:
            done.append(entry_id)
        else:
            failed.append(error)
    if done:
        logger.info("Trash: %s %d file(s) on the %s side (%s)", "restored" if action == "restore" else "deleted",
                    len(done), side.value, remote_dir if side == TrashSide.REMOTE else local_dir)
    return done, failed
