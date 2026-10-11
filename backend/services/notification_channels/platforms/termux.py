"""Android notification backend using termux-notification."""

from __future__ import annotations

import asyncio
import logging
import shutil

from backend.services.notification_events import NotificationSeverity
from backend.services.subprocesses import communicate_or_kill

logger = logging.getLogger(__name__)

_SEVERITY_TO_PRIORITY: dict[NotificationSeverity, str] = {
    NotificationSeverity.DEBUG: "low",
    NotificationSeverity.INFO: "low",
    NotificationSeverity.WARNING: "default",
    NotificationSeverity.ERROR: "high",
}


class TermuxNotifier:
    """Sends notifications via termux-notification on Android."""

    async def send(self, title: str, body: str, severity: NotificationSeverity) -> None:
        priority = _SEVERITY_TO_PRIORITY.get(severity, "default")
        cmd = [
            "termux-notification",
            "--title", title,
            "--content", body,
            "--priority", priority,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await communicate_or_kill(proc, timeout=5.0)
            if proc.returncode != 0:
                # errors="replace": a non-UTF-8 message (e.g. a Windows OEM code page)
                # must not turn the failure into a UnicodeDecodeError.
                detail = stderr.decode(errors="replace").strip()
                msg = f"termux-notification failed (rc={proc.returncode}): {detail}"
                logger.warning(msg)
                raise RuntimeError(msg)
        except asyncio.TimeoutError:  # the process is killed and reaped by then
            raise RuntimeError("termux-notification timed out after 5s")
        except FileNotFoundError:
            raise RuntimeError("termux-notification binary not found")

    def missing_dependencies(self) -> list[str]:
        return [] if shutil.which("termux-notification") is not None else ["termux-notification"]

    async def is_available(self) -> bool:
        return not self.missing_dependencies()
