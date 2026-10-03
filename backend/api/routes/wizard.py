"""Wizard API endpoints for the Remote Setup Wizard."""

from __future__ import annotations

import html
import logging
import os
import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from backend.api.errors import SEE_LOG, api_error
from backend.api.schemas import (
    AuthorizeRequest,
    AuthorizeResponse,
    AuthType,
    CreateRemoteRequest,
    FieldType,
    OAuthRedirectResponse,
    ProviderFieldResponse,
    ProviderResponse,
    ReconnectRemoteRequest,
    TestRemoteRequest,
    TestRemoteResponse,
    WizardSessionResponse,
    WizardSessionStatus,
)
from backend.exceptions import RcloneError, WizardSessionError
from backend.services import remote_auth
from backend.services.oauth import (
    OAuthFlowError,
    build_auth_url,
    exchange_code_for_token,
    fetch_onedrive_drive,
    get_oauth_config,
    new_pkce_pair,
    oauth_flow_message,
)
from backend.services.provider_registry import (
    ProviderInfo,
    get_provider,
    get_providers,
    missing_required,
    validate_remote_name,
    validate_remote_params,
)
from backend.services.rclone import RESERVED_NAME_MESSAGE, RcloneService, is_reserved_remote_name
from backend.services.wizard_sessions import WizardSession, WizardSessionManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wizard", tags=["wizard"])

CALLBACK_PATH = "/wizard/oauth/callback"

_HOST_RE = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?$")
_PREFIX_RE = re.compile(r"^(?:/[A-Za-z0-9._~-]+)*$")


def _first_value(header: str | None) -> str:
    """The first entry of a possibly comma-separated forwarding header."""
    return (header or "").split(",", 1)[0].strip()


def oauth_redirect_uri(req: Request) -> str:
    """The URL the provider sends the user's browser back to after consent.

    1. ``OMNISYNC_OAUTH_REDIRECT_URI``, when set, as is: the address the
       browser reaches the callback at (it must match the provider's app).
    2. Behind a proxy that says where the browser came from
       (``X-Forwarded-Host``, ``X-Forwarded-Proto``, and ``X-Forwarded-Prefix``
       for a path prefix): the web UI forwards these, so through it this is
       e.g. ``http://localhost:3000/api/wizard/oauth/callback``. Malformed
       values are ignored.
    3. Otherwise the address this request came in on.
    """
    explicit = _explicit_redirect_uri()
    if explicit:
        return explicit
    host = _first_value(req.headers.get("x-forwarded-host"))
    if host and _HOST_RE.match(host):
        proto = _first_value(req.headers.get("x-forwarded-proto")).lower()
        if proto not in ("http", "https"):
            proto = req.url.scheme
        prefix = _first_value(req.headers.get("x-forwarded-prefix")).rstrip("/")
        if not _PREFIX_RE.match(prefix):
            prefix = ""
        return f"{proto}://{host}{prefix}{CALLBACK_PATH}"
    return str(req.base_url).rstrip("/") + CALLBACK_PATH


# Module-level references, set by main.py at startup
_rclone_service: RcloneService | None = None
_session_manager = WizardSessionManager()


def set_rclone_service(service: RcloneService) -> None:
    """Set the rclone service reference."""
    global _rclone_service
    _rclone_service = service


def _get_rclone() -> RcloneService:
    """Return the rclone service or raise 503 if not available."""
    if _rclone_service is None:
        raise api_error(503, "service_unavailable", "rclone is not installed or not accessible")
    return _rclone_service


@router.get("/providers", response_model=list[ProviderResponse])
async def list_providers() -> list[ProviderResponse]:
    """Return the full provider list from the registry."""
    providers = get_providers()
    return [
        ProviderResponse(
            id=p.id,
            display_name=p.display_name,
            icon=p.icon,
            auth_type=p.auth_type,
            fields=[
                ProviderFieldResponse(
                    name=f.name,
                    label=f.label,
                    field_type=f.field_type,
                    required=f.required,
                    help_text=f.help_text,
                    options=list(f.options),
                    default=f.default,
                )
                for f in p.fields
            ],
            default_name=p.default_name,
            setup_guide=p.setup_guide,
        )
        for p in providers
    ]


