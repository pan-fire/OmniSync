"""GET /browse/remote: the folder paths it returns go back to rclone as typed."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.api.routes import browse
from backend.services.rclone import RcloneResult

LSD = (
    "          -1 2026-01-15 10:30:45        -1 Backups\n"
    "          -1 2026-01-15 10:30:45        -1 My Docs\n"
)


@pytest.fixture
def lsd(monkeypatch):
    service = MagicMock()
    service._run = AsyncMock(return_value=RcloneResult(stdout=LSD, stderr="", return_code=0, elapsed_seconds=0.1))
    monkeypatch.setattr(browse, "_rclone_service", service)
    return service._run


@pytest.mark.asyncio
@pytest.mark.parametrize(("remote_path", "expected"), [
    # At the root no leading /: on SFTP or a local-backed remote "nas:/Backups"
    # is the file system root, not the folder the listing showed.
    ("", ["nas:Backups", "nas:My Docs"]),
    ("work", ["nas:work/Backups", "nas:work/My Docs"]),
    ("work/", ["nas:work/Backups", "nas:work/My Docs"]),
    # An absolute path the user asked for stays absolute.
    ("/srv", ["nas:/srv/Backups", "nas:/srv/My Docs"]),
])
async def test_listed_folder_paths_stay_on_the_listed_folder(lsd, remote_path, expected):
    entries = await browse._list_remote_dirs("nas", remote_path)
    assert [e.path for e in entries] == expected
    assert [e.name for e in entries] == ["Backups", "My Docs"]
    assert lsd.await_args.kwargs["positional"] == [f"nas:{remote_path}" if remote_path else "nas:"]
