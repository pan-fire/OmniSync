"""Direct OAuth flow for the Remote Setup Wizard.

Implements OAuth 2.0 authorization code flow (with PKCE) directly,
bypassing rclone authorize (which runs a listener on 127.0.0.1:53682 and
doesn't work in Docker).

Every sign-in uses the user's own OAuth app: OmniSync ships no client IDs
or secrets. The provider sends the browser back to OmniSync's
``/wizard/oauth/callback``, which exchanges the code; the token is
formatted as rclone-compatible JSON and stays on the server.

Whether the app's client secret is needed depends on the provider's token
endpoint: Google's Web (and desktop) clients always need it; Dropbox apps
and Azure public clients redeem a PKCE code with the client ID alone.

OneDrive: after the exchange ``fetch_onedrive_drive`` reads the account's
drive id and type, which rclone needs in the remote's config (``drive_id``,
``drive_type``).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

# Tests replace this with an httpx.MockTransport.
_http_transport: httpx.AsyncBaseTransport | None = None

# Microsoft Graph: the signed-in user's default drive, as rclone's own
# OneDrive config flow reads it. A fixed address: nothing from a request or
# a token answer goes into it.
GRAPH_ME_DRIVE_URL = "https://graph.microsoft.com/v1.0/me/drive"
# rclone's drive_type values (backend/onedrive: driveTypePersonal, ...).
ONEDRIVE_DRIVE_TYPES = frozenset({"personal", "business", "documentLibrary"})
# Drive ids seen in practice: personal "0123abcd...", business "b!AbC-_...".
_DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9!._~-]{1,256}$")
# An error code from a provider's error body, as logged: short and code-like.
_ERROR_CODE_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


# The fixed English message of each OAuthFlowError code (the web UI shows
# its own translation, keyed by the code).
OAUTH_FLOW_MESSAGES: dict[str, str] = {
    "oauth_failed": "The sign-in failed. Start it again.",
    "onedrive_drive_lookup_failed": (
        "Signed in, but your OneDrive could not be read, so no remote was saved. "
        "If OneDrive was never opened with this account, open it once in the browser, "
        "then start the sign-in again."
    ),
}


def oauth_flow_message(code: str) -> str:
    """The message shown for OAuthFlowError ``code``: a constant, never exception text."""
    return OAUTH_FLOW_MESSAGES.get(code, OAUTH_FLOW_MESSAGES["oauth_failed"])


class OAuthFlowError(Exception):
    """A sign-in failure the user can act on.

    ``code`` is stable and machine-readable (the web UI translates it);
    callers show ``oauth_flow_message(e.code)``, a fixed English message.
    Neither carries anything from the provider's answer.
    """

    code = "oauth_failed"

    def __init__(self) -> None:
        super().__init__(oauth_flow_message(self.code))


class OneDriveDriveError(OAuthFlowError):
    """The OneDrive account's drive could not be read after the sign-in."""

    code = "onedrive_drive_lookup_failed"


@dataclass
class OAuthProviderConfig:
    """OAuth endpoints and scopes for a cloud provider."""

    auth_url: str
    token_url: str
    scopes: list[str]
    # Whether the token endpoint needs the app's client secret. Google:
    # yes, for Web and desktop clients alike. Dropbox and Azure public
    # clients: no, the PKCE verifier proves the exchange.
    requires_client_secret: bool = False


# OAuth configs for supported providers. The client ID (and secret) always
# come from the user's own app.
OAUTH_CONFIGS: dict[str, OAuthProviderConfig] = {
    "drive": OAuthProviderConfig(
        auth_url="https://accounts.google.com/o/oauth2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/drive"],
        requires_client_secret=True,
    ),
    "dropbox": OAuthProviderConfig(
        auth_url="https://www.dropbox.com/oauth2/authorize",
        token_url="https://api.dropboxapi.com/oauth2/token",
        scopes=[],
    ),
    "onedrive": OAuthProviderConfig(
        auth_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        scopes=["Files.Read", "Files.ReadWrite", "Files.Read.All",
                "Files.ReadWrite.All", "offline_access"],
    ),
}


def get_oauth_config(provider_id: str) -> OAuthProviderConfig | None:
    """Return OAuth config for a provider, or None if not an OAuth provider."""
    return OAUTH_CONFIGS.get(provider_id)


