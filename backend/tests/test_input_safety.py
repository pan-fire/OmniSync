"""Tests for the input checks that keep caller-supplied values away from
rclone's option parser, rclone.conf, the filesystem and the network."""

from __future__ import annotations

import os
import stat
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from backend.api.routes import wizard
from backend.api.schemas import (
    ProfileCreateRequest,
    ProfileUpdateRequest,
    PushSubscriptionRequest,
)
from backend.main import app
from backend.services.provider_registry import get_provider, validate_remote_name, validate_remote_params
from backend.services.rclone import RcloneService
from backend.tests.auth import AUTH_HEADERS

VALID_PROFILE = {"name": "Docs", "local_dir": "/home/me/Docs", "remote_dir": "gdrive:Docs"}


# --- Profile fields that reach rclone's command line ---


@pytest.mark.parametrize("local_dir", ["--log-file=/tmp/x", "-v", "relative/path", "~/Sync", "/ok\n/bad"])
def test_local_dir_must_be_absolute_and_single_line(local_dir):
    with pytest.raises(ValidationError):
        ProfileCreateRequest(**{**VALID_PROFILE, "local_dir": local_dir})


@pytest.mark.parametrize("remote_dir", ["--config=/tmp/evil:x", "-gdrive:x", "gdrive", "/local/path", "g drive:x"])
def test_remote_dir_must_name_a_remote(remote_dir):
    with pytest.raises(ValidationError):
        ProfileCreateRequest(**{**VALID_PROFILE, "remote_dir": remote_dir})


@pytest.mark.parametrize("args", [
    ["--sftp-ssh", "sh -c id"],
    ["--password-command=id"],
    ["--config", "/tmp/rclone.conf"],
    ["--log-file=/home/me/.bashrc"],
    ["-v"],
    ["stray-value"],
])
def test_unsafe_rclone_args_are_rejected(args):
    with pytest.raises(ValidationError):
        ProfileCreateRequest(**{**VALID_PROFILE, "rclone_args": args})


def test_safe_rclone_args_are_accepted():
    args = ["--bwlimit", "1M", "--transfers=4", "--max-delete", "50", "--fast-list"]
    assert ProfileCreateRequest(**{**VALID_PROFILE, "rclone_args": args}).rclone_args == args


def test_filter_rules_keep_leading_dash_but_not_line_breaks():
    ok = ProfileCreateRequest(**{**VALID_PROFILE, "rclone_filter": ["- *.tmp", "+ docs/**"]})
    assert ok.rclone_filter == ["- *.tmp", "+ docs/**"]
    with pytest.raises(ValidationError):
        ProfileCreateRequest(**{**VALID_PROFILE, "rclone_filter": ["- *.tmp\n--config=x"]})


def test_update_request_is_checked_too():
    with pytest.raises(ValidationError):
        ProfileUpdateRequest(local_dir="--log-file=/tmp/x")
    assert ProfileUpdateRequest(name="only the name").local_dir is None


def test_filters_are_passed_as_single_tokens():
    cmd = RcloneService(rclone_config_path="/tmp/x.conf")._build_command(["sync", "/a", "r:b"], rclone_filter=["- *.tmp"])
    assert "--filter=- *.tmp" in cmd
    assert "- *.tmp" not in cmd


@pytest.mark.parametrize(("name", "ok"), [("gdrive", True), ("my_s3-2", True), ("-config", False), ("a b", False), ("", False)])
def test_remote_names_cannot_start_with_a_dash(name, ok):
    assert validate_remote_name(name) is ok


# --- Remote creation: only the provider's own settings reach rclone.conf ---


def test_unknown_config_keys_are_rejected():
    sftp = get_provider("sftp")
    assert sftp is not None
    errors = validate_remote_params(sftp, {"host": "h", "ssh": "sh -c 'id'"}, has_token=False)
    assert errors == ["'ssh' is not a SFTP setting"]


def test_line_breaks_in_values_are_rejected():
    s3 = get_provider("s3")
    assert s3 is not None
    errors = validate_remote_params(s3, {"region": "eu\n[evil]\ntype = local"}, has_token=False)
    assert errors == ["'region' may not contain a line break"]


def test_token_only_for_oauth_providers():
    s3 = get_provider("s3")
    drive = get_provider("drive")
    assert s3 is not None and drive is not None
    assert validate_remote_params(s3, {}, has_token=True)
    assert validate_remote_params(drive, {}, has_token=True) == []


@pytest.fixture
async def wizard_client():
    mock = AsyncMock()
    mock.create_remote = AsyncMock(return_value=None)
    mock.obscure = AsyncMock(side_effect=lambda v: f"obscured({v})")
    original = wizard._rclone_service
    wizard._rclone_service = mock
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH_HEADERS) as client:
        yield client, mock
    wizard._rclone_service = original


async def test_create_rejects_unknown_key_with_422(wizard_client):
    client, mock = wizard_client
    resp = await client.post("/wizard/create", json={
        "name": "box", "provider_id": "sftp", "params": {"host": "h", "ssh": "sh -c id"},
    })
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_params"
    mock.create_remote.assert_not_awaited()


