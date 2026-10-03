"""Windows notification backend using PowerShell BurntToast."""

from __future__ import annotations

import asyncio
import logging
import shutil

from backend.services.notification_events import NotificationSeverity

logger = logging.getLogger(__name__)


# BurntToast has no priority, so severity picks the sound: quiet for
# information, the default sound for warnings, an alarm for errors.
_SEVERITY_TO_SOUND: dict[NotificationSeverity, str] = {
    NotificationSeverity.DEBUG: "-Silent",
    NotificationSeverity.INFO: "-Silent",
    NotificationSeverity.WARNING: "-Sound 'Default'",
    NotificationSeverity.ERROR: "-Sound 'Alarm'",
}


def _sanitize_powershell(text: str) -> str:
    """Escape characters that could break PowerShell string literals."""
    return text.replace("'", "''")


class WindowsNotifier:
    """Sends desktop notifications via PowerShell on Windows."""

    async def send(self, title: str, body: str, severity: NotificationSeverity) -> None:
        safe_title = _sanitize_powershell(title)
        safe_body = _sanitize_powershell(body)

        sound = _SEVERITY_TO_SOUND.get(severity, "-Sound 'Default'")
        script = (
            f"New-BurntToastNotification -Text '{safe_title}', '{safe_body}' "
            f"-AppLogo $null {sound}"
        )
        cmd = ["powershell.exe", "-NoProfile", "-Command", script]

        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            if proc.returncode != 0:
                msg = f"PowerShell toast failed (rc={proc.returncode}): {stderr.decode().strip()}"
                logger.warning(msg)
                raise RuntimeError(msg)
        except asyncio.TimeoutError:
            if proc is not None:
                proc.kill()
            raise RuntimeError("PowerShell timed out after 5s")
        except FileNotFoundError:
            raise RuntimeError("powershell.exe not found")

    def missing_dependencies(self) -> list[str]:
        # Whether the BurntToast module is installed is only known by running
        # PowerShell; a missing module fails the send with a clear error.
        return [] if shutil.which("powershell.exe") is not None else ["powershell.exe"]

    async def is_available(self) -> bool:
        return not self.missing_dependencies()
