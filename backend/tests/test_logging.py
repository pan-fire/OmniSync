"""The process-wide logging setup (backend/logging_setup.py).

- one setup: the backend, uvicorn's error log and library warnings reach
  stderr and the file; the access log reaches the file only on request;
- the file is owner-only and rotates at the configured size;
- secrets are masked in every handler's output, also in tracebacks, JSON
  fields and the access log;
- the JSON lines format, unhandled exceptions, request ids.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import sys
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend import logging_setup
from backend.api.request_id import RequestIdMiddleware, accepted_request_id
from backend.logging_setup import (
    AUDIT_LOGGER,
    LogRecordFilter,
    configure_logging,
    current_request_id,
    mask_secrets,
    register_secret,
)
from backend.services.log_reader import LogReader

_SAVED_LOGGERS = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "backend", AUDIT_LOGGER,
                  *logging_setup.LIBRARY_LOGGERS)


@pytest.fixture
def configure(tmp_path: Path):
    """configure_logging() into tmp_path; the process's logging is restored afterwards."""
    saved = {
        name: (list(logging.getLogger(name).handlers), logging.getLogger(name).level, logging.getLogger(name).propagate)
        for name in _SAVED_LOGGERS
    }
    hooks = (sys.excepthook, threading.excepthook)
    path = tmp_path / "omnisync.log"

    def run(**env: str) -> Path:
        configure_logging({"OMNISYNC_LOG_PATH": str(path), **env})
        return path

    yield run

    for name, (handlers, level, propagate) in saved.items():
        logger = logging.getLogger(name)
        for handler in logger.handlers:
            if handler not in handlers:
                handler.close()
        logger.handlers = handlers
        logger.setLevel(level)
        logger.propagate = propagate
    sys.excepthook, threading.excepthook = hooks
    logging_setup.forget_secrets()


