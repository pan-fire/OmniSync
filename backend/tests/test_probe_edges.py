"""The sync test of a new profile (ProbeMixin.test_sync) when a step breaks.

The sync test writes a probe file locally, uploads it, checks it and
deletes it again. Whatever step fails, the answer must say which, the run
must not stop half-way with the probe file left in the user's folder, and
a provider failure must never look like a success. Provider calls go to the
in-process fake provider of test_rclone_service.py (httpx.MockTransport);
the rclone fallback runs the real binary against a `local`-type remote.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from backend.exceptions import RcloneError
from backend.services.rclone import RcloneService
from backend.tests.test_rclone_service import (
    FakeProvider,
    FakeRclone,
    oauth_remote,
    serve_drive,
    serve_dropbox,
)

if shutil.which("rclone") is None and os.environ.get("OMNISYNC_REQUIRE_RCLONE") == "1":
    raise RuntimeError("OMNISYNC_REQUIRE_RCLONE=1 but rclone is not on PATH")
needs_rclone = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")

STEPS = ["local_write", "remote_upload", "remote_verify", "remote_cleanup", "local_cleanup"]
FOLDER_QUERY = "mimeType='application/vnd.google-apps.folder'"


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRclone:
    return FakeRclone(tmp_path, monkeypatch)


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    fake_provider = FakeProvider()
    real_client = httpx.AsyncClient

    def client(**kwargs) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(fake_provider), **kwargs)

    monkeypatch.setattr("backend.services.rclone.probe.httpx.AsyncClient", client)
    return fake_provider


def failing(exc: type[httpx.TransportError]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(req: httpx.Request) -> httpx.Response:
        raise exc("provider unreachable", request=req)
    return handler


def steps(result: dict) -> dict[str, bool]:
    return {s["step"]: s["ok"] for s in result["steps"]}


# --- a step raises ---


async def test_upload_crash_stops_and_removes_the_probe_file(fake, provider, tmp_path: Path) -> None:
    """A transport error during the upload ends the test there; the local probe file is gone."""
    provider.on("POST", "https://content.dropboxapi.com/2/files/upload", failing(httpx.ConnectError))
    fake.write_config({"box": oauth_remote("dropbox")})
    local = tmp_path / "local"
    result = await fake.service().test_sync(str(local), "box:docs")
    assert result["success"] is False and result["error"].startswith("Upload failed")
    assert steps(result) == {"local_write": True, "remote_upload": False}
    assert list(local.iterdir()) == []


async def test_verify_crash_still_cleans_up_both_sides(fake, provider, tmp_path: Path) -> None:
    """The check cannot reach the provider: reported as failed, and the probe is still deleted everywhere."""
    serve_dropbox(provider)
    provider.on("POST", "https://api.dropboxapi.com/2/files/get_metadata", failing(httpx.ReadTimeout))
    fake.write_config({"box": oauth_remote("dropbox")})
    local = tmp_path / "local"
    result = await fake.service().test_sync(str(local), "box:docs")
    assert result["success"] is False and result["error"] == "One or more steps failed"
    assert steps(result) == {s: s != "remote_verify" for s in STEPS}
    assert provider.files == {}  # deleted on the remote
    assert list(local.iterdir()) == []


async def test_cleanup_crash_is_reported_not_success(fake, provider, tmp_path: Path) -> None:
    """The probe could not be deleted from the remote: the test fails (a stray file is left there)."""
    serve_dropbox(provider)
    provider.on("POST", "https://api.dropboxapi.com/2/files/delete_v2", failing(httpx.ConnectError))
    fake.write_config({"box": oauth_remote("dropbox")})
    local = tmp_path / "local"
    result = await fake.service().test_sync(str(local), "box:docs")
    assert result["success"] is False
    assert steps(result) == {s: s != "remote_cleanup" for s in STEPS}
    assert list(local.iterdir()) == []


def test_local_cleanup_never_raises(fake, tmp_path: Path) -> None:
    """Removing the probe file is best effort: a path that cannot be unlinked is left, without an error."""
    folder = tmp_path / "a folder"
    folder.mkdir()
    fake.service()._cleanup_local(folder)  # IsADirectoryError, swallowed
    fake.service()._cleanup_local(tmp_path / "missing")
    assert folder.is_dir()


# --- which way the probe goes ---


async def test_unreadable_token_falls_back_to_rclone(fake, provider, tmp_path: Path) -> None:
    """A Drive remote whose token is not JSON is probed through rclone, not the provider API."""
    fake.write_config({"gd": {"type": "drive", "token": "{not json"}})
    result = await fake.service().test_sync(str(tmp_path), "gd:docs")
    assert result["success"] is True
    assert [argv[2] for argv in fake.calls] == ["copyto", "lsf", "deletefile"]
    assert provider.requests == []


@pytest.mark.parametrize(("remote_dir", "folders"), [
    ("gd:", []),  # the probe goes into the Drive root
    ("gd:a//b/", [("folder1", "b"), ("root", "a")]),  # empty path parts are skipped
])
async def test_drive_paths_at_the_root_and_with_empty_parts(
    fake, provider, tmp_path: Path, remote_dir: str, folders: list,
) -> None:
    """Odd remote paths still put the probe in the folder meant, and clean it up."""
    serve_drive(provider)
    fake.write_config({"gd": oauth_remote("drive")})
    result = await fake.service().test_sync(str(tmp_path), remote_dir)
    assert result["success"] is True, result
    assert sorted(provider.folders) == folders
    assert provider.files == {}


async def test_drive_folder_that_cannot_be_created_fails_the_upload(fake, provider, tmp_path: Path) -> None:
    """Creating the target folder fails: the upload is reported failed, nothing is left locally."""
    provider.on("GET", "https://www.googleapis.com/drive/v3/files?",
                lambda req: httpx.Response(200, json={"files": []}))
    provider.on("POST", "https://www.googleapis.com/drive/v3/files", lambda req: httpx.Response(403, json={}))
    fake.write_config({"gd": oauth_remote("drive")})
    local = tmp_path / "local"
    result = await fake.service().test_sync(str(local), "gd:docs")
    assert result["error"] == "Failed to upload test file to remote"
    assert list(local.iterdir()) == []


async def test_drive_folder_lookup_failing_after_upload(fake, provider, tmp_path: Path) -> None:
    """The folder lookup works for the upload and then fails: verify is false and no delete is sent blindly."""
    serve_drive(provider)
    search = provider.handlers[("GET", "https://www.googleapis.com/drive/v3/files?")]
    folder_lookups = 0

    def flaky(req: httpx.Request) -> httpx.Response:
        nonlocal folder_lookups
        if FOLDER_QUERY in req.url.params["q"]:
            folder_lookups += 1
            if folder_lookups > 1:
                return httpx.Response(503)
        return search(req)

    provider.on("GET", "https://www.googleapis.com/drive/v3/files?", flaky)
    fake.write_config({"gd": oauth_remote("drive")})
    result = await fake.service().test_sync(str(tmp_path), "gd:docs")
    assert result["success"] is False
    assert steps(result) == {s: s != "remote_verify" for s in STEPS}
    assert not any(r.method == "DELETE" for r in provider.requests)


async def test_drive_file_lookup_failing_after_upload(fake, provider, tmp_path: Path) -> None:
    """The file search fails: verify is false and the cleanup deletes nothing it has not found."""
    serve_drive(provider)
    search = provider.handlers[("GET", "https://www.googleapis.com/drive/v3/files?")]

    def files_fail(req: httpx.Request) -> httpx.Response:
        return search(req) if FOLDER_QUERY in req.url.params["q"] else httpx.Response(500)

    provider.on("GET", "https://www.googleapis.com/drive/v3/files?", files_fail)
    fake.write_config({"gd": oauth_remote("drive")})
    result = await fake.service().test_sync(str(tmp_path), "gd:docs")
    assert steps(result)["remote_verify"] is False and result["success"] is False
    assert not any(r.method == "DELETE" for r in provider.requests)


# --- the rclone fallback, against real rclone ---


@pytest.fixture
def local_remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[RcloneService, Path]:
    """A `local` remote; the working directory is a temp folder, so a relative remote path stays in it."""
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    remote.mkdir()
    conf = tmp_path / "rclone.conf"
    conf.write_text("[disk]\ntype = local\n")
    return RcloneService(rclone_config_path=str(conf)), remote


def spy_rclone(service: RcloneService, monkeypatch: pytest.MonkeyPatch, fail: str = "") -> list[list[str]]:
    """Record each rclone command (subcommand + positional args); ``fail`` fails as rclone would."""
    real_run = service._run
    calls: list[list[str]] = []

    async def run(cmd, *args, **kwargs):
        calls.append([cmd[0], *kwargs.get("positional", [])])
        if cmd[0] == fail:
            raise RcloneError(f"rclone {fail} failed")
        return await real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(service, "_run", run)
    return calls


@needs_rclone
async def test_rclone_round_trip_leaves_nothing_behind(local_remote, tmp_path: Path) -> None:
    """The fallback probe really reaches the remote folder and removes itself from both sides."""
    service, remote = local_remote
    local = tmp_path / "local"
    (remote / "sub dir").mkdir()
    result = await service.test_sync(str(local), "disk:remote/sub dir")
    assert result["success"] is True and steps(result) == dict.fromkeys(STEPS, True)
    assert list(local.iterdir()) == []
    assert list((remote / "sub dir").iterdir()) == []


@needs_rclone
async def test_rclone_verify_failure_fails_the_test_and_still_cleans_up(
    local_remote, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe cannot be found after the upload: not a success, and the uploaded probe is removed."""
    service, remote = local_remote
    spy_rclone(service, monkeypatch, fail="lsf")
    result = await service.test_sync(str(tmp_path / "local"), "disk:remote")
    assert result["success"] is False
    assert steps(result) == {s: s != "remote_verify" for s in STEPS}
    assert list(remote.iterdir()) == []


