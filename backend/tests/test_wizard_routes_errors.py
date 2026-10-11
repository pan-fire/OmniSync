"""Error answers of the /wizard routes: OAuth sessions, create, reconnect and test.

Same set-up as test_wizard.py (a mocked RcloneService, a fresh session
manager); the OAuth token endpoint and Microsoft Graph are an
httpx.MockTransport. Each case checks the status and error code, that the
remote config was not written (create_remote / update_remote not called),
and that no exception text or token reaches the client.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.routes import wizard
from backend.exceptions import RcloneError
from backend.main import app
from backend.services import oauth, remote_auth
from backend.services.wizard_sessions import WizardSession, WizardSessionManager
from backend.tests.auth import AUTH_HEADERS

LEAK = "stderr: token=secret-value /home/someone/.config"
TOKEN = json.dumps({"access_token": "ya29.server-side-only", "token_type": "Bearer"})


@pytest.fixture
def rclone() -> AsyncMock:
    mock = AsyncMock()
    mock.list_remotes = AsyncMock(return_value=[])
    mock.remote_section = AsyncMock(return_value=None)
    mock.obscure = AsyncMock(side_effect=lambda value: f"obscured-{len(value)}")
    return mock


@pytest.fixture
async def client(monkeypatch, rclone):
    monkeypatch.delenv("OMNISYNC_OAUTH_REDIRECT_URI", raising=False)
    monkeypatch.setattr(wizard, "_rclone_service", rclone)
    monkeypatch.setattr(wizard, "_session_manager", WizardSessionManager())
    remote_auth.reset()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH_HEADERS) as c:
        yield c
    remote_auth.reset()


def assert_error(resp, status: int, code: str) -> None:
    assert resp.status_code == status, resp.text
    assert resp.json()["code"] == code
    for secret in ("secret-value", "/home/someone", "ya29."):
        assert secret not in resp.text


async def completed(provider_id: str = "drive", remote_name: str | None = None, **changes) -> WizardSession:
    """A session whose sign-in finished, as the callback leaves it."""
    session = await wizard._session_manager.create_session(provider_id)
    session.status, session.token, session.client_id = "completed", TOKEN, "own-app"
    session.remote_name = remote_name
    for key, value in changes.items():
        setattr(session, key, value)
    return session


# --- authorize ---


async def test_provider_without_consent_url_cancels_the_session(client, monkeypatch):
    """No consent URL could be built: 400, and the session it opened does not linger."""
    monkeypatch.setattr(wizard, "build_auth_url", lambda **kwargs: None)
    resp = await client.post("/wizard/authorize", json={"provider_id": "drive", "client_id": "id",
                                                        "client_secret": "s"})
    assert_error(resp, 400, "oauth_not_supported")
    assert wizard._session_manager.active_count == 0


@pytest.mark.parametrize(("section", "body", "status", "code"), [
    (None, {"provider_id": "drive", "remote_name": "-x"}, 422, "invalid_remote_name"),
    (None, {"provider_id": "s3", "remote_name": "box"}, 422, "oauth_not_supported"),
    (None, {"provider_id": "drive", "remote_name": "gone"}, 404, "remote_not_found"),
    ({"type": "dropbox"}, {"provider_id": "drive", "remote_name": "box"}, 422, "provider_mismatch"),
    (OSError(LEAK), {"provider_id": "drive", "remote_name": "gd"}, 500, "internal_error"),
])
async def test_reconnect_authorize_refusals(client, rclone, section, body, status, code):
    """A reconnect only starts for an existing OAuth remote of that provider; no session is opened otherwise."""
    if isinstance(section, Exception):
        rclone.remote_section = AsyncMock(side_effect=section)
    else:
        rclone.remote_section = AsyncMock(return_value=section)
    assert_error(await client.post("/wizard/authorize", json=body), status, code)
    assert wizard._session_manager.active_count == 0


# --- callback ---


async def test_denied_consent_changes_nothing(client):
    """The user pressed 'Cancel' at the provider: a page says so, the session stays pending."""
    session = await wizard._session_manager.create_session("drive")
    resp = await client.get("/wizard/oauth/callback", params={"error": "access_denied", "state": session.session_id})
    assert resp.status_code == 200 and "Authorization denied" in resp.text
    assert session.status == "pending" and session.code_used is False


@pytest.mark.parametrize(("params", "status", "text"), [
    ({"code": "c"}, 400, "Missing parameters"),
    ({"state": "s"}, 400, "Missing parameters"),
    ({"code": "c", "state": "unknown"}, 404, "Session expired or not found"),
])
async def test_callback_without_a_usable_session(client, params, status, text):
    resp = await client.get("/wizard/oauth/callback", params=params)
    assert resp.status_code == status and text in resp.text


async def test_onedrive_reconnect_reads_the_drive_when_the_config_cannot_be_read(client, rclone, monkeypatch):
    """Whether the remote already has a drive is unknown: the drive is looked up rather than left out."""
    graph: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.microsoft.com":
            graph.append(request.url.path)
            return httpx.Response(200, json={"id": "b!drive-1", "driveType": "personal"})
        return httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600})

    monkeypatch.setattr(oauth, "_http_transport", httpx.MockTransport(answer))
    session = await wizard._session_manager.create_session("onedrive")
    session.client_id, session.redirect_uri, session.remote_name = "own-app", "http://test/cb", "od"
    rclone.remote_section = AsyncMock(side_effect=OSError(LEAK))
    resp = await client.get("/wizard/oauth/callback", params={"code": "c", "state": session.session_id})
    assert resp.status_code == 200 and "Authorization successful" in resp.text
    assert session.status == "completed"
    assert session.extra_config == {"drive_id": "b!drive-1", "drive_type": "personal"}
    assert graph == ["/v1.0/me/drive"]
    assert LEAK not in resp.text


# --- sessions ---


async def test_unknown_session_is_404_for_status_and_cancel(client):
    assert_error(await client.get("/wizard/sessions/nope"), 404, "wizard_session_not_found")
    assert_error(await client.delete("/wizard/sessions/nope"), 404, "wizard_session_not_found")


async def test_cancelled_session_is_gone(client):
    """After a cancel the session's token can no longer be used for a remote."""
    session = await completed()
    resp = await client.delete(f"/wizard/sessions/{session.session_id}")
    assert resp.status_code == 200
    assert_error(await client.get(f"/wizard/sessions/{session.session_id}"), 404, "wizard_session_not_found")
    resp = await client.post("/wizard/create", json={"name": "gd", "provider_id": "drive",
                                                     "session_id": session.session_id})
    assert_error(resp, 404, "wizard_session_not_found")


