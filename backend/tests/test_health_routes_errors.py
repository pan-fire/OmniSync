"""/health/remotes and /health/network when there is nothing to check or a check fails.

/health/network is a diagnosis page: every check must answer for itself
(never fail the whole request), give only an error type (the details go to
the server log) and never reach the internet from the test suite. Here the
providers are an httpx.MockTransport, rclone is a stand-in, and DNS is
patched. /health/remotes must report each remote once, with the profiles
using it, and only for profiles that have a remote.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.routes import health
from backend.main import app
from backend.services import remote_auth

LEAK = "stderr: token=secret-value /home/someone/.config"


@pytest.fixture(autouse=True)
def _clean_auth_marks():
    remote_auth.reset()
    yield
    remote_auth.reset()


def engine(remote_dir: str, check: AsyncMock | None = None) -> SimpleNamespace:
    rclone = SimpleNamespace(check_remote=check or AsyncMock(return_value=True))
    return SimpleNamespace(_profile=SimpleNamespace(remote_dir=remote_dir), _rclone=rclone)


# --- /health/remotes ---


async def test_remotes_without_a_manager_is_an_empty_list(test_client):
    """Before the engines start (or after shutdown) there is nothing to check, not an error."""
    health.set_manager(None)
    resp = await test_client.get("/health/remotes")
    assert resp.status_code == 200 and resp.json() == {"remotes": []}


async def test_profiles_without_a_remote_are_not_checked(test_client):
    """A profile whose remote_dir names no remote is skipped; with only such profiles the list is empty."""
    check = AsyncMock(return_value=True)
    health.set_manager(SimpleNamespace(engines={"plain": engine("/just/a/path", check), "empty": engine("")}))
    resp = await test_client.get("/health/remotes")
    assert resp.json() == {"remotes": []}
    check.assert_not_awaited()


async def test_each_remote_is_checked_once_with_its_profiles(test_client):
    """Two profiles on one remote: one check, both profiles named; a failed sign-in is flagged."""
    shared = AsyncMock(side_effect=lambda name: name == "gdrive")
    remote_auth.mark_auth_failed("box")
    health.set_manager(SimpleNamespace(engines={
        "docs": engine("gdrive:docs", shared), "photos": engine("gdrive:photos", shared),
        "work": engine("box:work", shared), "local": engine("/srv/x", shared),
    }))
    resp = await test_client.get("/health/remotes")
    assert resp.json() == {"remotes": [
        {"remote": "box", "accessible": False, "profiles": ["work"], "auth_error": True},
        {"remote": "gdrive", "accessible": True, "profiles": ["docs", "photos"], "auth_error": False},
    ]}
    assert sorted(c.args[0] for c in shared.await_args_list) == ["box", "gdrive"]


# --- /health/network ---


class FakeProc:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0, hang: bool = False) -> None:
        self._out, self._err, self.returncode, self._hang = stdout, stderr, returncode, hang

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._hang:
            await asyncio.Event().wait()  # never set: only the timeout ends it
        return self._out, self._err


@pytest.fixture
def network(monkeypatch: pytest.MonkeyPatch):
    """Offline stand-ins for the providers, the rclone binary and DNS; returns the settings to change."""
    answers: dict[str, int | Exception] = {"www.googleapis.com": 401, "1.1.1.1": 200}
    procs = {"version": FakeProc(stdout=b"rclone v1.70.0\n- os/version: test\n"), "lsf": FakeProc()}
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        answer = answers[request.url.host]
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer)

    def client(**kwargs) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    async def exec_(program: str, subcommand: str, *args, **kwargs) -> FakeProc:
        assert program == "rclone"
        return procs[subcommand]

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_)
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("192.0.2.10", port))])
    return SimpleNamespace(answers=answers, procs=procs)


async def test_network_all_ok(test_client, network):
    """Every check answers: a 401 from Google still proves the network works."""
    resp = await test_client.get("/health/network")
    assert resp.status_code == 200
    assert resp.json() == {
        "httpx_google": {"status": 401, "ok": True},
        "httpx_cloudflare": {"status": 200, "ok": True},
        "rclone_version": {"ok": True, "version": "rclone v1.70.0"},
        "rclone_network": {"ok": True, "return_code": 0},
        "dns_google": {"ok": True, "resolved_to": "192.0.2.10"},
    }


async def test_network_failures_are_reported_per_check(test_client, network, caplog):
    """Bad answers and a failing rclone fail their own check only; rclone's stderr stays in the log."""
    network.answers.update({"www.googleapis.com": 500, "1.1.1.1": 503})
    network.procs["version"] = FakeProc(stdout=b"")
    network.procs["lsf"] = FakeProc(stderr=LEAK.encode(), returncode=1)
    with caplog.at_level(logging.WARNING, logger="backend.api.routes.health"):
        body = (await test_client.get("/health/network")).json()
    assert body["httpx_google"] == {"status": 500, "ok": False}
    assert body["httpx_cloudflare"] == {"status": 503, "ok": False}
    assert body["rclone_version"] == {"ok": True, "version": "unknown"}
    assert body["rclone_network"] == {"ok": False, "return_code": 1}
    assert body["dns_google"]["ok"] is True
    assert "secret-value" not in str(body) and "/home/someone" not in str(body)
    assert "Network check rclone_network: exit 1" in caplog.text and "/home/someone/.config" in caplog.text