def _explicit_redirect_uri() -> str:
    return os.environ.get("OMNISYNC_OAUTH_REDIRECT_URI", "").strip()


@router.get("/oauth/redirect-uri", response_model=OAuthRedirectResponse)
async def get_oauth_redirect_uri(req: Request) -> OAuthRedirectResponse:
    """The redirect URI to register with your own OAuth app.

    The address a sign-in started by this client would send the browser
    back to (see ``oauth_redirect_uri``): through the web UI
    ``<ui>/api/wizard/oauth/callback``, or ``OMNISYNC_OAUTH_REDIRECT_URI``.
    """
    return OAuthRedirectResponse(redirect_uri=oauth_redirect_uri(req))


def _require_own_app(provider: ProviderInfo, client_id: str | None, client_secret: str | None) -> str:
    """The client ID, after checking the app credentials the provider needs.

    OmniSync ships no OAuth app: every sign-in uses the user's own. A client
    ID is always needed; a client secret only where the provider's token
    endpoint requires one (Google).
    """
    config = get_oauth_config(provider.id)
    if config is None:
        raise api_error(400, "oauth_not_supported", f"OAuth not supported for provider: {provider.id}")
    if not client_id:
        raise api_error(
            422, "oauth_client_id_required",
            f"{provider.display_name} needs your own OAuth app: register one with "
            f"{provider.display_name} and enter its client ID. See the remotes guide.",
        )
    if config.requires_client_secret and not client_secret:
        raise api_error(
            422, "oauth_client_secret_required",
            f"{provider.display_name} needs the client secret of your OAuth app as well as its client ID.",
        )
    return client_id


@router.post("/authorize", response_model=AuthorizeResponse)
async def start_authorize(request: AuthorizeRequest, req: Request) -> AuthorizeResponse:
    """Start an OAuth authorization flow with the user's own OAuth app.

    Builds the provider's OAuth consent URL directly (no rclone authorize).
    The user opens the URL in their browser and authorizes; the provider
    then redirects to /wizard/oauth/callback (``oauth_redirect_uri``),
    which exchanges the code. Without a client ID (or, for Google, a client
    secret) the request is refused: OmniSync has no built-in app.
    """
    _get_rclone()  # Ensure rclone is available (needed later for config create)

    provider = get_provider(request.provider_id)
    if provider is None:
        raise api_error(400, "unknown_provider", f"Unknown provider: {request.provider_id}")

    client_id = (request.client_id or "").strip() or None
    client_secret = (request.client_secret or "").strip() or None
    store_client = False
    if request.remote_name is not None:
        # Reconnect: the token goes into this remote (POST /wizard/reconnect).
        client_id, client_secret, store_client = await _reconnect_client(
            request.remote_name, provider, client_id, client_secret,
        )
    client_id = _require_own_app(provider, client_id, client_secret)

    try:
        session = await _session_manager.create_session(request.provider_id)
    except WizardSessionError as e:
        logger.warning("authorize: %s", e)
        raise api_error(
            429, "too_many_sessions",
            "Maximum concurrent wizard sessions reached. Finish or cancel one first.",
        )

    # Store the app credentials on the session for the token exchange
    session.client_id = client_id
    session.client_secret = client_secret
    session.remote_name = request.remote_name
    session.store_client = store_client

    redirect_uri = oauth_redirect_uri(req)
    verifier, challenge = new_pkce_pair()

    auth_url = build_auth_url(
        provider_id=request.provider_id,
        state=session.session_id,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=challenge,
    )

    if not auth_url:
        await _session_manager.cancel_session(session.session_id)
        raise api_error(400, "oauth_not_supported", f"OAuth not supported for provider: {request.provider_id}")

    session.auth_url = auth_url
    session.redirect_uri = redirect_uri
    session.code_verifier = verifier
    return AuthorizeResponse(session_id=session.session_id, auth_url=auth_url, redirect_uri=redirect_uri)