# --- create ---


SFTP = {"name": "box", "provider_id": "sftp", "params": {"host": "h.example", "user": "me", "pass": "pw"}}
CRYPT = {"name": "vault", "provider_id": "crypt", "params": {"remote": "box:v", "password": "pw"}}


async def test_invalid_name_is_refused_before_anything_runs(client, rclone):
    resp = await client.post("/wizard/create", json={**SFTP, "name": "-x"})
    assert_error(resp, 422, "invalid_remote_name")
    rclone.obscure.assert_not_awaited()
    rclone.create_remote.assert_not_awaited()


@pytest.mark.parametrize(("exc", "status", "code"), [
    (ValueError("Remote 'box' already exists"), 409, "remote_exists"),
    (ValueError("bad option: " + LEAK), 422, "invalid_params"),
    (RcloneError(LEAK), 500, "internal_error"),
    (FileNotFoundError(LEAK), 503, "service_unavailable"),
])
async def test_create_failures(client, rclone, exc, status, code, caplog):
    """Each way writing the remote can fail has its own answer; rclone's text stays in the log."""
    rclone.create_remote = AsyncMock(side_effect=exc)
    with caplog.at_level(logging.WARNING, logger="backend.api.routes.wizard"):
        assert_error(await client.post("/wizard/create", json=SFTP), status, code)


@pytest.mark.parametrize(("exc", "status", "code"), [
    (FileNotFoundError("rclone"), 503, "service_unavailable"),
    (RcloneError(LEAK), 500, "internal_error"),
])
async def test_password_that_cannot_be_obscured_is_never_stored(client, rclone, exc, status, code):
    """Without `rclone obscure` the password would be stored in clear text: nothing is created."""
    rclone.obscure = AsyncMock(side_effect=exc)
    assert_error(await client.post("/wizard/create", json=SFTP), status, code)
    rclone.create_remote.assert_not_awaited()


