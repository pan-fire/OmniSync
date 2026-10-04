"""Request ids: one per request, in the response header, the log and error answers.

``RequestIdMiddleware`` (the outermost middleware) gives every request an id
and returns it as ``X-Request-ID``. Every line logged while the request is
handled carries it (``[req:<id>]`` in the text log, ``request_id`` in JSON
lines), and so does every error answer (``request_id`` in the envelope), so
a user can quote it and the owner can find the matching log lines.

A client may send its own ``X-Request-ID`` (a reverse proxy that already
assigns ids, a script that wants to correlate); it is used only when it is a
short token: 8 to 64 letters, digits, ``.``, ``_`` or ``-``. Anything else
(too long, spaces, line breaks that could forge log lines) is replaced by a
fresh id.
"""

from __future__ import annotations

import re
import secrets

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.logging_setup import RequestContext, enter_request

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{7,63}")


def new_request_id() -> str:
    """A fresh id: 16 hex characters, short enough to quote."""
    return secrets.token_hex(8)


def accepted_request_id(value: str | None) -> str | None:
    """``value`` when it is a usable client-supplied id, else None."""
    if value and _VALID_ID.fullmatch(value):
        return value
    return None


def scope_request_id(scope: Scope) -> str | None:
    """The id the middleware gave this request (also after it finished)."""
    state = scope.get("state")
    value = state.get("request_id") if isinstance(state, dict) else None
    return value if isinstance(value, str) else None


class RequestIdMiddleware:
    """ASGI middleware: assign the request id, expose it to logging, return it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        incoming = None
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                incoming = value.decode("latin-1")
                break
        request_id = accepted_request_id(incoming) or new_request_id()
        client = scope.get("client")
        # The exception handler for unexpected errors runs outside this
        # middleware (Starlette's ServerErrorMiddleware); it finds the id here.
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if REQUEST_ID_HEADER not in headers:
                    headers.append(REQUEST_ID_HEADER, request_id)
            await send(message)

        leave = enter_request(RequestContext(request_id, client[0] if client else None))
        try:
            await self.app(scope, receive, send_with_id)
        finally:
            leave()
