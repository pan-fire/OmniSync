"""Tests for platform-specific notifiers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.notification_events import NotificationSeverity


# Stand-ins for asyncio.wait_for that never leave proc.communicate()'s
# coroutine unawaited (a patched wait_for returning a value would).
async def _await_now(awaitable, timeout):
    return await awaitable


async def _time_out(awaitable, timeout):
    awaitable.close()
    raise asyncio.TimeoutError


@pytest.mark.asyncio
class TestLinuxNotifier:
    """Tests for LinuxNotifier."""

    async def test_send_calls_notify_send(self) -> None:
        from backend.services.notification_channels.platforms.linux import LinuxNotifier

        notifier = LinuxNotifier()
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            with patch("asyncio.wait_for", side_effect=_await_now):
                await notifier.send("Test Title", "Test Body", NotificationSeverity.WARNING)

            mock_exec.assert_called_once()
            args = mock_exec.call_args[0]
            assert args[0] == "notify-send"
            assert "--app-name=omnisync" in args
            assert "--urgency=normal" in args
            assert "Test Title" in args
            assert "Test Body" in args

    async def test_severity_mapping(self) -> None:
        from backend.services.notification_channels.platforms.linux import (
            _SEVERITY_TO_URGENCY,
        )

        assert _SEVERITY_TO_URGENCY[NotificationSeverity.DEBUG] == "low"
        assert _SEVERITY_TO_URGENCY[NotificationSeverity.INFO] == "low"
        assert _SEVERITY_TO_URGENCY[NotificationSeverity.WARNING] == "normal"
        assert _SEVERITY_TO_URGENCY[NotificationSeverity.ERROR] == "critical"

    async def test_is_available_checks_dbus(self, tmp_path) -> None:
        from backend.services.notification_channels.platforms.linux import LinuxNotifier

        notifier = LinuxNotifier()
        socket = tmp_path / "bus"
        socket.touch()
        with patch("shutil.which", return_value="/usr/bin/notify-send"):
            with patch.dict("os.environ", {"DBUS_SESSION_BUS_ADDRESS": f"unix:path={socket}"}):
                assert await notifier.is_available() is True

            with patch.dict("os.environ", {}, clear=True):
                assert await notifier.is_available() is False
                assert notifier.missing_dependencies() == ["dbus_session_bus"]

    async def test_missing_socket_is_unavailable(self, tmp_path) -> None:
        """The variable alone (set by the compose file) is not enough; the socket must exist."""
        from backend.services.notification_channels.platforms.linux import LinuxNotifier

        notifier = LinuxNotifier()
        address = f"unix:path={tmp_path / 'not-mounted'},guid=abc"
        with patch("shutil.which", return_value="/usr/bin/notify-send"):
            with patch.dict("os.environ", {"DBUS_SESSION_BUS_ADDRESS": address}):
                assert await notifier.is_available() is False
                assert notifier.missing_dependencies() == ["dbus_socket"]

    async def test_missing_notify_send_is_unavailable(self, tmp_path) -> None:
        from backend.services.notification_channels.platforms.linux import LinuxNotifier

        notifier = LinuxNotifier()
        socket = tmp_path / "bus"
        socket.touch()
        with patch("shutil.which", return_value=None):
            with patch.dict("os.environ", {"DBUS_SESSION_BUS_ADDRESS": f"unix:path={socket}"}):
                assert await notifier.is_available() is False
                assert notifier.missing_dependencies() == ["notify-send"]

    async def test_dbus_socket_path(self) -> None:
        from backend.services.notification_channels.platforms.linux import dbus_socket_path

        assert dbus_socket_path("unix:path=/run/user/1000/bus") == "/run/user/1000/bus"
        assert dbus_socket_path("unix:path=/run/bus,guid=123") == "/run/bus"
        assert dbus_socket_path("unix:abstract=/tmp/dbus-x;unix:path=/b") == "/b"
        assert dbus_socket_path("unix:abstract=/tmp/dbus-x") is None
        assert dbus_socket_path("tcp:host=localhost,port=1") is None

    async def test_timeout_kills_process(self) -> None:
        from backend.services.notification_channels.platforms.linux import LinuxNotifier

        notifier = LinuxNotifier()
        mock_proc = AsyncMock()
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("asyncio.wait_for", side_effect=_time_out):
                with pytest.raises(RuntimeError, match="timed out"):
                    await notifier.send("T", "B", NotificationSeverity.INFO)
                mock_proc.kill.assert_called_once()


@pytest.mark.asyncio
class TestMacNotifier:
    """Tests for MacNotifier."""

    async def test_send_calls_osascript(self) -> None:
        from backend.services.notification_channels.platforms.macos import MacNotifier

        notifier = MacNotifier()
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            with patch("asyncio.wait_for", side_effect=_await_now):
                await notifier.send("Title", "Body", NotificationSeverity.INFO)

            args = mock_exec.call_args[0]
            assert args[0] == "osascript"
            assert args[1] == "-e"

    async def test_error_severity_adds_sound(self) -> None:
        from backend.services.notification_channels.platforms.macos import MacNotifier

        notifier = MacNotifier()
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            with patch("asyncio.wait_for", side_effect=_await_now):
                await notifier.send("Title", "Body", NotificationSeverity.ERROR)

            script = mock_exec.call_args[0][2]
            assert "sound name" in script

    async def test_sanitizes_quotes(self) -> None:
        from backend.services.notification_channels.platforms.macos import _sanitize_applescript

        assert _sanitize_applescript('say "hello"') == 'say \\"hello\\"'
        assert _sanitize_applescript("back\\slash") == "back\\\\slash"

    async def test_is_available(self) -> None:
        from backend.services.notification_channels.platforms.macos import MacNotifier

        notifier = MacNotifier()
        with patch("shutil.which", return_value="/usr/bin/osascript"):
            assert await notifier.is_available() is True
        with patch("shutil.which", return_value=None):
            assert await notifier.is_available() is False


@pytest.mark.asyncio
class TestWindowsNotifier:
    """Tests for WindowsNotifier."""

    async def test_send_calls_powershell(self) -> None:
        from backend.services.notification_channels.platforms.windows import WindowsNotifier

        notifier = WindowsNotifier()
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            with patch("asyncio.wait_for", side_effect=_await_now):
                await notifier.send("Title", "Body", NotificationSeverity.WARNING)

            args = mock_exec.call_args[0]
            assert args[0] == "powershell.exe"
            assert "-NoProfile" in args

    @pytest.mark.parametrize("severity, flag", [
        (NotificationSeverity.INFO, "-Silent"),
        (NotificationSeverity.WARNING, "-Sound 'Default'"),
        (NotificationSeverity.ERROR, "-Sound 'Alarm'"),
    ])
    async def test_severity_picks_the_sound(self, severity, flag) -> None:
        """Windows maps severity too (BurntToast has no urgency: the sound)."""
        from backend.services.notification_channels.platforms.windows import WindowsNotifier

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            with patch("asyncio.wait_for", side_effect=_await_now):
                await WindowsNotifier().send("Title", "Body", severity)
        script = mock_exec.call_args[0][-1]
        assert script.endswith(flag)

    async def test_sanitizes_quotes(self) -> None:
        from backend.services.notification_channels.platforms.windows import _sanitize_powershell

        assert _sanitize_powershell("it's here") == "it''s here"

    async def test_is_available(self) -> None:
        from backend.services.notification_channels.platforms.windows import WindowsNotifier

        notifier = WindowsNotifier()
        with patch("shutil.which", return_value="/usr/bin/powershell.exe"):
            assert await notifier.is_available() is True
        with patch("shutil.which", return_value=None):
            assert await notifier.is_available() is False


@pytest.mark.asyncio
class TestTermuxNotifier:
    """Tests for TermuxNotifier."""

    async def test_send_calls_termux_notification(self) -> None:
        from backend.services.notification_channels.platforms.termux import TermuxNotifier

        notifier = TermuxNotifier()
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            with patch("asyncio.wait_for", side_effect=_await_now):
                await notifier.send("Title", "Body", NotificationSeverity.ERROR)

            args = mock_exec.call_args[0]
            assert args[0] == "termux-notification"
            assert "--priority" in args

    async def test_severity_mapping(self) -> None:
        from backend.services.notification_channels.platforms.termux import _SEVERITY_TO_PRIORITY

        assert _SEVERITY_TO_PRIORITY[NotificationSeverity.INFO] == "low"
        assert _SEVERITY_TO_PRIORITY[NotificationSeverity.WARNING] == "default"
        assert _SEVERITY_TO_PRIORITY[NotificationSeverity.ERROR] == "high"

    async def test_is_available(self) -> None:
        from backend.services.notification_channels.platforms.termux import TermuxNotifier

        notifier = TermuxNotifier()
        with patch("shutil.which", return_value="/data/data/com.termux/files/usr/bin/termux-notification"):
            assert await notifier.is_available() is True
        with patch("shutil.which", return_value=None):
            assert await notifier.is_available() is False
