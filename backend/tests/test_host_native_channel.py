"""Tests for HostNativeChannel."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.services.host_detector import HostOS, HostOSDetector
from backend.services.notification_channels.host_native import HostNativeChannel
from backend.services.notification_events import (
    test_event as make_test_event,  # aliased: pytest would collect "test_event"
)


def _make_detector(os_type: HostOS) -> HostOSDetector:
    detector = HostOSDetector()
    detector.detected_os = os_type
    detector.detection_method = "test"
    return detector


@pytest.mark.asyncio
class TestHostNativeChannel:

    async def test_channel_name(self) -> None:
        channel = HostNativeChannel(_make_detector(HostOS.LINUX))
        assert channel.channel_name == "host_native"

    async def test_selects_linux_notifier(self) -> None:
        channel = HostNativeChannel(_make_detector(HostOS.LINUX))
        from backend.services.notification_channels.platforms.linux import LinuxNotifier
        assert isinstance(channel._notifier, LinuxNotifier)

    async def test_selects_mac_notifier(self) -> None:
        channel = HostNativeChannel(_make_detector(HostOS.MACOS))
        from backend.services.notification_channels.platforms.macos import MacNotifier
        assert isinstance(channel._notifier, MacNotifier)

    async def test_selects_windows_notifier(self) -> None:
        channel = HostNativeChannel(_make_detector(HostOS.WINDOWS))
        from backend.services.notification_channels.platforms.windows import WindowsNotifier
        assert isinstance(channel._notifier, WindowsNotifier)

    async def test_selects_termux_notifier(self) -> None:
        channel = HostNativeChannel(_make_detector(HostOS.ANDROID))
        from backend.services.notification_channels.platforms.termux import TermuxNotifier
        assert isinstance(channel._notifier, TermuxNotifier)

    async def test_unknown_os_no_notifier(self) -> None:
        channel = HostNativeChannel(_make_detector(HostOS.UNKNOWN))
        assert channel._notifier is None

    async def test_is_available_false_when_unknown(self) -> None:
        channel = HostNativeChannel(_make_detector(HostOS.UNKNOWN))
        assert await channel.is_available() is False

    async def test_is_available_delegates_to_notifier(self) -> None:
        channel = HostNativeChannel(_make_detector(HostOS.LINUX))
        channel._notifier = AsyncMock()
        channel._notifier.is_available = AsyncMock(return_value=True)
        assert await channel.is_available() is True

    async def test_send_delegates_to_notifier(self) -> None:
        channel = HostNativeChannel(_make_detector(HostOS.LINUX))
        channel._notifier = AsyncMock()
        channel._notifier.send = AsyncMock()

        event = make_test_event()
        await channel.send(event)
        channel._notifier.send.assert_called_once_with(
            event.title, event.body, event.severity
        )

    async def test_send_raises_when_no_notifier(self) -> None:
        channel = HostNativeChannel(_make_detector(HostOS.UNKNOWN))
        with pytest.raises(RuntimeError, match="No platform notifier"):
            await channel.send(make_test_event())

    async def test_describe_reports_host_and_what_is_missing(self) -> None:
        """The status API gets the detected OS, how it was detected, and what is missing."""
        from unittest.mock import patch

        channel = HostNativeChannel(_make_detector(HostOS.MACOS))
        with patch("shutil.which", return_value=None):
            info = await channel.describe()
        assert info == {
            "available": False, "host_os": "macos", "detection_method": "test",
            "missing_dependencies": ["osascript"],
        }

    async def test_describe_unknown_host(self) -> None:
        channel = HostNativeChannel(_make_detector(HostOS.UNKNOWN))
        info = await channel.describe()
        assert info["available"] is False
        assert info["host_os"] == "unknown"
        assert info["missing_dependencies"] == ["host_os"]
