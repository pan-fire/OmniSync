"""Tests for host OS detection."""

from __future__ import annotations

import os
from unittest.mock import patch

from backend.services.host_detector import HostOS, HostOSDetector


class TestEnvVarDetection:
    def test_env_var_linux(self) -> None:
        with patch.dict(os.environ, {"OMNISYNC_HOST_OS": "linux"}, clear=False):
            d = HostOSDetector()
            assert d.detect() == HostOS.LINUX
            assert d.detection_method == "env_var"

    def test_env_var_macos(self) -> None:
        with patch.dict(os.environ, {"OMNISYNC_HOST_OS": "macos"}, clear=False):
            d = HostOSDetector()
            assert d.detect() == HostOS.MACOS

    def test_env_var_windows(self) -> None:
        with patch.dict(os.environ, {"OMNISYNC_HOST_OS": "windows"}, clear=False):
            d = HostOSDetector()
            assert d.detect() == HostOS.WINDOWS

    def test_env_var_android(self) -> None:
        with patch.dict(os.environ, {"OMNISYNC_HOST_OS": "android"}, clear=False):
            d = HostOSDetector()
            assert d.detect() == HostOS.ANDROID

    def test_env_var_invalid(self) -> None:
        with patch.dict(os.environ, {"OMNISYNC_HOST_OS": "bsd"}, clear=False):
            d = HostOSDetector()
            assert d.detect() == HostOS.UNKNOWN
            assert d.detection_method == "env_var_invalid"

    def test_env_var_case_insensitive(self) -> None:
        with patch.dict(os.environ, {"OMNISYNC_HOST_OS": "Linux"}, clear=False):
            d = HostOSDetector()
            assert d.detect() == HostOS.LINUX


class TestMountedFileDetection:
    def test_host_os_release_ubuntu(self, tmp_path) -> None:
        release_file = tmp_path / "host-os-release"
        release_file.write_text('ID=ubuntu\nVERSION_ID="22.04"\n')
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("backend.services.host_detector.Path") as mock_path,
        ):
            # Remove OMNISYNC_HOST_OS if present
            os.environ.pop("OMNISYNC_HOST_OS", None)
            instance = mock_path.return_value
            instance.exists.return_value = True
            instance.read_text.return_value = release_file.read_text()
            # Directly call with real file
            d = HostOSDetector()
            # Patch the path used in detect
            with patch("backend.services.host_detector.Path") as mp:
                mp.return_value.exists.return_value = True
                mp.return_value.read_text.return_value = 'ID=ubuntu\nVERSION_ID="22.04"'
                os.environ.pop("OMNISYNC_HOST_OS", None)
                result = d.detect()
                assert result == HostOS.LINUX
                assert d.detection_method == "host_os_release"


class TestHeuristicDetection:
    def test_dbus_socket_detects_linux(self) -> None:
        env = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}
        with (
            patch.dict(os.environ, env, clear=False),
            patch("backend.services.host_detector.Path") as mp,
        ):
            os.environ.pop("OMNISYNC_HOST_OS", None)
            mp.return_value.exists.return_value = False
            d = HostOSDetector()
            assert d.detect() == HostOS.LINUX
            assert "dbus" in d.detection_method

    def test_osascript_detects_macos(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("backend.services.host_detector.Path") as mp,
            patch("backend.services.host_detector.shutil.which") as mock_which,
            patch("backend.services.host_detector.os.path.exists", return_value=False),
        ):
            os.environ.pop("OMNISYNC_HOST_OS", None)
            os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
            mp.return_value.exists.return_value = False
            mock_which.side_effect = lambda x: "/usr/bin/osascript" if x == "osascript" else None
            d = HostOSDetector()
            assert d.detect() == HostOS.MACOS

    def test_termux_detects_android(self) -> None:
        with (
            patch.dict(os.environ, {"TERMUX_VERSION": "0.118"}, clear=False),
            patch("backend.services.host_detector.Path") as mp,
            patch("backend.services.host_detector.shutil.which", return_value=None),
            patch("backend.services.host_detector.os.path.exists", return_value=False),
        ):
            os.environ.pop("OMNISYNC_HOST_OS", None)
            os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
            mp.return_value.exists.return_value = False
            d = HostOSDetector()
            assert d.detect() == HostOS.ANDROID

    def test_no_detection_returns_unknown(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("backend.services.host_detector.Path") as mp,
            patch("backend.services.host_detector.shutil.which", return_value=None),
            patch("backend.services.host_detector.os.path.exists", return_value=False),
        ):
            os.environ.pop("OMNISYNC_HOST_OS", None)
            os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
            os.environ.pop("TERMUX_VERSION", None)
            mp.return_value.exists.return_value = False
            d = HostOSDetector()
            assert d.detect() == HostOS.UNKNOWN


class TestDeterminism:
    def test_repeated_calls_return_same_result(self) -> None:
        """Property P9: Given identical env, detect() returns the same HostOS."""
        with patch.dict(os.environ, {"OMNISYNC_HOST_OS": "linux"}, clear=False):
            d = HostOSDetector()
            results = [d.detect() for _ in range(10)]
            assert all(r == HostOS.LINUX for r in results)

    def test_env_var_takes_priority_over_heuristic(self) -> None:
        env = {
            "OMNISYNC_HOST_OS": "macos",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        }
        with patch.dict(os.environ, env, clear=False):
            d = HostOSDetector()
            assert d.detect() == HostOS.MACOS
            assert d.detection_method == "env_var"
