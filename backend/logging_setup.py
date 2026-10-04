"""Logging for the whole backend process: one setup, one file, secrets masked.

``configure_logging()`` (called by ``backend.main`` when uvicorn imports
the app) puts two handlers on the root logger: stderr (what ``docker logs``
shows) and a rotating file (``OMNISYNC_LOG_PATH``, what GET /logs, the web
Logs page, the TUI and ``osync logs`` read). Everything that propagates to
the root reaches both:

- OmniSync's own loggers (``backend.*``), at the global ``log_level``
  setting (``apply_level``); ``backend.audit``, the audit trail of user
  actions, always at INFO;
- uvicorn's error logger (startup, shutdown, unhandled request errors);
- libraries (apscheduler, sqlalchemy, httpx, watchdog, alembic, ...) at
  WARNING; the scheduler, file watcher and migrations at INFO while
  ``log_level`` is DEBUG (SQL and HTTP request lines never);
- uvicorn's access log goes to stderr only, unless ``OMNISYNC_LOG_ACCESS=1``
  adds it to the file;
- unhandled exceptions (``sys.excepthook``, ``threading.excepthook`` and
  the event loop's exception handler, see ``install_loop_exception_handler``),
  with their tracebacks.

Uvicorn configures its own loggers before it imports the app; this setup
then replaces uvicorn's handlers with these, so its lines are formatted,
masked and written like every other line (and not printed twice).

Every handler carries ``LogRecordFilter``: it adds the id of the request the
line was written in (``request_id``, set by the request-id middleware) and
masks secrets (``mask_secrets``) in the final message, the exception text
and the structured fields, whichever logger the record came from.

``OMNISYNC_LOG_FORMAT=json`` writes one JSON object per line (``ts``,
``level``, ``logger``, ``msg``, and ``exc``, ``request_id`` and ``fields``
when present) instead of the text format. The file is rotated at
``OMNISYNC_LOG_MAX_BYTES`` (5 MB) with ``OMNISYNC_LOG_BACKUPS`` (3) old
files kept, and is readable by its owner only (0600), like the other files
in the data folder that can hold sensitive data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import re
import sys
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

DEFAULT_LOG_PATH = Path("/data/omnisync/omnisync.log")
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUPS = 3
LOG_FILE_MODE = 0o600

TEXT_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"

OWN_LOGGER = "backend"
AUDIT_LOGGER = "backend.audit"
# Libraries whose warnings and errors belong in the log. Their INFO lines
# (every scheduled run, every file event) are added while log_level is
# DEBUG, except for the ones in QUIET_LIBRARIES: SQL statements with their
# parameters and the URLs of HTTP requests (a webhook URL can carry a
# token) stay out of the log at every level.
LIBRARY_LOGGERS = (
    "apscheduler", "sqlalchemy", "httpx", "httpcore", "watchdog", "alembic",
    "aiosqlite", "asyncio", "pywebpush", "urllib3", "multipart",
)
QUIET_LIBRARIES = frozenset({"sqlalchemy", "httpx", "httpcore", "aiosqlite", "pywebpush", "urllib3"})

# Marks the handlers this module installed, so a second configure_logging
# (uvicorn --reload, tests) replaces them instead of adding more.
_HANDLER_MARK = "_omnisync_handler"
# Set on an exception the error handler logged already; uvicorn's own
# "Exception in ASGI application" line for it is then dropped.
LOGGED_MARK = "_omnisync_logged"


# --- Request context -------------------------------------------------------


@dataclass
class RequestContext:
    """The request a log line is written in: its id and the client address.

    ``active`` turns False when the response is done: tasks the request
    started (which inherit the context) stop carrying its id then.
    """

    request_id: str
    client: str | None = None
    active: bool = True


_current_request: ContextVar[RequestContext | None] = ContextVar("omnisync_request", default=None)


def current_request() -> RequestContext | None:
    """The request being handled now, or None outside a request."""
    ctx = _current_request.get()
    return ctx if ctx is not None and ctx.active else None


def current_request_id() -> str | None:
    ctx = current_request()
    return ctx.request_id if ctx else None


def enter_request(ctx: RequestContext) -> Callable[[], None]:
    """Make ``ctx`` the current request; returns the function that ends it."""
    token = _current_request.set(ctx)

    def leave() -> None:
        ctx.active = False
        _current_request.reset(token)

    return leave


# --- Secret masking ---------------------------------------------------------

MASK = "***"

# Exact secret values known at run time (the API token, notification
# passwords, backup passphrases). Shorter values are not registered: they
# would mask ordinary words.
MIN_SECRET_LENGTH = 6
_MAX_SECRETS = 256
_secrets: OrderedDict[str, None] = OrderedDict()
_secrets_lock = threading.Lock()
_secrets_re: re.Pattern[str] | None = None

# Names whose values are secrets: password, pass, passphrase, secret, key,
# token, and compounds ending in them (client_secret, access_token,
# api_key, encryption_password, ...).
_SECRET_NAME = r"(?:[A-Za-z0-9]+[_-])*(?:password\d?|passwd|pass|passphrase|secret|key|token|apikey|credentials?)"

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # "Bearer <token>", and "Authorization: <scheme> <credentials>" (Basic, ...).
    (re.compile(r"(?i)\b(bearer)(\s+)[A-Za-z0-9._~+/=-]{4,}"), rf"\1\2{MASK}"),
    (re.compile(r"(?i)\b(authorization[\"']?\s*[:=]\s*[\"']?[A-Za-z]+)(\s+)[A-Za-z0-9._~+/=-]{4,}"), rf"\1\2{MASK}"),
    # JSON or Python-repr pairs: "access_token": "...", 'password': '...'.
    (re.compile(rf"""(?i)(["']{_SECRET_NAME}["']\s*:\s*)("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')"""), rf'\1"{MASK}"'),
    # key=value (config lines, rclone connection strings, query strings),
    # not when the value is a JSON object or list (the pairs inside are
    # masked by the rule above).
    (re.compile(
        rf"""(?i)(?<![A-Za-z0-9_-])({_SECRET_NAME})(\s*=\s*)(?![\s{{\[])('(?:[^']|'')*'|"(?:[^"]|"")*"|[^\s,;&'"}}\])]+)"""
    ), rf"\1\2{MASK}"),
    # Credentials in URLs: scheme://user:password@host.
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]*):([^\s@/]+)@"), rf"\1:{MASK}@"),
)


