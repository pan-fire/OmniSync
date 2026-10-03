"""No secret in logs or API responses.

- Creating a remote whose token is SECRET123 leaves no trace of it in the
  captured logs or in GET /logs.
- The OAuth token never reaches the client; /wizard/create takes the
  session_id of a completed authorization instead.
- Error responses carry a generic message; rclone/OS details go to the log.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.routes import browse, config, conflicts, logs, remotes, wizard
from backend.api.routes import profiles as profiles_routes
from backend.exceptions import ConfigError, RcloneError
from backend.main import LOG_FORMAT, app
from backend.services.log_reader import LogReader
from backend.services.rclone import RcloneService
from backend.services.wizard_sessions import WizardSessionManager
from backend.tests.auth import AUTH_HEADERS

SECRET = "SECRET123"
SEE_LOG = "The OmniSync log has the details."

if shutil.which("rclone") is None and os.environ.get("OMNISYNC_REQUIRE_RCLONE") == "1":
    raise RuntimeError("OMNISYNC_REQUIRE_RCLONE=1 but rclone is not on PATH")
needs_rclone = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH_HEADERS) as c:
        yield c


# --- a real remote creation, end to end ---


@pytest.fixture
def log_capture(tmp_path, caplog):
    """Everything the backend logs, at DEBUG, in caplog and in the file GET /logs serves."""
    log_file = tmp_path / "omnisync.log"
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(handler)
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="backend")
    logs.set_log_reader(LogReader(log_file))
    yield caplog, log_file
    logs.set_log_reader(None)
    root.removeHandler(handler)
    handler.close()


@pytest.fixture
def real_rclone(tmp_path, monkeypatch):
    conf = tmp_path / "rclone.conf"
    service = RcloneService(rclone_config_path=str(conf))
    monkeypatch.setattr(wizard, "_rclone_service", service)
    monkeypatch.setattr(wizard, "_session_manager", WizardSessionManager())
    return service, conf


async def _all_logs(client: AsyncClient) -> str:
    resp = await client.get("/logs", params={"limit": 200})
    assert resp.status_code == 200
    return resp.text


@needs_rclone
async def test_oauth_remote_token_never_logged_or_returned(client, log_capture, real_rclone, monkeypatch):
    caplog, log_file = log_capture
    _, conf = real_rclone
    token = json.dumps({"access_token": f"ya29.{SECRET}", "token_type": "Bearer",
                        "refresh_token": f"1//{SECRET}", "expiry": "2030-01-01T00:00:00Z"})
    monkeypatch.setattr(wizard, "exchange_code_for_token", AsyncMock(return_value=token))
    responses: list[str] = []

    app_params = {"client_id": "own.apps.googleusercontent.com", "client_secret": "own-app-secret"}
    auth = await client.post("/wizard/authorize", json={"provider_id": "drive", **app_params})
    assert auth.status_code == 200
    session_id = auth.json()["session_id"]
    callback = await client.get("/wizard/oauth/callback", params={"state": session_id, "code": "4/0AbCdEfGh"})
    assert callback.status_code == 200
    poll = await client.get(f"/wizard/sessions/{session_id}")
    assert poll.status_code == 200
    assert poll.json()["status"] == "completed"
    assert "token" not in poll.json()
    create = await client.post("/wizard/create", json={
        "name": "gdrive", "provider_id": "drive", "params": app_params, "session_id": session_id,
    })
    assert create.status_code == 200, create.text
    responses += [auth.text, callback.text, poll.text, create.text]

    # The token did reach rclone.conf (so the test exercised the real path)...
    assert SECRET in conf.read_text()
    # ...but no response, log record or /logs entry carries it.
    for body in responses:
        assert SECRET not in body
    assert "gdrive" in caplog.text  # the creation was logged
    assert SECRET not in caplog.text
    assert SECRET not in log_file.read_text()
    served = await _all_logs(client)
    assert "Created remote" in served
    assert SECRET not in served


@needs_rclone
async def test_key_remote_secret_never_logged_or_returned(client, log_capture, real_rclone):
    caplog, log_file = log_capture
    _, conf = real_rclone
    create = await client.post("/wizard/create", json={
        "name": "s3box", "provider_id": "s3",
        "params": {"access_key_id": "AKIAEXAMPLE", "secret_access_key": SECRET},
    })
    assert create.status_code == 200, create.text
    assert SECRET in conf.read_text()
    assert SECRET not in create.text
    assert SECRET not in caplog.text
    assert SECRET not in log_file.read_text()
    assert SECRET not in await _all_logs(client)


# --- the wizard contract ---


@pytest.fixture
def wizard_mock(monkeypatch):
    mock = AsyncMock()
    mock.create_remote = AsyncMock(return_value=None)
    manager = WizardSessionManager()
    monkeypatch.setattr(wizard, "_rclone_service", mock)
    monkeypatch.setattr(wizard, "_session_manager", manager)
    return mock, manager


async def _completed_session(manager: WizardSessionManager, provider: str = "drive", token: str = '{"t":1}'):
    session = await manager.create_session(provider)
    # The app POST /wizard/authorize was started with.
    session.client_id = "own.apps"
    session.client_secret = "own-app-secret"
    session.status = "completed"
    session.token = token
    return session


async def test_session_response_has_no_token(client, wizard_mock):
    _, manager = wizard_mock
    session = await _completed_session(manager, token=SECRET)
    resp = await client.get(f"/wizard/sessions/{session.session_id}")
    assert resp.status_code == 200
    assert "token" not in resp.json()
    assert SECRET not in resp.text


async def test_create_takes_token_from_session_and_ends_it(client, wizard_mock):
    mock, manager = wizard_mock
    session = await _completed_session(manager, token='{"access_token":"abc"}')
    app_params = {"client_id": "own.apps", "client_secret": "own-app-secret"}
    resp = await client.post("/wizard/create", json={
        "name": "gdrive", "provider_id": "drive", "params": app_params, "session_id": session.session_id,
    })
    assert resp.status_code == 200
    mock.create_remote.assert_awaited_once_with(
        "gdrive", "drive", {**app_params, "token": '{"access_token":"abc"}'},
    )
    assert manager.get_session(session.session_id) is None
    assert (await client.get(f"/wizard/sessions/{session.session_id}")).status_code == 404


async def test_create_stores_the_sessions_app_when_params_omit_it(client, wizard_mock):
    mock, manager = wizard_mock
    session = await _completed_session(manager, token='{"access_token":"abc"}')
    resp = await client.post("/wizard/create", json={
        "name": "gdrive", "provider_id": "drive", "session_id": session.session_id,
        "params": {"client_id": "", "client_secret": ""},
    })
    assert resp.status_code == 200, resp.text
    mock.create_remote.assert_awaited_once_with(
        "gdrive", "drive",
        {"client_id": "own.apps", "client_secret": "own-app-secret", "token": '{"access_token":"abc"}'},
    )


@pytest.mark.parametrize("params", [
    {"client_id": "other.apps", "client_secret": "own-app-secret"},
    {"client_id": "own.apps", "client_secret": "other-secret"},
    {"client_id": "other.apps"},
])
async def test_create_refuses_an_app_other_than_the_sessions(client, wizard_mock, params):
    """The token was issued to the session's app: another app could never refresh it."""
    mock, manager = wizard_mock
    session = await _completed_session(manager)
    resp = await client.post("/wizard/create", json={
        "name": "gdrive", "provider_id": "drive", "params": params, "session_id": session.session_id,
    })
    assert resp.status_code == 422
    assert resp.json()["code"] == "oauth_client_mismatch"
    assert "other" not in resp.text
    mock.create_remote.assert_not_awaited()
    assert manager.get_session(session.session_id) is not None