def _flush() -> None:
    for name in ("", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            handler.flush()


def _text(path: Path) -> str:
    _flush()
    return path.read_text(encoding="utf-8")


# --- one setup for the process ------------------------------------------------


def test_backend_uvicorn_and_library_warnings_reach_the_file(configure, capsys) -> None:
    path = configure()
    logging.getLogger("backend.services.x").info("own info line")
    logging.getLogger("uvicorn.error").error("uvicorn error line")
    logging.getLogger("apscheduler.scheduler").warning("library warning line")
    logging.getLogger("apscheduler.scheduler").info("library info line")
    logging.getLogger("sqlalchemy.engine").info("SELECT noise")
    text = _text(path)
    assert "- INFO - backend.services.x - own info line" in text
    assert "- ERROR - uvicorn.error - uvicorn error line" in text
    assert "- WARNING - apscheduler.scheduler - library warning line" in text
    assert "library info line" not in text and "SELECT noise" not in text
    # stderr gets the same lines (docker logs).
    assert "own info line" in capsys.readouterr().err


def test_uvicorns_own_handlers_are_replaced(configure) -> None:
    """Uvicorn configures its loggers before importing the app; its lines then go through ours only."""
    stray = logging.StreamHandler(sys.stdout)
    logging.getLogger("uvicorn").addHandler(stray)
    logging.getLogger("uvicorn.access").addHandler(stray)
    configure()
    assert stray not in logging.getLogger("uvicorn").handlers
    assert stray not in logging.getLogger("uvicorn.access").handlers
    assert logging.getLogger("uvicorn").propagate is True


def test_configuring_twice_does_not_duplicate_lines(configure) -> None:
    configure()
    path = configure()
    logging.getLogger("backend").warning("once only")
    assert _text(path).count("once only") == 1


def test_access_log_stays_off_the_file_unless_enabled(configure, capsys) -> None:
    access = logging.getLogger("uvicorn.access")
    access.setLevel(logging.INFO)
    path = configure()
    access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", "/health", "1.1", 200)
    assert "/health" not in _text(path)
    assert '"GET /health HTTP/1.1" 200' in capsys.readouterr().err

    path = configure(OMNISYNC_LOG_ACCESS="1")
    access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", "/profiles", "1.1", 200)
    assert '"GET /profiles HTTP/1.1" 200' in _text(path)


def test_log_level_governs_own_loggers_and_libraries_follow_only_debug(configure) -> None:
    from backend.services.config import apply_log_level

    path = configure()
    apply_log_level("WARNING")
    logging.getLogger("backend.x").info("hidden info")
    logging.getLogger(AUDIT_LOGGER).info("audit always")
    assert logging.getLogger("apscheduler").level == logging.WARNING
    apply_log_level("DEBUG")
    logging.getLogger("backend.x").debug("debug shown")
    assert logging.getLogger("apscheduler").level == logging.INFO
    # SQL statements and HTTP request URLs stay out at every level.
    assert logging.getLogger("sqlalchemy").level == logging.WARNING
    assert logging.getLogger("httpx").level == logging.WARNING
    apply_log_level("INFO")
    assert logging.getLogger("apscheduler").level == logging.WARNING
    text = _text(path)
    assert "hidden info" not in text
    assert "audit always" in text and "debug shown" in text


def test_log_file_is_owner_only_and_rotates_as_configured(configure, tmp_path: Path) -> None:
    old = tmp_path / "omnisync.log.1"
    old.write_text("old\n")
    old.chmod(0o644)
    path = configure(OMNISYNC_LOG_MAX_BYTES="2048", OMNISYNC_LOG_BACKUPS="2")
    for i in range(200):
        logging.getLogger("backend").warning("line %d %s", i, "x" * 40)
    _flush()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    rotated = sorted(p.name for p in tmp_path.iterdir())
    assert rotated == ["omnisync.log", "omnisync.log.1", "omnisync.log.2"]
    for name in rotated:
        assert stat.S_IMODE(os.stat(tmp_path / name).st_mode) == 0o600
    assert path.stat().st_size <= 2048


def test_bad_rotation_settings_fall_back_to_the_defaults(capsys) -> None:
    settings = logging_setup.read_settings({"OMNISYNC_LOG_MAX_BYTES": "lots", "OMNISYNC_LOG_BACKUPS": "-1"})
    assert settings.max_bytes == logging_setup.DEFAULT_MAX_BYTES
    assert settings.backups == logging_setup.DEFAULT_BACKUPS
    assert "OMNISYNC_LOG_MAX_BYTES" in capsys.readouterr().err
    assert logging_setup.read_settings({}).json is False
    assert logging_setup.read_settings({"OMNISYNC_LOG_FORMAT": "JSON"}).json is True


def test_unwritable_log_folder_keeps_stderr(configure, tmp_path: Path, capsys) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    configure_logging({"OMNISYNC_LOG_PATH": str(blocker / "sub" / "o.log")})
    assert "Could not set up the log file" in capsys.readouterr().err


# --- secret masking -----------------------------------------------------------

SECRET_LINES = [
    # (what is logged, the secret in it)
    ("Authorization: Bearer abcDEF123token", "abcDEF123token"),
    # (split so the repository's secret scan does not take the fake token for a real one)
    ('token = {"access' + '_token":"ya29.OAUTHACCESS","token_type":"Bearer","refresh_token":"1//OAUTHREFRESH"}',
     "OAUTHACCESS"),
    ('token = {"access' + '_token":"ya29.x","refresh_token":"1//OAUTHREFRESH2"}', "OAUTHREFRESH2"),
    ("{'client_secret': 'REPRCLIENTSECRET'}", "REPRCLIENTSECRET"),
    ("rclone config create x drive client_secret=CLIENTSECRETVAL scope=drive", "CLIENTSECRETVAL"),
    ("smtp login password=PASSWORDVAL1 user=me", "PASSWORDVAL1"),
    (":sftp,host=h,pass='OBSCUREDPASS':/path", "OBSCUREDPASS"),
    ("s3 secret_access_key=S3SECRETKEYVAL region=eu", "S3SECRETKEYVAL"),
    ("key=PLAINKEYVALUE", "PLAINKEYVALUE"),
    ("secret = \"QUOTEDSECRET\"", "QUOTEDSECRET"),
    ("GET /logs?token=QUERYTOKENVAL&limit=5", "QUERYTOKENVAL"),
    ("webdav at https://alice:URLPASSWORD@dav.example.com/files", "URLPASSWORD"),
    ("json body {\"password\": \"JSONPASSWORD\"}", "JSONPASSWORD"),
]


@pytest.mark.parametrize(("line", "secret"), SECRET_LINES)
def test_mask_secrets_shapes(line: str, secret: str) -> None:
    masked = mask_secrets(line)
    assert secret not in masked
    assert "***" in masked


def test_mask_secrets_leaves_ordinary_text_alone() -> None:
    for text in (
        "Missing API token. Send 'Authorization: Bearer <token>'.",
        "Synced 3 files for profile docs (token_type unchanged)",
        "Backup target 4: 12 files, keep_last=3",
        "bypass=1 monkey=2",
    ):
        assert mask_secrets(text) == text


def test_registered_values_are_masked_anywhere() -> None:
    register_secret("correct-horse-battery")
    register_secret("short")  # too short to register
    try:
        assert mask_secrets("passphrase is correct-horse-battery!") == "passphrase is ***!"
        assert mask_secrets("a short word") == "a short word"
    finally:
        logging_setup.forget_secrets()


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_every_kind_of_secret_is_masked_in_the_file(configure, fmt: str) -> None:
    """Deliberately log each kind of secret; only the masked form reaches the file."""
    from backend.security import get_api_token

    path = configure(OMNISYNC_LOG_FORMAT=fmt, OMNISYNC_LOG_ACCESS="1")
    api_token = get_api_token()
    register_secret("backup passphrase PASSPHRASEVAL")
    register_secret("NTFYTOKENVALUE")
    log = logging.getLogger("backend.test")
    log.warning("the API token is %s", api_token)
    log.warning("ntfy said: NTFYTOKENVALUE rejected")
    log.warning("restoring with backup passphrase PASSPHRASEVAL")
    for line, _ in SECRET_LINES:
        log.warning("%s", line)
    try:
        raise RuntimeError("rclone failed: password=TRACEBACKPASS https://bob:TRACEURLPASS@h/")
    except RuntimeError:
        log.exception("crashed with token=%s", "MSGTOKENVALUE")
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", "/x?api_key=ACCESSLOGKEY", "1.1", 200,
    )
    logging.getLogger(AUDIT_LOGGER).info("audit", extra={"fields": {"note": "password=FIELDPASSWORD"}})

    text = _text(path)
    secrets = [api_token, "PASSPHRASEVAL", "NTFYTOKENVALUE", "TRACEBACKPASS", "TRACEURLPASS", "MSGTOKENVALUE",
               "ACCESSLOGKEY", "FIELDPASSWORD", *(s for _, s in SECRET_LINES)]
    for secret in secrets:
        assert secret not in text, secret
    assert "Traceback (most recent call last)" in text
    assert "ACCESSLOGKEY" not in text and "api_key=***" in text
    if fmt == "json":
        lines = [json.loads(line) for line in text.splitlines()]
        assert any("exc" in entry and "RuntimeError" in entry["exc"] for entry in lines)


