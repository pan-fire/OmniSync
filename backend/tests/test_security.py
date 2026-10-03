"""Tests for API token authentication and the Host allow-list."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend import security
from backend.tests.auth import AUTH_HEADERS, TEST_API_TOKEN


@pytest.fixture
async def bare_client(test_client):
    """A client on the same app and overrides as test_client, without credentials."""
    from backend.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_protected_route_without_token_is_401(bare_client):
    resp = await bare_client.get("/logs")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


async def test_protected_route_with_wrong_token_is_401(bare_client):
    resp = await bare_client.get("/logs", headers={"Authorization": "Bearer not-the-token"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid API token."


async def test_non_bearer_scheme_is_401(bare_client):
    resp = await bare_client.get("/logs", headers={"Authorization": f"Basic {TEST_API_TOKEN}"})
    assert resp.status_code == 401


async def test_protected_route_with_token_passes(bare_client):
    resp = await bare_client.get("/logs", headers=AUTH_HEADERS)
    assert resp.status_code != 401


async def test_mutating_route_without_token_is_401(bare_client):
    # A body-less POST is what a cross-site form could send.
    resp = await bare_client.post("/profiles/default/sync/stop")
    assert resp.status_code == 401


async def test_health_is_public(bare_client):
    resp = await bare_client.get("/health")
    assert resp.status_code == 200


async def test_oauth_callback_is_public(bare_client):
    # The provider redirects the browser here; the OAuth state protects it.
    resp = await bare_client.get("/wizard/oauth/callback")
    assert resp.status_code != 401


async def test_api_docs_are_off_by_default(bare_client):
    # FastAPI's docs routes skip app-level dependencies, so they must not exist.
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert (await bare_client.get(path, headers=AUTH_HEADERS)).status_code == 404


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "192.168.1.20:8000"])
async def test_foreign_host_is_rejected(bare_client, host):
    resp = await bare_client.get("/health", headers={"Host": host})
    assert resp.status_code == 400


@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "127.0.0.1:8000", "[::1]:8000"])
async def test_loopback_hosts_are_allowed(bare_client, host):
    resp = await bare_client.get("/health", headers={"Host": host})
    assert resp.status_code == 200


def test_allowed_hosts_env_adds_names(monkeypatch):
    monkeypatch.setenv("OMNISYNC_ALLOWED_HOSTS", " NAS.local , sync.home ")
    assert {"nas.local", "sync.home", "localhost"} <= security.allowed_hosts()


@pytest.mark.parametrize(("header", "name"), [
    ("localhost:8000", "localhost"),
    ("LOCALHOST", "localhost"),
    ("[::1]:8000", "::1"),
    ("[::1]", "::1"),
    ("::1", "::1"),
    ("", ""),
])
def test_host_name_strips_port(header, name):
    assert security._host_name(header) == name


def test_token_is_generated_once_with_owner_only_permissions(monkeypatch, tmp_path):
    token_file = tmp_path / "data" / "api-token"
    monkeypatch.delenv("OMNISYNC_API_TOKEN", raising=False)
    monkeypatch.setenv("OMNISYNC_API_TOKEN_FILE", str(token_file))
    monkeypatch.setattr(security, "_cached", None)

    token = security.get_api_token()

    assert len(token) >= 32
    assert token_file.read_text().strip() == token
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    monkeypatch.setattr(security, "_cached", None)
    assert security.get_api_token() == token  # read back, not regenerated


def test_env_token_wins_over_file(monkeypatch, tmp_path):
    token_file = tmp_path / "api-token"
    token_file.write_text("from-file\n")
    monkeypatch.setenv("OMNISYNC_API_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("OMNISYNC_API_TOKEN", "from-env")
    monkeypatch.setattr(security, "_cached", None)
    assert security.get_api_token() == "from-env"


def test_write_secret_file_tightens_existing_file(tmp_path):
    path = tmp_path / "secret"
    path.write_text("old")
    path.chmod(0o644)
    security.write_secret_file(path, "new")
    assert path.read_text() == "new"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_secret_file_is_atomic(tmp_path, monkeypatch):
    """A write that fails halfway (full disk) leaves the old file and no temporary file."""
    path = tmp_path / "secret"
    path.write_text("old content")
    real_write = os.write
    calls = []

    def disk_full(fd, data):
        calls.append(fd)
        if len(calls) == 1:
            return real_write(fd, bytes(data[:3]))
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "write", disk_full)
    with pytest.raises(OSError, match="No space left"):
        security.write_secret_file(path, "new content that does not fit")
    monkeypatch.undo()
    assert path.read_text() == "old content"
    assert [p.name for p in tmp_path.iterdir()] == ["secret"]


def test_write_secret_file_keeps_a_backup(tmp_path):
    path = tmp_path / "secret"
    security.write_secret_file(path, "one", keep_backup=True)
    assert not (tmp_path / "secret.bak").exists()  # nothing to keep yet
    security.write_secret_file(path, "two", keep_backup=True)
    security.write_secret_file(path, "three", keep_backup=True)
    assert path.read_text() == "three"
    assert (tmp_path / "secret.bak").read_text() == "two"
    assert stat.S_IMODE((tmp_path / "secret.bak").stat().st_mode) == 0o600


def test_atomic_write_file_sets_the_mode(tmp_path):
    path = tmp_path / "config.toml"
    security.atomic_write_file(path, b"x = 1\n", mode=0o644)
    assert path.read_bytes() == b"x = 1\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


# --- uvicorn must not believe X-Forwarded-For (the throttle keys on the peer) ---

_REPO = Path(__file__).resolve().parents[2]


def _uvicorn_proxy_headers(argv: list[str], env: dict[str, str]) -> bool:
    """Whether uvicorn's own command line parser turns proxy headers on for ``argv``."""
    from uvicorn.main import main as uvicorn_cli

    assert argv[0] == "uvicorn"
    args = list(argv[1:])
    if "--reload-dir" in args:  # a container path; click wants it to exist
        i = args.index("--reload-dir")
        del args[i:i + 2]
    with pytest.MonkeyPatch.context() as mp:
        for name in [n for n in os.environ if n.startswith("UVICORN_")]:
            mp.delenv(name)
        for name, value in env.items():
            mp.setenv(name, value)
        ctx = uvicorn_cli.make_context("uvicorn", args)
    return bool(ctx.params["proxy_headers"])