async def test_create_refuses_a_secret_for_an_app_authorized_without_one(client, wizard_mock):
    mock, manager = wizard_mock
    session = await _completed_session(manager, provider="dropbox")
    session.client_secret = None
    resp = await client.post("/wizard/create", json={
        "name": "box", "provider_id": "dropbox", "session_id": session.session_id,
        "params": {"client_id": "own.apps", "client_secret": "added-later"},
    })
    assert resp.status_code == 422
    assert resp.json()["code"] == "oauth_client_mismatch"
    mock.create_remote.assert_not_awaited()


async def test_create_rejects_a_client_supplied_token(client, wizard_mock):
    mock, manager = wizard_mock
    session = await _completed_session(manager)
    resp = await client.post("/wizard/create", json={
        "name": "gdrive", "provider_id": "drive", "params": {},
        "session_id": session.session_id, "token": '{"access_token":"forged"}',
    })
    assert resp.status_code == 422
    mock.create_remote.assert_not_awaited()


async def test_oauth_create_needs_a_session_id(client, wizard_mock):
    mock, _ = wizard_mock
    resp = await client.post("/wizard/create", json={"name": "gdrive", "provider_id": "drive", "params": {}})
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)
    mock.create_remote.assert_not_awaited()


async def test_oauth_create_with_unknown_session_is_404(client, wizard_mock):
    mock, _ = wizard_mock
    resp = await client.post("/wizard/create", json={
        "name": "gdrive", "provider_id": "drive", "params": {}, "session_id": "nope",
    })
    assert resp.status_code == 404
    mock.create_remote.assert_not_awaited()


