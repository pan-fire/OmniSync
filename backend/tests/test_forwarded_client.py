"""X-OmniSync-Client: the browser's address the web UI vouches for, audit trail only."""

from __future__ import annotations

import logging
import time

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from backend import security
from backend.api import forwarded_client
from backend.api.errors import install_error_handlers
from backend.api.forwarded_client import MAX_AGE_SECONDS, sign, verified_address
from backend.api.request_id import RequestIdMiddleware
from backend.audit import audited
from backend.logging_setup import AUDIT_LOGGER
from backend.tests.auth import AUTH_HEADERS, TEST_API_TOKEN

PEER = "172.18.0.3"  # the web UI's container
BROWSER = "203.0.113.9"


def _app() -> FastAPI:
    app = FastAPI(dependencies=[Depends(security.require_api_token)])
    install_error_handlers(app)
    app.add_middleware(RequestIdMiddleware)

    @app.post("/things/{slug}")
    @audited("thing.poke", profile="slug")
    async def poke(slug: str) -> dict[str, str]:
        return {"ok": slug}

    return app


async def _poke(headers: dict[str, str] | list[tuple[str, str]], peer: str = PEER) -> int:
    transport = ASGITransport(app=_app(), raise_app_exceptions=False, client=(peer, 4321))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return (await client.post("/things/docs", headers=headers)).status_code


def _audit_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == AUDIT_LOGGER]


def _header(address: str = BROWSER, key: str = TEST_API_TOKEN, age: float = 0) -> dict[str, str]:
    return {"x-omnisync-client": sign(key, address, int(time.time() - age))}


@pytest.fixture(autouse=True)
def _reset_warning(monkeypatch) -> None:
    monkeypatch.setattr(forwarded_client, "_last_warning", 0.0)


async def test_a_verified_header_names_the_browser_in_the_audit_trail(caplog) -> None:
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    assert await _poke({**AUTH_HEADERS, **_header()}) == 200
    [line] = _audit_lines(caplog)
    assert line == f"thing.poke profile=docs outcome=ok client={BROWSER} via={PEER}"
    [record] = [r for r in caplog.records if r.name == AUDIT_LOGGER]
    assert record.fields["client"] == BROWSER and record.fields["via"] == PEER  # type: ignore[attr-defined]


async def test_ipv6_addresses_are_normalised(caplog) -> None:
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    assert await _poke({**AUTH_HEADERS, **_header("[2001:DB8::0:1]")}) == 200
    assert _audit_lines(caplog)[0].endswith("client=2001:db8::1 via=172.18.0.3")


def _tampered_address() -> dict[str, str]:
    _, _, stamp, mac = sign(TEST_API_TOKEN, BROWSER, int(time.time())).split(";")
    return {"x-omnisync-client": f"v1;198.51.100.1;{stamp};{mac}"}


def _tampered_time() -> dict[str, str]:
    _, address, stamp, mac = sign(TEST_API_TOKEN, BROWSER, int(time.time()) - 300).split(";")
    return {"x-omnisync-client": f"v1;{address};{int(time.time())};{mac}"}


@pytest.mark.parametrize("spoof", [
    pytest.param(lambda: {"x-omnisync-client": f"v1;{BROWSER};{int(time.time())};{'0' * 64}"}, id="made-up-mac"),
    pytest.param(lambda: _header(key="some-other-key"), id="other-key"),
    pytest.param(lambda: _header(age=MAX_AGE_SECONDS + 2), id="stale"),
    pytest.param(lambda: _header(age=-60), id="from-the-future"),
    pytest.param(_tampered_address, id="address-changed-after-signing"),
    pytest.param(_tampered_time, id="replayed-with-a-new-time"),
    pytest.param(lambda: _header("1.2.3.4 outcome=ok"), id="not-an-address"),
    pytest.param(lambda: _header("evil\nline"), id="line-break"),
    pytest.param(lambda: {"x-omnisync-client": BROWSER}, id="bare-address"),
    pytest.param(lambda: {"x-omnisync-client": "v2" + sign(TEST_API_TOKEN, BROWSER, int(time.time()))[2:]},
                 id="unknown-version"),
    pytest.param(lambda: {"x-omnisync-client": "v1;" + "1" * 300}, id="oversized"),
])
async def test_a_spoofed_header_is_ignored(caplog, spoof) -> None:
    caplog.set_level(logging.INFO)
    assert await _poke({**AUTH_HEADERS, **spoof()}) == 200
    assert _audit_lines(caplog) == [f"thing.poke profile=docs outcome=ok client={PEER}"]
    assert "does not verify" in caplog.text


