"""Central notification dispatcher for OmniSync.

Receives notification events, fans them out to all enabled channels that
meet the severity threshold, and logs results to the notification_log table.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
from collections.abc import Iterable
from typing import cast

from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import NotificationLog
from backend.services.config import ConfigService
from backend.services.notification_channels.base import NotificationChannelBase
from backend.logging_setup import register_secret
from backend.services.notification_channels.settings import SECRET_FIELDS, SETTINGS_MODELS, has_secrets
from backend.services.notification_events import (
    SEVERITY_ORDER,
    NotificationEvent,
    NotificationEventType,
    NotificationSeverity,
)

logger = logging.getLogger(__name__)


def _register_channel_secrets(channels: dict[str, dict[str, object]]) -> None:
    """Mask the channels' passwords, tokens and webhook header values in the log."""
    for name, cfg in channels.items():
        settings = cfg.get("settings")
        if not isinstance(settings, BaseModel):
            continue
        for field in SECRET_FIELDS.get(name, ()):
            register_secret(getattr(settings, field, None))
        headers = getattr(settings, "headers", None)
        if isinstance(headers, dict):
            for value in headers.values():
                register_secret(value)

MAX_LOG_ROWS = 100

# Seconds one channel may take for its availability check and delivery.
# Channels are sent to concurrently, so a slow one never holds up another;
# Web Push bounds each push at PUSH_TIMEOUT + 5 (15 s) itself.
CHANNEL_TIMEOUT = 20.0

# Per-channel error codes dispatch() reports (the details go to the log;
# API responses never carry exception text).
ERROR_UNAVAILABLE = "unavailable"
ERROR_TIMEOUT = "timeout"
ERROR_FAILED = "failed"

# Default per-channel config when [notifications] section is missing from TOML.
# Host native is off by default: it needs a desktop session (the D-Bus
# socket is opt-in in Docker, docker-compose.dbus.yml), so turning it on is
# the user's decision. Web Push only delivers to browsers that subscribed.
# Webhook, ntfy and email need an address first, so they start off too.
_CHANNEL_DEFAULTS: dict[str, dict[str, object]] = {
    "webpush": {"enabled": True, "min_severity": "warning"},
    "host_native": {"enabled": False, "min_severity": "warning"},
    "webhook": {"enabled": False, "min_severity": "warning"},
    "ntfy": {"enabled": False, "min_severity": "warning"},
    "email": {"enabled": False, "min_severity": "warning"},
}

_VALID_SEVERITIES = {s.value for s in NotificationSeverity}


