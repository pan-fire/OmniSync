"""Configuration endpoints for OmniSync.

After multi-sync-profiles migration, this serves only global settings
(log_level) and the test-sync endpoint. Per-profile config lives under
/profiles/{slug}.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from backend.audit import audited
from backend.api.errors import SEE_LOG, api_error, failed_test_sync, public_test_sync_result
from backend.api.schemas import GlobalConfigResponse, GlobalConfigUpdateRequest, TestSyncRequest, TestSyncResponse
from backend.exceptions import ConfigError
from backend.services.config import ConfigService, apply_log_level
from backend.services.rclone import RcloneService

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level reference to the config service, set by main.py at startup
_config_service: ConfigService | None = None
_rclone_service: RcloneService | None = None


def set_config_service(service: ConfigService | None) -> None:
    """Set the config service reference."""
    global _config_service
    _config_service = service


def set_rclone_service(service: RcloneService) -> None:
    """Set the rclone service reference for test-sync."""
    global _rclone_service
    _rclone_service = service


@router.get("/config", response_model=GlobalConfigResponse)
async def get_config() -> GlobalConfigResponse:
    """Return the current global configuration."""
    if _config_service is None:
        raise api_error(503, "service_unavailable", "Config service not available")
    try:
        return _config_service.read_global()
    except ConfigError as exc:
        logger.error("Reading the configuration failed: %s", exc)
        raise api_error(500, "internal_error", f"Could not read the configuration. {SEE_LOG}")


@router.put("/config", response_model=GlobalConfigResponse)
@audited("settings.update", lambda kw: {"fields": sorted(kw["request"].model_fields_set), "log_level": kw["request"].log_level})
async def update_config(request: GlobalConfigUpdateRequest) -> GlobalConfigResponse:
    """Update global configuration with partial values."""
    if _config_service is None:
        raise api_error(503, "service_unavailable", "Config service not available")
    try:
        result = _config_service.write_global(request)
    except ConfigError as exc:
        logger.error("Saving the configuration failed: %s", exc)
        raise api_error(500, "internal_error", f"Could not save the configuration. {SEE_LOG}")
    apply_log_level(result.log_level)
    return result


@router.post("/config/test-sync", response_model=TestSyncResponse)
async def test_sync(request: TestSyncRequest) -> TestSyncResponse:
    """Run a round-trip sync test with a dummy file."""
    if _rclone_service is None:
        raise api_error(503, "service_unavailable", "rclone service not available")

    if not request.local_dir or not request.remote_dir:
        raise api_error(422, "invalid_request", "Both local_dir and remote_dir are required")

    logger.info("test_sync: local=%s remote=%s", request.local_dir, request.remote_dir)

    try:
        result = await _rclone_service.test_sync(request.local_dir, request.remote_dir)
    except Exception:
        return failed_test_sync(logger)
    return public_test_sync_result(result, logger)