async def _reconnect_client(
    name: str, provider: ProviderInfo, client_id: str | None, client_secret: str | None,
) -> tuple[str | None, str | None, bool]:
    """The app credentials for reconnecting remote ``name``, and whether the
    reconnect stores them.

    Without a client_id in the request the remote's own credentials from
    rclone.conf are used, so the new token belongs to the client rclone
    refreshes it with. A remote without a client_id (made with rclone's
    built-in app) needs a client_id in the request: OmniSync has no built-in
    app. With one, the new pair is stored together with the token. Only an OAuth remote of the
    requested provider can be reconnected: a crypt remote, for one, has no
    sign-in of its own.
    """
    if not validate_remote_name(name):
        raise api_error(422, "invalid_remote_name", "Invalid remote name")
    if provider.auth_type != AuthType.OAUTH:
        raise api_error(422, "oauth_not_supported", f"{provider.display_name} remotes do not sign in with OAuth.")
    try:
        section = await _get_rclone().remote_section(name)
    except Exception:
        logger.exception("Reading remote '%s' for a reconnect failed", name)
        raise api_error(500, "internal_error", f"Could not read the remote. {SEE_LOG}")
    if section is None:
        raise api_error(404, "remote_not_found", f"Remote '{name}' not found")
    if section.get("type") != provider.id:
        raise api_error(
            422, "provider_mismatch",
            f"Remote '{name}' is not a {provider.display_name} remote and cannot be reconnected as one.",
        )
    if client_id:
        return client_id, client_secret, True
    return section.get("client_id") or None, section.get("client_secret") or None, False


async def _complete_with_code(session: WizardSession, code: str) -> bool:
    """Exchange ``code`` for the session's token; True on success.

    The caller has checked that the session is pending and unused. The code
    is marked used before the exchange, so a concurrent second request with
    the same session cannot start another one.
    """
    session.code_used = True
    try:
        token = await exchange_code_for_token(
            provider_id=session.provider_id,
            code=code,
            client_id=session.client_id or "",
            client_secret=session.client_secret,
            redirect_uri=session.redirect_uri or "",
            code_verifier=session.code_verifier,
        )
        if token and session.provider_id == "onedrive" and await _needs_onedrive_drive(session):
            session.extra_config = await fetch_onedrive_drive(token)
    except OAuthFlowError as e:
        session.code_verifier = None
        session.status = "failed"
        # A constant looked up by code: no exception text reaches the session,
        # which the status endpoint and the callback page show.
        session.error_code = e.code
        session.error = oauth_flow_message(e.code)
        return False
    session.code_verifier = None
    if token:
        session.token = token
        session.status = "completed"
        return True
    session.status = "failed"
    session.error = "Failed to exchange authorization code for token"
    return False


async def _needs_onedrive_drive(session: WizardSession) -> bool:
    """Whether a OneDrive sign-in must look up the account's drive.

    A new remote always does; a reconnect only when the remote has no
    drive_id or drive_type yet (rclone cannot use it without them, and a
    drive chosen in rclone's own config, e.g. a SharePoint library, stays).
    """
    if session.remote_name is None:
        return True
    try:
        section = await _get_rclone().remote_section(session.remote_name)
    except Exception:
        logger.exception("Reading remote '%s' for its OneDrive drive failed", session.remote_name)
        return True
    return not (section and section.get("drive_id") and section.get("drive_type"))


