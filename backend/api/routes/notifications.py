"""Notification management API routes for OmniSync."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import TypedDict, cast

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.errors import SEE_LOG, api_error
from backend.api.schemas import (
    ChannelConfig,
    ChannelConfigUpdate,
    ChannelStatusInfo,
    ChannelStatusResponse,
    EmailSettingsView,
    NotificationConfigResponse,
    NtfySettingsView,
    WebhookHeaderView,
    WebhookSettingsUpdate,
    WebhookSettingsView,
    NotificationConfigUpdateRequest,
    NotificationHistoryResponse,
    NotificationLogEntry,
    PushSubscriptionRequest,
    PushUnsubscribeRequest,
    TestNotificationRequest,
    TestNotificationResponse,
    VapidPublicKeyResponse,
)
from backend.db.database import get_session
from backend.db.models import NotificationLog
from backend.exceptions import ConfigError
from backend.services.notification_channels.net import UnsafeAddressError, check_http_url
from backend.services.notification_channels.settings import (
    SECRET_FIELDS,
    SETTINGS_MODELS,
    EmailSettings,
    NtfySettings,
    WebhookSettings,
)
from backend.services.notification_channels.webpush import WebPushChannel
from backend.services.notification_dispatcher import NotificationDispatcher
from backend.services.notification_events import test_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])

# Module-level references, set by main.py at startup
_dispatcher: NotificationDispatcher | None = None
_webpush_channel: WebPushChannel | None = None


def set_dispatcher(dispatcher: NotificationDispatcher | None) -> None:
    global _dispatcher
    _dispatcher = dispatcher


def set_webpush_channel(channel: WebPushChannel | None) -> None:
    global _webpush_channel
    _webpush_channel = channel


def _get_dispatcher() -> NotificationDispatcher:
    if _dispatcher is None:
        raise api_error(503, "service_unavailable", "Notification system not initialized")
    return _dispatcher


def _get_webpush() -> WebPushChannel:
    if _webpush_channel is None:
        raise api_error(503, "service_unavailable", "WebPush channel not initialized")
    return _webpush_channel


class _SettingsViews(TypedDict, total=False):
    webhook: WebhookSettingsView
    ntfy: NtfySettingsView
    email: EmailSettingsView


def _settings_view(name: str, settings: object) -> _SettingsViews:
    """The ChannelConfig fields of a channel's settings, secrets masked."""
    if isinstance(settings, WebhookSettings):
        return {"webhook": WebhookSettingsView(
            url=settings.url, allow_http=settings.allow_http,
            headers=[WebhookHeaderView(name=k, value_set=bool(v)) for k, v in settings.headers.items()],
        )}
    if isinstance(settings, NtfySettings):
        return {"ntfy": NtfySettingsView(
            server=settings.server, topic=settings.topic, allow_http=settings.allow_http,
            username=settings.username, token_set=bool(settings.token), password_set=bool(settings.password),
        )}
    if isinstance(settings, EmailSettings):
        return {"email": EmailSettingsView(
            host=settings.host, port=settings.port, security=settings.security, username=settings.username,
            password_set=bool(settings.password), from_addr=settings.from_addr, to=list(settings.to),
        )}
    return {}


@router.get("/config", response_model=NotificationConfigResponse, response_model_exclude_none=True)
async def get_notification_config() -> NotificationConfigResponse:
    """Read current notification channel preferences (credentials are never returned)."""
    dispatcher = _get_dispatcher()
    raw_config = dispatcher.get_config()
    channels = {
        name: ChannelConfig(
            enabled=bool(cfg.get("enabled", True)),
            min_severity=str(cfg.get("min_severity", "warning")),
            **_settings_view(name, cfg.get("settings")),
        )
        for name, cfg in raw_config.items()
    }
    return NotificationConfigResponse(channels=channels)


