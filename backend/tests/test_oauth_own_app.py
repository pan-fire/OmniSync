"""OAuth sign-ins with the user's own app, and the token exchange.

OmniSync ships no OAuth client IDs or secrets: /wizard/authorize refuses an
OAuth provider without the user's client ID, and Google also without its
client secret (Google's token endpoint needs it; Dropbox apps and Azure
public clients redeem a PKCE code with the client ID alone). The provider
sends the browser back to /wizard/oauth/callback. The token endpoint is
faked with an httpx.MockTransport; nobody can test against the real
providers here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.routes import wizard
from backend.main import app
from backend.services import oauth
from backend.services.oauth import build_auth_url, exchange_code_for_token, new_pkce_pair
from backend.services.provider_registry import get_provider
from backend.services.wizard_sessions import WizardSessionManager
from backend.tests.auth import AUTH_HEADERS

CODE = "4/0AeanS0aExampleCode-_x"
TOKEN_SECRET = "ya29.never-leaves-the-server"
CALLBACK = "http://test/wizard/oauth/callback"

# What each provider needs from the user's app.
OWN_APPS = {
    "drive": {"client_id": "own.apps.googleusercontent.com", "client_secret": "own-google-secret"},
    "dropbox": {"client_id": "ownappkey"},
    "onedrive": {"client_id": "00000000-1111-2222-3333-444444444444"},
}


class FakeTokenEndpoint:
    """Records token requests and answers like an OAuth token endpoint."""

    def __init__(self) -> None:
        self.status = 200
        self.body: object = {
            "access_token": TOKEN_SECRET, "token_type": "Bearer",
            "expires_in": 3599, "refresh_token": "1//refresh",
        }
        self.requests: list[dict[str, str]] = []
        self.urls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        form = parse_qs(request.content.decode(), keep_blank_values=True)
        self.requests.append({k: v[0] for k, v in form.items()})
        if "graph.microsoft.com" in str(request.url):
            return httpx.Response(200, json={"id": "drive-1", "driveType": "personal"})
        return httpx.Response(self.status, json=self.body)


@pytest.fixture
def token_endpoint(monkeypatch) -> FakeTokenEndpoint:
    fake = FakeTokenEndpoint()
    monkeypatch.setattr(oauth, "_http_transport", httpx.MockTransport(fake))
    return fake


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.delenv("OMNISYNC_OAUTH_REDIRECT_URI", raising=False)
    rclone = AsyncMock()
    rclone.create_remote = AsyncMock(return_value=None)
    monkeypatch.setattr(wizard, "_rclone_service", rclone)
    monkeypatch.setattr(wizard, "_session_manager", WizardSessionManager())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH_HEADERS) as c:
        c.rclone = rclone  # type: ignore[attr-defined]
        yield c


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


# --- The own app is required ---


class TestOwnAppRequired:
    @pytest.mark.parametrize("provider", sorted(OWN_APPS))
    @pytest.mark.parametrize("client_id", [None, "", "   "])
    async def test_no_client_id_is_refused(self, client, token_endpoint, provider, client_id) -> None:
        body: dict[str, object] = {"provider_id": provider}
        if client_id is not None:
            body["client_id"] = client_id
        resp = await client.post("/wizard/authorize", json=body)
        assert resp.status_code == 422
        assert resp.json()["code"] == "oauth_client_id_required"
        assert "own OAuth app" in resp.json()["detail"]
        assert not wizard._session_manager._sessions
        assert token_endpoint.requests == []

    @pytest.mark.parametrize("secret", [None, "", "  "])
    async def test_google_needs_the_client_secret(self, client, secret) -> None:
        body: dict[str, object] = {"provider_id": "drive", "client_id": "own.apps.googleusercontent.com"}
        if secret is not None:
            body["client_secret"] = secret
        resp = await client.post("/wizard/authorize", json=body)
        assert resp.status_code == 422
        assert resp.json()["code"] == "oauth_client_secret_required"
        assert not wizard._session_manager._sessions

    @pytest.mark.parametrize("provider", sorted(OWN_APPS))
    async def test_own_app_signs_in_through_the_callback(self, client, token_endpoint, provider) -> None:
        resp = await client.post("/wizard/authorize", json={"provider_id": provider, **OWN_APPS[provider]})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) == {"session_id", "auth_url", "redirect_uri"}
        assert body["redirect_uri"] == CALLBACK
        q = _query(body["auth_url"])
        assert q["client_id"] == OWN_APPS[provider]["client_id"]
        assert q["redirect_uri"] == CALLBACK and q["state"] == body["session_id"]
        assert q["code_challenge_method"] == "S256"

        done = await client.get("/wizard/oauth/callback", params={"code": CODE, "state": body["session_id"]})
        assert done.status_code == 200 and "successful" in done.text
        sent = token_endpoint.requests[0]
        assert sent["client_id"] == OWN_APPS[provider]["client_id"]
        assert sent.get("client_secret") == OWN_APPS[provider].get("client_secret")
        assert sent["redirect_uri"] == CALLBACK and sent["code"] == CODE
        verifier = sent["code_verifier"]
        digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        assert digest == q["code_challenge"]
        assert wizard._session_manager.get_session(body["session_id"]).status == "completed"

    @pytest.mark.parametrize("provider", ["dropbox", "onedrive"])
    async def test_public_clients_may_add_a_secret(self, client, token_endpoint, provider) -> None:
        creds = {**OWN_APPS[provider], "client_secret": "confidential-too"}
        sid = (await client.post("/wizard/authorize", json={"provider_id": provider, **creds})).json()["session_id"]
        await client.get("/wizard/oauth/callback", params={"code": CODE, "state": sid})
        assert token_endpoint.requests[0]["client_secret"] == "confidential-too"

    async def test_explicit_redirect_setting_is_honoured(self, client, monkeypatch) -> None:
        monkeypatch.setenv("OMNISYNC_OAUTH_REDIRECT_URI", "https://sync.lan/api/wizard/oauth/callback")
        resp = await client.post("/wizard/authorize", json={"provider_id": "dropbox", **OWN_APPS["dropbox"]})
        assert _query(resp.json()["auth_url"])["redirect_uri"] == "https://sync.lan/api/wizard/oauth/callback"
        shown = await client.get("/wizard/oauth/redirect-uri")
        assert shown.json() == {"redirect_uri": "https://sync.lan/api/wizard/oauth/callback"}

    async def test_create_needs_the_client_id_too(self, client, token_endpoint) -> None:
        sid = (await client.post("/wizard/authorize", json={
            "provider_id": "dropbox", **OWN_APPS["dropbox"],
        })).json()["session_id"]
        await client.get("/wizard/oauth/callback", params={"code": CODE, "state": sid})
        refused = await client.post("/wizard/create", json={
            "name": "box", "provider_id": "dropbox", "params": {}, "session_id": sid,
        })
        assert refused.status_code == 422
        created = await client.post("/wizard/create", json={
            "name": "box", "provider_id": "dropbox", "params": OWN_APPS["dropbox"], "session_id": sid,
        })
        assert created.status_code == 200, created.text
        name, provider, params = client.rclone.create_remote.await_args.args
        assert (name, provider, params["client_id"]) == ("box", "dropbox", "ownappkey")
        assert json.loads(params["token"])["access_token"] == TOKEN_SECRET
        assert TOKEN_SECRET not in created.text

    async def test_redirect_uri_endpoint_needs_the_api_token(self) -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bare:
            assert (await bare.get("/wizard/oauth/redirect-uri")).status_code == 401

    async def test_the_paste_route_is_gone(self, client) -> None:
        resp = await client.post("/wizard/sessions/x/redirect", json={"redirect_url": CODE})
        assert resp.status_code in (404, 405)


# --- Consent URL ---


def test_pkce_challenge_matches_verifier() -> None:
    verifier, challenge = new_pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    url = build_auth_url("dropbox", "st", CALLBACK, client_id="k", code_challenge=challenge)
    assert url is not None
    q = _query(url)
    assert q["code_challenge"] == challenge and q["token_access_type"] == "offline"
    assert "access_type" not in q


def test_only_google_requires_a_secret() -> None:
    assert {p: c.requires_client_secret for p, c in oauth.OAUTH_CONFIGS.items()} == {
        "drive": True, "dropbox": False, "onedrive": False,
    }


# --- Token exchange against a fake token endpoint ---


class TestExchange:
    async def test_sends_code_redirect_verifier_and_app_and_builds_rclone_token(self, token_endpoint) -> None:
        token = await exchange_code_for_token(
            "drive", CODE, redirect_uri=CALLBACK, client_id="mine", client_secret="shh", code_verifier="v" * 50,
        )
        assert token_endpoint.urls == ["https://oauth2.googleapis.com/token"]
        sent = token_endpoint.requests[0]
        assert sent == {
            "grant_type": "authorization_code", "code": CODE, "redirect_uri": CALLBACK,
            "client_id": "mine", "client_secret": "shh", "code_verifier": "v" * 50,
        }
        assert token is not None
        parsed = json.loads(token)
        assert parsed["access_token"] == TOKEN_SECRET
        assert parsed["refresh_token"] == "1//refresh"
        assert parsed["token_type"] == "Bearer"
        assert parsed["expiry"].endswith("Z")

    async def test_public_client_sends_no_empty_secret(self, token_endpoint) -> None:
        await exchange_code_for_token("dropbox", CODE, redirect_uri=CALLBACK, client_id="k", code_verifier="v" * 50)
        assert token_endpoint.urls == ["https://api.dropboxapi.com/oauth2/token"]
        assert "client_secret" not in token_endpoint.requests[0]

    async def test_provider_error_returns_none_and_logs_no_code(self, token_endpoint, caplog) -> None:
        token_endpoint.status = 400
        token_endpoint.body = {"error": "invalid_grant", "error_description": f"bad code {CODE}"}
        with caplog.at_level(logging.ERROR):
            assert await exchange_code_for_token("drive", CODE, redirect_uri=CALLBACK, client_id="k") is None
        assert "invalid_grant" in caplog.text
        assert CODE not in caplog.text

    async def test_answer_without_access_token_returns_none(self, token_endpoint) -> None:
        token_endpoint.body = {"token_type": "Bearer"}
        assert await exchange_code_for_token("drive", CODE, redirect_uri=CALLBACK, client_id="k") is None

    async def test_failed_exchange_ends_the_session(self, client, token_endpoint) -> None:
        token_endpoint.status = 400
        token_endpoint.body = {"error": "invalid_grant"}
        sid = (await client.post("/wizard/authorize", json={
            "provider_id": "dropbox", **OWN_APPS["dropbox"],
        })).json()["session_id"]
        resp = await client.get("/wizard/oauth/callback", params={"code": CODE, "state": sid})
        assert "invalid_grant" not in resp.text
        assert wizard._session_manager.get_session(sid).status == "failed"
        # The code is single-use at the provider anyway; a retry starts over.
        again = await client.get("/wizard/oauth/callback", params={"code": CODE, "state": sid})
        assert again.status_code == 409
        assert len(token_endpoint.requests) == 1


# --- No built-in credentials anywhere ---


def test_no_builtin_client_fields_remain() -> None:
    for config in oauth.OAUTH_CONFIGS.values():
        assert not hasattr(config, "default_client_id")
        assert not hasattr(config, "default_client_secret")


@pytest.mark.parametrize("provider_id", sorted(oauth.OAUTH_CONFIGS))
def test_setup_guide_asks_for_the_own_app(provider_id: str) -> None:
    provider = get_provider(provider_id)
    assert provider is not None
    guide = provider.setup_guide
    assert "your own OAuth app" in guide
    assert "/api/wizard/oauth/callback" in guide
    assert "OMNISYNC_OAUTH_REDIRECT_URI" in guide
    assert "docs/gem/remotes.md" in guide
    assert "53682" not in guide and "paste" not in guide and "built-in" not in guide
    fields = {f.name: f for f in provider.fields}
    assert fields["client_id"].required
    assert fields["client_secret"].required is oauth.OAUTH_CONFIGS[provider_id].requires_client_secret