@needs_rclone
async def test_rclone_cleanup_failure_is_best_effort(
    local_remote, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing `rclone deletefile` does not fail the test (current behaviour: the probe stays on the remote)."""
    service, remote = local_remote
    spy_rclone(service, monkeypatch, fail="deletefile")
    local = tmp_path / "local"
    result = await service.test_sync(str(local), "disk:remote")
    assert result["success"] is True
    assert [p.name.startswith(".omnisync-test-") for p in remote.iterdir()] == [True]
    assert list(local.iterdir()) == []


@needs_rclone
@pytest.mark.xfail(strict=True, reason="probe.py strips the leading '/' of the remote path: "
                   "'disk:/abs/dir' is probed as 'disk:abs/dir' (relative to the remote's home or cwd)")
async def test_rclone_probe_goes_to_an_absolute_remote_folder(
    local_remote, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absolute remote path (sftp, local, smb) is probed there, not at the same path under the home folder."""
    service, remote = local_remote
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)  # where a wrongly relative path would land
    calls = spy_rclone(service, monkeypatch)
    result = await service.test_sync(str(tmp_path / "local"), f"disk:{remote}")
    assert result["success"] is True
    assert calls[0][0] == "copyto" and calls[0][2].startswith(f"disk:{remote}/.omnisync-test-")
    assert list(cwd.iterdir()) == []  # nothing made outside the remote folder
