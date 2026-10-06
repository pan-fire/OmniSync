"""Error answers of the /remotes routes: each failure has its status and code, and changes nothing.

Like test_remote_management.py these run against the real rclone binary
and a temp rclone.conf; a failure rclone or the config file cannot be made
to produce on demand (a locked or broken config, a race with another
writer) is injected by replacing that one RcloneService method. Every
case checks the error envelope and that rclone.conf is untouched, and that
no exception text reaches the client.
"""

from __future__ import annotations

import configparser
import logging
import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from backend.api.routes import remotes, wizard
from backend.db.models import Remote
from backend.main import app
from backend.services import remote_auth
from backend.services.rclone import RcloneService
from backend.services.wizard_sessions import WizardSessionManager
from backend.tests.auth import AUTH_HEADERS

pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="needs the rclone binary")

LEAK = "config locked by /home/someone/.config/rclone/rclone.conf token=secret-value"
SFTP = {"type": "sftp", "host": "box.example", "user": "me"}


def write_conf(path: Path, sections: dict[str, dict[str, str]]) -> None:
    config = configparser.RawConfigParser()
    config.optionxform = str  # type: ignore[assignment,method-assign]
    for name, options in sections.items():
        config[name] = options
    with path.open("w") as f:
        config.write(f)


def assert_error(resp, status: int, code: str) -> dict:
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert body["code"] == code and body["detail"]
    assert "secret-value" not in resp.text and "/home/someone" not in resp.text
    return body


@pytest.fixture
def conf(tmp_path: Path) -> Path:
    path = tmp_path / "rclone.conf"
    write_conf(path, {"box": SFTP, "gdrive": {"type": "drive", "token": "{}"}})
    return path


@pytest.fixture
def rclone(conf: Path) -> RcloneService:
    return RcloneService(rclone_config_path=str(conf))


@pytest.fixture
async def client(monkeypatch, rclone, test_db_factory):
    monkeypatch.setattr(wizard, "_rclone_service", rclone)
    monkeypatch.setattr(wizard, "_session_manager", WizardSessionManager())
    monkeypatch.setattr(remotes, "_rclone_service", rclone)
    monkeypatch.setattr(remotes, "_db_factory", test_db_factory)
    remote_auth.reset()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH_HEADERS) as c:
        yield c
    remote_auth.reset()


def broken(rclone: RcloneService, method: str, exc: Exception, monkeypatch) -> AsyncMock:
    mock = AsyncMock(side_effect=exc)
    monkeypatch.setattr(rclone, method, mock)
    return mock


# --- services not wired ---


async def test_without_rclone_every_route_is_503(client, monkeypatch, conf):
    """Before startup wired rclone in, the routes answer 503 instead of crashing."""
    monkeypatch.setattr(remotes, "_rclone_service", None)
    before = conf.read_text()
    for method, path, body in [
        ("GET", "/remotes", None), ("POST", "/remotes/import/preview", {"content": "[a]\ntype = memory\n"}),
        ("POST", "/remotes/box/test", None), ("DELETE", "/remotes/box?force=true", None),
        ("GET", "/remotes/box/config", None), ("GET", "/remotes/box/about", None),
    ]:
        assert_error(await client.request(method, path, json=body), 503, "service_unavailable")
    assert conf.read_text() == before


async def test_without_database_dependencies_are_503_and_nothing_is_deleted(client, monkeypatch, conf):
    """The in-use check needs the database: without it a delete is refused, not done blindly."""
    monkeypatch.setattr(remotes, "_db_factory", None)
    assert_error(await client.get("/remotes/box/dependencies"), 503, "service_unavailable")
    assert_error(await client.delete("/remotes/box"), 503, "service_unavailable")
    assert "box" in conf.read_text()


# --- listing and import: rclone or the config fails ---


async def test_listing_failure_is_500_without_details(client, rclone, monkeypatch, caplog):
    """`rclone listremotes` failing: a generic 500, the reason in the log."""
    broken(rclone, "list_remotes", RuntimeError(LEAK), monkeypatch)
    with caplog.at_level(logging.ERROR, logger="backend.api.routes.remotes"):
        assert_error(await client.get("/remotes"), 500, "internal_error")
        assert_error(await client.post("/remotes/import/preview", json={"content": "[a]\ntype = memory\n"}),
                     500, "internal_error")
    assert "Listing remotes failed" in caplog.text


async def test_import_of_an_unreadable_file_is_422(client, conf):
    """The import itself (not only the preview) refuses a file that is not an rclone.conf."""
    before = conf.read_text()
    body = assert_error(await client.post("/remotes/import", json={"content": "not a config",
                                                                   "remotes": [{"source": "a"}]}),
                        422, "invalid_rclone_config")
    assert body["detail"]
    assert conf.read_text() == before


async def test_same_section_selected_twice_is_422(client, conf):
    """One section imported under two names would duplicate its credentials: refused, nothing written."""
    before = conf.read_text()
    body = assert_error(await client.post("/remotes/import", json={
        "content": "[mem]\ntype = memory\n", "remotes": [{"source": "mem", "name": "a"}, {"source": "mem", "name": "b"}],
    }), 422, "invalid_import")
    assert body["details"]["errors"] == ["'mem' is selected twice"]
    assert conf.read_text() == before