async def test_oauth_create_with_pending_session_is_409(client, wizard_mock):
    mock, manager = wizard_mock
    session = await manager.create_session("drive")
    resp = await client.post("/wizard/create", json={
        "name": "gdrive", "provider_id": "drive", "params": {}, "session_id": session.session_id,
    })
    assert resp.status_code == 409
    mock.create_remote.assert_not_awaited()
    assert manager.get_session(session.session_id) is not None


async def test_oauth_create_with_other_providers_session_is_422(client, wizard_mock):
    mock, manager = wizard_mock
    session = await _completed_session(manager, provider="dropbox")
    resp = await client.post("/wizard/create", json={
        "name": "gdrive", "provider_id": "drive", "params": {}, "session_id": session.session_id,
    })
    assert resp.status_code == 422
    mock.create_remote.assert_not_awaited()


async def test_failed_create_keeps_the_session_for_a_retry(client, wizard_mock):
    mock, manager = wizard_mock
    mock.create_remote.side_effect = ValueError("Remote 'gdrive' already exists")
    session = await _completed_session(manager)
    resp = await client.post("/wizard/create", json={
        "name": "gdrive", "provider_id": "drive", "session_id": session.session_id,
        "params": {"client_id": "own.apps", "client_secret": "own-app-secret"},
    })
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Remote 'gdrive' already exists"
    assert manager.get_session(session.session_id) is not None


async def test_key_provider_ignores_session_and_needs_no_token(client, wizard_mock):
    mock, _ = wizard_mock
    resp = await client.post("/wizard/create", json={
        "name": "box", "provider_id": "ftp", "params": {"host": "h", "user": "u"},
    })
    assert resp.status_code == 200
    args = mock.create_remote.await_args.args
    assert "token" not in args[2]


# --- generic error details, specifics in the log ---


LEAK = "rclone: Failed to create file system: /home/alice/.config/secret-path SECRET123"


async def test_wizard_create_rclone_error_is_generic(client, wizard_mock, caplog):
    mock, _ = wizard_mock
    mock.create_remote.side_effect = RcloneError(LEAK)
    resp = await client.post("/wizard/create", json={"name": "box", "provider_id": "ftp", "params": {"host": "h", "user": "u"}})
    assert resp.status_code == 500
    assert resp.json()["detail"] == f"Failed to create the remote. {SEE_LOG}"
    assert LEAK in caplog.text


