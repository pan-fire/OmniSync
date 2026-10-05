"""Windows notification backend using PowerShell BurntToast."""

from __future__ import annotations

import asyncio
import logging
import os
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


# The title and body reach PowerShell as environment variables, never as
# script text: the command is fixed, so no file name can end a string literal
# (PowerShell also ends '...' at the typographic quotes U+2018..U+201B) and
# run code. A variable's value is data however it is quoted.
TITLE_VAR = "OMNISYNC_TOAST_TITLE"
BODY_VAR = "OMNISYNC_TOAST_BODY"


def toast_script(severity: NotificationSeverity) -> str:
    """The PowerShell command for a toast: the same for every title and body."""
    sound = _SEVERITY_TO_SOUND.get(severity, "-Sound 'Default'")
    return f"New-BurntToastNotification -Text $env:{TITLE_VAR}, $env:{BODY_VAR} -AppLogo $null {sound}"


def _env_value(text: str) -> str:
    """An environment variable cannot hold NUL; drop it rather than fail the toast."""
    return text.replace("\0", "")


class WindowsNotifier:
    """Sends desktop notifications via PowerShell on Windows."""

    async def send(self, title: str, body: str, severity: NotificationSeverity) -> None:
        cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", toast_script(severity)]
        env = {**os.environ, TITLE_VAR: _env_value(title), BODY_VAR: _env_value(body)}

        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
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
