"""Abstract base class for notification channels."""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.services.notification_events import NotificationEvent


class NotificationChannelBase(ABC):
    """Base class that all notification channels must implement."""

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Identifier for this channel (e.g. 'webpush', 'host_native')."""
        ...

    @abstractmethod
    async def send(self, event: NotificationEvent) -> None:
        """Deliver the notification. Raises on failure."""
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Check whether this channel can currently deliver notifications."""
        ...

    async def describe(self) -> dict[str, object]:
        """Status for the API: ``available`` plus whatever else the channel knows.

        Keys a channel may add: ``missing_dependencies`` (codes of what is
        missing, e.g. "notify-send"), ``host_os``, ``detection_method`` and
        ``subscriptions``. The default reports availability only.
        """
        return {"available": await self.is_available()}
