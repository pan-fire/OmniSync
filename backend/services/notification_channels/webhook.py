"""Webhook notification channel: POST a JSON document to a configured URL.

For Home Assistant, n8n, Node-RED, a chat bot or anything else that takes
an HTTP request; works on a headless server. The URL is checked against
SSRF (notification_channels.net) before every request.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from backend.services.notification_channels.base import NotificationChannelBase
from backend.services.notification_channels.net import describe_url, post_json
from backend.services.notification_channels.settings import WebhookSettings
from backend.services.notification_events import NotificationEvent
from backend.version import get_version

logger = logging.getLogger(__name__)


def event_payload(event: NotificationEvent) -> dict[str, object]:
    """The JSON document a webhook receives (documented in docs/gem/configuration.md)."""
    return {
        "source": "omnisync",
        "version": get_version(),
        "event_type": event.event_type.value,
        "severity": event.severity.value,
        "title": event.title,
        "body": event.body,
        "profile_slug": event.profile_slug,
        "profile_name": event.profile_name,
        "timestamp": event.timestamp.isoformat(),
    }


class WebhookChannel(NotificationChannelBase):
    """Delivers each notification as an HTTP POST with a JSON body."""

    def __init__(self, settings: Callable[[], WebhookSettings]) -> None:
        self._settings = settings

    @property
    def channel_name(self) -> str:
        return "webhook"

    def missing_dependencies(self) -> list[str]:
        return [] if self._settings().url else ["webhook_url"]

    async def is_available(self) -> bool:
        return not self.missing_dependencies()

    async def describe(self) -> dict[str, object]:
        missing = self.missing_dependencies()
        return {"available": not missing, "missing_dependencies": missing}

    async def send(self, event: NotificationEvent) -> None:
        settings = self._settings()
        if not settings.url:
            raise RuntimeError("No webhook URL configured")
        await post_json(
            settings.url, event_payload(event),
            allow_http=settings.allow_http,
            headers={"User-Agent": f"OmniSync/{get_version()}", **settings.headers},
        )
        logger.debug("Webhook notification sent to %s", describe_url(settings.url))
