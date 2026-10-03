"""The services every router needs, wired in one place.

main.py wires the real services at startup; the test app wires the same set
(with fakes), so a router added here is wired in both.
backend/tests/test_wiring.py fails when a router's ``set_*`` function is
not called from wire_routes().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.api.routes import backups, browse, config, conflicts, health, logs, notifications, remotes, sync, wizard
from backend.api.routes import profiles as profiles_router

if TYPE_CHECKING:
    from backend.services.backup_service import BackupService
    from backend.services.config import ConfigService
    from backend.services.log_reader import LogReader
    from backend.services.notification_channels.webpush import WebPushChannel
    from backend.services.notification_dispatcher import NotificationDispatcher
    from backend.services.profile_service import ProfileService
    from backend.services.rclone import RcloneService
    from backend.services.sync_engine_manager import SyncEngineManager


@dataclass
class RouteServices:
    manager: SyncEngineManager | None = None
    profile_service: ProfileService | None = None
    rclone: RcloneService | None = None
    config_service: ConfigService | None = None
    db_factory: async_sessionmaker[AsyncSession] | None = None
    log_reader: LogReader | None = None
    dispatcher: NotificationDispatcher | None = None
    webpush_channel: WebPushChannel | None = None
    backup_service: BackupService | None = None


def wire_routes(services: RouteServices) -> None:
    """Hand each router the services it uses (None clears them)."""
    health.set_manager(services.manager)
    sync.set_manager(services.manager)
    conflicts.set_manager(services.manager)
    config.set_config_service(services.config_service)
    config.set_rclone_service(services.rclone)  # type: ignore[arg-type]
    profiles_router.set_manager(services.manager)
    profiles_router.set_profile_service(services.profile_service)
    profiles_router.set_rclone_service(services.rclone)
    remotes.set_rclone_service(services.rclone)
    remotes.set_db_factory(services.db_factory)
    browse.set_rclone_service(services.rclone)  # type: ignore[arg-type]
    wizard.set_rclone_service(services.rclone)  # type: ignore[arg-type]
    logs.set_log_reader(services.log_reader)
    notifications.set_dispatcher(services.dispatcher)
    notifications.set_webpush_channel(services.webpush_channel)
    backups.set_backup_service(services.backup_service)
    backups.set_db_factory(services.db_factory)


def unwire_routes() -> None:
    """Clear every router's services."""
    wire_routes(RouteServices())