def register_secret(value: object) -> None:
    """Mask ``value`` wherever it appears in a log line from now on.

    For secrets the patterns cannot recognise by their shape: the API token
    in a message that does not say "token=", a notification password, a
    backup passphrase. Values shorter than MIN_SECRET_LENGTH are ignored.
    """
    global _secrets_re
    if not isinstance(value, str):
        return
    value = value.strip()
    if len(value) < MIN_SECRET_LENGTH:
        return
    with _secrets_lock:
        if value in _secrets:
            _secrets.move_to_end(value)
            return
        _secrets[value] = None
        while len(_secrets) > _MAX_SECRETS:
            _secrets.popitem(last=False)
        # Longest first, so a secret containing another is masked whole.
        ordered = sorted(_secrets, key=len, reverse=True)
        _secrets_re = re.compile("|".join(re.escape(s) for s in ordered))


def forget_secrets() -> None:
    """Drop every registered value (tests)."""
    global _secrets_re
    with _secrets_lock:
        _secrets.clear()
        _secrets_re = None


def mask_secrets(text: str) -> str:
    """``text`` with secrets replaced by ***.

    Registered values, then the shapes: Bearer/Basic credentials,
    secret-named JSON or ``key=value`` pairs (password, pass, passphrase,
    secret, key, token, client_secret, access_token, refresh_token, ...),
    credentials in URLs, and rclone connection-string passwords
    (``redact_secrets``, which API answers use).
    """
    if not text:
        return text
    exact = _secrets_re
    if exact is not None:
        text = exact.sub(MASK, text)
    from backend.services.rclone.errors import redact_secrets  # import late: logging is set up first

    text = redact_secrets(text)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_secrets(value)
    if isinstance(value, Mapping):
        return {k: _mask_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask_value(v) for v in value]
    return value


# --- Filter and formatters --------------------------------------------------


