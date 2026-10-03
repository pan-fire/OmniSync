"""Backups and restores remove their temporary folders on every path: success, errors and cancellation."""

from __future__ import annotations

import asyncio
import os
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.api.schemas import BackupTargetType
from backend.db.models import BackupTarget, SyncProfile
from backend.exceptions import RcloneError
from backend.services.backup_service import BackupService, common
from backend.services.backup_service.common import scratch_dir

PREFIXES = ("omnisync-backup-", "omnisync-restore-", "omnisync-manifest-")


@pytest.fixture
def tmp_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """mkdtemp creates its folders here, so a test sees exactly what was left behind."""
    root = tmp_path / "tmp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


def _left(root: Path) -> list[str]:
    return sorted(name for name in os.listdir(root) if name.startswith(PREFIXES))


def _service(rclone: AsyncMock) -> BackupService:
    return BackupService(rclone, AsyncMock(), MagicMock(), MagicMock())


def _remote_target() -> BackupTarget:
    return BackupTarget(
        id=1, profile_id=1, name="t", target_path="remote:backups",
        target_type=BackupTargetType.REMOTE.value, verify_after_backup=False,
    )


def _profile(local_dir: Path) -> SyncProfile:
    return SyncProfile(id=1, name="p", slug="p", local_dir=str(local_dir), remote_dir="remote:data")


class TestScratchDir:
    @pytest.mark.asyncio
    async def test_removed_after_the_block(self, tmp_root: Path) -> None:
        async with scratch_dir("omnisync-backup-") as path:
            Path(path, "f").write_text("x")
            assert _left(tmp_root)
        assert _left(tmp_root) == []

    @pytest.mark.asyncio
    async def test_removed_on_error(self, tmp_root: Path) -> None:
        with pytest.raises(RuntimeError):
            async with scratch_dir("omnisync-restore-"):
                raise RuntimeError("boom")
        assert _left(tmp_root) == []

    @pytest.mark.asyncio
    async def test_removed_on_cancellation(self, tmp_root: Path) -> None:
        entered = asyncio.Event()

        async def body() -> None:
            async with scratch_dir("omnisync-manifest-"):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(body())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert _left(tmp_root) == []

    @pytest.mark.asyncio
    async def test_removed_when_cancelled_again_during_the_removal(
        self, tmp_root: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A second cancel while the removal thread is awaited still leaves no folder."""
        removing = asyncio.Event()

        async def stuck_to_thread(*_args: object, **_kwargs: object) -> None:
            removing.set()
            await asyncio.Event().wait()

        entered = asyncio.Event()

        async def body() -> None:
            async with scratch_dir("omnisync-backup-"):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(body())
        await entered.wait()
        monkeypatch.setattr(common.asyncio, "to_thread", stuck_to_thread)
        task.cancel()
        await removing.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert _left(tmp_root) == []


class TestServicePaths:
    @pytest.mark.asyncio
    async def test_archive_backup_upload_error(self, tmp_root: Path, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("a")
        rclone = AsyncMock()
        rclone.copy_files.side_effect = RcloneError("upload failed")
        with pytest.raises(RcloneError):
            await _service(rclone)._execute_archive(_remote_target(), _profile(src), "2026-01-01T00-00-00")
        assert _left(tmp_root) == []

    @pytest.mark.asyncio
    async def test_archive_backup_cancelled_during_upload(self, tmp_root: Path, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("a")
        uploading = asyncio.Event()

        async def hang(*_args: object, **_kwargs: object) -> None:
            uploading.set()
            await asyncio.Event().wait()

        rclone = AsyncMock()
        rclone.copy_files.side_effect = hang
        task = asyncio.create_task(
            _service(rclone)._execute_archive(_remote_target(), _profile(src), "2026-01-01T00-00-00"),
        )
        await uploading.wait()
        assert _left(tmp_root)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert _left(tmp_root) == []

    @pytest.mark.asyncio
    async def test_archive_restore_download_error(self, tmp_root: Path, tmp_path: Path) -> None:
        rclone = AsyncMock()
        rclone.copy_files.side_effect = RcloneError("download failed")
        service = _service(rclone)
        service._restore_destinations = MagicMock(return_value=[str(tmp_path)])  # type: ignore[method-assign]
        with pytest.raises(RcloneError):
            await service._restore_archive(
                _remote_target(), _profile(tmp_path), "backup-2026-01-01T00-00-00.tar.gz", MagicMock(),
            )
        assert _left(tmp_root) == []

    @pytest.mark.asyncio
    async def test_archive_restore_missing_download(self, tmp_root: Path, tmp_path: Path) -> None:
        """rclone succeeded but wrote nothing: the restore is refused and the folder removed."""
        service = _service(AsyncMock())
        service._restore_destinations = MagicMock(return_value=[str(tmp_path)])  # type: ignore[method-assign]
        with pytest.raises(ValueError):
            await service._restore_archive(
                _remote_target(), _profile(tmp_path), "backup-2026-01-01T00-00-00.tar.gz", MagicMock(),
            )
        assert _left(tmp_root) == []

    @pytest.mark.asyncio
    async def test_selected_files_restore_refused(self, tmp_root: Path, tmp_path: Path) -> None:
        archive = tmp_path / "backup-2026-01-01T00-00-00.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            (tmp_path / "a.txt").write_text("a")
            tar.add(tmp_path / "a.txt", arcname="a.txt")
        target = BackupTarget(
            id=1, profile_id=1, name="t", target_path=str(tmp_path),
            target_type=BackupTargetType.LOCAL.value, verify_after_backup=False,
        )
        with pytest.raises(common.RestoreRefused):
            await _service(AsyncMock())._restore_archive_files(
                target, archive.name, lambda _path: False, str(tmp_path / "dest"),
            )
        assert _left(tmp_root) == []

    @pytest.mark.asyncio
    async def test_manifest_upload_error(self, tmp_root: Path) -> None:
        rclone = AsyncMock()
        rclone.copyto.side_effect = RcloneError("upload failed")
        with pytest.raises(RcloneError):
            await _service(rclone)._write_manifest("remote:backups", "2026-01-01T00-00-00", [])
        assert _left(tmp_root) == []

    @pytest.mark.asyncio
    async def test_manifest_cancelled_during_upload(self, tmp_root: Path) -> None:
        uploading = asyncio.Event()

        async def hang(*_args: object, **_kwargs: object) -> None:
            uploading.set()
            await asyncio.Event().wait()

        rclone = AsyncMock()
        rclone.copyto.side_effect = hang
        task = asyncio.create_task(_service(rclone)._write_manifest("remote:backups", "2026-01-01T00-00-00", []))
        await uploading.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert _left(tmp_root) == []
