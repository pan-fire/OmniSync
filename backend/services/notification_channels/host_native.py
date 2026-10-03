"""Host-native notification channel — cross-platform desktop notifications."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import cast

from backend.services.host_detector import HostOS, HostOSDetector
from backend.services.notification_channels.base import NotificationChannelBase
from backend.services.notification_events import NotificationEvent

logger = logging.getLogger(__name__)


class HostNativeChannel(NotificationChannelBase):
    """Dispatches notifications to the host OS native notification system."""

    def __init__(self, detector: HostOSDetector) -> None:
        self._detector = detector
        self._notifier = self._select_notifier(detector.detected_os)

    @property
    def channel_name(self) -> str:
        return "host_native"

    def _select_notifier(self, os_type: HostOS):
        if os_type == HostOS.LINUX:
            from backend.services.notification_channels.platforms.linux import LinuxNotifier
            return LinuxNotifier()
        elif os_type == HostOS.MACOS:
            from backend.services.notification_channels.platforms.macos import MacNotifier
            return MacNotifier()
        elif os_type == HostOS.WINDOWS:
            from backend.services.notification_channels.platforms.windows import WindowsNotifier
            return WindowsNotifier()
        elif os_type == HostOS.ANDROID:
            from backend.services.notification_channels.platforms.termux import TermuxNotifier
            return TermuxNotifier()
        return None

    async def send(self, event: NotificationEvent) -> None:
        if self._notifier is None:
            raise RuntimeError("No platform notifier available")
        await self._notifier.send(event.title, event.body, event.severity)

    async def is_available(self) -> bool:
        if self._notifier is None:
            return False
        return await self._notifier.is_available()

    def missing_dependencies(self) -> list[str]:
        """Codes of what the host notifier lacks; "host_os" when none was detected."""
        if self._notifier is None:
            return ["host_os"]
        probe = getattr(self._notifier, "missing_dependencies", None)
        return list(cast("Iterable[str]", probe())) if callable(probe) else []

    async def describe(self) -> dict[str, object]:
        available = await self.is_available()
        return {
            "available": available,
            "host_os": self._detector.detected_os.value,
            "detection_method": self._detector.detection_method or None,
            "missing_dependencies": [] if available else (self.missing_dependencies() or ["unknown"]),
        }