async def test_reading_the_existing_config_fails_during_an_import(client, rclone, monkeypatch, conf):
    """A wrapper whose target must be looked up in a config that cannot be read: 500, nothing imported."""
    before = conf.read_text()
    broken(rclone, "remote_section", OSError(LEAK), monkeypatch)
    text = "[w]\ntype = alias\nremote = elsewhere:x\n"
    assert_error(await client.post("/remotes/import", json={"content": text, "remotes": [{"source": "w"}]}),
                 500, "internal_error")
    assert_error(await client.post("/remotes/import/preview", json={"content": text}), 500, "internal_error")
    assert conf.read_text() == before


async def test_a_wrapped_name_rclone_refuses_counts_as_unknown(client, rclone, monkeypatch, conf):
    """remote_section refuses a name (ValueError): the lookup treats it as no such remote, not as a crash."""
    broken(rclone, "remote_section", ValueError("not a remote name"), monkeypatch)
    text = "[w]\ntype = alias\nremote = elsewhere:x\n"
    resp = await client.post("/remotes/import/preview", json={"content": text})
    assert resp.status_code == 200
    assert resp.json()["remotes"][0]["problems"] == []


@pytest.mark.parametrize(("exc", "status", "code"), [
    (ValueError("Remotes already exist: mem"), 409, "name_clash"),
    (ValueError("refused: " + LEAK), 422, "invalid_import"),
    (OSError(LEAK), 500, "internal_error"),
])
async def test_writing_the_import_fails(client, rclone, monkeypatch, conf, exc, status, code):
    """A remote added meanwhile is a clash (409); any other refusal or write error says nothing more."""
    before = conf.read_text()
    broken(rclone, "add_remotes", exc, monkeypatch)
    body = assert_error(await client.post("/remotes/import", json={
        "content": "[mem]\ntype = memory\n", "remotes": [{"source": "mem"}],
    }), status, code)
    if code == "name_clash":
        assert body["details"] == {"names": ["mem"]}
    assert conf.read_text() == before


# --- edit ---


async def test_config_that_cannot_be_read_is_500(client, rclone, monkeypatch):
    """The edit form and the edit itself need the current settings: unreadable is 500, not 404."""
    broken(rclone, "remote_section", OSError(LEAK), monkeypatch)
    assert_error(await client.get("/remotes/box/config"), 500, "internal_error")
    assert_error(await client.put("/remotes/box", json={"params": {"host": "h2.example"}}), 500, "internal_error")


@pytest.mark.parametrize(("exc", "status", "code"), [
    (ValueError("Remote 'box' not found"), 409, "remote_changed"),
    (OSError(LEAK), 500, "internal_error"),
])
async def test_update_that_loses_a_race_or_fails_writing(client, rclone, monkeypatch, conf, exc, status, code):
    """Deleted or retyped meanwhile: 409 'reload'; a write failure: 500. The file stays as it was."""
    before = conf.read_text()
    broken(rclone, "update_remote", exc, monkeypatch)
    remote_auth.mark_auth_failed("box")
    assert_error(await client.put("/remotes/box", json={"params": {"host": "h2.example"}}), status, code)
    assert conf.read_text() == before
    assert remote_auth.auth_failed("box")  # not cleared: nothing changed


async def test_invalid_names_are_refused_before_rclone_runs(client, rclone, monkeypatch):
    """A name rclone would read as an option never reaches its command line."""
    run = broken(rclone, "_run", AssertionError("rclone must not run"), monkeypatch)
    for method, path in [("GET", "/remotes/-x/about"), ("POST", "/remotes/--log-file=x/test"),
                         ("GET", "/remotes/-x/config"), ("PUT", "/remotes/-x")]:
        body = {"params": {}} if method == "PUT" else None
        assert_error(await client.request(method, path, json=body), 422, "invalid_remote_name")
    run.assert_not_awaited()


# --- delete and test ---


async def test_deleting_a_missing_remote_is_404(client, conf):
    before = conf.read_text()
    assert_error(await client.delete("/remotes/nothere?force=true"), 404, "remote_not_found")
    assert conf.read_text() == before


async def test_delete_failure_is_500_and_keeps_the_remote(client, rclone, monkeypatch, conf):
    broken(rclone, "delete_remote", OSError(LEAK), monkeypatch)
    assert_error(await client.delete("/remotes/box"), 500, "internal_error")
    assert "[box]" in conf.read_text()


async def test_bookkeeping_failure_does_not_fail_a_good_connection_test(
    client, rclone, monkeypatch, test_db_factory, caplog,
):
    """The test succeeded; failing to record when is logged, not turned into a failed test."""
    monkeypatch.setattr(rclone, "_run", AsyncMock())
    broken(rclone, "list_remotes", RuntimeError(LEAK), monkeypatch)
    with caplog.at_level(logging.ERROR, logger="backend.api.routes.remotes"):
        resp = await client.post("/remotes/box/test")
    assert resp.status_code == 200 and resp.json()["success"] is True
    assert "Could not record the successful test of remote 'box'" in caplog.text
    async with test_db_factory() as session:
        assert (await session.execute(select(Remote))).scalars().all() == []


# --- authentication ---


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/remotes"), ("DELETE", "/remotes/box?force=true"), ("PUT", "/remotes/box"),
    ("POST", "/remotes/import"), ("GET", "/remotes/box/config"),
])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong-token"}])
async def test_remote_routes_need_the_token(client, conf, method, path, headers):
    """Without the right token nothing is read or changed (a config holds credentials)."""
    before = conf.read_text()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as anon:
        resp = await anon.request(method, path, json={"params": {"host": "x"}} if method == "PUT" else None)
    assert resp.status_code == 401
    assert "box.example" not in resp.text
    assert conf.read_text() == before