async def test_network_crashing_checks_give_only_the_error_type(test_client, network, monkeypatch, caplog):
    """A check that raises answers {ok: false, error: <type>}; the text goes to the log."""
    network.answers["1.1.1.1"] = httpx.ConnectError(LEAK)

    async def no_rclone(*args, **kwargs):
        raise FileNotFoundError(LEAK)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_rclone)
    with caplog.at_level(logging.WARNING, logger="backend.api.routes.health"):
        body = (await test_client.get("/health/network")).json()
    assert body["httpx_cloudflare"] == {"ok": False, "error": "ConnectError"}
    assert body["rclone_version"] == {"ok": False, "error": "FileNotFoundError"}
    assert body["rclone_network"] == {"ok": False, "error": "FileNotFoundError"}
    assert "/home/someone" not in str(body)
    assert "Network check httpx_cloudflare failed" in caplog.text and "/home/someone/.config" in caplog.text


async def test_network_rclone_that_hangs_times_out(test_client, network, monkeypatch):
    """A hanging `rclone lsf` (no route out) ends with a timeout answer instead of a hung request."""
    network.procs["lsf"] = FakeProc(hang=True)
    real_wait_for = asyncio.wait_for

    async def quick_wait_for(aw, timeout):
        return await real_wait_for(aw, timeout=min(timeout, 0.05))

    monkeypatch.setattr(health.asyncio, "wait_for", quick_wait_for)
    body = (await test_client.get("/health/network")).json()
    assert body["rclone_network"] == {"ok": False, "error": "timed out after 10s"}
    assert body["rclone_version"]["ok"] is True


# --- authentication ---


@pytest.mark.parametrize("path", ["/health/remotes", "/health/network"])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong-token"}])
async def test_diagnostics_need_the_token(test_services, path, headers):
    """Remote and network checks call providers: without the right token they answer 401 and do nothing."""
    from backend.api.wiring import unwire_routes, wire_routes

    wire_routes(test_services)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
            resp = await client.get(path)
    finally:
        unwire_routes()
    assert resp.status_code == 401
    test_services.rclone.check_remote.assert_not_awaited()


async def test_liveness_needs_no_token(test_client, monkeypatch):
    """/health is the container probe: it answers without a token."""
    monkeypatch.setattr(health.shutil, "which", lambda name: f"/usr/bin/{name}")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200 and resp.json()["status"] == "ok"
