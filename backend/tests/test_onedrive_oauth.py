"""OneDrive sign-in with the user's own Azure app, and the drive lookup.

The sign-in uses the user's own (public client) Azure app: a client ID and
no secret. After a successful exchange the wizard reads the account's drive
from Microsoft Graph (GET /me/drive) and stores drive_id and drive_type in
the remote, as rclone's own config flow does.

The token endpoint and Graph are faked with an httpx.MockTransport; the
remotes are written to a temporary rclone.conf by the real RcloneService and
read back with the real rclone.
"""

from __future__ import annotations

import asyncio
import configparser
import json
import logging
import shutil
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.routes import wizard
from backend.main import app
from backend.services import oauth
from backend.services.oauth import (
    GRAPH_ME_DRIVE_URL,
    OneDriveDriveError,
    fetch_onedrive_drive,
)
from backend.services.rclone import RcloneService
from backend.services.wizard_sessions import WizardSessionManager
from backend.tests.auth import AUTH_HEADERS

pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="needs the rclone binary")

CODE = "M.C552_BAY.2.U.example-code"
ACCESS_TOKEN = "EwB4A8l6BAAU-never-leaves-the-server"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
DRIVE_ID = "b!xYz-AbC_123"
OLD_TOKEN = json.dumps({"access_token": "old", "token_type": "Bearer", "refresh_token": "r-old",
                        "expiry": "2020-01-01T00:00:00Z"})

APP_ID = "00000000-1111-2222-3333-444444444444"
APP = {"client_id": APP_ID}


class FakeMicrosoft:
    """The token endpoint and Graph's /me/drive, recording what they were sent."""

    def __init__(self) -> None:
        self.token_status = 200
        self.token_body: object = {
            "access_token": ACCESS_TOKEN, "token_type": "Bearer", "expires_in": 3599, "refresh_token": "r-new",
        }
        self.drive_status = 200
        self.drive_body: object = {"id": DRIVE_ID, "driveType": "business", "owner": {"user": {}}}
        self.token_requests: list[str] = []
        self.drive_requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == TOKEN_URL:
            self.token_requests.append(request.content.decode())
            return httpx.Response(self.token_status, json=self.token_body)
        if url == GRAPH_ME_DRIVE_URL:
            self.drive_requests.append(request)
            if isinstance(self.drive_body, str):
                return httpx.Response(self.drive_status, text=self.drive_body)
            return httpx.Response(self.drive_status, json=self.drive_body)
        return httpx.Response(599, text=f"unexpected request to {url}")


@pytest.fixture
def microsoft(monkeypatch) -> FakeMicrosoft:
    fake = FakeMicrosoft()
    monkeypatch.setattr(oauth, "_http_transport", httpx.MockTransport(fake))
    return fake


@pytest.fixture
def conf(tmp_path) -> Path:
    return tmp_path / "rclone.conf"


@pytest.fixture
async def client(monkeypatch, conf):
    monkeypatch.delenv("OMNISYNC_OAUTH_REDIRECT_URI", raising=False)
    monkeypatch.setattr(wizard, "_rclone_service", RcloneService(rclone_config_path=str(conf)))
    monkeypatch.setattr(wizard, "_session_manager", WizardSessionManager())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH_HEADERS) as c:
        yield c


def _write_conf(path: Path, sections: dict[str, dict[str, str]]) -> None:
    config = configparser.RawConfigParser()
    config.optionxform = str  # type: ignore[assignment,method-assign]
    for name, options in sections.items():
        config[name] = options
    with path.open("w") as f:
        config.write(f)


