"""Archive backups: writing, installing, reading and extracting tar.gz snapshots."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import zlib
from collections.abc import Callable
from datetime import datetime, timezone
from typing import IO, TypeVar

from backend.api.schemas import RestoreScope
from backend.db.models import BackupTarget, SyncProfile
from backend.services.rclone import SENTINEL_FILE, TRASH_DIR, TRASH_FILTER, classify_failure, process_env
from backend.services.sync_engine import remote_join
from backend.services.backup_service.common import RestoreRefused, SnapshotFile, _BackupOutcome, logger
from backend.services.backup_service.base import BackupBase

# A local archive is written under this prefix and renamed when complete, so a
# full disk or a crash never leaves a truncated backup-*.tar.gz that would be
# listed (and restored) as a snapshot. Leftovers are removed by the next run.
PARTIAL_ARCHIVE_PREFIX = ".omnisync-partial-"

_PARTIAL_FILE = re.compile(r".*\.[0-9a-f]{8}\.partial")


def _member_path(name: str) -> str | None:
    """A tar member name ("./a/b.txt") as a snapshot path ("a/b.txt"); None for the top."""
    path = name.removeprefix("./").strip("/")
    return None if path in ("", ".") else path


def _list_archive(fileobj: IO[bytes]) -> list[SnapshotFile]:
    """The regular files of a tar.gz stream, read sequentially (nothing is extracted)."""
    files: list[SnapshotFile] = []
    with tarfile.open(fileobj=fileobj, mode="r|gz") as tar:
        for member in tar:
            path = _member_path(member.name)
            if path is not None and member.isfile():
                files.append(SnapshotFile(path, member.size, datetime.fromtimestamp(member.mtime, timezone.utc)))
    return files


def _extract_selected(fileobj: IO[bytes], dest_dir: str, select: Callable[[str], bool]) -> list[str]:
    """Extract only the selected regular files of a tar.gz stream into dest_dir; returns their paths."""
    extracted: list[str] = []
    with tarfile.open(fileobj=fileobj, mode="r|gz") as tar:
        for member in tar:
            path = _member_path(member.name)
            if path is None or not member.isfile() or path == SENTINEL_FILE or not select(path):
                continue
            tar.extract(member, dest_dir, filter="data")
            extracted.append(path)
    return extracted


def _read_back_archive(path: str) -> tuple[int, int]:
    """Read a whole tar.gz (every member's data, so gzip checks its CRC); returns (files, bytes)."""
    files = total = 0
    with tarfile.open(path, "r|gz") as tar:
        for member in tar:
            if member.isfile():
                fh = tar.extractfile(member)
                if fh is not None:
                    while chunk := fh.read(1024 * 1024):
                        total += len(chunk)
                files += 1
    return files, total


_T = TypeVar("_T")
_ARCHIVE_ERRORS = (tarfile.TarError, EOFError, OSError, zlib.error)


def _read_archive_stream(source: str | list[str], consume: Callable[[IO[bytes]], _T]) -> _T:
    """Run ``consume`` on an archive: a local file (a path) or ``rclone cat`` output (a command).

    Streams; runs in a thread. A failing rclone (a missing file, a wrong
    passphrase) raises its own error rather than the tar error it causes.
    """
    if isinstance(source, str):
        with open(source, "rb") as fh:
            return consume(fh)
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(source, stdout=subprocess.PIPE, stderr=err, env=process_env(source))
        assert proc.stdout is not None
        try:
            return consume(proc.stdout)
        except _ARCHIVE_ERRORS as exc:
            try:
                rc = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                raise exc  # rclone is still sending: the archive itself is damaged
            if rc:
                err.seek(0)
                raise classify_failure(rc, err.read().decode("utf-8", errors="replace")) from exc
            raise
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            proc.stdout.close()


def _archive_member_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """Leave the trash folder and rclone's partial files out of an archive (BACKUP_FILTER)."""
    if info.name == f"./{TRASH_DIR}" or info.name.startswith(f"./{TRASH_DIR}/"):
        return None
    if not info.isdir() and _PARTIAL_FILE.fullmatch(info.name.rsplit("/", 1)[-1]):
        return None
    return info


def _create_archive(source_dir: str, archive_path: str) -> int:
    """tar.gz a folder (runs in a thread); returns the archive size."""
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(source_dir, arcname=".", filter=_archive_member_filter)
    return os.path.getsize(archive_path)


def _install_archive(source: str, target_dir: str, filename: str) -> None:
    """Copy a finished archive into a local target under its final name, atomically (runs in a thread).

    It is written as PARTIAL_ARCHIVE_PREFIX + filename, flushed to disk and
    only then renamed, so a failed copy never leaves a truncated snapshot.
    Partial files of earlier, interrupted runs are removed first.
    """
    os.makedirs(target_dir, exist_ok=True)
    for name in os.listdir(target_dir):
        stale = os.path.join(target_dir, name)
        if name.startswith(PARTIAL_ARCHIVE_PREFIX) and os.path.isfile(stale) and not os.path.islink(stale):
            try:
                os.remove(stale)
            except OSError as exc:
                logger.warning("Could not remove the partial archive %s: %s", stale, exc)
    partial = os.path.join(target_dir, PARTIAL_ARCHIVE_PREFIX + filename)
    try:
        shutil.copy2(source, partial)
        with open(partial, "rb") as fh:
            os.fsync(fh.fileno())
        os.replace(partial, os.path.join(target_dir, filename))
    except BaseException:
        try:
            os.remove(partial)
        except OSError:
            pass
        raise


def _extract_archive(archive_path: str, extract_dir: str) -> None:
    """Extract a tar.gz safely (runs in a thread)."""
    with tarfile.open(archive_path, "r:gz") as tar:
        tar.extractall(extract_dir, filter="data")


class ArchiveMixin(BackupBase):
    """Runs an archive backup and restores from archives (whole, or selected files)."""

    async def _execute_archive(
        self, target: BackupTarget, profile: SyncProfile, timestamp: str,
    ) -> _BackupOutcome:
        """Create tar.gz archive and upload via rclone (through crypt for an encrypted target)."""
        filename = f"backup-{timestamp}.tar.gz"
        temp_dir = tempfile.mkdtemp(prefix="omnisync-backup-")
        temp_path = os.path.join(temp_dir, filename)

        try:
            # tar/gzip of a whole folder takes minutes: never on the event loop
            size = await asyncio.to_thread(_create_archive, profile.local_dir, temp_path)

            if self._plain_local(target):
                await asyncio.to_thread(_install_archive, temp_path, target.target_path, filename)
            else:
                await self._rclone.copy_files(temp_dir, self.storage_root(target), [filename],
                                              rclone_args=self.transfer_args(profile))

            outcome = _BackupOutcome(size, filename)
            if target.verify_after_backup:
                outcome.verify_status, outcome.verify_message = await self._verify_archive(
                    target, temp_path, filename, size,
                )
            return outcome
        finally:
            await asyncio.to_thread(shutil.rmtree, temp_dir, True)

    def _archive_source(self, target: BackupTarget, snapshot_id: str) -> str | list[str]:
        """What _read_archive_stream reads an archive from: its local path, or an rclone cat command."""
        if self._plain_local(target):
            return os.path.join(target.target_path, snapshot_id)
        path = remote_join(self.storage_root(target), snapshot_id)
        return [*self._rclone._base_cmd(), "cat", "--", path]

    async def _restore_archive(
        self,
        target: BackupTarget,
        profile: SyncProfile,
        snapshot_id: str,
        scope: RestoreScope,
    ) -> None:
        """Restore from a tar.gz archive (a full snapshot).

        The destination is made equal to the archive, except for the trash
        folder and the sync marker; every file replaced or removed goes into
        a timestamped pre-restore safety folder on the destination's remote.
        """
        self._check_archive_id(snapshot_id)
        dests = self._restore_destinations(profile, scope)
        args = self.transfer_args(profile)
        temp_dir = await asyncio.to_thread(tempfile.mkdtemp, prefix="omnisync-restore-")
        try:
            # Download archive (decrypted on the way for an encrypted target)
            if self._plain_local(target):
                archive_path = os.path.join(target.target_path, snapshot_id)
            else:
                await self._rclone.copy_files(self.storage_root(target), temp_dir, [snapshot_id], rclone_args=args)
                archive_path = os.path.join(temp_dir, snapshot_id)
                if not await asyncio.to_thread(os.path.isfile, archive_path):
                    raise ValueError(f"Snapshot '{snapshot_id}' not found at the target")

            # Extract (in a thread: this can take minutes)
            extract_dir = os.path.join(temp_dir, "extracted")
            await asyncio.to_thread(_extract_archive, archive_path, extract_dir)

            for dest in dests:
                await self._rclone.sync_with_backup_dir(
                    extract_dir, dest, self.pre_restore_dir(dest),
                    rclone_filter=[TRASH_FILTER, f"- /{SENTINEL_FILE}"], rclone_args=args,
                )
        finally:
            await asyncio.to_thread(shutil.rmtree, temp_dir, True)

    async def _restore_archive_files(
        self, target: BackupTarget, snapshot_id: str, select_path: Callable[[str], bool], dest: str,
        rclone_args: list[str] | None = None,
    ) -> None:
        """Extract only the selected files of an archive (streamed) and copy them into dest (with ``rclone_args``)."""
        temp_dir = await asyncio.to_thread(tempfile.mkdtemp, prefix="omnisync-restore-")
        try:
            extract_dir = os.path.join(temp_dir, "extracted")
            source = self._archive_source(target, snapshot_id)
            extracted = await asyncio.to_thread(
                _read_archive_stream, source, lambda fh: _extract_selected(fh, extract_dir, select_path),
            )
            if not extracted:
                raise RestoreRefused(f"None of the selected files is in {snapshot_id}; nothing was restored.")
            safety = self.pre_restore_dir(dest)
            await self._rclone.copy_files(extract_dir, dest, extracted, backup_dir=safety, rclone_args=rclone_args)
            logger.info("Restored %d file(s) of %s to %s (replaced files kept in %s)",
                        len(extracted), snapshot_id, dest, safety)
        finally:
            await asyncio.to_thread(shutil.rmtree, temp_dir, True)
