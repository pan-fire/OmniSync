"""The audit trail (backend/audit.py): who did what, with names and ids only."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.errors import api_error, install_error_handlers
from backend.api.request_id import RequestIdMiddleware
from backend.audit import MAX_VALUE_LENGTH, audit, audited
from backend.logging_setup import AUDIT_LOGGER, RequestContext, enter_request


def _audit_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == AUDIT_LOGGER]


def test_record_shape(caplog) -> None:
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    leave = enter_request(RequestContext("abcdef0123456789", "10.1.2.3"))
    try:
        audit("profile.update", profile="docs", fields=["name", "local_dir"], force=False, skipped=None)
    finally:
        leave()
    [record] = _audit_records(caplog)
    assert record.getMessage() == (
        "profile.update profile=docs fields=[name,local_dir] force=false outcome=ok client=10.1.2.3"
    )
    assert record.levelno == logging.INFO
    assert record.fields == {  # type: ignore[attr-defined]
        "action": "profile.update", "profile": "docs", "fields": ["name", "local_dir"], "force": False,
        "outcome": "ok", "client": "10.1.2.3",
    }


def test_values_cannot_forge_lines_and_are_bounded(caplog) -> None:
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    audit("remote.delete", remote="evil\n2026-01-01 00:00:00,000 - INFO - backend.audit - x", name="a b")
    audit("remote.delete", remote="x" * 500)
    first, second = (r.getMessage() for r in _audit_records(caplog))
    assert "\n" not in first
    assert 'remote="evil?2026-01-01 00:00:00,000 - INFO - backend.audit - x"' in first
    assert 'name="a b"' in first
    assert len(second) < MAX_VALUE_LENGTH + 60


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(RequestIdMiddleware)

    @app.post("/things/{slug}")
    @audited("thing.poke", lambda kw: {"count": kw["n"]}, profile="slug")
    async def poke(slug: str, n: int = 1) -> dict[str, str]:
        if slug == "busy":
            raise api_error(409, "sync_busy", "Busy")
        if slug == "boom":
            raise RuntimeError("password=never-in-the-audit")
        return {"ok": slug}

    return app


async def test_audited_routes_record_their_outcome(caplog) -> None:
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    transport = ASGITransport(app=_app(), raise_app_exceptions=False, client=("192.0.2.7", 1234))
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        ok = await client.post("/things/docs", params={"n": 3})
        assert ok.status_code == 200
        assert (await client.post("/things/busy")).status_code == 409
        assert (await client.post("/things/boom")).status_code == 500
        assert (await client.post("/things/docs", params={"n": "x"})).status_code == 422  # never reached the route

    ok_rec, refused, failed = _audit_records(caplog)
    assert ok_rec.getMessage() == "thing.poke profile=docs count=3 outcome=ok client=192.0.2.7"
    assert ok_rec.request_id == ok.headers["x-request-id"]  # type: ignore[attr-defined]
    assert refused.getMessage() == (
        "thing.poke profile=busy count=1 status=409 code=sync_busy outcome=refused client=192.0.2.7"
    )
    assert refused.levelno == logging.WARNING
    assert failed.getMessage() == "thing.poke profile=boom count=1 outcome=failed client=192.0.2.7"
    assert "never-in-the-audit" not in caplog.text.split("Unhandled error")[0]


def test_audited_keeps_the_route_signature() -> None:
    import inspect

    route = next(r for r in _app().routes if getattr(r, "path", "") == "/things/{slug}")
    assert list(inspect.signature(route.endpoint).parameters) == ["slug", "n"]  # type: ignore[attr-defined]


# Every user action the audit trail must cover, by route.
EXPECTED_ACTIONS = {
    ("POST", "/profiles/{slug}/sync/start"): "sync.start",
    ("POST", "/profiles/{slug}/sync/resync"): "sync.resync",
    ("POST", "/profiles/{slug}/sync/stop"): "sync.stop",
    ("POST", "/profiles/{slug}/sync/selective"): "sync.selective",
    ("POST", "/profiles/{slug}/sync/pause"): "sync.pause",
    ("POST", "/profiles/{slug}/sync/resume-intervals"): "sync.resume",
    ("POST", "/profiles/pause-all"): "sync.pause_all",
    ("POST", "/profiles/resume-all"): "sync.resume_all",
    ("POST", "/profiles"): "profile.create",
    ("PUT", "/profiles/{slug}"): "profile.update",
    ("DELETE", "/profiles/{slug}"): "profile.delete",
    ("POST", "/profiles/{slug}/enable"): "profile.enable",
    ("POST", "/profiles/{slug}/disable"): "profile.disable",
    ("POST", "/wizard/create"): "remote.create",
    ("PUT", "/remotes/{name}"): "remote.edit",
    ("POST", "/wizard/reconnect"): "remote.reconnect",
    ("POST", "/remotes/import"): "remote.import",
    ("DELETE", "/remotes/{name}"): "remote.delete",
    ("POST", "/profiles/{slug}/backups"): "backup.target_create",
    ("PUT", "/profiles/{slug}/backups/{target_id}"): "backup.target_update",
    ("DELETE", "/profiles/{slug}/backups/{target_id}"): "backup.target_delete",
    ("POST", "/profiles/{slug}/backups/{target_id}/run"): "backup.run",
    ("POST", "/profiles/{slug}/backups/{target_id}/restore"): "backup.restore",
    ("POST", "/profiles/{slug}/backups/{target_id}/restore-files"): "backup.restore_files",
    ("POST", "/profiles/{slug}/trash/restore"): "trash.restore",
    ("POST", "/profiles/{slug}/trash/delete"): "trash.delete",
    ("POST", "/conflicts/{conflict_id}/resolve"): "conflict.resolve",
    ("PUT", "/notifications/config"): "notifications.update",
    ("PUT", "/config"): "settings.update",
}


def test_every_user_action_is_audited() -> None:
    from backend.tests.test_route_auth import _all_routes

    found = {}
    for route, path, methods in _all_routes():
        action = getattr(getattr(route, "endpoint", None), "audit_action", None)
        for method in methods or ():
            if action:
                found[(method, path)] = action
    missing = {k: v for k, v in EXPECTED_ACTIONS.items() if found.get(k) != v}
    assert not missing, missing


@pytest.fixture
def test_services(test_services, tmp_path):
    """test_client with a real ConfigService on a scratch config.toml."""
    import dataclasses

    from backend.services.config import ConfigService

    return dataclasses.replace(test_services, config_service=ConfigService(tmp_path / "config.toml"))


async def test_log_level_change_is_audited_even_at_level_error(test_client, caplog) -> None:
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    try:
        resp = await test_client.put("/config", json={"log_level": "ERROR"})
        assert resp.status_code == 200
        assert logging.getLogger(AUDIT_LOGGER).isEnabledFor(logging.INFO)
        resp = await test_client.put("/config", json={"log_level": "INFO"})
    finally:
        logging.getLogger("backend").setLevel(logging.INFO)
    first, second = (r.getMessage() for r in _audit_records(caplog))
    assert first.startswith("settings.update fields=[log_level] log_level=ERROR outcome=ok")
    assert second.startswith("settings.update fields=[log_level] log_level=INFO outcome=ok")


async def test_sync_start_is_audited_with_direction_and_force(test_client, test_services, caplog) -> None:
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    engine = test_services.manager.get_engine.return_value
    engine.launch = AsyncMock(return_value=SimpleNamespace(job_id=7, wait_started=AsyncMock(return_value=False)))
    engine.profile = SimpleNamespace(two_way=False)
    resp = await test_client.post("/profiles/default/sync/start", json={"direction": "push", "force": True})
    assert resp.status_code == 202, resp.text
    [record] = _audit_records(caplog)
    assert record.getMessage().startswith("sync.start profile=default direction=push force=true outcome=ok")


async def test_backup_target_passphrase_never_reaches_the_audit(test_client, caplog) -> None:
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    await test_client.post("/profiles/default/backups", json={
        "name": "nas", "target_path": "/backups", "target_type": "local",
        "encryption_passphrase": "my-secret-passphrase-123",
    })
    [record] = _audit_records(caplog)
    assert record.getMessage().startswith("backup.target_create profile=default name=nas type=local encrypted=true")
    assert "my-secret-passphrase-123" not in caplog.text