def _merged_settings(name: str, current: BaseModel | None, update: ChannelConfigUpdate) -> BaseModel | None:
    """The channel's settings with ``update`` applied; None when it changes none.

    Raises HTTPException 400 for settings of another channel, invalid
    values or a refused address.
    """
    given = {k for k in SETTINGS_MODELS if getattr(update, k) is not None}
    if given - {name}:
        raise api_error(
            400, "invalid_channel_settings",
            f"Settings for {', '.join(sorted(given - {name}))} "
            f"cannot be stored on the channel '{name}'",
        )
    patch = getattr(update, name, None) if name in SETTINGS_MODELS else None
    if patch is None or current is None:
        return None
    data = current.model_dump()
    if isinstance(patch, WebhookSettingsUpdate):
        if patch.url is not None:
            data["url"] = patch.url
        if patch.allow_http is not None:
            data["allow_http"] = patch.allow_http
        if patch.headers is not None:
            stored = {k.lower(): v for k, v in data["headers"].items()}
            headers: dict[str, str] = {}
            for header in patch.headers:
                if header.name.lower() in (h.lower() for h in headers):
                    raise api_error(400, "invalid_channel_settings", f"The header '{header.name}' is given twice")
                value = header.value or stored.get(header.name.lower(), "")
                if not value:
                    raise api_error(400, "invalid_channel_settings", f"The header '{header.name}' needs a value")
                headers[header.name] = value
            data["headers"] = headers
    else:
        fields = patch.model_dump(exclude_none=True, exclude={"clear"})
        for secret in SECRET_FIELDS[name]:
            if not fields.get(secret):  # empty: keep the stored secret
                fields.pop(secret, None)
        data.update(fields)
        for secret in patch.clear:  # type: ignore[union-attr]
            data[secret] = ""
    try:
        merged = SETTINGS_MODELS[name].model_validate(data)
    except ValidationError as exc:
        reasons = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.errors(include_input=False, include_url=False)
        )
        raise api_error(400, "invalid_channel_settings", f"Invalid {name} settings: {reasons}")
    url = getattr(merged, "url", None) or getattr(merged, "server", None)
    if url:
        try:
            check_http_url(url, bool(getattr(merged, "allow_http", False)))
        except UnsafeAddressError as exc:
            raise api_error(400, "invalid_channel_settings", f"Invalid {name} URL: {exc}")
    return merged


@router.put("/config", response_model=NotificationConfigResponse, response_model_exclude_none=True)
async def update_notification_config(
    request: NotificationConfigUpdateRequest,
) -> NotificationConfigResponse:
    """Update notification channel preferences and persist to config.toml.

    Partial: only the channels and fields given change; a field left out
    keeps its current value.
    """
    dispatcher = _get_dispatcher()

    if request.channels:
        known = set(dispatcher.get_config())
        unknown = sorted(name for name in request.channels if name not in known)
        if unknown:
            raise api_error(400, "unknown_channel", f"Unknown notification channel: {', '.join(unknown)}")

        # Read current TOML, merge notification section, save
        try:
            raw = dispatcher._config_service._load_toml()
        except ConfigError:
            logger.exception("Could not read the config file to save notification settings")
            raise api_error(
                500, "internal_error",
                f"The config file could not be read, so nothing was saved. {SEE_LOG}",
            )
        notif_section = raw.setdefault("notifications", {})
        channels_section = notif_section.setdefault("channels", {})

        current = dispatcher.get_config()
        # Check every channel's update before anything is written.
        new_settings = {
            name: _merged_settings(
                name, dispatcher.channel_settings(name) if name in SETTINGS_MODELS else None, update,
            )
            for name, update in request.channels.items()
        }
        for name, update in request.channels.items():
            section = channels_section.get(name)
            merged = dict(section) if isinstance(section, dict) else {}
            # Keep the effective values (defaults included) for fields not given
            merged.setdefault("enabled", bool(current[name].get("enabled", True)))
            merged.setdefault("min_severity", str(current[name].get("min_severity", "warning")))
            merged.update(update.model_dump(exclude_none=True, include={"enabled", "min_severity"}))
            settings = new_settings[name]
            if settings is not None:
                merged.update(settings.model_dump())
            channels_section[name] = merged

        dispatcher._config_service._save_toml(raw)
        dispatcher.reload()

    return await get_notification_config()


