"""FastAPI application entry point for OmniSync."""

from __future__ import annotations

import logging
import logging.handlers
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from fastapi import Depends, FastAPI

from backend.api.errors import ERROR_RESPONSES, install_error_handlers
from backend.api.routes import backups, browse, config, conflicts, health, jobs, logs, notifications, remotes, sync, wizard
from backend.api.routes import profiles as profiles_router
from backend.api.wiring import RouteServices, unwire_routes, wire_routes
from backend.db.database import init_database
from backend.services.config import ConfigService, apply_log_level
from backend.services.history import HistoryMaintenance, recover_interrupted_jobs
from backend.services.liveness_monitor import LivenessMonitor, start_liveness_monitor
from backend.services.host_detector import HostOSDetector
from backend.services.log_reader import LogReader
from backend.services.migration import migrate_backup_dir_to_targets, migrate_legacy_config
from backend.services.notification_channels.host_native import HostNativeChannel
from backend.services.notification_channels.ntfy import NtfyChannel
from backend.services.notification_channels.settings import EmailSettings, NtfySettings, WebhookSettings
from backend.services.notification_channels.smtp import EmailChannel
from backend.services.notification_channels.webhook import WebhookChannel
from backend.services.notification_channels.webpush import WebPushChannel
from backend.services.notification_dispatcher import NotificationDispatcher
from backend.services.notification_events import startup_failure_event, startup_success_event
from backend.services.profile_service import ProfileService
from backend.services.rclone import RcloneService
from backend.services.sync_engine_manager import SyncEngineManager
from backend.services.backup_service import BackupService
from backend.security import BodySizeLimit, TrustedHostGuard, get_api_token, require_api_token
from backend.services.path_guard import warn_about_stored_paths
from backend.version import get_version

LOG_FILE = Path(os.environ.get("OMNISYNC_LOG_PATH", "/data/omnisync/omnisync.log"))
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"


def _setup_logging() -> None:
    """Configure logging to write to both stderr and the OmniSync log file.

    The log file is what the /logs endpoint reads from, so without this
    the Logs page in the UI would always be empty.
    """
    # The level is the global log_level setting, applied at startup and on
    # every change (backend.services.config.apply_log_level); the handlers
    # pass whatever the logger lets through.
    root = logging.getLogger("backend")
    root.setLevel(logging.INFO)

    # Avoid adding duplicate handlers on uvicorn reload
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        return

    # Stream handler (stderr) — so docker logs still work
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(stream_handler)

    # File handler — rotating, 5 MB max, keep 3 backups
    # Gracefully skip if the directory can't be created (e.g. outside Docker)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(file_handler)
    except (PermissionError, OSError) as exc:
        root.warning("Could not set up log file at %s: %s", LOG_FILE, exc)


_setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB, migrate legacy config, start engines. Shutdown: stop engines."""
    await init_database()

    # Resolve (or generate) the API token now, so a fresh install writes its
    # token file and logs where it is before any client connects.
    get_api_token()

    config_service = ConfigService()
    try:
        apply_log_level(config_service.read_global().log_level)
    except Exception as exc:
        logger.warning("Could not apply the configured log level: %s", exc)
    rclone = RcloneService()

    # Ensure the rclone config directory exists
    rclone_config_dir = os.path.dirname(rclone.rclone_config_path)
    if rclone_config_dir:
        os.makedirs(rclone_config_dir, exist_ok=True)

    from backend.db.database import _async_session_factory
    assert _async_session_factory is not None, "Database not initialized"

    # Jobs the last run left "running" (it crashed or was killed) end as
    # failed, before any engine starts new ones.
    await recover_interrupted_jobs(_async_session_factory)

    # --- Legacy migration ---
    profile_service = ProfileService(_async_session_factory)
    await migrate_legacy_config(config_service, profile_service, _async_session_factory)
    await migrate_backup_dir_to_targets(_async_session_factory)
    await warn_about_stored_paths(_async_session_factory)

    # --- Notification system setup ---
    host_detector = HostOSDetector()
    host_detector.detect()

    dispatcher = NotificationDispatcher(config_service, _async_session_factory)

    startup_failures: list[tuple[str, str]] = []
    webpush_channel = WebPushChannel(_async_session_factory)
    try:
        webpush_channel.ensure_vapid_keys()
    except Exception as exc:
        logger.warning("Could not initialize VAPID keys: %s", exc)
        startup_failures.append(("Web Push (VAPID keys)", "the keys could not be created or read; see the log"))

    host_native_channel = HostNativeChannel(host_detector)
    dispatcher.register_channel(webpush_channel)
    dispatcher.register_channel(host_native_channel)
    # Channels that work on a headless server; they read their settings
    # from the dispatcher, so a saved change applies to the next event.
    dispatcher.register_channel(WebhookChannel(lambda: cast(WebhookSettings, dispatcher.channel_settings("webhook"))))
    dispatcher.register_channel(NtfyChannel(lambda: cast(NtfySettings, dispatcher.channel_settings("ntfy"))))
    dispatcher.register_channel(EmailChannel(lambda: cast(EmailSettings, dispatcher.channel_settings("email"))))
    # Warn about enabled channels that cannot deliver (the settings page
    # shows the same through /notifications/channels/status).
    await dispatcher.validate_channels()

    # --- Engine manager ---
    manager = SyncEngineManager(rclone, dispatcher, _async_session_factory)

    await manager.start_all()

    # --- Backup service ---
    backup_service = BackupService(rclone, dispatcher, _async_session_factory, manager)
    await backup_service.start()

    # Subsystem health (schedulers, database, rclone), once a minute on a
    # scheduler of its own: it runs with no profile too.
    liveness_scheduler = start_liveness_monitor(LivenessMonitor(
        lambda: [
            *((f"profile '{e.profile.slug}'", e._scheduler) for e in manager.engines.values()),
            ("backups", backup_service._scheduler),
        ],
        dispatcher, _async_session_factory,
    ))

    # Old job history: cleaned up now and once a day.
    history = HistoryMaintenance(_async_session_factory, lambda: config_service.read_global().history_days)
    history.start()

    # Wire up route dependencies
    wire_routes(RouteServices(
        manager=manager,
        profile_service=profile_service,
        rclone=rclone,
        config_service=config_service,
        db_factory=_async_session_factory,
        log_reader=LogReader(LOG_FILE),
        dispatcher=dispatcher,
        webpush_channel=webpush_channel,
        backup_service=backup_service,
    ))

    # Dispatch the startup result
    try:
        for subsystem, error in startup_failures:
            await dispatcher.dispatch(startup_failure_event(subsystem, error))
        if not startup_failures:
            await dispatcher.dispatch(startup_success_event())
    except Exception:
        pass

    logger.info("OmniSync %s backend started (%d profile engine(s))", get_version(), len(manager.engines))

    yield

    liveness_scheduler.shutdown(wait=False)
    # Bounded (5 s and 20 s), so with the engines' 60 s the shutdown stays
    # inside the container's 90 s grace period.
    await history.stop()
    await backup_service.stop()
    # Requests during shutdown get 503 instead of reaching stopping services.
    unwire_routes()
    await manager.stop_all()
    await dispatcher.drain(timeout=5)
    logger.info("OmniSync backend stopped")


# Every route requires the API token (see backend/security.py for the few
# public ones), and requests for unknown Host names are rejected.
# The interactive docs and OpenAPI schema bypass app-level dependencies, so
# they are served only when OMNISYNC_API_DOCS=1 (development).
_docs = os.environ.get("OMNISYNC_API_DOCS") == "1"
app = FastAPI(
    title="OmniSync",
    version=get_version(),
    lifespan=lifespan,
    dependencies=[Depends(require_api_token)],
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
    responses=ERROR_RESPONSES,
)
install_error_handlers(app)
app.add_middleware(BodySizeLimit)
app.add_middleware(TrustedHostGuard)  # outermost: foreign hosts are refused first

# Register routers
app.include_router(health.router)
app.include_router(sync.router)
app.include_router(config.router)
app.include_router(profiles_router.router)
app.include_router(jobs.router)
app.include_router(conflicts.router)
app.include_router(remotes.router)
app.include_router(logs.router)
app.include_router(wizard.router)
app.include_router(browse.router)
app.include_router(notifications.router)
app.include_router(backups.router)
