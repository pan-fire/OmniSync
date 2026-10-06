"""The sync test of a new profile (ProbeMixin.test_sync) when a step breaks.

The sync test writes a probe file locally, uploads it, checks it and
deletes it again. Whatever step fails, the answer must say which, the run
must not stop half-way with the probe file left in the user's folder, and
a provider failure must never look like a success. Provider calls go to the
in-process fake provider of test_rclone_service.py (httpx.MockTransport);
the rclone fallback runs the real binary against a `local`-type remote.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from backend.api.errors import public_test_sync_result
from backend.exceptions import RcloneError
from backend.services.rclone import RcloneService
from backend.tests.test_rclone_service import (
    FakeProvider,
    FakeRclone,
    oauth_remote,
    serve_drive,
    serve_dropbox,
    serve_onedrive,
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
    """The folder lookup works for the upload and then fails: verify is false, no delete is sent
    blindly, and the cleanup fails too (nothing shows the probe is gone)."""
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
    assert steps(result) == {s: s not in ("remote_verify", "remote_cleanup") for s in STEPS}
    assert not any(r.method == "DELETE" for r in provider.requests)


async def test_drive_file_lookup_failing_after_upload(fake, provider, tmp_path: Path) -> None:
    """The file search fails: verify is false, and the cleanup deletes nothing it has not found
    and fails, since nothing shows the probe is gone."""
    serve_drive(provider)
    search = provider.handlers[("GET", "https://www.googleapis.com/drive/v3/files?")]

    def files_fail(req: httpx.Request) -> httpx.Response:
        return search(req) if FOLDER_QUERY in req.url.params["q"] else httpx.Response(500)

    provider.on("GET", "https://www.googleapis.com/drive/v3/files?", files_fail)
    fake.write_config({"gd": oauth_remote("drive")})
    result = await fake.service().test_sync(str(tmp_path), "gd:docs")
    assert steps(result) == {s: s not in ("remote_verify", "remote_cleanup") for s in STEPS}
    assert result["success"] is False
    assert not any(r.method == "DELETE" for r in provider.requests)


# --- the cleanup checks what the provider answers ---

DROPBOX_DELETE = "https://api.dropboxapi.com/2/files/delete_v2"
ONEDRIVE = "https://graph.microsoft.com/v1.0/me/drive/root:/"
DRIVE_DELETE = "https://www.googleapis.com/drive/v3/files/"
NOT_FOUND = {"error_summary": "path_lookup/not_found/..", "error": {".tag": "path_lookup"}}


def _cleanup_failed(result: dict) -> None:
    """Failed at the cleanup step only, with the same public message as the rclone route."""
    assert result["success"] is False and result["error"] == "One or more steps failed"
    assert steps(result) == {s: s != "remote_cleanup" for s in STEPS}
    public = public_test_sync_result(result, logging.getLogger("test"))
    assert public.error is not None and public.error.startswith("The test file could not be removed from the remote.")


@pytest.mark.parametrize(("remote_type", "serve", "method", "url", "answer"), [
    ("dropbox", serve_dropbox, "POST", DROPBOX_DELETE, httpx.Response(500, json={})),
    ("dropbox", serve_dropbox, "POST", DROPBOX_DELETE,
     httpx.Response(409, json={"error_summary": "path/restricted_content/..", "error": {}})),
    ("onedrive", serve_onedrive, "DELETE", ONEDRIVE, httpx.Response(403, json={})),
    ("drive", serve_drive, "DELETE", DRIVE_DELETE, httpx.Response(500, json={})),
], ids=["dropbox-500", "dropbox-409-other", "onedrive-403", "drive-500"])
async def test_refused_api_delete_fails_the_cleanup(
    fake, provider, tmp_path: Path, remote_type, serve, method, url, answer,
) -> None:
    """The provider refuses the delete: the probe stays there, so the test must not pass."""
    serve(provider)
    provider.on(method, url, lambda req: answer)
    fake.write_config({"r": oauth_remote(remote_type)})
    result = await fake.service().test_sync(str(tmp_path / "local"), "r:docs")
    _cleanup_failed(result)
    cleanup = next(s for s in result["steps"] if s["step"] == "remote_cleanup")
    assert f"HTTP {answer.status_code}" in cleanup["error"]


@pytest.mark.parametrize(("remote_type", "serve", "method", "url", "answer"), [
    ("dropbox", serve_dropbox, "POST", DROPBOX_DELETE, httpx.Response(409, json=NOT_FOUND)),
    ("onedrive", serve_onedrive, "DELETE", ONEDRIVE, httpx.Response(404, json={})),
    ("drive", serve_drive, "DELETE", DRIVE_DELETE, httpx.Response(404, json={})),
], ids=["dropbox-not-found", "onedrive-404", "drive-404"])
async def test_already_gone_counts_as_cleaned_up(
    fake, provider, tmp_path: Path, remote_type, serve, method, url, answer,
) -> None:
    """"Not found" on delete means the probe is gone: that is what the cleanup wants."""
    serve(provider)
    provider.on(method, url, lambda req: answer)
    fake.write_config({"r": oauth_remote(remote_type)})
    result = await fake.service().test_sync(str(tmp_path / "local"), "r:docs")
    assert result["success"] is True, result


async def test_drive_cleanup_never_creates_folders(fake, provider, tmp_path: Path) -> None:
    """The probe's folder vanished after the check: nothing to delete, so the cleanup succeeds,
    and it does not create the folder again just to look into it."""
    serve_drive(provider)
    search = provider.handlers[("GET", "https://www.googleapis.com/drive/v3/files?")]
    vanished_at: list[int] = []

    def verify_then_vanish(req: httpx.Request) -> httpx.Response:
        answer = search(req)
        if FOLDER_QUERY not in req.url.params["q"] and not vanished_at:  # the verify step's file search
            provider.folders.clear()  # e.g. removed by someone else meanwhile
            provider.files.clear()
            vanished_at.append(len(provider.requests))
        return answer

    provider.on("GET", "https://www.googleapis.com/drive/v3/files?", verify_then_vanish)
    fake.write_config({"gd": oauth_remote("drive")})
    result = await fake.service().test_sync(str(tmp_path / "local"), "gd:a/b")
    assert result["success"] is True, result
    after = provider.requests[vanished_at[0]:]
    assert after and all(r.method == "GET" for r in after)  # looked, created and deleted nothing


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
async def test_rclone_cleanup_failure_is_reported(
    local_remote, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing `rclone deletefile` fails the test, as a failed delete on a provider API does:
    the probe is still on the remote, and the user must hear about it, with the step named."""
    service, remote = local_remote
    spy_rclone(service, monkeypatch, fail="deletefile")
    local = tmp_path / "local"
    result = await service.test_sync(str(local), "disk:remote")
    assert result["success"] is False and result["error"] == "One or more steps failed"
    assert steps(result) == {s: s != "remote_cleanup" for s in STEPS}
    left = [p.name for p in remote.iterdir()]
    assert len(left) == 1 and left[0].startswith(".omnisync-test-")
    cleanup = next(s for s in result["steps"] if s["step"] == "remote_cleanup")
    assert cleanup["error"].startswith(f"disk:remote/{left[0]} could not be removed")
    assert list(local.iterdir()) == []

    public = public_test_sync_result(result, logging.getLogger("test"))
    assert public.error is not None and public.error.startswith("The test file could not be removed from the remote.")


