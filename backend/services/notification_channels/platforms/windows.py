"""Windows notification backend using PowerShell BurntToast."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import sys

from backend.services.notification_events import NotificationSeverity
from backend.services.subprocesses import communicate_or_kill

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
# Always "1": the script stops with LOST_ENV_EXIT when it is missing, so an
# environment that did not reach PowerShell fails the toast instead of
# showing an empty one.
MARKER_VAR = "OMNISYNC_TOAST"
LOST_ENV_EXIT = 3


def toast_script(severity: NotificationSeverity) -> str:
    """The PowerShell command for a toast: the same for every title and body."""
    sound = _SEVERITY_TO_SOUND.get(severity, "-Sound 'Default'")
    return (f"if (-not $env:{MARKER_VAR}) {{ exit {LOST_ENV_EXIT} }}; "
            f"New-BurntToastNotification -Text $env:{TITLE_VAR}, $env:{BODY_VAR} -AppLogo $null {sound}")


def _env_value(text: str) -> str:
    """An environment variable cannot hold NUL; drop it rather than fail the toast."""
    return text.replace("\0", "")


def in_wsl() -> bool:
    """Whether OmniSync runs in Linux under WSL, where powershell.exe is
    started through WSL interop (also detected by HostDetector as Windows)."""
    if not sys.platform.startswith("linux"):
        return False
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    return "microsoft" in platform.uname().release.lower()


def toast_env(title: str, body: str) -> dict[str, str]:
    """The environment for powershell.exe: this process's, plus the toast's variables.

    Under WSL, Linux environment variables reach a Windows process only
    when WSLENV names them, so they are added to it (``/u``: from WSL to
    Windows only), after any names already there. Native Windows ignores
    WSLENV and gets none.
    """
    env = {**os.environ, MARKER_VAR: "1", TITLE_VAR: _env_value(title), BODY_VAR: _env_value(body)}
    if in_wsl():
        ours = (MARKER_VAR, TITLE_VAR, BODY_VAR)
        kept = [item for item in os.environ.get("WSLENV", "").split(":")
                if item and item.split("/", 1)[0] not in ours]
        env["WSLENV"] = ":".join([*kept, *(f"{name}/u" for name in ours)])
    return env


class WindowsNotifier:
    """Sends desktop notifications via PowerShell on Windows."""

    async def send(self, title: str, body: str, severity: NotificationSeverity) -> None:
        cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", toast_script(severity)]
        env = toast_env(title, body)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await communicate_or_kill(proc, timeout=5.0)
            if proc.returncode == LOST_ENV_EXIT:
                msg = ("PowerShell toast failed: the title and body did not reach PowerShell through its "
                       "environment (under WSL, WSLENV must pass them on)")
                logger.warning(msg)
                raise RuntimeError(msg)
            if proc.returncode != 0:
                # errors="replace": a non-UTF-8 message (e.g. a Windows OEM code page)
                # must not turn the failure into a UnicodeDecodeError.
                detail = stderr.decode(errors="replace").strip()
                msg = f"PowerShell toast failed (rc={proc.returncode}): {detail}"
                logger.warning(msg)
                raise RuntimeError(msg)
        except asyncio.TimeoutError:  # the process is killed and reaped by then
            raise RuntimeError("PowerShell timed out after 5s")
        except FileNotFoundError:
            raise RuntimeError("powershell.exe not found")

    def missing_dependencies(self) -> list[str]:
        # Whether the BurntToast module is installed is only known by running
        # PowerShell; a missing module fails the send with a clear error.
        return [] if shutil.which("powershell.exe") is not None else ["powershell.exe"]

    async def is_available(self) -> bool:
        return not self.missing_dependencies()
