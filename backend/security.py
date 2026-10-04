"""Request authentication and host validation for the OmniSync API.

Every route requires ``Authorization: Bearer <token>`` except the ones in
``PUBLIC_ROUTES``: the container health check, and the OAuth redirect target
the provider sends the user's browser to (that one is protected by the OAuth
``state`` parameter instead). Requiring a header also defeats cross-site form
posts, which cannot set one.

The token comes from ``OMNISYNC_API_TOKEN``. When that is unset, one is
generated on first use and stored in ``OMNISYNC_API_TOKEN_FILE`` (default
``/data/omnisync/api-token``, mode 0600), so a fresh install is never open.

Requests whose ``Host`` header is not in the allow-list are rejected before
routing, which stops DNS-rebinding attacks from web pages the user visits.
The default list covers loopback; ``OMNISYNC_ALLOWED_HOSTS`` adds more
(comma-separated host names, without ports).

A token from ``OMNISYNC_API_TOKEN`` shorter than ``MIN_TOKEN_LENGTH`` is
used, but logged as a warning at startup. Wrong tokens are recorded in the audit
trail (``auth.token_rejected``, at most once per client address and minute,
so the log cannot be flooded); after
``AUTH_FAILURE_LIMIT`` of them within a minute the address gets 429 for
every request without the right token for ``AUTH_BLOCK_SECONDS``. A request
with the right token always passes, so nobody can lock the owner out by
failing from the owner's address.

The client address is the TCP peer. uvicorn must run with
``--no-proxy-headers`` (the Docker image does): with proxy headers on, it
believes ``X-Forwarded-For`` from loopback, and a client there could pick
any address per request to dodge the limit. The web UI's own login
throttle handles ``X-Forwarded-For`` from a reverse proxy. The browser
address the web UI vouches for (``X-OmniSync-Client``, see
backend/api/forwarded_client.py) goes into the audit trail only, never
into these decisions.

Request bodies larger than ``MAX_BODY_BYTES`` are refused with 413 before
any route reads them.
"""

from __future__ import annotations

import contextlib
import hmac
import logging
import os
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.api.errors import api_error, error_body_bytes
from backend.api.forwarded_client import apply_forwarded_client
from backend.audit import audit
from backend.logging_setup import register_secret

logger = logging.getLogger(__name__)

DEFAULT_TOKEN_FILE = "/data/omnisync/api-token"
DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "::1")

# (method, path) pairs reachable without a token.
PUBLIC_ROUTES = frozenset({
    ("GET", "/health"),
    ("GET", "/wizard/oauth/callback"),
})

_cached: tuple[str | None, str] | None = None  # (env value it was read under, token)

# A generated token has 43 characters (32 random bytes); shorter configured
# tokens are accepted, since refusing them would lock out existing installs.
MIN_TOKEN_LENGTH = 32

# Failed-authentication throttling, per client address.
AUTH_FAILURE_WINDOW = 60.0     # seconds over which failures are counted
AUTH_FAILURE_LIMIT = 10        # failures in the window before blocking
AUTH_BLOCK_SECONDS = 60.0      # how long a blocked address gets 429
AUTH_LOG_INTERVAL = 60.0       # at most one failure log line per address
_MAX_TRACKED_CLIENTS = 10000   # bounds memory under many source addresses

# Largest request body accepted (JSON requests are a few kB at most).
MAX_BODY_BYTES = 1024 * 1024