async def _rclone_show(conf: Path, name: str) -> dict[str, str]:
    """The remote's settings as the real rclone reads them."""
    proc = await asyncio.create_subprocess_exec(
        "rclone", "--config", str(conf), "config", "dump",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    assert proc.returncode == 0, err.decode()
    return json.loads(out)[name]


async def _sign_in(client: AsyncClient, **body: str) -> dict:
    """Start a OneDrive sign-in with the own app (a reconnect: the remote's
    stored one) and finish it as Microsoft's redirect to the callback would;
    the session's status afterwards."""
    app_creds = {} if "remote_name" in body else APP
    start = await client.post("/wizard/authorize", json={"provider_id": "onedrive", **app_creds, **body})
    assert start.status_code == 200, start.text
    sid = start.json()["session_id"]
    done = await client.get("/wizard/oauth/callback", params={"code": CODE, "state": sid})
    assert done.status_code == 200, done.text
    resp = await client.get(f"/wizard/sessions/{sid}")
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestPublicClient:
    async def test_exchange_sends_the_client_id_and_verifier_but_no_secret(self, client, microsoft) -> None:
        body = await _sign_in(client)
        assert body["status"] == "completed"
        sent = dict(httpx.QueryParams(microsoft.token_requests[0]))
        assert sent["client_id"] == APP_ID and sent["code_verifier"]
        assert sent["redirect_uri"] == "http://test/wizard/oauth/callback"
        assert "client_secret" not in sent

    async def test_a_refused_exchange_is_a_plain_failure(self, client, conf, microsoft) -> None:
        microsoft.token_status = 401
        microsoft.token_body = {"error": "invalid_client", "error_codes": [7000218],
                                "error_description": "AADSTS7000218: client_secret required"}
        body = await _sign_in(client)
        assert body["status"] == "failed" and body["error_code"] is None
        assert "AADSTS" not in json.dumps(body)
        assert microsoft.drive_requests == []
        assert not conf.exists()


# --- GET /me/drive ---


class TestFetchDrive:
    async def test_reads_the_drive_with_the_access_token(self, microsoft) -> None:
        token = json.dumps({"access_token": ACCESS_TOKEN})
        assert await fetch_onedrive_drive(token) == {"drive_id": DRIVE_ID, "drive_type": "business"}
        request = microsoft.drive_requests[0]
        assert str(request.url) == "https://graph.microsoft.com/v1.0/me/drive"
        assert request.method == "GET"
        assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"

    @pytest.mark.parametrize("drive_type", ["personal", "business", "documentLibrary"])
    async def test_every_rclone_drive_type_is_kept(self, microsoft, drive_type) -> None:
        microsoft.drive_body = {"id": "0123456789abcdef", "driveType": drive_type}
        drive = await fetch_onedrive_drive(json.dumps({"access_token": ACCESS_TOKEN}))
        assert drive == {"drive_id": "0123456789abcdef", "drive_type": drive_type}

    @pytest.mark.parametrize("status, body", [
        (404, {"error": {"code": "itemNotFound", "message": f"mysite not found {ACCESS_TOKEN}"}}),
        (403, {"error": {"code": "accessDenied"}}),
        (500, "<html>oops</html>"),
        (200, "not json"),
        (200, ["a", "list"]),
        (200, {"driveType": "personal"}),
        (200, {"id": DRIVE_ID, "driveType": "sharepoint"}),
        (200, {"id": DRIVE_ID}),
        # A value that would break out of its rclone.conf line.
        (200, {"id": "abc\n[evil]\ntype = local", "driveType": "personal"}),
        (200, {"id": "", "driveType": "personal"}),
        (200, {"id": 42, "driveType": "personal"}),
    ])
    async def test_unusable_answers_raise_and_log_no_token(self, microsoft, caplog, status, body) -> None:
        microsoft.drive_status = status
        microsoft.drive_body = body
        with caplog.at_level(logging.ERROR), pytest.raises(OneDriveDriveError) as raised:
            await fetch_onedrive_drive(json.dumps({"access_token": ACCESS_TOKEN}))
        assert raised.value.code == "onedrive_drive_lookup_failed"
        assert ACCESS_TOKEN not in caplog.text

    @pytest.mark.parametrize("token", ["not json", "[]", "{}", json.dumps({"access_token": ""})])
    async def test_a_token_without_access_token_raises_without_a_request(self, microsoft, token) -> None:
        with pytest.raises(OneDriveDriveError):
            await fetch_onedrive_drive(token)
        assert microsoft.drive_requests == []

    async def test_a_network_error_raises(self, monkeypatch) -> None:
        def fail(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        monkeypatch.setattr(oauth, "_http_transport", httpx.MockTransport(fail))
        with pytest.raises(OneDriveDriveError):
            await fetch_onedrive_drive(json.dumps({"access_token": ACCESS_TOKEN}))

    async def test_a_redirect_is_not_followed(self, monkeypatch) -> None:
        seen: list[str] = []

        def answer(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})

        monkeypatch.setattr(oauth, "_http_transport", httpx.MockTransport(answer))
        with pytest.raises(OneDriveDriveError):
            await fetch_onedrive_drive(json.dumps({"access_token": ACCESS_TOKEN}))
        assert seen == [GRAPH_ME_DRIVE_URL]


# --- The wizard stores the drive ---


class TestWizardStoresTheDrive:
    async def test_new_remote_gets_drive_id_and_type(self, client, conf, microsoft) -> None:
        body = await _sign_in(client)
        assert body["status"] == "completed" and body["error_code"] is None
        assert len(microsoft.drive_requests) == 1

        create = await client.post("/wizard/create", json={
            "name": "onedrive", "provider_id": "onedrive", "params": APP, "session_id": body["session_id"],
        })
        assert create.status_code == 200, create.text
        section = await _rclone_show(conf, "onedrive")
        assert section["type"] == "onedrive" and section["client_id"] == APP_ID
        assert "client_secret" not in section
        assert section["drive_id"] == DRIVE_ID and section["drive_type"] == "business"
        assert json.loads(section["token"])["access_token"] == ACCESS_TOKEN

    async def test_failed_lookup_fails_the_step_and_creates_nothing(self, client, conf, microsoft) -> None:
        microsoft.drive_status = 404
        microsoft.drive_body = {"error": {"code": "itemNotFound"}}
        body = await _sign_in(client)
        assert body["status"] == "failed"
        assert body["error_code"] == "onedrive_drive_lookup_failed"
        assert ACCESS_TOKEN not in json.dumps(body)
        session = wizard._session_manager.get_session(body["session_id"])
        assert session is not None and session.token is None

        create = await client.post("/wizard/create", json={
            "name": "onedrive", "provider_id": "onedrive", "params": APP, "session_id": body["session_id"],
        })
        assert create.status_code == 409
        assert not conf.exists()

    async def test_callback_flow_shows_the_lookup_failure(self, client, microsoft) -> None:
        microsoft.drive_status = 403
        microsoft.drive_body = {"error": {"code": "accessDenied"}}
        start = await client.post("/wizard/authorize", json={"provider_id": "onedrive", **APP})
        sid = start.json()["session_id"]
        resp = await client.get("/wizard/oauth/callback", params={"code": CODE, "state": sid})
        assert resp.status_code == 200
        assert "OneDrive could not be read" in resp.text
        assert wizard._session_manager.get_session(sid).error_code == "onedrive_drive_lookup_failed"

    async def test_other_providers_do_not_ask_graph(self, client, monkeypatch) -> None:
        seen: list[str] = []

        def answer(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json={"access_token": ACCESS_TOKEN, "expires_in": 3599})

        monkeypatch.setattr(oauth, "_http_transport", httpx.MockTransport(answer))
        start = await client.post("/wizard/authorize", json={"provider_id": "dropbox", "client_id": "own-key"})
        sid = start.json()["session_id"]
        await client.get("/wizard/oauth/callback", params={"code": CODE, "state": sid})
        assert (await client.get(f"/wizard/sessions/{sid}")).json()["status"] == "completed"
        assert seen == ["https://api.dropboxapi.com/oauth2/token"]


class TestReconnect:
    async def test_adds_the_drive_when_the_remote_lacks_it(self, client, conf, microsoft) -> None:
        _write_conf(conf, {"od": {"type": "onedrive", "client_id": APP_ID, "token": OLD_TOKEN}})
        body = await _sign_in(client, remote_name="od")
        assert body["status"] == "completed"
        assert len(microsoft.drive_requests) == 1

        resp = await client.post("/wizard/reconnect", json={"name": "od", "session_id": body["session_id"]})
        assert resp.status_code == 200, resp.text
        section = await _rclone_show(conf, "od")
        assert section["drive_id"] == DRIVE_ID and section["drive_type"] == "business"
        assert json.loads(section["token"])["access_token"] == ACCESS_TOKEN

    async def test_keeps_a_drive_the_remote_has(self, client, conf, microsoft) -> None:
        # E.g. a SharePoint library chosen in rclone's own config.
        _write_conf(conf, {"od": {"type": "onedrive", "client_id": APP_ID, "token": OLD_TOKEN,
                                  "drive_id": "b!library", "drive_type": "documentLibrary"}})
        body = await _sign_in(client, remote_name="od")
        assert body["status"] == "completed"
        assert microsoft.drive_requests == []

        assert (await client.post("/wizard/reconnect", json={
            "name": "od", "session_id": body["session_id"],
        })).status_code == 200
        section = await _rclone_show(conf, "od")
        assert section["drive_id"] == "b!library" and section["drive_type"] == "documentLibrary"
        assert json.loads(section["token"])["access_token"] == ACCESS_TOKEN

    async def test_failed_lookup_leaves_the_remote_alone(self, client, conf, microsoft) -> None:
        _write_conf(conf, {"od": {"type": "onedrive", "client_id": APP_ID, "token": OLD_TOKEN,
                                  "drive_type": "personal"}})
        microsoft.drive_status = 500
        microsoft.drive_body = {}
        body = await _sign_in(client, remote_name="od")
        assert body["status"] == "failed" and body["error_code"] == "onedrive_drive_lookup_failed"

        resp = await client.post("/wizard/reconnect", json={"name": "od", "session_id": body["session_id"]})
        assert resp.status_code == 409
        section = await _rclone_show(conf, "od")
        assert section["token"] == OLD_TOKEN and "drive_id" not in section
