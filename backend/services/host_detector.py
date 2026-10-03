"""Host OS detection from inside Docker containers."""

from __future__ import annotations

import logging
import os
import shutil
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class HostOS(str, Enum):
    LINUX = "linux"
    MACOS = "macos"
    WINDOWS = "windows"
    ANDROID = "android"
    UNKNOWN = "unknown"


# Mapping from /etc/host-os-release ID values to HostOS
_OS_RELEASE_MAP: dict[str, HostOS] = {
    "ubuntu": HostOS.LINUX,
    "debian": HostOS.LINUX,
    "fedora": HostOS.LINUX,
    "arch": HostOS.LINUX,
    "centos": HostOS.LINUX,
    "rhel": HostOS.LINUX,
    "opensuse": HostOS.LINUX,
    "alpine": HostOS.LINUX,
    "manjaro": HostOS.LINUX,
    "neon": HostOS.LINUX,
    "macos": HostOS.MACOS,
    "darwin": HostOS.MACOS,
    "windows": HostOS.WINDOWS,
    "android": HostOS.ANDROID,
}

_VALID_HOST_OS = {e.value for e in HostOS if e != HostOS.UNKNOWN}


class HostOSDetector:
    """Detect the host OS from inside a Docker container using layered probes."""

    def __init__(self) -> None:
        self.detected_os: HostOS = HostOS.UNKNOWN
        self.detection_method: str = ""

    def detect(self) -> HostOS:
        """Run layered detection probes. Called once at startup."""
        # 1. Check OMNISYNC_HOST_OS env var (highest priority)
        env_val = os.environ.get("OMNISYNC_HOST_OS", "").lower().strip()
        if env_val:
            if env_val in _VALID_HOST_OS:
                self.detected_os = HostOS(env_val)
                self.detection_method = "env_var"
                logger.info("Host OS detected via env var: %s", self.detected_os.value)
                return self.detected_os
            logger.error(
                "Invalid OMNISYNC_HOST_OS value '%s'. Valid: %s",
                env_val, ", ".join(sorted(_VALID_HOST_OS)),
            )
            self.detected_os = HostOS.UNKNOWN
            self.detection_method = "env_var_invalid"
            return self.detected_os

        # 2. Check mounted /etc/host-os-release file
        host_release = Path("/etc/host-os-release")
        if host_release.exists():
            try:
                content = host_release.read_text()
                for line in content.splitlines():
                    if line.startswith("ID="):
                        os_id = line.split("=", 1)[1].strip().strip('"').lower()
                        if os_id in _OS_RELEASE_MAP:
                            self.detected_os = _OS_RELEASE_MAP[os_id]
                            self.detection_method = "host_os_release"
                            logger.info("Host OS detected via /etc/host-os-release: %s", self.detected_os.value)
                            return self.detected_os
            except OSError as exc:
                logger.warning("Failed to read /etc/host-os-release: %s", exc)

        # 3. Heuristic detection
        result = self._detect_heuristic()
        self.detected_os = result
        return result

    def _detect_heuristic(self) -> HostOS:
        """Detect host OS via available binaries and env vars."""
        # D-Bus socket → Linux
        dbus_addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
        if dbus_addr or os.path.exists("/run/user"):
            self.detection_method = "heuristic_dbus"
            logger.info("Host OS detected via D-Bus heuristic: linux")
            return HostOS.LINUX

        # osascript → macOS
        if shutil.which("osascript"):
            self.detection_method = "heuristic_osascript"
            logger.info("Host OS detected via osascript heuristic: macos")
            return HostOS.MACOS

        # PowerShell → Windows
        if shutil.which("powershell.exe"):
            self.detection_method = "heuristic_powershell"
            logger.info("Host OS detected via PowerShell heuristic: windows")
            return HostOS.WINDOWS

        # Termux → Android
        if os.environ.get("TERMUX_VERSION"):
            self.detection_method = "heuristic_termux"
            logger.info("Host OS detected via Termux heuristic: android")
            return HostOS.ANDROID

        self.detection_method = "none"
        logger.warning("Could not detect host OS — host native notifications disabled")
        return HostOS.UNKNOWN