class NotificationDispatcher:
    """Receives events and fans them out to registered notification channels."""

    def __init__(
        self,
        config_service: ConfigService,
        db_session_factory: async_sessionmaker[AsyncSession],
        channel_timeout: float = CHANNEL_TIMEOUT,
    ) -> None:
        self._config_service = config_service
        self._db_session_factory = db_session_factory
        self._channel_timeout = channel_timeout
        self._channels: list[NotificationChannelBase] = []
        self._channel_config: dict[str, dict[str, object]] = {}
        self._config_error: bool = False
        self._unavailable: set[str] = set()  # channels last seen unavailable (logged once)
        self._pending: set[asyncio.Task[object]] = set()
        self._reload_config()

    def register_channel(self, channel: NotificationChannelBase) -> None:
        """Add a channel to the dispatch list."""
        self._channels.append(channel)

    @property
    def channel_names(self) -> list[str]:
        return [c.channel_name for c in self._channels]

    def _reload_config(self) -> None:
        """Read [notifications.channels] from config TOML, cache preferences.

        A config file that cannot be read, or a channel section with invalid
        values, is logged and falls back to the defaults for what is invalid;
        config_error then says so (the settings page shows it).
        """
        self._config_error = False
        try:
            raw = self._config_service._load_toml()
        except Exception as exc:
            logger.error("Notification settings could not be read, using the defaults: %s", exc)
            self._config_error = True
            raw = {}

        notif_section = raw.get("notifications", {})
        channels_cfg = notif_section.get("channels", {}) if isinstance(notif_section, dict) else {}
        if not isinstance(channels_cfg, dict):
            logger.error("[notifications.channels] in the config is not a table; using the defaults")
            self._config_error = True
            channels_cfg = {}

        merged: dict[str, dict[str, object]] = {}
        for name in [*_CHANNEL_DEFAULTS, *(n for n in channels_cfg if n not in _CHANNEL_DEFAULTS)]:
            defaults = _CHANNEL_DEFAULTS.get(name, {"enabled": True, "min_severity": "warning"})
            merged[name] = {**defaults, **self._valid_section(name, channels_cfg.get(name, {}))}

        self._channel_config = merged
        _register_channel_secrets(merged)
        if has_secrets(channels_cfg):
            self._tighten_config_mode()

    def _tighten_config_mode(self) -> None:
        """config.toml holds channel credentials: readable by its owner only.

        Saves write it 0600 already (ConfigService._save_toml); this covers
        a file edited by hand or written by an older version.
        """
        path = self._config_service.config_path
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                os.chmod(path, mode & 0o700)
                logger.info("Restricted %s to its owner: it holds notification credentials", path)
        except OSError as exc:
            logger.warning("Could not restrict the permissions of %s: %s", path, exc)

    def _valid_section(self, name: str, section: object) -> dict[str, object]:
        """The valid keys of one channel's config section (invalid ones are logged)."""
        if not isinstance(section, dict):
            logger.error("[notifications.channels.%s] is not a table; using the defaults", name)
            self._config_error = True
            return {}
        valid: dict[str, object] = {}
        enabled = section.get("enabled")
        if enabled is not None:
            if isinstance(enabled, bool):
                valid["enabled"] = enabled
            else:
                logger.error("notifications.channels.%s.enabled must be true or false", name)
                self._config_error = True
        severity = section.get("min_severity")
        if severity is not None:
            if severity in _VALID_SEVERITIES:
                valid["min_severity"] = severity
            else:
                logger.error(
                    "notifications.channels.%s.min_severity must be one of %s",
                    name, ", ".join(sorted(_VALID_SEVERITIES)),
                )
                self._config_error = True
        model = SETTINGS_MODELS.get(name)
        if model is not None:
            fields = {k: v for k, v in section.items() if k in model.model_fields}
            try:
                valid["settings"] = model.model_validate(fields)
            except ValidationError as exc:
                # Field names and reasons only: the values may be credentials.
                reasons = "; ".join(
                    f"{err['loc'][0] if err['loc'] else '?'}: {err['msg']}"
                    for err in exc.errors(include_input=False, include_url=False)
                )
                logger.error("[notifications.channels.%s] has invalid settings (%s); they are ignored",
                             name, reasons)
                self._config_error = True
                valid["settings"] = model()
        return valid

    def reload(self) -> None:
        """Public method to reload config (called after config updates)."""
        self._reload_config()

    def get_config(self) -> dict[str, dict[str, object]]:
        """Return the current cached channel configuration."""
        return dict(self._channel_config)

    def channel_settings(self, name: str) -> BaseModel:
        """The stored settings of webhook, ntfy or email (defaults when unset)."""
        settings = self._channel_config.get(name, {}).get("settings")
        if isinstance(settings, BaseModel):
            return settings
        return SETTINGS_MODELS[name]()

    @property
    def config_error(self) -> bool:
        """Whether the last read of the notification settings found errors."""
        return self._config_error

    def unknown_channels(self) -> list[str]:
        """Configured channel names no registered channel has (e.g. typos)."""
        registered = set(self.channel_names)
        return sorted(n for n in self._channel_config if n not in registered and n not in _CHANNEL_DEFAULTS)

    async def validate_channels(self) -> dict[str, list[str]]:
        """Startup check: every enabled channel must be able to deliver.

        Logs a warning per enabled channel that cannot (with what it is
        missing), and one per configured channel name nothing handles.
        Returns {channel: missing dependencies} of the enabled, unavailable
        ones.
        """
        problems: dict[str, list[str]] = {}
        for channel in self._channels:
            name = channel.channel_name
            if not self._channel_config.get(name, {}).get("enabled", False):
                continue
            info = await self._describe(channel)
            if not info.get("available"):
                deps = cast("Iterable[object]", info.get("missing_dependencies", []) or [])
                missing = [str(m) for m in deps] or ["unknown"]
                problems[name] = missing
                self._unavailable.add(name)
                logger.warning(
                    "Notification channel '%s' is enabled but cannot deliver: missing %s",
                    name, ", ".join(missing),
                )
        for name in self.unknown_channels():
            logger.warning("Notification settings name an unknown channel '%s'; it is ignored", name)
        if self._config_error:
            logger.warning("The notification settings have errors; defaults are used where they are invalid")
        return problems

    def emit(self, event: NotificationEvent) -> None:
        """Dispatch in the background: for callers that must not wait (a sync).

        Failures are logged. drain() waits for what is still pending.
        """
        task: asyncio.Task[object] = asyncio.get_running_loop().create_task(self._dispatch_logged(event))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _dispatch_logged(self, event: NotificationEvent) -> None:
        try:
            await self.dispatch(event)
        except Exception as exc:
            logger.warning("Could not dispatch notification '%s': %s", event.event_type.value, exc)

    async def drain(self, timeout: float | None = None) -> None:
        """Wait for background dispatches (emit) to finish, at most ``timeout`` s."""
        if self._pending:
            await asyncio.wait(set(self._pending), timeout=timeout)

    async def dispatch(
        self, event: NotificationEvent, only_channel: str | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        """Fan out event to all enabled channels meeting severity threshold.

        The channels are sent to concurrently, each bounded by the channel
        timeout, so a slow or hung channel delays neither the others nor
        the caller beyond that bound.

        ``only_channel`` sends to that one channel only, whether it is
        enabled or not (a per-channel test).

        Returns (channels_delivered, errors) where errors maps a channel
        name to an error code (ERROR_UNAVAILABLE, ERROR_TIMEOUT, ERROR_FAILED).
        """
        # Log the event at the corresponding Python logging level
        _log_event(event)

        selected = [c for c in self._channels if self._wants(c.channel_name, event, only_channel)]
        results = await asyncio.gather(*(self._deliver(c, event) for c in selected))

        channels_delivered = [name for name, error in results if error is None]
        errors = {name: error for name, error in results if error is not None}

        # Write to notification_log
        await self._write_log(event, channels_delivered)

        return channels_delivered, errors

    def _wants(self, name: str, event: NotificationEvent, only_channel: str | None) -> bool:
        """Whether ``event`` goes to channel ``name`` (enabled, severity threshold)."""
        if only_channel is not None:
            return name == only_channel
        cfg = self._channel_config.get(name, _CHANNEL_DEFAULTS.get(name, {}))
        if not cfg.get("enabled", True):
            return False
        if event.event_type == NotificationEventType.TEST:  # test events always pass
            return True
        try:
            min_sev = NotificationSeverity(str(cfg.get("min_severity", "warning")))
        except ValueError:
            min_sev = NotificationSeverity.WARNING
        return SEVERITY_ORDER.get(event.severity, 0) >= SEVERITY_ORDER.get(min_sev, 2)

    async def _deliver(self, channel: NotificationChannelBase, event: NotificationEvent) -> tuple[str, str | None]:
        """(channel name, None if delivered or an error code)."""
        name = channel.channel_name
        try:
            async with asyncio.timeout(self._channel_timeout):
                try:
                    available = await channel.is_available()
                except Exception as exc:
                    logger.debug("Availability check of channel '%s' failed: %s", name, exc)
                    available = False
                if not available:
                    if name not in self._unavailable:
                        logger.warning("Notification channel '%s' is unavailable; skipping it", name)
                        self._unavailable.add(name)
                    return name, ERROR_UNAVAILABLE
                if name in self._unavailable:
                    logger.info("Notification channel '%s' is available again", name)
                    self._unavailable.discard(name)
                await channel.send(event)
                return name, None
        except TimeoutError:
            logger.warning("Channel '%s' did not deliver within %ss", name, self._channel_timeout)
            return name, ERROR_TIMEOUT
        except Exception as exc:
            logger.warning("Channel '%s' failed to deliver: %s", name, exc)
            return name, ERROR_FAILED

    async def _describe(self, channel: NotificationChannelBase) -> dict[str, object]:
        try:
            async with asyncio.timeout(self._channel_timeout):
                return dict(await channel.describe())
        except Exception as exc:
            logger.debug("Status of channel '%s' could not be read: %s", channel.channel_name, exc)
            return {"available": False}

    async def get_channel_status(self) -> dict[str, dict[str, object]]:
        """Return the status (availability and what is missing) of every registered channel."""
        infos = await asyncio.gather(*(self._describe(c) for c in self._channels))
        return {c.channel_name: info for c, info in zip(self._channels, infos, strict=True)}

    async def _write_log(
        self, event: NotificationEvent, channels_delivered: list[str]
    ) -> None:
        """Write notification to log table and evict oldest rows if > MAX_LOG_ROWS."""
        try:
            async with self._db_session_factory() as session:
                log_entry = NotificationLog(
                    event_type=event.event_type.value,
                    severity=event.severity.value,
                    title=event.title,
                    body=event.body,
                    timestamp=event.timestamp,
                    channels_delivered=json.dumps(channels_delivered),
                    profile_slug=getattr(event, "profile_slug", None),
                )
                session.add(log_entry)
                await session.commit()

                # Evict oldest rows if over limit
                count_result = await session.execute(
                    select(func.count(NotificationLog.id))
                )
                total = count_result.scalar() or 0
                if total > MAX_LOG_ROWS:
                    excess = total - MAX_LOG_ROWS
                    oldest = await session.execute(
                        select(NotificationLog.id)
                        .order_by(NotificationLog.id.asc())
                        .limit(excess)
                    )
                    old_ids = [row[0] for row in oldest.all()]
                    if old_ids:
                        await session.execute(
                            delete(NotificationLog).where(NotificationLog.id.in_(old_ids))
                        )
                        await session.commit()
        except Exception as exc:
            logger.warning("Failed to write notification log: %s", exc)


def _log_event(event: NotificationEvent) -> None:
    """Log the event at the corresponding Python logging level."""
    level_map = {
        NotificationSeverity.DEBUG: logging.DEBUG,
        NotificationSeverity.INFO: logging.INFO,
        NotificationSeverity.WARNING: logging.WARNING,
        NotificationSeverity.ERROR: logging.ERROR,
    }
    level = level_map.get(event.severity, logging.INFO)
    logger.log(level, "[%s] %s: %s", event.event_type.value, event.title, event.body)
