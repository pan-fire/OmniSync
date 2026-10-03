"""Unit tests for the Remote Setup Wizard API endpoints.

Tests cover provider listing, remote creation, OAuth authorization,
connection testing, session limits, and rclone availability checks.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from backend.tests.auth import AUTH_HEADERS

from backend.api.routes import wizard
from backend.exceptions import RcloneError
from backend.main import app


@pytest.fixture
def mock_rclone_service() -> AsyncMock:
    """Create a mock RcloneService for wizard tests."""
    mock = AsyncMock()
    mock.create_remote = AsyncMock(return_value=None)
    mock.check_remote = AsyncMock(return_value=True)
    mock.list_remotes = AsyncMock(return_value=[])
    mock.authorize = AsyncMock()
    return mock


@pytest.fixture
async def wizard_client(mock_rclone_service: AsyncMock):
    """httpx AsyncClient with mocked rclone service wired into wizard module."""
    original_service = wizard._rclone_service
    original_manager = wizard._session_manager

    wizard._rclone_service = mock_rclone_service
    wizard._session_manager = wizard.WizardSessionManager()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=AUTH_HEADERS) as client:
        yield client

    wizard._rclone_service = original_service
    wizard._session_manager = original_manager


# --- Provider list returns all 7 providers ---


class TestListProviders:
    """GET /wizard/providers returns the full provider list."""

    @pytest.mark.asyncio
    async def test_returns_all_providers(
        self, wizard_client: AsyncClient
    ) -> None:
        response = await wizard_client.get("/wizard/providers")
        assert response.status_code == 200
        providers = response.json()
        assert len(providers) == 10

        provider_ids = {p["id"] for p in providers}
        expected = {"drive", "dropbox", "onedrive", "s3", "b2", "sftp", "ftp", "webdav", "smb", "crypt"}
        assert provider_ids == expected

    @pytest.mark.asyncio
    async def test_provider_has_required_fields(
        self, wizard_client: AsyncClient
    ) -> None:
        response = await wizard_client.get("/wizard/providers")
        providers = response.json()
        for p in providers:
            assert "id" in p
            assert "display_name" in p
            assert "icon" in p
            assert "auth_type" in p
            assert p["auth_type"] in ("key", "oauth")
            assert "fields" in p
            assert "default_name" in p


# --- Key-based remote creation happy path ---


class TestCreateRemote:
    """POST /wizard/create with valid key-based params succeeds."""

    @pytest.mark.asyncio
    async def test_key_based_creation_success(
        self,
        wizard_client: AsyncClient,
        mock_rclone_service: AsyncMock,
    ) -> None:
        response = await wizard_client.post(
            "/wizard/create",
            json={
                "name": "my-s3",
                "provider_id": "s3",
                "params": {
                    "access_key_id": "AKIAIOSFODNN7EXAMPLE",
                    "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                },
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert "my-s3" in body["detail"]
        mock_rclone_service.create_remote.assert_awaited_once_with(
            "my-s3",
            "s3",
            {
                "access_key_id": "AKIAIOSFODNN7EXAMPLE",
                "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            },
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", [
        "omnisync_backup_crypt_1", "OMNISYNC_BACKUP_CRYPT_x", "omnisync-backup-crypt-2",
        "omnisync_bisync_1_local", "OmniSync-Bisync-x",
    ])
    async def test_reserved_name_is_refused(
        self,
        wizard_client: AsyncClient,
        mock_rclone_service: AsyncMock,
        name: str,
    ) -> None:
        response = await wizard_client.post(
            "/wizard/create",
            json={"name": name, "provider_id": "s3", "params": {"access_key_id": "AKIA", "secret_access_key": "x"}},
        )
        assert response.status_code == 422
        assert "reserved" in response.json()["detail"]
        mock_rclone_service.create_remote.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_400(
        self, wizard_client: AsyncClient
    ) -> None:
        response = await wizard_client.post(
            "/wizard/create",
            json={
                "name": "test-remote",
                "provider_id": "nonexistent",
                "params": {},
            },
        )
        assert response.status_code == 400
        assert "Unknown provider" in response.json()["detail"]


# --- OAuth authorize starts subprocess and returns auth_url ---


class TestAuthorize:
    """POST /wizard/authorize builds OAuth consent URL and returns session + auth_url."""

    @pytest.mark.asyncio
    async def test_authorize_returns_session_and_auth_url(
        self,
        wizard_client: AsyncClient,
        mock_rclone_service: AsyncMock,
    ) -> None:
        response = await wizard_client.post(
            "/wizard/authorize",
            json={"provider_id": "drive", "client_id": "own.apps", "client_secret": "own-secret"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "session_id" in body
        assert "auth_url" in body
        assert body["auth_url"].startswith("https://accounts.google.com")

    @pytest.mark.asyncio
    async def test_authorize_with_custom_credentials(
        self,
        wizard_client: AsyncClient,
        mock_rclone_service: AsyncMock,
    ) -> None:
        response = await wizard_client.post(
            "/wizard/authorize",
            json={
                "provider_id": "drive",
                "client_id": "custom-id",
                "client_secret": "custom-secret",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert "client_id=custom-id" in body["auth_url"]

    @pytest.mark.asyncio
    async def test_authorize_unknown_provider_returns_400(
        self, wizard_client: AsyncClient
    ) -> None:
        response = await wizard_client.post(
            "/wizard/authorize",
            json={"provider_id": "nonexistent"},
        )
        assert response.status_code == 400
        assert "Unknown provider" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_authorize_key_based_provider_returns_400(
        self, wizard_client: AsyncClient
    ) -> None:
        response = await wizard_client.post(
            "/wizard/authorize",
            json={"provider_id": "s3"},
        )
        assert response.status_code == 400
        assert "OAuth not supported" in response.json()["detail"]


class TestRedirectUri:
    """The provider must send the browser back to an address it can reach."""

    @staticmethod
    async def _authorize(client: AsyncClient, headers: dict[str, str] | None = None) -> str:
        from urllib.parse import parse_qs, urlparse

        # Own client credentials: the callback flow, which needs this address.
        response = await client.post(
            "/wizard/authorize",
            json={"provider_id": "drive", "client_id": "own.apps.googleusercontent.com", "client_secret": "s"},
            headers=headers or {},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        redirect = parse_qs(urlparse(body["auth_url"]).query)["redirect_uri"][0]
        # The token exchange uses the same URI, and the answer names it.
        assert wizard._session_manager.get_session(body["session_id"]).redirect_uri == redirect
        assert body["redirect_uri"] == redirect
        # GET /wizard/oauth/redirect-uri tells the user the same address up front.
        shown = await client.get("/wizard/oauth/redirect-uri", headers=headers or {})
        assert shown.status_code == 200 and shown.json() == {"redirect_uri": redirect}
        return redirect

    @pytest.mark.asyncio
    async def test_direct_request_uses_its_own_address(self, wizard_client, monkeypatch) -> None:
        monkeypatch.delenv("OMNISYNC_OAUTH_REDIRECT_URI", raising=False)
        assert await self._authorize(wizard_client) == "http://test/wizard/oauth/callback"

    @pytest.mark.asyncio
    async def test_through_the_web_ui_uses_the_browsers_address(self, wizard_client, monkeypatch) -> None:
        """The web UI proxy forwards where the browser came from; the callback is under /api there."""
        monkeypatch.delenv("OMNISYNC_OAUTH_REDIRECT_URI", raising=False)
        redirect = await self._authorize(wizard_client, {
            "X-Forwarded-Host": "sync.example.com",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Prefix": "/api",
        })
        assert redirect == "https://sync.example.com/api/wizard/oauth/callback"

        redirect = await self._authorize(wizard_client, {
            "X-Forwarded-Host": "localhost:3000, proxy.internal",
            "X-Forwarded-Proto": "http",
            "X-Forwarded-Prefix": "/api/",
        })
        assert redirect == "http://localhost:3000/api/wizard/oauth/callback"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("headers", [
        {"X-Forwarded-Host": "evil.example/path"},
        {"X-Forwarded-Host": "user@evil.example"},
        {"X-Forwarded-Host": ""},
    ])
    async def test_malformed_forwarded_host_is_ignored(self, wizard_client, monkeypatch, headers) -> None:
        monkeypatch.delenv("OMNISYNC_OAUTH_REDIRECT_URI", raising=False)
        assert await self._authorize(wizard_client, headers) == "http://test/wizard/oauth/callback"

    @pytest.mark.asyncio
    async def test_odd_proto_and_prefix_fall_back(self, wizard_client, monkeypatch) -> None:
        monkeypatch.delenv("OMNISYNC_OAUTH_REDIRECT_URI", raising=False)
        redirect = await self._authorize(wizard_client, {
            "X-Forwarded-Host": "localhost:3000",
            "X-Forwarded-Proto": "javascript",
            "X-Forwarded-Prefix": "//evil.example",
        })
        assert redirect == "http://localhost:3000/wizard/oauth/callback"

    @pytest.mark.asyncio
    async def test_explicit_setting_wins(self, wizard_client, monkeypatch) -> None:
        monkeypatch.setenv("OMNISYNC_OAUTH_REDIRECT_URI", "https://omnisync.lan/api/wizard/oauth/callback")
        redirect = await self._authorize(wizard_client, {"X-Forwarded-Host": "localhost:3000"})
        assert redirect == "https://omnisync.lan/api/wizard/oauth/callback"

    @pytest.mark.asyncio
    async def test_callback_is_public_behind_the_proxy(self, wizard_client) -> None:
        """The proxied callback carries no browser token; the OAuth state protects it."""
        from httpx import ASGITransport

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bare:
            response = await bare.get(
                "/wizard/oauth/callback", params={"state": "unknown", "code": "x"},
                headers={"X-Forwarded-Host": "localhost:3000", "X-Forwarded-Proto": "http"},
            )
        assert response.status_code == 404  # reached the route: unknown session
        assert "Session expired" in response.text


# --- Connection test success and failure paths ---


class TestConnectionTest:
    """POST /wizard/test returns success/failure based on rclone check_remote."""

    @pytest.mark.asyncio
    async def test_connection_test_success(
        self,
        wizard_client: AsyncClient,
        mock_rclone_service: AsyncMock,
    ) -> None:
        mock_rclone_service.check_remote.return_value = True
        response = await wizard_client.post(
            "/wizard/test",
            json={"name": "my-remote"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["error"] is None

    @pytest.mark.asyncio
    async def test_connection_test_failure(
        self,
        wizard_client: AsyncClient,
        mock_rclone_service: AsyncMock,
    ) -> None:
        mock_rclone_service.check_remote.return_value = False
        response = await wizard_client.post(
            "/wizard/test",
            json={"name": "bad-remote"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert body["error"] is not None

    @pytest.mark.asyncio
    async def test_connection_test_rclone_error(
        self,
        wizard_client: AsyncClient,
        mock_rclone_service: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        mock_rclone_service.check_remote.side_effect = RcloneError(
            "connection refused"
        )
        response = await wizard_client.post(
            "/wizard/test",
            json={"name": "error-remote"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        # Generic message for the client, rclone's text in the server log.
        assert "connection refused" not in body["error"]
        assert body["error"].startswith("Connection test failed.")
        assert "connection refused" in caplog.text


# --- Max 3 concurrent sessions enforced ---


class TestMaxConcurrentSessions:
    """POST /wizard/authorize returns 429 when max sessions exceeded."""

    @pytest.mark.asyncio
    async def test_max_sessions_returns_429(
        self,
        wizard_client: AsyncClient,
        mock_rclone_service: AsyncMock,
    ) -> None:
        # Create 3 sessions (the max) — no subprocess needed with direct OAuth
        for _ in range(3):
            resp = await wizard_client.post(
                "/wizard/authorize",
                json={"provider_id": "dropbox", "client_id": "own-key"},
            )
            assert resp.status_code == 200

        # 4th should be rejected
        response = await wizard_client.post(
            "/wizard/authorize",
            json={"provider_id": "dropbox", "client_id": "own-key"},
        )
        assert response.status_code == 429
        assert "Maximum" in response.json()["detail"]


# --- Rclone not installed returns 503 ---


class TestRcloneNotInstalled:
    """Any endpoint returns 503 when rclone service is None."""

    @pytest.fixture
    async def no_rclone_client(self):
        """Client with _rclone_service set to None."""
        original = wizard._rclone_service
        wizard._rclone_service = None
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test", headers=AUTH_HEADERS
        ) as client:
            yield client
        wizard._rclone_service = original

    @pytest.mark.asyncio
    async def test_create_returns_503(self, no_rclone_client: AsyncClient) -> None:
        response = await no_rclone_client.post(
            "/wizard/create",
            json={"name": "test", "provider_id": "s3", "params": {}},
        )
        assert response.status_code == 503
        assert "rclone" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_test_returns_503(self, no_rclone_client: AsyncClient) -> None:
        response = await no_rclone_client.post(
            "/wizard/test",
            json={"name": "test"},
        )
        assert response.status_code == 503
        assert "rclone" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_authorize_returns_503(
        self, no_rclone_client: AsyncClient
    ) -> None:
        response = await no_rclone_client.post(
            "/wizard/authorize",
            json={"provider_id": "drive"},
        )
        assert response.status_code == 503
        assert "rclone" in response.json()["detail"].lower()
