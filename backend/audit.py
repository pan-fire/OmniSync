"""The audit trail: who did what, in the same log as everything else.

Records go to the ``backend.audit`` logger (always at INFO or above,
whatever the log level). One record per user action::

    sync.start profile=docs direction=push force=false outcome=ok client=127.0.0.1

``client`` is the TCP peer; for a web UI request whose proxy vouched for the
browser's address (backend/api/forwarded_client.py) it is that address,
followed by ``via=<the web UI's server>``. Plus the request id the log filter adds (``[req:<id>]``). In the JSON
format the same values are in ``fields``: ``action``, ``outcome``,
``client`` and the targets (``profile``, ``remote``, ``target``, ...).

Only names, ids and flags are recorded, never a value that may hold a
secret (no request bodies, no passwords, tokens, URLs or remote settings).
Values are cut to 120 characters and cleaned of control characters, so a
crafted name cannot forge a log line.

``audited(...)`` wraps a route: it records the action once the route
returns (``outcome=ok``), or raised (``refused`` for a 4xx answer, with its
code; ``failed`` for anything else). ``audit(...)`` records one directly.
"""

from __future__ import annotations

import functools
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, ParamSpec, TypeVar

from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.logging_setup import AUDIT_LOGGER, current_request

audit_logger = logging.getLogger(AUDIT_LOGGER)

MAX_VALUE_LENGTH = 120
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_PLAIN = re.compile(r"[A-Za-z0-9._:/@+-]*")

P = ParamSpec("P")
R = TypeVar("R")


def _clean(value: object) -> str | bool | int | float | list[str]:
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(_clean(v)) for v in list(value)[:50]]
    text = getattr(value, "value", value)  # enums: their value
    text = _CONTROL.sub("?", str(text))
    return text if len(text) <= MAX_VALUE_LENGTH else text[: MAX_VALUE_LENGTH - 1] + "…"


def _render(value: str | bool | int | float | list[str]) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ",".join(_render(v) for v in value) + "]"
    text = str(value)
    if text and _PLAIN.fullmatch(text):
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def audit(
    action: str, *, outcome: str = "ok", level: int = logging.INFO, client: str | None = None, **fields: object,
) -> None:
    """Record ``action`` (e.g. "profile.update") with its targets and outcome.

    ``fields`` are names, ids and flags only; None values are left out. The
    client address defaults to the current request's.
    """
    _record(action, outcome, level, client, fields)


def _record(action: str, outcome: str, level: int, client: str | None, fields: Mapping[str, object]) -> None:
    ctx = current_request()
    # The web UI's server, when the client is the browser behind it.
    via = ctx.via if ctx and not client else None
    client = client or (ctx.client if ctx else None)
    values = {k: _clean(v) for k, v in fields.items() if v is not None}
    parts = [action, *(f"{k}={_render(v)}" for k, v in values.items()), f"outcome={outcome}"]
    if client:
        parts.append(f"client={_render(_clean(client))}")
    if via:
        parts.append(f"via={_render(_clean(via))}")
    audit_logger.log(
        level, "%s", " ".join(parts),
        extra={
            "fields": {"action": action, **values, "outcome": outcome, "client": client,
                       **({"via": via} if via else {})},
            "request_id": ctx.request_id if ctx else None,
        },
    )


def audited(
    action: str,
    targets: Callable[[Mapping[str, Any]], Mapping[str, object]] | None = None,
    **params: str,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Decorate an async route so each call is recorded as ``action``.

    ``params`` maps a field name to the route parameter it comes from
    (``profile="slug"``); ``targets`` computes more fields from the call's
    keyword arguments (e.g. the direction in the request body). Neither may
    return anything secret.
    """

    def decorate(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            fields: dict[str, object] = {name: kwargs.get(param) for name, param in params.items()}
            if targets is not None:
                try:
                    fields.update(targets(kwargs))
                except Exception:  # a malformed body; the route answers 422 itself
                    pass
            try:
                result = await func(*args, **kwargs)
            except StarletteHTTPException as exc:
                code = getattr(exc, "code", None)
                outcome = "refused" if exc.status_code < 500 else "failed"
                _record(action, outcome, logging.WARNING, None, {**fields, "status": exc.status_code, "code": code})
                raise
            except Exception:
                _record(action, "failed", logging.WARNING, None, fields)
                raise
            _record(action, "ok", logging.INFO, None, fields)
            return result

        vars(wrapper)["audit_action"] = action  # lets a test list what is audited
        return wrapper

    return decorate