@router.get("/channels/status", response_model=ChannelStatusResponse)
async def get_channel_status() -> ChannelStatusResponse:
    """Status of every registered channel: availability, what is missing, the detected host."""
    dispatcher = _get_dispatcher()
    raw_status = await dispatcher.get_channel_status()
    channels = {
        name: ChannelStatusInfo(
            available=bool(info.get("available", False)),
            detection_method=_opt_str(info.get("detection_method")),
            host_os=_opt_str(info.get("host_os")),
            missing_dependencies=[str(m) for m in cast("Iterable[object]", info.get("missing_dependencies", []) or [])],
            permission_status=_opt_str(info.get("permission_status")),
            subscriptions=subs if isinstance(subs := info.get("subscriptions"), int) else None,
        )
        for name, info in raw_status.items()
    }
    return ChannelStatusResponse(
        channels=channels,
        config_error=dispatcher.config_error is True,
        unknown_channels=list(dispatcher.unknown_channels() or []),
    )


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


@router.get("/vapid-public-key", response_model=VapidPublicKeyResponse)
async def get_vapid_public_key() -> VapidPublicKeyResponse:
    """Return the VAPID public key for browser push subscription."""
    webpush = _get_webpush()
    return VapidPublicKeyResponse(public_key=webpush.get_public_key())


@router.post("/push-subscription", status_code=201)
async def subscribe_push(request: PushSubscriptionRequest, response: Response) -> dict[str, str]:
    """Register a browser push subscription (idempotent: a known endpoint is updated)."""
    webpush = _get_webpush()
    p256dh = request.keys.get("p256dh", "")
    auth = request.keys.get("auth", "")
    if not p256dh or not auth:
        raise api_error(400, "invalid_subscription", "Missing p256dh or auth key")
    created = await webpush.add_subscription(request.endpoint, p256dh, auth)
    if created is False:
        response.status_code = 200
        return {"detail": "Subscription updated"}
    return {"detail": "Subscription created"}


@router.delete("/push-subscription")
async def unsubscribe_push(request: PushUnsubscribeRequest) -> dict[str, str]:
    """Remove a browser push subscription."""
    webpush = _get_webpush()
    removed = await webpush.remove_subscription(request.endpoint)
    if not removed:
        raise api_error(404, "subscription_not_found", "Subscription not found")
    return {"detail": "Subscription removed"}


@router.get("/history", response_model=NotificationHistoryResponse)
async def get_notification_history(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    profile: str | None = Query(default=None, description="Filter by profile slug"),
    session: AsyncSession = Depends(get_session),
) -> NotificationHistoryResponse:
    """Return paginated notification history."""
    # Build base filters
    filters = []
    if profile is not None:
        filters.append(NotificationLog.profile_slug == profile)

    # Count total
    count_stmt = select(func.count(NotificationLog.id))
    for f in filters:
        count_stmt = count_stmt.where(f)
    count_result = await session.execute(count_stmt)
    total = count_result.scalar() or 0

    # Fetch page
    query = select(NotificationLog).order_by(NotificationLog.id.desc()).offset(offset).limit(limit)
    for f in filters:
        query = query.where(f)
    result = await session.execute(query)
    rows = result.scalars().all()

    items = [
        NotificationLogEntry(
            id=row.id,
            event_type=row.event_type,
            severity=row.severity,
            title=row.title,
            body=row.body,
            timestamp=row.timestamp.isoformat() if row.timestamp else "",
            channels_delivered=json.loads(row.channels_delivered) if row.channels_delivered else [],
        )
        for row in rows
    ]

    return NotificationHistoryResponse(items=items, total=total)


@router.post("/test", response_model=TestNotificationResponse)
async def send_test_notification(
    request: TestNotificationRequest | None = None,
) -> TestNotificationResponse:
    """Send a test notification through all enabled channels, or one given channel.

    A test of one channel is sent even if the channel is turned off, so it
    can be checked before it is turned on.
    """
    dispatcher = _get_dispatcher()
    channel = request.channel if request is not None else None
    if channel is not None and channel not in dispatcher.channel_names:
        raise api_error(404, "unknown_channel", f"Unknown notification channel: {channel}")
    event = test_event()
    channels_delivered, errors = await dispatcher.dispatch(event, only_channel=channel)

    return TestNotificationResponse(
        success=len(channels_delivered) > 0 and not errors,
        channels_delivered=channels_delivered,
        errors=errors,
    )
