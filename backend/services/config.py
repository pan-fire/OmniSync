"""TOML-based configuration service for OmniSync."""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

import toml

from backend.api.schemas import GlobalConfigResponse, GlobalConfigUpdateRequest
from backend.exceptions import ConfigError
from backend.logging_setup import apply_level
from backend.security import atomic_write_file

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("/data/omnisync/config.toml")

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def apply_log_level(level: str | None) -> None:
    """Set the level of OmniSync's own loggers ("backend.*").

    Libraries log warnings and errors only, unless the level is DEBUG; the
    audit trail is always recorded (backend.logging_setup.apply_level). An
    unknown level (e.g. a hand-edited config.toml) falls back to INFO.
    """
    name = (level or "INFO").upper()
    if name not in LOG_LEVELS:
        logger.warning("Unknown log_level %r in config; using INFO", level)
        name = "INFO"
    apply_level(name)


def _holds_secrets(data: dict) -> bool:
    """Whether the config has notification credentials (an SMTP password, a token)."""
    from backend.services.notification_channels.settings import has_secrets

    notifications = data.get("notifications")
    channels = notifications.get("channels") if isinstance(notifications, dict) else None
    return isinstance(channels, dict) and has_secrets(channels)


class ConfigService:
    """Reads and writes OmniSync configuration from a TOML file.

    After multi-sync-profiles migration the only top-level fields are global
    settings (log_level, history_days) and the [notifications] section. Per-profile settings
    live in the database.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        # OMNISYNC_CONFIG_PATH moves it, like the other OMNISYNC_*_PATH files.
        self.config_path = config_path or Path(os.environ.get("OMNISYNC_CONFIG_PATH") or DEFAULT_CONFIG_PATH)
        self._global_defaults: dict = {
            "log_level": "INFO",
            "history_days": 90,
        }

    # --- Global config methods ---

    def read_global(self) -> GlobalConfigResponse:
        """Read only the global settings from TOML."""
        try:
            raw = self._load_toml()
            merged = {**self._global_defaults}
            if "log_level" in raw:
                merged["log_level"] = raw["log_level"]
            if "history_days" in raw:
                days = raw["history_days"]
                if isinstance(days, int) and not isinstance(days, bool) and days >= 0:
                    merged["history_days"] = days
                else:  # hand-edited: keep the default rather than fail every read
                    logger.warning("Invalid history_days %r in config; using %d", days, merged["history_days"])
            return GlobalConfigResponse(**merged)
        except Exception as exc:
            if isinstance(exc, ConfigError):
                raise
            raise ConfigError(f"Failed to read global config: {exc}") from exc

    def write_global(self, updates: GlobalConfigUpdateRequest) -> GlobalConfigResponse:
        """Update only global settings in TOML."""
        try:
            raw = self._load_toml()
            update_fields = updates.model_dump(exclude_none=True)
            raw.update(update_fields)
            self._save_toml(raw)
            return self.read_global()
        except Exception as exc:
            if isinstance(exc, ConfigError):
                raise
            raise ConfigError(f"Failed to write global config: {exc}") from exc

    # --- I/O helpers ---

    def _load_toml(self) -> dict:
        """Load raw TOML, return empty dict if file missing."""
        if not self.config_path.exists():
            return {}
        try:
            with open(self.config_path, "r") as f:
                return toml.load(f)
        except toml.TomlDecodeError as exc:
            raise ConfigError(f"Invalid TOML in {self.config_path}: {exc}") from exc

    def _save_toml(self, data: dict) -> None:
        """Write dict to TOML file, atomically: a failed write leaves the old file."""
        # Filter out None values — TOML doesn't have a null type
        clean = {k: v for k, v in data.items() if v is not None}
        try:
            try:
                mode = stat.S_IMODE(self.config_path.stat().st_mode)
            except FileNotFoundError:
                mode = 0o644
            if _holds_secrets(clean):
                mode &= 0o700  # notification credentials: the owner only
            atomic_write_file(self.config_path, toml.dumps(clean).encode(), mode)
        except OSError as exc:
            raise ConfigError(f"Cannot write to {self.config_path}: {exc}") from exc