async def test_create_obscures_passwords_and_drops_empty_values(wizard_client):
    client, mock = wizard_client
    resp = await client.post("/wizard/create", json={
        "name": "box", "provider_id": "sftp",
        "params": {"host": "h", "user": "me", "pass": "hunter2", "port": ""},
    })
    assert resp.status_code == 200
    mock.create_remote.assert_awaited_once_with("box", "sftp", {"host": "h", "user": "me", "pass": "obscured(hunter2)"})


# --- rclone.conf is written owner-only ---


async def test_rclone_conf_is_written_owner_only(tmp_path):
    conf = tmp_path / "rclone.conf"
    service = RcloneService(rclone_config_path=str(conf))
    service.list_remotes = AsyncMock(return_value=[])  # type: ignore[method-assign]
    service._run = AsyncMock()  # type: ignore[method-assign]
    await service.create_remote("s3box", "s3", {"access_key_id": "AKIA", "secret_access_key": "secret"})
    assert stat.S_IMODE(conf.stat().st_mode) == 0o600
    assert "secret_access_key = secret" in conf.read_text()


async def test_obscure_matches_rclone(tmp_path):
    import shutil

    if shutil.which("rclone") is None:
        pytest.skip("rclone not installed")
    service = RcloneService(rclone_config_path=str(tmp_path / "rclone.conf"))
    obscured = await service.obscure("hunter2")
    assert obscured and obscured != "hunter2"


# --- Directory browsing is confined to the allowed roots ---


@pytest.fixture
async def browse_client(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "Documents").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    monkeypatch.setenv("OMNISYNC_BROWSE_ROOTS", str(root))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH_HEADERS) as client:
        yield client, root, tmp_path


async def test_browse_inside_root_lists_directories(browse_client):
    client, root, _ = browse_client
    resp = await client.get("/browse/local", params={"path": str(root)})
    assert resp.status_code == 200
    assert [e["name"] for e in resp.json()["entries"]] == ["Documents"]
    assert resp.json()["parent"] is None  # no way up out of the root


@pytest.mark.parametrize("path", ["/etc", "/", "{tmp}/outside", "{root}/../outside"])
async def test_browse_outside_roots_is_403(browse_client, path):
    client, root, tmp = browse_client
    resp = await client.get("/browse/local", params={"path": path.format(tmp=tmp, root=root)})
    assert resp.status_code == 403


async def test_browse_symlink_out_of_root_is_403(browse_client):
    client, root, tmp = browse_client
    (root / "escape").symlink_to(tmp / "outside")
    resp = await client.get("/browse/local", params={"path": str(root / "escape")})
    assert resp.status_code == 403


async def test_browse_sibling_with_root_as_prefix_is_403(browse_client):
    # "/x/root-other" starts with "/x/root" but is not inside it.
    client, root, _ = browse_client
    sibling = root.parent / (root.name + "-other")
    sibling.mkdir()
    resp = await client.get("/browse/local", params={"path": str(sibling)})
    assert resp.status_code == 403


async def test_browse_root_slash_allows_everything(browse_client, monkeypatch):
    client, root, _ = browse_client
    monkeypatch.setenv("OMNISYNC_BROWSE_ROOTS", "/")
    resp = await client.get("/browse/local", params={"path": str(root)})
    assert resp.status_code == 200
    assert resp.json()["current"] == os.path.realpath(root)
    assert (await client.get("/browse/local", params={"path": "/"})).json()["parent"] is None


# --- Web Push endpoints must be real push services ---


@pytest.mark.parametrize("endpoint", [
    "https://fcm.googleapis.com/fcm/send/abc",
    "https://updates.push.services.mozilla.com/wpush/v2/abc",
    "https://web.push.apple.com/abc",
    "https://db5p.notify.windows.com/w/?token=abc",
])
def test_push_service_endpoints_are_accepted(endpoint):
    assert PushSubscriptionRequest(endpoint=endpoint, keys={}).endpoint == endpoint


@pytest.mark.parametrize("endpoint", [
    "http://fcm.googleapis.com/fcm/send/abc",       # not https
    "https://127.0.0.1:8000/sync/push",             # loopback
    "https://192.168.1.1/admin",                    # LAN
    "https://169.254.169.254/latest/meta-data",     # cloud metadata
    "https://evil.example/fcm.googleapis.com",      # suffix in the path only
    "https://fcm.googleapis.com.evil.example/x",    # suffix trick
])
def test_other_push_endpoints_are_rejected(endpoint):
    with pytest.raises(ValidationError):
        PushSubscriptionRequest(endpoint=endpoint, keys={})


def test_extra_push_hosts_can_be_allowed(monkeypatch):
    monkeypatch.setenv("OMNISYNC_PUSH_HOSTS", "push.example.org")
    assert PushSubscriptionRequest(endpoint="https://a.push.example.org/x", keys={})
