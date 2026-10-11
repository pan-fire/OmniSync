"""Linux notification backend using notify-send (D-Bus)."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil

from backend.services.notification_events import NotificationSeverity
from backend.services.subprocesses import communicate_or_kill

logger = logging.getLogger(__name__)

_SEVERITY_TO_URGENCY: dict[NotificationSeverity, str] = {
    NotificationSeverity.DEBUG: "low",
    NotificationSeverity.INFO: "low",
    NotificationSeverity.WARNING: "normal",
    NotificationSeverity.ERROR: "critical",
}


class LinuxNotifier:
    """Sends desktop notifications via notify-send on Linux."""

    async def send(self, title: str, body: str, severity: NotificationSeverity) -> None:
        urgency = _SEVERITY_TO_URGENCY.get(severity, "normal")
        # "--" ends the options: a title or body starting with "-" (a file
        # named "-draft.txt") is text, not an option notify-send rejects.
        cmd = ["notify-send", "--app-name=omnisync", f"--urgency={urgency}", "--", title, body]

        env = os.environ.copy()
        dbus_addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
        if dbus_addr:
            env["DBUS_SESSION_BUS_ADDRESS"] = dbus_addr

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await communicate_or_kill(proc, timeout=5.0)
            if proc.returncode != 0:
                # errors="replace": a non-UTF-8 message (e.g. a Windows OEM code page)
                # must not turn the failure into a UnicodeDecodeError.
                detail = stderr.decode(errors="replace").strip()
                msg = f"notify-send failed (rc={proc.returncode}): {detail}"
                logger.warning(msg)
                raise RuntimeError(msg)
        except asyncio.TimeoutError:  # the process is killed and reaped by then
            raise RuntimeError("notify-send timed out after 5s")
        except FileNotFoundError:
            raise RuntimeError("notify-send binary not found")

    def missing_dependencies(self) -> list[str]:
        """What keeps notify-send from reaching a desktop, empty when nothing.

        Checks the interface itself, not just the configuration: the
        variable must be set, a unix:path socket it names must exist (a
        compose file can set the variable without the socket being
        mounted), and notify-send must be installed.
        """
        missing: list[str] = []
        address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
        if not address:
            missing.append("dbus_session_bus")
        else:
            socket_path = dbus_socket_path(address)
            if socket_path is not None and not os.path.exists(socket_path):
                missing.append("dbus_socket")
        if shutil.which("notify-send") is None:
            missing.append("notify-send")
        return missing

    async def is_available(self) -> bool:
        return not self.missing_dependencies()


def dbus_socket_path(address: str) -> str | None:
    """The socket file of a ``unix:path=...`` D-Bus address, or None.

    Other transports (abstract sockets, tcp) have no file to check. An
    address may list several transports separated by ';' - the first
    unix:path one is taken.
    """
    for transport in address.split(";"):
        if not transport.startswith("unix:"):
            continue
        for part in transport[len("unix:"):].split(","):
            key, _, value = part.partition("=")
            if key == "path" and value:
                return value
    return None
