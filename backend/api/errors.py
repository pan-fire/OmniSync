"""The API's error answers, and keeping internal failure details out of them.

Every error answer has one shape (``ErrorResponse``, docs/api-errors.md)::

    {"detail": "<message for people>", "code": "<stable_snake_case>", "details": {...},
     "request_id": "<id>"}

``detail`` stays a human-readable string, so clients that only show it keep
working; ``code`` is what a client branches on; ``details`` (present only
when there is something in it) carries extra data such as ``errors``,
``invalid_paths``, ``names``, ``replacement`` or ``retry_after``; ``request_id`` is the id of the request (also in the
``X-Request-ID`` header), which finds its lines in the log. Routes raise
``api_error(...)``; ``install_error_handlers`` puts that, FastAPI's own
HTTP and validation errors, and unexpected exceptions into this shape.

Exception text from rclone or the OS (stderr, file paths, config fragments)
goes to the server log; clients get a fixed human-readable message.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api.request_id import REQUEST_ID_HEADER, scope_request_id
from backend.api.schemas import ErrorResponse, TestSyncResponse
from backend.logging_setup import LOGGED_MARK, current_request_id

logger = logging.getLogger(__name__)

# The code of an error raised without one (FastAPI's own 404/405, a plain
# HTTPException): by status. Unlisted statuses get "http_<status>".
STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "body_too_large",
    415: "unsupported_media_type",
    422: "invalid_request",
    429: "too_many_requests",
    500: "internal_error",
    502: "upstream_error",
    503: "service_unavailable",
}

VALIDATION_FAILED = "validation_failed"
INTERNAL_ERROR_MESSAGE = "Internal server error. The OmniSync log has the details."

# The OpenAPI "responses" every operation shares: errors come in the envelope.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    "4XX": {"model": ErrorResponse, "description": "Client error (codes: docs/api-errors.md)"},
    "5XX": {"model": ErrorResponse, "description": "Server error (codes: docs/api-errors.md)"},
}


class ApiError(StarletteHTTPException):
    """An HTTP error with a stable ``code`` and optional ``details``.

    A subclass of HTTPException, so ``exc.detail`` is still the message and
    code that catches HTTPException keeps working.
    """

    def __init__(
        self, status_code: int, code: str, message: str, *,
        headers: dict[str, str] | None = None, details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code = code
        self.details = details or {}


def api_error(
    status_code: int, code: str, message: str, *, headers: dict[str, str] | None = None, **details: Any,
) -> ApiError:
    """The exception for an error answer: ``raise api_error(404, "profile_not_found", "...")``.

    Keyword arguments other than ``headers`` go into ``details``.
    """
    return ApiError(status_code, code, message, headers=headers, details=details)


def error_body(
    code: str, message: str, details: dict[str, Any] | None = None, request_id: str | None = None,
) -> dict[str, Any]:
    """The JSON body of an error answer; ``details`` is left out when empty.

    ``request_id`` defaults to the id of the request being handled.
    """
    body: dict[str, Any] = {"detail": message, "code": code}
    if details:
        body["details"] = jsonable_encoder(details)
    request_id = request_id or current_request_id()
    if request_id:
        body["request_id"] = request_id
    return body


def error_body_bytes(code: str, message: str) -> bytes:
    """``error_body`` encoded, for ASGI middleware that answers by itself."""
    return json.dumps(error_body(code, message), separators=(",", ":")).encode()


def default_code(status_code: int) -> str:
    return STATUS_CODES.get(status_code, f"http_{status_code}")


def _location(loc: Sequence[Any]) -> str:
    # loc is e.g. ("body", "remote_dir", 0); the request part says nothing to people.
    parts = list(loc)
    if parts and parts[0] in ("body", "query", "path", "header", "cookie"):
        parts = parts[1:]
    return ".".join(str(p) for p in parts)


def validation_summary(errors: Sequence[Any]) -> str:
    """One readable line for FastAPI's validation errors: "field: message; ..."."""
    lines = []
    for err in errors:
        where = _location(err.get("loc", ()))
        msg = str(err.get("msg", "invalid value"))
        lines.append(f"{where}: {msg}" if where else msg)
    return "; ".join(lines) or "The request is not valid."