def atomic_write_file(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Replace ``path`` with ``data`` so a crash leaves the old or the new file, never a mix.

    The data goes to a temporary file in the same directory (created with
    ``mode``), is flushed to disk, and then renamed over ``path``; the
    directory is synced so the rename itself survives a power loss. If
    anything fails (a full disk, say), ``path`` is untouched and the
    temporary file is removed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        try:
            os.fchmod(fd, mode)
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    _fsync_dir(path.parent)


def _fsync_dir(directory: Path) -> None:
    try:
        dir_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return  # e.g. a platform that cannot open directories
    try:
        os.fsync(dir_fd)
    except OSError:
        pass  # some file systems do not support syncing a directory
    finally:
        os.close(dir_fd)


def write_secret_file(path: Path, content: str, keep_backup: bool = False) -> None:
    """Write ``content`` to ``path`` readable and writable by the owner only.

    The write is atomic (see atomic_write_file). ``keep_backup`` first
    copies the current file to ``<path>.bak`` (also owner-only), so the
    previous version can be restored by hand.
    """
    if keep_backup:
        try:
            previous = path.read_bytes()
        except FileNotFoundError:
            previous = None
        if previous:
            atomic_write_file(path.with_name(path.name + ".bak"), previous)
    atomic_write_file(path, content.encode())


def get_api_token() -> str:
    """Return the API token, generating and storing one if none is configured."""
    global _cached
    env_token = os.environ.get("OMNISYNC_API_TOKEN", "").strip() or None
    if _cached is not None and _cached[0] == env_token:
        register_secret(_cached[1])  # a no-op once registered
        return _cached[1]

    if env_token:
        token = env_token
        if len(token) < MIN_TOKEN_LENGTH:
            logger.warning(
                "OMNISYNC_API_TOKEN is only %d characters long; use at least %d random characters "
                "(e.g. 'openssl rand -hex 32') so it cannot be guessed.",
                len(token), MIN_TOKEN_LENGTH,
            )
    else:
        token_file = Path(os.environ.get("OMNISYNC_API_TOKEN_FILE", DEFAULT_TOKEN_FILE))
        try:
            token = token_file.read_text().strip()
        except FileNotFoundError:
            token = ""
        if not token:
            token = secrets.token_urlsafe(32)
            write_secret_file(token_file, token + "\n")
            logger.warning(
                "No OMNISYNC_API_TOKEN set; generated an API token in %s. "
                "Clients must send it as 'Authorization: Bearer <token>'.",
                token_file,
            )
    _cached = (env_token, token)
    # Masked in every log line from now on, whatever the line looks like.
    register_secret(token)
    return token


def allowed_hosts() -> set[str]:
    extra = os.environ.get("OMNISYNC_ALLOWED_HOSTS", "")
    return set(DEFAULT_ALLOWED_HOSTS) | {h.strip().lower() for h in extra.split(",") if h.strip()}


def _host_name(host_header: str) -> str:
    """Strip the port from a Host header value, keeping IPv6 literals intact."""
    host = host_header.strip().lower()
    if host.startswith("["):
        return host[1:host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


@dataclass
class _ClientFailures:
    window_start: float
    count: int = 0
    blocked_until: float = 0.0
    last_logged: float = float("-inf")
    unlogged: int = 0  # failures since the last log line


_failures: dict[str, _ClientFailures] = {}


def reset_auth_throttle() -> None:
    """Forget all recorded failures (tests, or after changing the token)."""
    _failures.clear()


def _client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _prune(now: float) -> None:
    """Drop entries that no longer block or count, keeping the table bounded."""
    if len(_failures) < _MAX_TRACKED_CLIENTS:
        return
    for addr in [a for a, f in _failures.items()
                 if f.blocked_until <= now and now - f.window_start >= AUTH_FAILURE_WINDOW]:
        del _failures[addr]
    while len(_failures) >= _MAX_TRACKED_CLIENTS:  # all still active: drop the oldest
        del _failures[next(iter(_failures))]


def _check_blocked(addr: str, now: float) -> None:
    entry = _failures.get(addr)
    if entry is not None and entry.blocked_until > now:
        retry_after = max(1, int(entry.blocked_until - now + 0.999))
        raise api_error(
            429, "auth_throttled",
            "Too many failed authentication attempts. Try again in a minute.",
            headers={"Retry-After": str(retry_after)}, retry_after=retry_after,
        )


def _record_failure(addr: str, now: float) -> None:
    entry = _failures.get(addr)
    if entry is None:
        _prune(now)
        entry = _failures[addr] = _ClientFailures(window_start=now)
    if now - entry.window_start >= AUTH_FAILURE_WINDOW:
        entry.window_start, entry.count = now, 0
    entry.count += 1
    entry.unlogged += 1
    if entry.count >= AUTH_FAILURE_LIMIT:
        entry.blocked_until = now + AUTH_BLOCK_SECONDS
        entry.window_start, entry.count = now, 0
    if now - entry.last_logged >= AUTH_LOG_INTERVAL:
        # In the audit trail; the guessed token itself is never logged.
        audit(
            "auth.token_rejected", outcome="denied", level=logging.WARNING, client=addr,
            attempts=entry.unlogged,
            blocked_seconds=int(AUTH_BLOCK_SECONDS) if entry.blocked_until > now else None,
        )
        entry.last_logged, entry.unlogged = now, 0


async def require_api_token(request: Request) -> None:
    """FastAPI dependency: reject requests without the right bearer token.

    A request with the right token always passes, also from a blocked
    address: the block only slows down guessing, and must not let anyone
    who can make requests from (or appear to come from) the owner's address
    lock the owner out. Without the right token a blocked address gets 429
    before the token is looked at.
    """
    if (request.method, request.url.path) in PUBLIC_ROUTES:
        return
    header = request.headers.get("authorization", "")
    scheme, _, supplied = header.partition(" ")
    bearer = scheme.lower() == "bearer" and bool(supplied)
    token = get_api_token()
    if bearer and hmac.compare_digest(supplied.strip().encode(), token.encode()):
        # Only now: the web UI's word for the browser's address, for the audit trail.
        apply_forwarded_client(request, token)
        return
    addr = _client_address(request)
    now = time.monotonic()
    _check_blocked(addr, now)
    if not bearer:
        raise api_error(
            401, "token_missing",
            "Missing API token. Send 'Authorization: Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # A missing header (above) is a client not set up yet and is not
    # counted; a wrong token may be someone guessing.
    _record_failure(addr, now)
    raise api_error(401, "token_invalid", "Invalid API token.", headers={"WWW-Authenticate": "Bearer"})


class TrustedHostGuard:
    """ASGI middleware that rejects requests whose Host is not allow-listed.

    Unlike Starlette's TrustedHostMiddleware it re-reads the allow-list per
    request, so ``OMNISYNC_ALLOWED_HOSTS`` changes need no code path to
    rebuild the middleware stack.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        host = ""
        for name, value in scope.get("headers", []):
            if name == b"host":
                host = value.decode("latin-1")
                break
        if _host_name(host) not in allowed_hosts():
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            body = error_body_bytes(
                "host_not_allowed", "Host not allowed. Add it to OMNISYNC_ALLOWED_HOSTS if this is intended.",
            )
            await send({
                "type": "http.response.start",
                "status": 400,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            })
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


class BodySizeLimit:
    """ASGI middleware that refuses request bodies over ``max_bytes`` with 413.

    A declared Content-Length is checked up front; a body without one
    (chunked) is read up to the limit before the app sees it, so no route
    ever has to hold more than ``max_bytes`` of a request in memory.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def _reject(self, send: Send) -> None:
        body = error_body_bytes("body_too_large", f"Request body too large (limit {self.max_bytes} bytes).")
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = -1
                if declared < 0 or declared > self.max_bytes:
                    await self._reject(send)
                    return
                break

        # Read the body now (it is at most max_bytes), then hand it on.
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # The client went away; let the app see that.
                pending: list = [message]
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_bytes:
                await self._reject(send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                pending = [{"type": "http.request", "body": b"".join(chunks), "more_body": False}]
                break

        async def replay() -> Message:
            if pending:
                return pending.pop(0)
            return await receive()

        await self.app(scope, replay, send)