def test_filter_masks_once_and_keeps_other_handlers_consistent() -> None:
    record = logging.LogRecord("backend.x", logging.INFO, "f", 1, "pw %s", ("password=abcdefgh",), None)
    flt = LogRecordFilter()
    assert flt.filter(record) and flt.filter(record)
    assert record.getMessage() == "pw password=***"


def test_notification_channel_secrets_are_registered(tmp_path: Path) -> None:
    from backend.services.config import ConfigService
    from backend.services.notification_dispatcher import NotificationDispatcher

    config = tmp_path / "config.toml"
    config.write_text(
        '[notifications.channels.ntfy]\nenabled = true\ntopic = "t"\ntoken = "tk_NTFYCHANNELTOKEN"\n'
        '[notifications.channels.email]\npassword = "EMAILPASSWORD"\n'
    )
    try:
        NotificationDispatcher(ConfigService(config), MagicMock())
        masked = mask_secrets("token tk_NTFYCHANNELTOKEN and EMAILPASSWORD")
        assert "NTFYCHANNELTOKEN" not in masked and "EMAILPASSWORD" not in masked
    finally:
        logging_setup.forget_secrets()


async def test_backup_passphrase_is_registered() -> None:
    from backend.services.backup_service.base import BackupBase

    base = BackupBase.__new__(BackupBase)
    base._rclone = MagicMock(obscure=AsyncMock(return_value="OBSCUREDFORMVALUE"))
    try:
        await base.obscure_passphrase("my long backup phrase")
        assert mask_secrets("x my long backup phrase OBSCUREDFORMVALUE") == "x *** ***"
    finally:
        logging_setup.forget_secrets()


# --- JSON lines -----------------------------------------------------------------


def test_json_lines_have_the_documented_keys(configure) -> None:
    from backend.logging_setup import RequestContext, enter_request

    path = configure(OMNISYNC_LOG_FORMAT="json")
    leave = enter_request(RequestContext("req12345abc", "10.0.0.1"))
    try:
        logging.getLogger(AUDIT_LOGGER).info("sync.start", extra={"fields": {"action": "sync.start"}})
    finally:
        leave()
    logging.getLogger("backend.x").error("plain")
    first, second = (json.loads(line) for line in _text(path).splitlines())
    assert set(first) == {"ts", "level", "logger", "msg", "request_id", "fields"}
    assert first["ts"].endswith("Z") and "T" in first["ts"]
    assert (first["level"], first["logger"], first["msg"]) == ("INFO", AUDIT_LOGGER, "sync.start")
    assert first["request_id"] == "req12345abc"
    assert first["fields"] == {"action": "sync.start"}
    assert set(second) == {"ts", "level", "logger", "msg"}