def _public_validation_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    # Only where, what and which kind: "input" would echo the submitted
    # value (a password, a token) back; "ctx" and "url" add nothing here.
    return [{"loc": list(e.get("loc", ())), "msg": str(e.get("msg", "")), "type": str(e.get("type", ""))}
            for e in errors]


async def _http_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    if isinstance(exc, ApiError):
        body = error_body(exc.code, exc.detail, exc.details)
    elif isinstance(exc.detail, str):
        body = error_body(default_code(exc.status_code), exc.detail)
    else:  # an object or list detail from code that does not use api_error
        raw: Any = exc.detail
        detail: dict[str, Any] = raw if isinstance(raw, dict) else {"errors": raw}
        code = detail.get("code")
        message = detail.get("message")
        body = error_body(
            code if isinstance(code, str) else default_code(exc.status_code),
            message if isinstance(message, str) else "The request failed.",
            {k: v for k, v in detail.items() if k not in ("code", "message")},
        )
    return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)


async def _validation_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = exc.errors()
    body = error_body(VALIDATION_FAILED, validation_summary(errors), {"errors": _public_validation_errors(errors)})
    return JSONResponse(body, status_code=422)


async def _unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # Starlette's ServerErrorMiddleware calls this outside the request-id
    # middleware, then re-raises the exception for the server. It is logged
    # here, with its traceback and the request's id (the server's own
    # "Exception in ASGI application" line for it is then dropped, see
    # LogRecordFilter).
    request_id = scope_request_id(request.scope)
    logger.error(
        "Unhandled error in %s %s", request.method, request.url.path, exc_info=exc, extra={"request_id": request_id},
    )
    try:
        setattr(exc, LOGGED_MARK, True)
    except AttributeError:  # an exception type with __slots__
        pass
    headers = {REQUEST_ID_HEADER: request_id} if request_id else None
    return JSONResponse(
        error_body("internal_error", INTERNAL_ERROR_MESSAGE, request_id=request_id), status_code=500, headers=headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    """Answer every error of ``app`` in the envelope (see the module docstring)."""
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unexpected_error)


SEE_LOG = "The OmniSync log has the details."

# Messages rclone.test_sync builds without exception text, safe to pass on.
_SAFE_TEST_SYNC_ERRORS = frozenset({
    "Invalid remote_dir format — expected 'name:path'",
    "Failed to upload test file to remote",
    "One or more steps failed",
})

_TEST_SYNC_STEP_ERRORS = {
    "local_write": "Cannot write to the local folder.",
    "remote_upload": "Uploading the test file to the remote failed.",
    "remote_verify": "The test file was not found on the remote after the upload.",
    "remote_cleanup": "The test file could not be removed from the remote.",
    "local_cleanup": "The local test file could not be removed.",
}


def public_test_sync_result(result: dict[str, Any], log: logging.Logger) -> TestSyncResponse:
    """Build the API response for a sync test, logging what it leaves out.

    Steps keep their name and outcome; per-step exception text is dropped.
    """
    steps: list[dict[str, object]] = [
        {"step": s.get("step"), "ok": bool(s.get("ok"))} for s in result.get("steps", [])
    ]
    if result.get("success"):
        return TestSyncResponse(success=True, steps=steps)

    log.warning("Sync test failed: %s", result)
    error = result.get("error")
    failed = next((s["step"] for s in steps if not s["ok"]), None)
    if isinstance(failed, str) and failed in _TEST_SYNC_STEP_ERRORS:
        public = f"{_TEST_SYNC_STEP_ERRORS[failed]} {SEE_LOG}"
    elif error in _SAFE_TEST_SYNC_ERRORS:
        public = str(error)
    else:
        public = f"The sync test failed. {SEE_LOG}"
    return TestSyncResponse(success=False, steps=steps, error=public)


def failed_test_sync(log: logging.Logger) -> TestSyncResponse:
    """Response for a sync test that raised; call inside the except block."""
    log.exception("Sync test failed unexpectedly")
    return TestSyncResponse(success=False, error=f"The sync test failed. {SEE_LOG}")
