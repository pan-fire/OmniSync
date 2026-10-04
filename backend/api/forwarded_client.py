"""The browser's address behind the web UI, for the audit trail only.

Requests from the web UI reach the backend from the web UI's server, so
their TCP peer is that container, not the browser. The web UI's proxy
(frontend/src/proxy.ts and src/lib/forwarded-client.ts) therefore sends the
address it saw the browser at in ``X-OmniSync-Client``::

    v1;<address>;<unix seconds>;<hex HMAC-SHA256>

The HMAC covers ``v1``, the address and the time, keyed with the API token
the proxy already holds, so no other client can make one up without the
token. The value is believed only when

- the request carried the right API token (``require_api_token`` calls
  ``apply_forwarded_client`` only after that check passed),
- there is exactly one such header,
- the HMAC checks out (compared in constant time),
- it is at most ``MAX_AGE_SECONDS`` old and not from the future (beyond
  ``MAX_CLOCK_SKEW_SECONDS``), and
- the address is a plain IPv4 or IPv6 address (so it cannot forge a log line).

It then replaces the client address of the current request's log context:
what the audit trail records as ``client`` (with the TCP peer as ``via``).
Nothing else uses it. Authentication, the failed-token throttle and every
other decision keep using the TCP peer, so a valid header can neither
unblock an address nor get one blocked.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import time

from starlette.requests import Request

from backend.logging_setup import current_request

logger = logging.getLogger(__name__)

HEADER = "x-omnisync-client"
VERSION = "v1"
MAX_AGE_SECONDS = 60
MAX_CLOCK_SKEW_SECONDS = 5
# An address with a zone id ("fe80::1%eth0") at most; longer values are refused unread.
_MAX_VALUE_LENGTH = 200
# An unverifiable header is a misconfiguration (or a probe with the token):
# logged at most once per this many seconds.
_WARN_INTERVAL = 60.0
_last_warning = 0.0


def _mac(token: str, address: str, timestamp: int) -> str:
    message = f"{VERSION}\n{address}\n{timestamp}".encode()
    return hmac.new(token.encode(), message, hashlib.sha256).hexdigest()


def sign(token: str, address: str, timestamp: int) -> str:
    """The header value for ``address`` at ``timestamp`` (what the web UI's proxy sends)."""
    return f"{VERSION};{address};{timestamp};{_mac(token, address, timestamp)}"


def _plain_address(value: str) -> str | None:
    """``value`` as a normalised IP address, or None for anything else."""
    candidate = value[1:-1] if value.startswith("[") and value.endswith("]") else value
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def verified_address(value: str, token: str, now: float | None = None) -> str | None:
    """The address a header value vouches for, or None when it does not verify."""
    if not token or len(value) > _MAX_VALUE_LENGTH:
        return None
    parts = value.split(";")
    if len(parts) != 4 or parts[0] != VERSION:
        return None
    _, address, stamp, mac = parts
    if not stamp.isascii() or not stamp.isdigit() or len(stamp) > 12:
        return None
    timestamp = int(stamp)
    if not hmac.compare_digest(mac.encode(), _mac(token, address, timestamp).encode()):
        return None
    age = (time.time() if now is None else now) - timestamp
    if age > MAX_AGE_SECONDS or age < -MAX_CLOCK_SKEW_SECONDS:
        return None
    return _plain_address(address)


def apply_forwarded_client(request: Request, token: str) -> None:
    """Record the web UI's browser address as the request's client, for the audit trail.

    Call only once the request's API token was checked. Without the header
    nothing changes; a header that does not verify is ignored (and logged).
    """
    global _last_warning
    values = request.headers.getlist(HEADER)
    if not values:
        return
    address = verified_address(values[0], token) if len(values) == 1 else None
    ctx = current_request()
    if address is None:
        now = time.monotonic()
        if now - _last_warning >= _WARN_INTERVAL:
            _last_warning = now
            logger.warning(
                "Ignored an %s header that does not verify (stale, wrong key or malformed); "
                "the audit trail records the TCP peer instead", HEADER,
            )
        return
    if ctx is not None:
        ctx.via = ctx.client
        ctx.client = address