async def test_wizard_create_invalid_params_keeps_structured_detail(client, wizard_mock):
    resp = await client.post("/wizard/create", json={"name": "box", "provider_id": "ftp", "params": {"x": "1"}})
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_params"


@pytest.fixture
def remotes_mock(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(remotes, "_rclone_service", mock)
    return mock


async def test_list_remotes_error_is_generic(client, remotes_mock, caplog):
    remotes_mock.list_remotes.side_effect = RcloneError(LEAK)
    resp = await client.get("/remotes")
    assert resp.status_code == 500
    assert resp.json()["detail"] == f"Failed to list remotes. {SEE_LOG}"
    assert LEAK in caplog.text


async def test_remote_about_error_is_generic(client, remotes_mock, caplog):
    remotes_mock.about.side_effect = RcloneError(LEAK)
    resp = await client.get("/remotes/gdrive/about")
    assert resp.status_code == 503
    assert resp.json()["detail"] == f"Remote unreachable. {SEE_LOG}"
    assert LEAK in caplog.text


async def test_remote_about_unsupported_still_detected(client, remotes_mock):
    remotes_mock.about.side_effect = RcloneError("about not supported by this backend")
    resp = await client.get("/remotes/ftp/about")
    assert resp.status_code == 200
    assert resp.json()["supported"] is False


async def test_delete_remote_errors_are_generic(client, remotes_mock, caplog):
    remotes_mock.delete_remote.side_effect = RcloneError(LEAK)
    resp = await client.delete("/remotes/gdrive", params={"force": "true"})
    assert resp.status_code == 500
    assert SECRET not in resp.text and "/home/alice" not in resp.text
    assert LEAK in caplog.text

    remotes_mock.delete_remote.side_effect = ValueError("Remote 'gdrive' not found")
    resp = await client.delete("/remotes/gdrive", params={"force": "true"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Remote 'gdrive' not found"


async def test_browse_remote_error_is_generic(client, monkeypatch, caplog):
    monkeypatch.setattr(browse, "_rclone_service", AsyncMock())
    monkeypatch.setattr(browse, "_list_remote_dirs", AsyncMock(side_effect=RcloneError(LEAK)))
    resp = await client.get("/browse/remote", params={"path": "gdrive:docs"})
    assert resp.status_code == 500
    assert isinstance(resp.json()["detail"], str)
    assert SECRET not in resp.text and "/home/alice" not in resp.text
    assert LEAK in caplog.text


async def test_browse_local_missing_dir_does_not_echo_path(client, tmp_path, monkeypatch):
    monkeypatch.setenv("OMNISYNC_BROWSE_ROOTS", str(tmp_path))
    resp = await client.get("/browse/local", params={"path": str(tmp_path / "gone")})
    assert resp.status_code == 404
    assert str(tmp_path) not in resp.json()["detail"]


async def test_config_errors_are_generic(client, monkeypatch, caplog):
    svc = SimpleNamespace(
        read_global=lambda: (_ for _ in ()).throw(ConfigError(LEAK)),
        write_global=lambda req: (_ for _ in ()).throw(ConfigError(LEAK)),
    )
    monkeypatch.setattr(config, "_config_service", svc)
    for resp in (await client.get("/config"), await client.put("/config", json={})):
        assert resp.status_code == 500
        assert isinstance(resp.json()["detail"], str)
        assert SECRET not in resp.text and "/home/alice" not in resp.text
    assert LEAK in caplog.text


LEAKY_TEST_SYNC = {
    "success": False,
    "steps": [
        {"step": "local_write", "ok": True},
        {"step": "remote_upload", "ok": False, "error": LEAK},
    ],
    "error": f"Upload failed: {LEAK}",
}


@pytest.fixture
def test_sync_rclone(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(config, "_rclone_service", mock)
    monkeypatch.setattr(profiles_routes, "_rclone_service", mock)
    svc = AsyncMock()
    svc.get_by_slug = AsyncMock(return_value=SimpleNamespace(local_dir="/l", remote_dir="r:x"))
    monkeypatch.setattr(profiles_routes, "_profile_service", svc)
    return mock


async def _post_test_sync(client: AsyncClient, route: str):
    if route == "config":
        return await client.post("/config/test-sync", json={"local_dir": "/l", "remote_dir": "r:x"})
    return await client.post("/profiles/p/config/test-sync")


@pytest.mark.parametrize("route", ["config", "profile"])
async def test_test_sync_failure_is_generic(client, test_sync_rclone, caplog, route):
    test_sync_rclone.test_sync = AsyncMock(return_value=LEAKY_TEST_SYNC)
    resp = await _post_test_sync(client, route)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["steps"] == [{"step": "local_write", "ok": True}, {"step": "remote_upload", "ok": False}]
    assert body["error"] == f"Uploading the test file to the remote failed. {SEE_LOG}"
    assert SECRET not in resp.text and "/home/alice" not in resp.text
    assert LEAK in caplog.text


@pytest.mark.parametrize("route", ["config", "profile"])
async def test_test_sync_exception_is_generic(client, test_sync_rclone, caplog, route):
    test_sync_rclone.test_sync = AsyncMock(side_effect=OSError(LEAK))
    resp = await _post_test_sync(client, route)
    assert resp.status_code == 200
    assert resp.json()["error"] == f"The sync test failed. {SEE_LOG}"
    assert LEAK in caplog.text


@pytest.mark.parametrize("route", ["config", "profile"])
async def test_test_sync_success_passes_through(client, test_sync_rclone, route):
    steps = [{"step": s, "ok": True} for s in ("local_write", "remote_upload", "remote_verify")]
    test_sync_rclone.test_sync = AsyncMock(return_value={"success": True, "steps": steps, "error": None})
    body = (await _post_test_sync(client, route)).json()
    assert body == {"success": True, "steps": steps, "error": None}


async def test_network_check_hides_exception_text(client, monkeypatch, caplog):
    import httpx
    import socket

    class Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            raise httpx.ConnectError(LEAK)

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", Boom)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(OSError(LEAK)))
    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError(LEAK)))
    resp = await client.get("/health/network")
    assert resp.status_code == 200
    body = resp.json()
    assert body["httpx_google"] == {"ok": False, "error": "ConnectError"}
    assert body["dns_google"] == {"ok": False, "error": "OSError"}
    assert SECRET not in resp.text and "/home/alice" not in resp.text
    assert LEAK in caplog.text


async def test_conflict_resolve_rclone_error_is_generic(client, monkeypatch, caplog):
    """rclone stderr from a failed resolve goes to the log, never to the client."""
    from datetime import datetime, timezone
    from unittest.mock import MagicMock

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from backend.db.database import get_session
    from backend.db.models import Base, Conflict, SyncProfile

    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as session:
        profile = SyncProfile(slug="docs", name="Docs", local_dir="/l", remote_dir="r:x",
                              created_at=now, updated_at=now)
        session.add(profile)
        await session.flush()
        conflict = Conflict(profile_id=profile.id, file_path="plan.md", resolved=False)
        session.add(conflict)
        await session.commit()
        conflict_id = conflict.id

    async def _session():
        async with factory() as session:
            yield session

    engine = AsyncMock()
    engine.resolve_conflict.side_effect = RcloneError(LEAK)
    manager = MagicMock()
    manager.get_engine_by_id.return_value = engine
    monkeypatch.setitem(app.dependency_overrides, get_session, _session)
    monkeypatch.setattr(conflicts, "_manager", manager)
    try:
        resp = await client.post(f"/conflicts/{conflict_id}/resolve", json={"resolution": "keep_local"})
    finally:
        await db.dispose()
    assert resp.status_code == 502
    assert resp.json()["detail"] == f"Could not resolve the conflict. {SEE_LOG}"
    assert SECRET not in resp.text and "/home/alice" not in resp.text
    assert LEAK in caplog.text