async def test_two_headers_are_ignored_even_if_one_verifies(caplog) -> None:
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    headers = [*AUTH_HEADERS.items(), ("x-omnisync-client", _header("198.51.100.1")["x-omnisync-client"]),
               ("x-omnisync-client", _header()["x-omnisync-client"])]
    assert await _poke(headers) == 200
    assert _audit_lines(caplog) == [f"thing.poke profile=docs outcome=ok client={PEER}"]


async def test_without_the_right_token_the_header_is_never_used(caplog) -> None:
    """A valid header is worthless without the token: 401, and the peer is what is recorded and throttled."""
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    assert await _poke({"Authorization": "Bearer wrong-token", **_header()}) == 401
    assert await _poke(_header()) == 401
    [rejected] = _audit_lines(caplog)
    assert rejected.startswith("auth.token_rejected") and f"client={PEER}" in rejected
    assert BROWSER not in rejected


async def test_throttling_keeps_using_the_tcp_peer() -> None:
    # Failures from the peer block the peer, whatever address a header names...
    for _ in range(security.AUTH_FAILURE_LIMIT):
        assert await _poke({"Authorization": "Bearer wrong", **_header("198.51.100.7")}) == 401
    assert await _poke({"Authorization": "Bearer wrong", **_header("198.51.100.8")}) == 429
    # ...a request with the right token still passes (the block only slows guessing)...
    assert await _poke({**AUTH_HEADERS, **_header("198.51.100.8")}) == 200
    # ...and a valid header naming the blocked peer does not block another peer.
    assert await _poke({**AUTH_HEADERS, **_header(PEER)}, peer="192.0.2.50") == 200
    assert await _poke({"Authorization": "Bearer wrong"}, peer="192.0.2.50") == 401


async def test_without_a_header_nothing_changes(caplog) -> None:
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    assert await _poke(AUTH_HEADERS) == 200
    assert _audit_lines(caplog) == [f"thing.poke profile=docs outcome=ok client={PEER}"]
    assert "does not verify" not in caplog.text


def test_verified_address_bounds() -> None:
    now = 1_800_000_000
    value = sign("k" * 43, "10.0.0.1", now)
    assert verified_address(value, "k" * 43, now=now + MAX_AGE_SECONDS) == "10.0.0.1"
    assert verified_address(value, "k" * 43, now=now + MAX_AGE_SECONDS + 1) is None
    assert verified_address(value, "k" * 43, now=now - 5) == "10.0.0.1"
    assert verified_address(value, "k" * 43, now=now - 6) is None
    assert verified_address(value, "", now=now) is None  # no token, no trust
    assert verified_address(sign("k", "fe80::1%eth0", now), "k", now=now) == "fe80::1%eth0"
    assert verified_address(sign("k", "unknown", now), "k", now=now) is None


def test_the_frontend_signs_the_same_way() -> None:
    """Pinned against src/__tests__/forwarded-client.test.ts, which checks the same value."""
    assert sign("server-token", "203.0.113.9", 1_800_000_000) == (
        "v1;203.0.113.9;1800000000;" + _expected_mac()
    )


def _expected_mac() -> str:
    import hashlib
    import hmac

    return hmac.new(b"server-token", b"v1\n203.0.113.9\n1800000000", hashlib.sha256).hexdigest()