@needs_rclone
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
    assert list(remote.iterdir()) == []  # and the probe is gone again


@pytest.mark.parametrize(("remote_dir", "rclone_dir", "api_dir"), [
    ("disk:/abs/dir", "disk:/abs/dir/", "abs/dir/"),  # absolute stays absolute for rclone
    ("disk:/", "disk:/", ""),
    ("disk:rel/dir/", "disk:rel/dir/", "rel/dir/"),
    ("disk:", "disk:", ""),  # the remote's root (or home folder)
])
async def test_rclone_and_api_paths_each_keep_their_form(
    fake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remote_dir: str, rclone_dir: str, api_dir: str,
) -> None:
    """rclone gets the profile's path as written; the provider APIs get it from the drive root."""
    seen: dict[str, tuple[str, str]] = {}

    async def upload(remote_name, remote_file, local_path, rclone_target):
        seen["upload"] = (remote_file, rclone_target)
        return False

    service = fake.service()
    monkeypatch.setattr(service, "_test_upload", upload)
    await service.test_sync(str(tmp_path / "local"), remote_dir)
    remote_file, rclone_target = seen["upload"]
    name = remote_file.rsplit("/", 1)[-1]
    assert name.startswith(".omnisync-test-")
    assert (remote_file, rclone_target) == (api_dir + name, rclone_dir + name)