def test_text_lines_cannot_forge_entries(configure) -> None:
    path = configure()
    logging.getLogger("backend.x").warning(
        "remote name: a\n2026-01-01 00:00:00,000 - INFO - backend.audit - forged\n{\"ts\": \"x\"}",
    )
    entries = LogReader(path).read()
    assert [e.message for e in entries] == ["remote name: a"]
    assert entries[0].exc is not None and "forged" in entries[0].exc


# --- unhandled exceptions -------------------------------------------------------


def _boom() -> None:
    raise ValueError("unhandled password=HOOKPASSWORD")


def test_excepthook_logs_the_traceback(configure) -> None:
    path = configure()
    try:
        _boom()
    except ValueError:
        sys.excepthook(*sys.exc_info())  # type: ignore[arg-type]
    text = _text(path)
    assert "CRITICAL - backend - Unhandled exception" in text
    assert "in _boom" in text and "HOOKPASSWORD" not in text


def test_thread_excepthook_logs_the_traceback(configure) -> None:
    path = configure()
    try:
        _boom()
    except ValueError as exc:
        threading.excepthook(threading.ExceptHookArgs(  # type: ignore[call-arg]
            (type(exc), exc, exc.__traceback__, threading.current_thread()),
        ))
    assert "Unhandled exception in thread" in _text(path)


async def test_loop_exception_handler_logs_the_traceback(configure) -> None:
    from backend.logging_setup import install_loop_exception_handler

    path = configure()
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    install_loop_exception_handler()
    try:
        try:
            _boom()
        except ValueError as exc:
            loop.call_exception_handler({"message": "Task exception was never retrieved", "exception": exc})
    finally:
        loop.set_exception_handler(previous)
    text = _text(path)
    assert "ERROR - backend.asyncio - Task exception was never retrieved" in text
    assert "in _boom" in text


# --- request ids ----------------------------------------------------------------


def test_only_token_like_request_ids_are_accepted() -> None:
    assert accepted_request_id("abc-123_DEF.9") == "abc-123_DEF.9"
    for bad in (None, "", "short", "x" * 65, "has space1", "line\nbreak1", "-leadingdash"):
        assert accepted_request_id(bad) is None


async def test_request_id_header_and_log_lines() -> None:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    seen: dict[str, str | None] = {}
    later: asyncio.Event = asyncio.Event()

    async def after_the_request() -> None:
        await later.wait()
        seen["after"] = current_request_id()

    tasks: list[asyncio.Task] = []
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    collector = Collect()
    collector.addFilter(LogRecordFilter())
    logging.getLogger("backend.test").addHandler(collector)

    @app.get("/x")
    async def x() -> dict[str, str]:
        seen["during"] = current_request_id()
        tasks.append(asyncio.create_task(after_the_request()))
        logging.getLogger("backend.test").warning("inside the request")
        return {}

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            generated = await client.get("/x")
            assert generated.headers["x-request-id"] == seen["during"]
            assert len(seen["during"] or "") == 16
            kept = await client.get("/x", headers={"X-Request-ID": "proxy-id-12345"})
            assert kept.headers["x-request-id"] == "proxy-id-12345"
            replaced = await client.get("/x", headers={"X-Request-ID": "bad id with spaces"})
            assert replaced.headers["x-request-id"] != "bad id with spaces"
    finally:
        logging.getLogger("backend.test").removeHandler(collector)
    later.set()
    await asyncio.gather(*tasks)
    # A task the request started does not keep its id once the answer is sent.
    assert seen["after"] is None
    assert current_request_id() is None
    assert [getattr(r, "request_id", None) for r in records] == [
        generated.headers["x-request-id"], "proxy-id-12345", replaced.headers["x-request-id"],
    ]


async def test_text_lines_carry_the_request_id(configure, test_client) -> None:
    path = configure()
    resp = await test_client.delete("/profiles/nope", params={"confirm": "true"})  # audited, refused
    assert resp.status_code == 404
    request_id = resp.headers["x-request-id"]
    logging.getLogger("backend").warning("outside")
    entries = LogReader(path).read(limit=200)
    [audit_entry] = [e for e in entries if e.request_id == request_id]
    assert audit_entry.logger == AUDIT_LOGGER
    assert audit_entry.message.startswith("profile.delete profile=nope")
    assert next(e for e in entries if e.message == "outside").request_id is None
