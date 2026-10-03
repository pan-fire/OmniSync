"""Unit and property tests for the config service.

config.toml holds only global settings (log_level) and the [notifications]
section; per-profile settings live in the database (multi-sync-profiles).
"""

from pathlib import Path

import pytest
import toml
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from backend.api.schemas import GlobalConfigUpdateRequest
from backend.exceptions import ConfigError
from backend.services.config import ConfigService

log_levels = st.sampled_from(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])


@given(level=log_levels)
@settings(max_examples=25)
def test_global_config_round_trip(level: str) -> None:
    """write_global then read_global returns the written log level."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        service = ConfigService(config_path=Path(tmpdir) / "config.toml")
        assert service.write_global(GlobalConfigUpdateRequest(log_level=level)).log_level == level
        assert service.read_global().log_level == level


class TestGlobalConfig:
    def test_defaults_when_file_missing(self, tmp_path: Path) -> None:
        service = ConfigService(config_path=tmp_path / "nonexistent.toml")
        assert service.read_global().log_level == "INFO"

    def test_defaults_when_log_level_missing(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text('[notifications.webpush]\nenabled = true\n')
        assert ConfigService(config_path=config_file).read_global().log_level == "INFO"

    def test_write_creates_file_and_parent_dirs(self, tmp_path: Path) -> None:
        config_file = tmp_path / "sub" / "dir" / "config.toml"
        ConfigService(config_path=config_file).write_global(GlobalConfigUpdateRequest(log_level="DEBUG"))
        assert toml.loads(config_file.read_text()) == {"log_level": "DEBUG"}

    def test_write_keeps_notifications_and_other_sections(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            'log_level = "INFO"\n'
            'unknown_key = "kept"\n'
            '[notifications.webpush]\nenabled = true\nmin_severity = "error"\n'
            '[notifications.host_native]\nenabled = false\n'
        )
        before = toml.loads(config_file.read_text())

        ConfigService(config_path=config_file).write_global(GlobalConfigUpdateRequest(log_level="ERROR"))

        after = toml.loads(config_file.read_text())
        assert after == {**before, "log_level": "ERROR"}

    def test_empty_update_changes_nothing(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        service = ConfigService(config_path=config_file)
        service.write_global(GlobalConfigUpdateRequest(log_level="WARNING"))
        assert service.write_global(GlobalConfigUpdateRequest()).log_level == "WARNING"
        assert toml.loads(config_file.read_text()) == {"log_level": "WARNING"}

    def test_unknown_log_level_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            GlobalConfigUpdateRequest(log_level="LOUD")

    def test_invalid_toml_raises_config_error(self, tmp_path: Path) -> None:
        config_file = tmp_path / "bad.toml"
        config_file.write_text("this is not valid = = toml [[[")
        service = ConfigService(config_path=config_file)
        with pytest.raises(ConfigError):
            service.read_global()
        with pytest.raises(ConfigError):
            service.write_global(GlobalConfigUpdateRequest(log_level="DEBUG"))
        assert config_file.read_text() == "this is not valid = = toml [[["

    def test_unwritable_location_raises_config_error(self, tmp_path: Path) -> None:
        blocker = tmp_path / "file"
        blocker.write_text("")
        service = ConfigService(config_path=blocker / "config.toml")
        with pytest.raises(ConfigError):
            service.write_global(GlobalConfigUpdateRequest(log_level="DEBUG"))


def test_config_path_follows_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OMNISYNC_CONFIG_PATH moves config.toml, like the other data files."""
    target = tmp_path / "elsewhere" / "config.toml"
    monkeypatch.setenv("OMNISYNC_CONFIG_PATH", str(target))
    service = ConfigService()
    assert service.config_path == target
    service.write_global(GlobalConfigUpdateRequest(log_level="ERROR"))
    assert toml.loads(target.read_text())["log_level"] == "ERROR"
    # An explicit path still wins.
    assert ConfigService(tmp_path / "own.toml").config_path == tmp_path / "own.toml"


def test_config_path_is_part_of_the_data_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.services.path_guard import data_dirs

    monkeypatch.setenv("OMNISYNC_CONFIG_PATH", str(tmp_path / "conf" / "config.toml"))
    assert (tmp_path / "conf").resolve() in data_dirs()