def _dockerfile() -> tuple[list[str], dict[str, str]]:
    import json
    import re

    text = (_REPO / "Dockerfile").read_text()
    cmd = json.loads(re.findall(r"^CMD (\[.*\])$", text, re.M)[-1])
    env = dict(re.findall(r"^ENV (UVICORN_[A-Z_]+)=(\S+)$", text, re.M))
    return cmd, env


def test_the_image_runs_uvicorn_without_proxy_headers():
    cmd, env = _dockerfile()
    assert not _uvicorn_proxy_headers(cmd, {})
    # A compose `command:` without the flag still gets it from the image's ENV.
    assert not _uvicorn_proxy_headers(["uvicorn", "backend.main:app"], env)
    assert _uvicorn_proxy_headers(["uvicorn", "backend.main:app"], {})  # the default this guards against


def test_the_dev_compose_command_has_no_proxy_headers():
    import json
    import re

    text = (_REPO / "docker-compose.dev.yml").read_text()
    (command,) = [json.loads(c) for c in re.findall(r"command: (\[\"uvicorn\".*\])$", text, re.M)]
    assert not _uvicorn_proxy_headers(command, {})


@pytest.mark.parametrize("doc", ["CONTRIBUTING.md", "docs/gem/operations.md"])
def test_documented_commands_have_no_proxy_headers(doc):
    text = (_REPO / doc).read_text()
    lines = [line for line in text.splitlines() if "uvicorn backend.main:app --host" in line]
    assert lines
    assert all("--no-proxy-headers" in line for line in lines)