@router.get("/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """OAuth callback endpoint.

    The OAuth provider redirects here after the user authorizes.
    Exchanges the auth code for tokens and updates the wizard session.
    Returns a simple HTML page telling the user to return to OmniSync.
    """
    if error:
        return HTMLResponse(
            content=_callback_html("Authorization denied", error=True),
            status_code=200,
        )

    if not state or not code:
        return HTMLResponse(
            content=_callback_html("Missing parameters", error=True),
            status_code=400,
        )

    # Only an open (pending) session may receive a code: an unknown state or
    # a replayed callback changes nothing.
    session = _session_manager.get_session(state)
    if session is None:
        return HTMLResponse(
            content=_callback_html("Session expired or not found", error=True),
            status_code=404,
        )
    if session.status != "pending" or session.code_used:
        return HTMLResponse(
            content=_callback_html("This authorization was already handled", error=True),
            status_code=409,
        )
    if await _complete_with_code(session, code):
        return HTMLResponse(content=_callback_html("Authorization successful"))
    # A failure with a code has a fixed message of ours worth showing here;
    # the wizard shows it translated.
    message = session.error if session.error_code and session.error else "Token exchange failed"
    return HTMLResponse(
        content=_callback_html(html.escape(message), error=True),
    )


def _callback_html(message: str, error: bool = False) -> str:
    """Generate a simple HTML response for the OAuth callback page."""
    color = "#ef4444" if error else "#22c55e"
    icon = "&#10060;" if error else "&#10004;"
    return f"""<!DOCTYPE html>
<html><head><title>OmniSync — OAuth</title>
<style>
  body {{ font-family: system-ui, sans-serif; display: flex; justify-content: center;
         align-items: center; min-height: 100vh; margin: 0; background: #0a0a0a; color: #fafafa; }}
  .card {{ text-align: center; padding: 2rem; border-radius: 0.75rem;
           border: 1px solid #27272a; background: #18181b; max-width: 400px; }}
  .icon {{ font-size: 3rem; margin-bottom: 1rem; }}
  h1 {{ font-size: 1.25rem; margin: 0 0 0.5rem; color: {color}; }}
  p {{ color: #a1a1aa; font-size: 0.875rem; margin: 0; }}
</style></head>
<body><div class="card">
  <div class="icon">{icon}</div>
  <h1>{message}</h1>
  <p>You can close this tab and return to OmniSync.</p>
</div></body></html>"""


@router.get("/sessions/{session_id}", response_model=WizardSessionResponse)
async def get_session(session_id: str) -> WizardSessionResponse:
    """Poll the status of an OAuth wizard session."""
    session = _session_manager.get_session(session_id)
    if session is None:
        raise api_error(404, "wizard_session_not_found", "Wizard session not found")

    # The token stays on the server; /wizard/create reads it from the session.
    return WizardSessionResponse(
        session_id=session.session_id,
        status=WizardSessionStatus(session.status),
        auth_url=session.auth_url,
        error=session.error,
        error_code=session.error_code,
    )


@router.delete("/sessions/{session_id}")
async def cancel_session(session_id: str) -> dict[str, str]:
    """Cancel an OAuth wizard session and kill any running subprocess."""
    session = _session_manager.get_session(session_id)
    if session is None:
        raise api_error(404, "wizard_session_not_found", "Wizard session not found")

    await _session_manager.cancel_session(session_id)
    return {"detail": "Session cancelled"}


def _session_app(session: WizardSession, params: dict[str, str]) -> dict[str, str]:
    """The OAuth app (client_id, client_secret) the session's token was issued to.

    A client_id or client_secret in ``params`` must be that app's: anything
    else is refused (``oauth_client_mismatch``), since the remote would hold
    a token its app cannot refresh. Empty values count as not sent.
    """
    app = {"client_id": session.client_id or ""}
    if session.client_secret:
        app["client_secret"] = session.client_secret
    for key in ("client_id", "client_secret"):
        sent = (params.get(key) or "").strip()
        if sent and sent != app.get(key, ""):
            raise api_error(
                422, "oauth_client_mismatch",
                "The client ID or secret differs from the app this authorization was started with. "
                "Start the sign-in again with the app you want the remote to use.",
            )
    return app


@router.post("/create")
async def create_remote(request: CreateRemoteRequest) -> dict[str, str]:
    """Create an rclone remote.

    Key-based providers pass their settings in ``params``. OAuth providers
    pass the ``session_id`` of a completed authorization; the token is taken
    from that session on the server and the session ends once the remote
    exists. Clients never see or send the token. The remote stores the OAuth
    app (client_id, client_secret) the authorization was started with; a
    different one in ``params`` is refused (``oauth_client_mismatch``).
    """
    rclone = _get_rclone()

    provider = get_provider(request.provider_id)
    if provider is None:
        raise api_error(400, "unknown_provider", f"Unknown provider: {request.provider_id}")

    token: str | None = None
    session_id: str | None = None
    extra_config: dict[str, str] = {}
    app: dict[str, str] = {}
    if provider.auth_type == AuthType.OAUTH:
        if not request.session_id:
            raise api_error(
                422, "authorization_required",
                f"{provider.display_name} needs the session_id of a completed authorization.",
            )
        session = _session_manager.get_session(request.session_id)
        if session is None:
            raise api_error(404, "wizard_session_not_found", "Wizard session not found or expired")
        if session.provider_id != provider.id:
            raise api_error(422, "session_mismatch", "The wizard session belongs to a different provider.")
        if session.remote_name is not None:
            raise api_error(
                422, "wrong_oauth_flow",
                "This authorization reconnects an existing remote: finish it with /wizard/reconnect.",
            )
        if session.status != "completed" or not session.token:
            raise api_error(409, "authorization_pending", "The authorization is not complete yet.")
        token = session.token
        session_id = session.session_id
        extra_config = session.extra_config
        app = _session_app(session, request.params)

    params_in = dict(request.params)
    if provider.auth_type == AuthType.OAUTH:
        # The token belongs to the app the authorization was started with:
        # that app goes into the remote, never one sent along with the token.
        params_in.pop("client_id", None)
        params_in.pop("client_secret", None)
        params_in.update(app)

    if not validate_remote_name(request.name):
        raise api_error(
            422, "invalid_remote_name",
            "Remote name must contain only letters, digits, hyphens and underscores, "
            "and may not start with a hyphen.",
        )
    if is_reserved_remote_name(request.name):
        raise api_error(422, "invalid_remote_name", f"Invalid remote name: {RESERVED_NAME_MESSAGE}.")

    existing = await existing_remote_names(rclone, provider)
    errors = validate_remote_params(
        provider, params_in, has_token=token is not None, existing_remotes=existing, own_name=request.name,
    )
    errors += missing_required(provider, params_in)
    if errors:
        raise api_error(422, "invalid_params", "; ".join(errors), errors=errors)

    params = await obscure_secrets(rclone, provider, {k: v for k, v in params_in.items() if v != ""}, request.name)
    if token is not None:
        params["token"] = token
        # Found by the sign-in (OneDrive's drive_id and drive_type).
        params.update(extra_config)

    try:
        await rclone.create_remote(request.name, request.provider_id, params)
    except ValueError as e:
        if "already exists" in str(e):
            raise api_error(409, "remote_exists", f"Remote '{request.name}' already exists")
        logger.warning("create_remote rejected '%s': %s", request.name, e)
        raise api_error(422, "invalid_params", "The remote settings were rejected.")
    except RcloneError as e:
        logger.error("rclone create_remote failed for '%s': %s", request.name, e)
        raise api_error(500, "internal_error", f"Failed to create the remote. {SEE_LOG}")
    except FileNotFoundError:
        raise api_error(503, "service_unavailable", "rclone is not installed or not accessible")

    if session_id is not None:
        await _session_manager.cancel_session(session_id)
    return {"detail": f"Remote '{request.name}' created successfully"}


@router.post("/reconnect")
async def reconnect_remote(request: ReconnectRemoteRequest) -> dict[str, str]:
    """Store the token of a completed reconnect authorization in its remote.

    Only the token changes (and the app credentials, if the authorization
    was started with new ones); every other setting stays. Like
    /wizard/create, the token is taken from the session on the server.
    """
    rclone = _get_rclone()
    if not validate_remote_name(request.name):
        raise api_error(422, "invalid_remote_name", "Invalid remote name")
    session = _session_manager.get_session(request.session_id)
    if session is None:
        raise api_error(404, "wizard_session_not_found", "Wizard session not found or expired")
    if session.remote_name != request.name:
        raise api_error(422, "session_mismatch", "The wizard session was not started for this remote.")
    if session.status != "completed" or not session.token:
        raise api_error(409, "authorization_pending", "The authorization is not complete yet.")
    provider = get_provider(session.provider_id)
    if provider is None or provider.auth_type != AuthType.OAUTH:
        raise api_error(422, "wrong_oauth_flow", "The wizard session is not an OAuth sign-in.")

    # Plus what the sign-in found that the remote lacked (OneDrive's drive).
    values = {**session.extra_config, "token": session.token}
    remove: set[str] = set()
    if session.store_client:
        values["client_id"] = session.client_id or ""
        if session.client_secret:
            values["client_secret"] = session.client_secret
        else:
            remove.add("client_secret")
    try:
        await rclone.update_remote(request.name, provider.id, values, remove)
    except ValueError as e:
        logger.warning("Reconnect of '%s' refused: %s", request.name, e)
        raise api_error(
            409, "remote_changed",
            f"Remote '{request.name}' no longer exists as a {provider.display_name} remote.",
        )
    except Exception:
        logger.exception("Storing the new token of '%s' failed", request.name)
        raise api_error(500, "internal_error", f"Failed to update the remote. {SEE_LOG}")

    remote_auth.clear_auth_failed(request.name)
    await _session_manager.cancel_session(session.session_id)
    logger.info("Reconnected remote '%s'", request.name)
    return {"detail": f"Remote '{request.name}' reconnected"}


async def existing_remote_names(rclone: RcloneService, provider: ProviderInfo) -> set[str] | None:
    """The configured remotes, for a provider with a remote-path field (crypt);
    None for the others, which do not need them."""
    if not any(f.field_type == FieldType.REMOTE_PATH for f in provider.fields):
        return None
    try:
        return {r.name for r in await rclone.list_remotes()}
    except FileNotFoundError:
        raise api_error(503, "service_unavailable", "rclone is not installed or not accessible")
    except Exception:
        logger.exception("Listing remotes failed")
        raise api_error(500, "internal_error", f"Failed to list remotes. {SEE_LOG}")


async def obscure_secrets(
    rclone: RcloneService, provider: ProviderInfo, params: dict[str, str], name: str,
) -> dict[str, str]:
    """``params`` with the values rclone stores obscured run through `rclone obscure`."""
    out = dict(params)
    try:
        for f in provider.fields:
            if f.obscure and out.get(f.name):
                out[f.name] = await rclone.obscure(out[f.name])
    except FileNotFoundError:
        raise api_error(503, "service_unavailable", "rclone is not installed or not accessible")
    except RcloneError as e:
        logger.error("rclone obscure failed for remote '%s': %s", name, e)
        raise api_error(500, "internal_error", "Could not prepare the password for rclone")
    return out


@router.post("/test", response_model=TestRemoteResponse)
async def test_remote(request: TestRemoteRequest) -> TestRemoteResponse:
    """Run a connection test against a named remote."""
    rclone = _get_rclone()
    logger.info("test_remote: starting test for '%s'", request.name)

    try:
        success = await rclone.check_remote(request.name)
        logger.info("test_remote: '%s' result=%s", request.name, success)
        if success:
            return TestRemoteResponse(success=True)
        return TestRemoteResponse(
            success=False, error="Connection test failed", auth_error=remote_auth.auth_failed(request.name),
        )
    except RcloneError as e:
        logger.warning("test_remote: RcloneError for '%s': %s", request.name, e)
        return TestRemoteResponse(
            success=False, error=f"Connection test failed. {SEE_LOG}"
        )
    except FileNotFoundError:
        raise api_error(503, "service_unavailable", "rclone is not installed or not accessible")
    except Exception:
        logger.exception("test_remote: unexpected error for '%s'", request.name)
        return TestRemoteResponse(
            success=False, error=f"Connection test failed. {SEE_LOG}"
        )
