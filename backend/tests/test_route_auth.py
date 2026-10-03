"""Every route needs the API token.

The test enumerates app.routes, so a route added later is covered without
editing this file. Only the routes in ``security.PUBLIC_ROUTES`` are exempt.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from backend import security
from backend.api.routes import wizard
from backend.main import app
from backend.services.rclone import RcloneService
from backend.services.wizard_sessions import WizardSessionManager
from backend.tests.auth import AUTH_HEADERS

try:  # FastAPI >= 0.140 keeps included routers as nodes in app.routes
    from fastapi.routing import iter_route_contexts
except ImportError:  # pragma: no cover - older FastAPI lists APIRoutes directly
    iter_route_contexts = None

EXPECTED_PUBLIC = {("GET", "/health"), ("GET", "/wizard/oauth/callback")}


def _dummy_path(path: str) -> str:
    """Fill each path parameter ({name} or {name:convertor}) with a valid value."""
    def value(match: re.Match[str]) -> str:
        convertor = match.group(2) or "str"
        if convertor in ("int", "float"):
            return "1"
        if convertor == "path":
            return "dir/file.txt"
        return "dummy"

    return re.sub(r"{([^}:]+)(?::([^}]*))?}", value, path)


def _all_routes():
    """(original route, full path, methods) for every route the app serves."""
    if iter_route_contexts is None:
        for route in app.routes:
            yield route, getattr(route, "path", None), getattr(route, "methods", None)
        return
    for ctx in iter_route_contexts(app.routes):
        yield ctx.original_route, ctx.path, ctx.methods


def _operations() -> list[tuple[str, str, str]]:
    ops = []
    for route, path, methods in _all_routes():
        # Anything that is not an APIRoute (a websocket, a mount) would skip
        # the app-level token dependency, so none may exist.
        assert isinstance(route, APIRoute), f"unexpected route type {type(route).__name__}: {route}"
        assert path and methods
        for method in sorted(methods):
            ops.append((method, path, _dummy_path(path)))
    return ops


OPERATIONS = _operations()
PROTECTED = [op for op in OPERATIONS if (op[0], op[1]) not in EXPECTED_PUBLIC]


def test_public_routes_are_exactly_health_and_oauth_callback():
    assert security.PUBLIC_ROUTES == EXPECTED_PUBLIC
    registered = {(method, path) for method, path, _ in OPERATIONS}
    assert EXPECTED_PUBLIC <= registered
    # Sanity check that the enumeration sees the whole API.
    assert len(PROTECTED) >= 60


@pytest.fixture
async def bare_client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.parametrize(("method", "template", "path"), PROTECTED, ids=[f"{m} {t}" for m, t, _ in PROTECTED])
async def test_route_without_token_is_401(bare_client, method, template, path):
    resp = await bare_client.request(method, path)
    assert resp.status_code == 401, f"{method} {template} answered {resp.status_code} without a token"
    assert resp.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(("method", "template", "path"), PROTECTED, ids=[f"{m} {t}" for m, t, _ in PROTECTED])
async def test_route_with_wrong_token_is_401(bare_client, method, template, path):
    resp = await bare_client.request(method, path, headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401, f"{method} {template} answered {resp.status_code} with a wrong token"


async def test_public_routes_answer_without_token(test_client):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/health")).status_code != 401
        assert (await client.get("/wizard/oauth/callback")).status_code != 401


# --- The OAuth callback accepts only the state of an open wizard session ---


@pytest.fixture
async def callback_setup(tmp_path, monkeypatch):
    """A real RcloneService on a scratch rclone.conf, one open wizard session."""
    conf = tmp_path / "rclone.conf"
    conf.write_text("[existing]\ntype = local\n")
    rclone = RcloneService(rclone_config_path=str(conf))
    write_config = MagicMock()
    monkeypatch.setattr(rclone, "_write_config", write_config)
    exchange = AsyncMock(return_value='{"access_token": "tok"}')
    monkeypatch.setattr(wizard, "exchange_code_for_token", exchange)
    monkeypatch.setattr(wizard, "_rclone_service", rclone)
    monkeypatch.setattr(wizard, "_session_manager", WizardSessionManager())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Own client credentials: the callback flow.
        resp = await client.post("/wizard/authorize", json={
            "provider_id": "drive", "client_id": "own.apps.googleusercontent.com", "client_secret": "s",
        }, headers=AUTH_HEADERS)
        assert resp.status_code == 200
        session = wizard._session_manager.get_session(resp.json()["session_id"])
        assert session is not None
        yield client, session, conf, exchange, write_config


def _snapshot(session) -> dict:
    return {k: v for k, v in vars(session).items()}


@pytest.mark.parametrize("state", ["unknown-state", "00000000-0000-0000-0000-000000000000"])
async def test_callback_with_unknown_state_changes_nothing(callback_setup, state):
    client, session, conf, exchange, write_config = callback_setup
    before_session = _snapshot(session)
    before_conf = conf.read_bytes()

    resp = await client.get("/wizard/oauth/callback", params={"code": "attacker-code", "state": state})

    assert resp.status_code == 404
    exchange.assert_not_awaited()
    write_config.assert_not_called()
    assert _snapshot(session) == before_session
    assert session.status == "pending" and session.token is None
    assert wizard._session_manager.active_count == 1
    assert conf.read_bytes() == before_conf


async def test_callback_replay_on_completed_session_changes_nothing(callback_setup):
    client, session, conf, exchange, write_config = callback_setup
    ok = await client.get("/wizard/oauth/callback", params={"code": "c1", "state": session.session_id})
    assert ok.status_code == 200
    assert session.status == "completed" and session.token == '{"access_token": "tok"}'
    exchange.reset_mock()
    before_session = _snapshot(session)
    before_conf = conf.read_bytes()

    replay = await client.get("/wizard/oauth/callback", params={"code": "c2", "state": session.session_id})

    assert replay.status_code == 409
    exchange.assert_not_awaited()
    write_config.assert_not_called()
    assert _snapshot(session) == before_session
    assert conf.read_bytes() == before_conf
