"""Every error answer has the same envelope: detail (text), code, optional details.

docs/api-errors.md is the list of codes clients can rely on; a test here
keeps it in step with the codes the backend raises.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend import security
from backend.api import errors
from backend.api.errors import ApiError, api_error, install_error_handlers
from backend.tests.auth import AUTH_HEADERS

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "api-errors.md"


def _assert_envelope(body: dict, code: str) -> None:
    assert isinstance(body["detail"], str) and body["detail"]
    assert body["code"] == code
    assert set(body) <= {"detail", "code", "details"}
    if "details" in body:
        assert isinstance(body["details"], dict) and body["details"]


@pytest.fixture
async def bare_client(test_client):
    """The app without credentials (same wiring as test_client)."""
    from backend.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_route_error_has_code(test_client):
    resp = await test_client.get("/profiles/nope")
    assert resp.status_code == 404
    _assert_envelope(resp.json(), "profile_not_found")
    assert resp.json()["detail"] == "Profile 'nope' not found"


async def test_unknown_route_gets_a_status_code(test_client):
    resp = await test_client.get("/no-such-route")
    assert resp.status_code == 404
    _assert_envelope(resp.json(), "not_found")


async def test_wrong_method_gets_a_status_code(test_client):
    resp = await test_client.delete("/health")
    assert resp.status_code == 405
    _assert_envelope(resp.json(), "method_not_allowed")


async def test_validation_error_is_text_with_the_issues_in_details(test_client):
    resp = await test_client.post("/profiles", json={"name": "x", "local_dir": "/tmp/x", "secret_field": "hunter2"})
    assert resp.status_code == 422
    body = resp.json()
    _assert_envelope(body, "validation_failed")
    issues = body["details"]["errors"]
    assert issues and all(set(i) == {"loc", "msg", "type"} for i in issues)
    # "field: message", the request part of loc dropped.
    assert "remote_dir: Field required" in body["detail"]
    assert "hunter2" not in resp.text  # the submitted values are not echoed


async def test_missing_and_invalid_token_codes(bare_client):
    resp = await bare_client.get("/logs")
    _assert_envelope(resp.json(), "token_missing")
    resp = await bare_client.get("/logs", headers={"Authorization": "Bearer wrong"})
    _assert_envelope(resp.json(), "token_invalid")
    assert resp.json()["detail"] == "Invalid API token."


async def test_throttled_auth_carries_retry_after(bare_client):
    for _ in range(security.AUTH_FAILURE_LIMIT):
        await bare_client.get("/logs", headers={"Authorization": "Bearer wrong"})
    resp = await bare_client.get("/logs", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 429
    body = resp.json()
    _assert_envelope(body, "auth_throttled")
    assert body["details"]["retry_after"] == int(resp.headers["retry-after"])


async def test_middleware_answers_use_the_envelope(bare_client):
    resp = await bare_client.get("/health", headers={"Host": "evil.example"})
    assert resp.status_code == 400
    _assert_envelope(resp.json(), "host_not_allowed")

    resp = await bare_client.post("/profiles", headers={**AUTH_HEADERS, "content-length": str(10 ** 9)}, content=b"{}")
    assert resp.status_code == 413
    _assert_envelope(resp.json(), "body_too_large")


async def test_retired_route_names_its_replacement(test_client):
    resp = await test_client.post("/sync/start", json={"direction": "push"})
    assert resp.status_code == 410
    body = resp.json()
    _assert_envelope(body, "route_removed")
    assert body["details"]["replacement"] in body["detail"]


async def test_unexpected_exception_and_legacy_details():
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret path /etc/x")

    @app.get("/legacy")
    async def legacy() -> None:
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail={"code": "name_clash", "message": "Taken", "names": ["a"]})

    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t") as c:
        resp = await c.get("/boom")
        assert resp.status_code == 500
        _assert_envelope(resp.json(), "internal_error")
        assert "secret" not in resp.text

        resp = await c.get("/legacy")
        assert resp.json() == {"detail": "Taken", "code": "name_clash", "details": {"names": ["a"]}}


def test_api_error_is_an_http_exception_with_details():
    exc = api_error(409, "sync_busy", "Busy", headers={"X": "1"}, job_id=3)
    assert isinstance(exc, ApiError)
    assert (exc.status_code, exc.detail, exc.code, exc.details, exc.headers) == (409, "Busy", "sync_busy", {"job_id": 3}, {"X": "1"})
    assert errors.error_body("x", "y") == {"detail": "y", "code": "x"}


def _raised_codes() -> set[str]:
    """Every code literal passed to api_error / error_body(_bytes) in the backend."""
    codes: set[str] = set()
    for path in (ROOT / "backend").rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            index = {"api_error": 1, "ApiError": 1, "error_body": 0, "error_body_bytes": 0}.get(name or "")
            if index is None or len(node.args) <= index:
                continue
            arg = node.args[index]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                codes.add(arg.value)
    return codes


def test_every_code_is_documented():
    documented = set(re.findall(r"^\| `([a-z0-9_]+)` \|", DOC.read_text(), flags=re.MULTILINE))
    raised = _raised_codes() | set(errors.STATUS_CODES.values()) | {errors.VALIDATION_FAILED}
    assert len(raised) > 40
    assert not raised - documented, f"add to docs/api-errors.md: {sorted(raised - documented)}"
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", c) for c in raised)


def test_openapi_documents_the_envelope():
    from backend.main import app

    schema = app.openapi()
    assert "ErrorResponse" in schema["components"]["schemas"]
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    for path, ops in schema["paths"].items():
        for method, op in ops.items():
            # (the OAuth callback answers HTML; its media type follows the route's)
            [content] = op["responses"]["4XX"]["content"].values()
            ref = content["schema"]["$ref"]
            assert ref.endswith("/ErrorResponse"), (method, path)