class LogRecordFilter(logging.Filter):
    """On every handler: add the request id, mask secrets, drop duplicates.

    The message is rendered once (``msg % args``), masked, and stored back
    with no args; the exception and stack text are rendered and masked the
    same way, so every handler and formatter sees only the masked form.
    """

    _exc_formatter = logging.Formatter()

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.error" and record.exc_info:
            exc = record.exc_info[1]
            if exc is not None and getattr(exc, LOGGED_MARK, False):
                return False  # the API's error handler logged it, with the request id
        if getattr(record, "_omnisync_done", False):
            return True
        if getattr(record, "request_id", None) is None:
            record.__dict__["request_id"] = current_request_id()
        try:
            message = record.getMessage()
        except Exception:  # a broken format string: keep what there is
            message = f"{record.msg!r} {record.args!r}"
        record.msg = mask_secrets(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = self._exc_formatter.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = mask_secrets(record.exc_text)
        if record.stack_info:
            record.stack_info = mask_secrets(record.stack_info)
        fields = getattr(record, "fields", None)
        if isinstance(fields, Mapping):
            record.__dict__["fields"] = _mask_value(fields)
        record.__dict__["_omnisync_done"] = True
        return True


# A continuation line that could pass for a record of its own (a timestamp,
# a JSON object) is indented, so text a user controls (a remote name in an
# exception message) cannot forge a log entry.
_LOOKS_LIKE_RECORD = re.compile(r"^(?:\d|\{)")


class TextFormatter(logging.Formatter):
    """``<time> - <LEVEL> - <logger> - [req:<id>] <message>``, then the traceback.

    The request id part is there only for lines written during a request.
    """

    def __init__(self) -> None:
        super().__init__(TEXT_FORMAT)

    def formatMessage(self, record: logging.LogRecord) -> str:
        request_id = getattr(record, "request_id", None)
        if request_id:
            record.message = f"[req:{request_id}] {record.message}"
        return super().formatMessage(record)

    def format(self, record: logging.LogRecord) -> str:
        first, *rest = super().format(record).split("\n")
        return "\n".join([first, *(f"  {line}" if _LOOKS_LIKE_RECORD.match(line) else line for line in rest)])


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts (ISO 8601, UTC), level, logger, msg, [exc, request_id, fields]."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        exc = record.exc_text or ""
        if record.stack_info:
            exc = f"{exc}\n{self.formatStack(record.stack_info)}" if exc else self.formatStack(record.stack_info)
        if exc:
            entry["exc"] = exc
        request_id = getattr(record, "request_id", None)
        if request_id:
            entry["request_id"] = request_id
        fields = getattr(record, "fields", None)
        if isinstance(fields, Mapping) and fields:
            entry["fields"] = dict(fields)
        return json.dumps(entry, ensure_ascii=False, default=str)


# --- Handlers ---------------------------------------------------------------


class OwnerOnlyRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """A RotatingFileHandler whose files are created (and kept) at mode 0600."""

    def _open(self):  # noqa: ANN202 - the base class's signature
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT, LOG_FILE_MODE)
        try:
            os.fchmod(fd, LOG_FILE_MODE)  # an older file may have been created 0644
        except OSError:
            pass
        return os.fdopen(fd, "a", encoding=self.encoding, errors=self.errors)


@dataclass(frozen=True)
class LogSettings:
    path: Path
    json: bool
    max_bytes: int
    backups: int
    access_to_file: bool


