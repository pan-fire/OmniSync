"""macOS notification backend using osascript."""

from __future__ import annotations

import asyncio
import logging
import shutil

from backend.services.notification_events import NotificationSeverity

logger = logging.getLogger(__name__)


def _sanitize_applescript(text: str) -> str:
    """Escape characters that could break AppleScript string literals."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


class MacNotifier:
    """Sends desktop notifications via osascript on macOS."""

    async def send(self, title: str, body: str, severity: NotificationSeverity) -> None:
        safe_title = _sanitize_applescript(title)
        safe_body = _sanitize_applescript(body)

        sound_part = ' sound name "Funk"' if severity == NotificationSeverity.ERROR else ""
        script = f'display notification "{safe_body}" with title "{safe_title}"{sound_part}'

        cmd = ["osascript", "-e", script]
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            if proc.returncode != 0:
                msg = f"osascript failed (rc={proc.returncode}): {stderr.decode().strip()}"
                logger.warning(msg)
                raise RuntimeError(msg)
        except asyncio.TimeoutError:
            if proc is not None:
                proc.kill()
            raise RuntimeError("osascript timed out after 5s")
        except FileNotFoundError:
            raise RuntimeError("osascript binary not found")

    def missing_dependencies(self) -> list[str]:
        return [] if shutil.which("osascript") is not None else ["osascript"]

    async def is_available(self) -> bool:
        return not self.missing_dependencies()