def new_pkce_pair() -> tuple[str, str]:
    """A PKCE (RFC 7636) code verifier and its S256 challenge."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_auth_url(
    provider_id: str,
    state: str,
    redirect_uri: str,
    client_id: str,
    code_challenge: str | None = None,
) -> str | None:
    """Build the OAuth consent URL for a provider.

    Args:
        provider_id: The rclone provider type (e.g. "drive")
        state: CSRF state parameter (use session_id)
        redirect_uri: Where the provider sends the browser afterwards; the
            token exchange must use the same value
        client_id: The client ID of the user's own app
        code_challenge: PKCE S256 challenge, or None for no PKCE

    Returns:
        The full authorization URL, or None if provider not supported.
    """
    config = get_oauth_config(provider_id)
    if config is None:
        return None

    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }

    if config.scopes:
        params["scope"] = " ".join(config.scopes)

    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"

    # Dropbox uses token_access_type instead of access_type
    if provider_id == "dropbox":
        params.pop("access_type", None)
        params.pop("prompt", None)
        params["token_access_type"] = "offline"

    return f"{config.auth_url}?{urlencode(params)}"


async def exchange_code_for_token(
    provider_id: str,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str | None = None,
    code_verifier: str | None = None,
) -> str | None:
    """Exchange an authorization code for an access token.

    ``redirect_uri`` must be the one the consent URL used. Returns the token
    as a JSON string in rclone's expected format, or None if the exchange
    fails (the reason goes to the log, never to the caller).
    """
    config = get_oauth_config(provider_id)
    if config is None:
        return None

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
    }
    # Public clients (and PKCE exchanges without a secret) send none at all:
    # an empty client_secret is an invalid_client error at some providers.
    if client_secret:
        data["client_secret"] = client_secret
    if code_verifier:
        data["code_verifier"] = code_verifier

    async with httpx.AsyncClient(timeout=30.0, transport=_http_transport) as client:
        try:
            resp = await client.post(config.token_url, data=data)
            resp.raise_for_status()
            token_data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            # Only the provider's error code (e.g. invalid_grant) is logged,
            # never the request with the code and secret.
            reason = type(e).__name__
            if isinstance(e, httpx.HTTPStatusError):
                reason = f"HTTP {e.response.status_code} {_token_error_code(e.response)}"
            logger.error("Token exchange failed for %s: %s", provider_id, reason)
            return None

    if not isinstance(token_data, dict) or not token_data.get("access_token"):
        logger.error("Token exchange for %s returned no access token", provider_id)
        return None

    # Build rclone-compatible token JSON
    rclone_token: dict[str, object] = {
        "access_token": token_data["access_token"],
        "token_type": token_data.get("token_type", "Bearer"),
        "expiry": _compute_expiry(token_data.get("expires_in")),
    }

    if "refresh_token" in token_data:
        rclone_token["refresh_token"] = token_data["refresh_token"]

    return json.dumps(rclone_token)


async def fetch_onedrive_drive(token_json: str) -> dict[str, str]:
    """The ``drive_id`` and ``drive_type`` of the signed-in account's OneDrive.

    ``token_json`` is the token ``exchange_code_for_token`` returned. Reads
    GET /me/drive from Microsoft Graph (a fixed URL) and returns the values
    rclone keeps in the remote's config. Raises OneDriveDriveError when the
    drive cannot be read or the answer is not a usable drive; the reason
    goes to the log, never the token or the answer.
    """
    try:
        access_token = json.loads(token_json)["access_token"]
    except (ValueError, KeyError, TypeError):
        raise OneDriveDriveError() from None
    if not isinstance(access_token, str) or not access_token:
        raise OneDriveDriveError()

    async with httpx.AsyncClient(timeout=30.0, transport=_http_transport, follow_redirects=False) as client:
        try:
            resp = await client.get(GRAPH_ME_DRIVE_URL, headers={"Authorization": f"Bearer {access_token}"})
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            reason = type(e).__name__
            if isinstance(e, httpx.HTTPStatusError):
                reason = f"HTTP {e.response.status_code} {_graph_error_code(e.response)}"
            logger.error("Reading the OneDrive drive failed: %s", reason)
            raise OneDriveDriveError() from None

    drive_id = body.get("id") if isinstance(body, dict) else None
    drive_type = body.get("driveType") if isinstance(body, dict) else None
    if not isinstance(drive_id, str) or not _DRIVE_ID_RE.match(drive_id):
        logger.error("Microsoft Graph returned no usable drive id")
        raise OneDriveDriveError()
    if drive_type not in ONEDRIVE_DRIVE_TYPES:
        logger.error("Microsoft Graph returned an unknown drive type")
        raise OneDriveDriveError()
    return {"drive_id": drive_id, "drive_type": drive_type}


def _graph_error_code(response: httpx.Response) -> str:
    """The ``error.code`` of a Microsoft Graph error body, if any."""
    try:
        body = response.json()
    except ValueError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and _ERROR_CODE_RE.fullmatch(code) else ""


def _token_error_code(response: httpx.Response) -> str:
    """The RFC 6749 ``error`` field of a token endpoint's error body, if any.

    Only a short code-like value (e.g. ``invalid_grant``) is returned, so a
    provider echoing anything else into the field never reaches the log.
    """
    try:
        body = response.json()
    except ValueError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, str) and _ERROR_CODE_RE.fullmatch(error) else ""


def _compute_expiry(expires_in: int | None) -> str:
    """Convert expires_in seconds to an ISO 8601 expiry timestamp."""
    if expires_in is None:
        expires_in = 3600
    from datetime import datetime, timezone, timedelta
    expiry = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    return expiry.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