def _positive_int(env: Mapping[str, str], name: str, default: int, minimum: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < minimum:
        print(f"omnisync: ignoring {name}={raw!r} (need an integer >= {minimum}); using {default}", file=sys.stderr)
        return default
    return value


def read_settings(env: Mapping[str, str] | None = None) -> LogSettings:
    """The logging settings from the environment (OMNISYNC_LOG_*)."""
    env = os.environ if env is None else env
    fmt = env.get("OMNISYNC_LOG_FORMAT", "text").strip().lower() or "text"
    if fmt not in ("text", "json"):
        print(f"omnisync: unknown OMNISYNC_LOG_FORMAT={fmt!r}; using text", file=sys.stderr)
        fmt = "text"
    return LogSettings(
        path=Path(env.get("OMNISYNC_LOG_PATH") or DEFAULT_LOG_PATH),
        json=fmt == "json",
        max_bytes=_positive_int(env, "OMNISYNC_LOG_MAX_BYTES", DEFAULT_MAX_BYTES, 1024),
        backups=_positive_int(env, "OMNISYNC_LOG_BACKUPS", DEFAULT_BACKUPS, 0),
        access_to_file=env.get("OMNISYNC_LOG_ACCESS", "").strip().lower() in ("1", "true", "yes", "on"),
    )


def _formatter(settings: LogSettings) -> logging.Formatter:
    return JsonFormatter() if settings.json else TextFormatter()


def _mark(handler: logging.Handler, settings: LogSettings) -> logging.Handler:
    handler.addFilter(LogRecordFilter())
    handler.setFormatter(_formatter(settings))
    setattr(handler, _HANDLER_MARK, True)
    return handler


def _remove_ours(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            logger.removeHandler(handler)
            handler.close()


def _tighten_old_files(settings: LogSettings) -> None:
    """Rotated files from before 0600 was enforced: make them owner-only too."""
    for i in range(1, settings.backups + 1):
        try:
            os.chmod(f"{settings.path}.{i}", LOG_FILE_MODE)
        except OSError:
            pass


def configure_logging(env: Mapping[str, str] | None = None) -> LogSettings:
    """Set up logging for the process (see the module docstring); safe to call again."""
    settings = read_settings(env)
    root = logging.getLogger()
    uvicorn_logger = logging.getLogger("uvicorn")
    access_logger = logging.getLogger("uvicorn.access")
    for logger in (root, uvicorn_logger, access_logger, logging.getLogger(OWN_LOGGER)):
        _remove_ours(logger)

    stderr_handler = _mark(logging.StreamHandler(sys.stderr), settings)
    file_handler: logging.Handler | None = None
    file_error: OSError | None = None
    try:
        settings.path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = _mark(
            OwnerOnlyRotatingFileHandler(
                settings.path, maxBytes=settings.max_bytes, backupCount=settings.backups, encoding="utf-8",
            ),
            settings,
        )
        _tighten_old_files(settings)
    except OSError as exc:  # e.g. run outside Docker without /data: stderr only
        file_error = exc

    root.addHandler(stderr_handler)
    if file_handler is not None:
        root.addHandler(file_handler)
    root.setLevel(logging.WARNING)

    # uvicorn's error logger: through the root handlers instead of its own.
    for handler in [h for h in uvicorn_logger.handlers if not getattr(h, _HANDLER_MARK, False)]:
        uvicorn_logger.removeHandler(handler)
    uvicorn_logger.propagate = True

    # The access log: stderr always (if uvicorn writes one), the file on request.
    for handler in [h for h in access_logger.handlers if not getattr(h, _HANDLER_MARK, False)]:
        access_logger.removeHandler(handler)
    access_logger.propagate = False
    access_logger.addHandler(stderr_handler)
    if file_handler is not None and settings.access_to_file:
        access_logger.addHandler(file_handler)

    own = logging.getLogger(OWN_LOGGER)
    own.propagate = True
    if own.level == logging.NOTSET:
        own.setLevel(logging.INFO)
    logging.getLogger(AUDIT_LOGGER).setLevel(logging.INFO)
    _set_library_level(own.level <= logging.DEBUG)

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook

    if file_error is not None:
        own.warning("Could not set up the log file at %s: %s", settings.path, file_error)
    return settings


def _set_library_level(debug: bool) -> None:
    for name in LIBRARY_LOGGERS:
        verbose = debug and name not in QUIET_LIBRARIES
        logging.getLogger(name).setLevel(logging.INFO if verbose else logging.WARNING)


def apply_level(name: str) -> None:
    """Set OmniSync's own level (DEBUG..CRITICAL); some libraries follow only for DEBUG.

    The audit trail stays at INFO whatever the level, so user actions are
    always recorded.
    """
    level = logging.getLevelName(name)
    logging.getLogger(OWN_LOGGER).setLevel(level)
    logging.getLogger(AUDIT_LOGGER).setLevel(logging.INFO)
    _set_library_level(level == logging.DEBUG)


# --- Unhandled exceptions ---------------------------------------------------


def _excepthook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    logging.getLogger(OWN_LOGGER).critical("Unhandled exception", exc_info=(exc_type, exc, tb))


def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
    if args.exc_type is SystemExit:
        return
    name = args.thread.name if args.thread is not None else "?"
    exc_info = (args.exc_type, args.exc_value, args.exc_traceback) if args.exc_value is not None else None
    logging.getLogger(OWN_LOGGER).error("Unhandled exception in thread %s", name, exc_info=exc_info)


def _loop_exception_handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    exc = context.get("exception")
    message = context.get("message") or "Unhandled exception in the event loop"
    task = context.get("task") or context.get("future")
    where = f" ({task.get_name()})" if isinstance(task, asyncio.Task) else ""
    exc_info = (type(exc), exc, exc.__traceback__) if isinstance(exc, BaseException) else None
    logging.getLogger("backend.asyncio").error("%s%s", message, where, exc_info=exc_info)


def install_loop_exception_handler(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Log exceptions nothing awaited (a crashed background task) with their traceback."""
    (loop or asyncio.get_running_loop()).set_exception_handler(_loop_exception_handler)