@pytest.mark.parametrize(("exc", "status", "code"), [
    (FileNotFoundError("rclone"), 503, "service_unavailable"),
    (RuntimeError(LEAK), 500, "internal_error"),
])
async def test_crypt_needs_the_remote_list(client, rclone, exc, status, code):
    """A crypt remote is checked against the existing remotes; if they cannot be listed, nothing is created."""
    rclone.list_remotes = AsyncMock(side_effect=exc)
    assert_error(await client.post("/wizard/create", json=CRYPT), status, code)
    rclone.create_remote.assert_not_awaited()


# --- reconnect ---


async def test_reconnect_refusals(client, rclone):
    """Only a completed sign-in started for exactly this remote can replace its token."""
    other = await completed(remote_name="other")
    pending = await completed(remote_name="gd", status="pending", token=None)
    key_based = await completed(provider_id="s3", remote_name="gd")
    cases = [
        ({"name": "-x", "session_id": other.session_id}, 422, "invalid_remote_name"),
        ({"name": "gd", "session_id": "unknown"}, 404, "wizard_session_not_found"),
        ({"name": "gd", "session_id": other.session_id}, 422, "session_mismatch"),
        ({"name": "gd", "session_id": pending.session_id}, 409, "authorization_pending"),
        ({"name": "gd", "session_id": key_based.session_id}, 422, "wrong_oauth_flow"),
    ]
    for body, status, code in cases:
        assert_error(await client.post("/wizard/reconnect", json=body), status, code)
    rclone.update_remote.assert_not_awaited()


@pytest.mark.parametrize(("exc", "status", "code"), [
    (ValueError("Remote 'gd' not found"), 409, "remote_changed"),
    (OSError(LEAK), 500, "internal_error"),
])
async def test_reconnect_write_failure_keeps_the_session(client, rclone, exc, status, code):
    """The token could not be stored: the error says so, the sign-in stays usable for a retry."""
    session = await completed(remote_name="gd")
    remote_auth.mark_auth_failed("gd")
    rclone.update_remote = AsyncMock(side_effect=exc)
    resp = await client.post("/wizard/reconnect", json={"name": "gd", "session_id": session.session_id})
    assert_error(resp, status, code)
    assert wizard._session_manager.get_session(session.session_id) is session
    assert remote_auth.auth_failed("gd")  # still failing: nothing changed


# --- test ---


async def test_connection_test_without_rclone_is_503(client, rclone):
    rclone.check_remote = AsyncMock(side_effect=FileNotFoundError("rclone"))
    assert_error(await client.post("/wizard/test", json={"name": "box"}), 503, "service_unavailable")


async def test_connection_test_crash_is_a_failed_test_without_details(client, rclone, caplog):
    """An unexpected error is a failed test (200), the exception text only in the log."""
    rclone.check_remote = AsyncMock(side_effect=RuntimeError(LEAK))
    with caplog.at_level(logging.ERROR, logger="backend.api.routes.wizard"):
        resp = await client.post("/wizard/test", json={"name": "box"})
    assert resp.status_code == 200 and resp.json()["success"] is False
    assert "secret-value" not in resp.text
    assert "test_remote: unexpected error for 'box'" in caplog.text


# --- authentication ---


@pytest.mark.parametrize(("method", "path", "body"), [
    ("POST", "/wizard/create", SFTP), ("POST", "/wizard/authorize", {"provider_id": "drive"}),
    ("POST", "/wizard/reconnect", {"name": "gd", "session_id": "x"}), ("GET", "/wizard/sessions/x", None),
    ("POST", "/wizard/test", {"name": "box"}),
])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong-token"}])
async def test_wizard_routes_need_the_token(client, rclone, method, path, body, headers):
    """Without the right token no remote is created or tested and no sign-in starts."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as anon:
        resp = await anon.request(method, path, json=body)
    assert resp.status_code == 401
    rclone.create_remote.assert_not_awaited()
    rclone.check_remote.assert_not_awaited()
    assert wizard._session_manager.active_count == 0
