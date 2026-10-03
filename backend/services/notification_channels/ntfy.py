"""ntfy notification channel: publish to a topic on ntfy.sh or a self-hosted server.

Uses ntfy's JSON publish API (POST to the server root with the topic in
the body), so titles and messages travel as UTF-8 JSON and need no header
encoding. The ntfy phone app shows them as push notifications.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from backend.services.notification_channels.base import NotificationChannelBase
from backend.services.notification_channels.net import describe_url, post_json
from backend.services.notification_channels.settings import NtfySettings
from backend.services.notification_events import NotificationEvent, NotificationSeverity
from backend.version import get_version

logger = logging.getLogger(__name__)

# ntfy priorities: 1 min, 2 low, 3 default, 4 high, 5 max (urgent).
PRIORITY: dict[NotificationSeverity, int] = {
    NotificationSeverity.DEBUG: 2,
    NotificationSeverity.INFO: 3,
    NotificationSeverity.WARNING: 4,
    NotificationSeverity.ERROR: 5,
}

# Tags; ntfy shows the ones that name an emoji as that emoji.
TAGS: dict[NotificationSeverity, str] = {
    NotificationSeverity.DEBUG: "mag",
    NotificationSeverity.INFO: "information_source",
    NotificationSeverity.WARNING: "warning",
    NotificationSeverity.ERROR: "rotating_light",
}


def ntfy_message(event: NotificationEvent, topic: str) -> dict[str, object]:
    """The JSON publish request for ``event``."""
    tags = [TAGS.get(event.severity, "bell"), "omnisync", event.event_type.value]
    if event.profile_slug:
        tags.append(event.profile_slug[:64])
    return {
        "topic": topic,
        "title": event.title,
        "message": event.body or event.title,
        "priority": PRIORITY.get(event.severity, 3),
        "tags": tags,
    }


class NtfyChannel(NotificationChannelBase):
    """Delivers notifications through an ntfy server."""

    def __init__(self, settings: Callable[[], NtfySettings]) -> None:
        self._settings = settings

    @property
    def channel_name(self) -> str:
        return "ntfy"

    def missing_dependencies(self) -> list[str]:
        settings = self._settings()
        missing = []
        if not settings.server:
            missing.append("ntfy_server")
        if not settings.topic:
            missing.append("ntfy_topic")
        return missing

    async def is_available(self) -> bool:
        return not self.missing_dependencies()

    async def describe(self) -> dict[str, object]:
        missing = self.missing_dependencies()
        return {"available": not missing, "missing_dependencies": missing}

    async def send(self, event: NotificationEvent) -> None:
        settings = self._settings()
        if self.missing_dependencies():
            raise RuntimeError("ntfy server or topic not configured")
        headers = {"User-Agent": f"OmniSync/{get_version()}"}
        auth: tuple[str, str] | None = None
        if settings.token:
            headers["Authorization"] = f"Bearer {settings.token}"
        elif settings.username:
            auth = (settings.username, settings.password)
        url = settings.server.rstrip("/") + "/"
        await post_json(
            url, ntfy_message(event, settings.topic),
            allow_http=settings.allow_http, headers=headers, auth=auth,
        )
        logger.debug("ntfy notification sent to %s", describe_url(url))
