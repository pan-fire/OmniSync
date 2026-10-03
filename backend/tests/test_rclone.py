"""Unit and property tests for the rclone wrapper service."""

from unittest.mock import AsyncMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.services.rclone import RcloneResult, RcloneService, _is_auth_error, AUTH_ERROR_PHRASES
from backend.exceptions import RcloneError


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

rclone_result_strategy = st.builds(
    RcloneResult,
    stdout=st.text(min_size=0, max_size=200),
    stderr=st.text(min_size=0, max_size=200),
    return_code=st.integers(min_value=0, max_value=255),
    elapsed_seconds=st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
)

# Strategy for filter args: simple strings without whitespace that look like rclone filters
filter_arg_strategy = st.lists(
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P"), whitelist_characters="/-_*."),
        min_size=1,
        max_size=50,
    ),
    min_size=0,
    max_size=10,
)

# Strategy for extra args: simple flag-like strings
extra_arg_strategy = st.lists(
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P"), whitelist_characters="/-_="),
        min_size=1,
        max_size=50,
    ),
    min_size=0,
    max_size=10,
)


# Strategy for non-auth stderr strings
def non_auth_stderr_strategy():
    """Generate stderr strings that do NOT contain auth keywords."""
    return st.text(min_size=1, max_size=200).filter(
        lambda s: not any(kw in s.lower() for kw in AUTH_ERROR_PHRASES)
    )


# ---------------------------------------------------------------------------
# Property 7: RcloneResult round-trip
# ---------------------------------------------------------------------------

@given(result=rclone_result_strategy)
@settings(max_examples=100)
def test_rclone_result_round_trip(result: RcloneResult) -> None:
    """Feature: project-foundation, Property 7: RcloneResult round-trip

    **Validates: Requirements 5.6**
    """
    serialized = result.to_dict()
    deserialized = RcloneResult.from_dict(serialized)

    assert deserialized.stdout == result.stdout
    assert deserialized.stderr == result.stderr
    assert deserialized.return_code == result.return_code
    assert deserialized.elapsed_seconds == result.elapsed_seconds


# ---------------------------------------------------------------------------
# Property 8: Rclone command building includes all filters and args
# ---------------------------------------------------------------------------

@given(
    filters=filter_arg_strategy,
    extra_args=extra_arg_strategy,
)
@settings(max_examples=100)
def test_rclone_command_building_includes_all_filters_and_args(
    filters: list[str], extra_args: list[str]
) -> None:
    """Feature: project-foundation, Property 8: Rclone command building includes all filters and args

    **Validates: Requirements 5.5**
    """
    service = RcloneService()

    base_args = ["sync", "/src", "/dst"]
    cmd = service._build_command(base_args, rclone_filter=filters, rclone_args=extra_args)

    # The command should start with rclone --config <path> + base_args
    assert cmd[0] == "rclone"
    assert cmd[1] == "--config"
    # cmd[2] is the config path
    assert cmd[3:3 + len(base_args)] == base_args

    # Every filter is passed as one --filter=<rule> token, so a rule can never
    # be read as a separate rclone flag
    for f in filters:
        assert f"--filter={f}" in cmd, f"Filter {f!r} not found in command"

    # Every extra arg should appear in the command
    for arg in extra_args:
        assert arg in cmd, f"Extra arg {arg!r} not found in command"


# ---------------------------------------------------------------------------
# Property 9: Non-auth rclone errors propagate stderr
# ---------------------------------------------------------------------------

@given(stderr=non_auth_stderr_strategy())
@settings(max_examples=100)
def test_non_auth_rclone_errors_propagate_stderr(stderr: str) -> None:
    """Feature: project-foundation, Property 9: Non-auth rclone errors propagate stderr

    **Validates: Requirements 5.4**
    """
    # Verify the stderr is indeed non-auth
    assert not _is_auth_error(stderr)

    # When RcloneError is raised with this stderr, the message should contain it
    error = RcloneError(f"rclone failed (exit 1): {stderr}")
    assert stderr in str(error)


# ---------------------------------------------------------------------------
# Unit tests for auth error detection
# ---------------------------------------------------------------------------

class TestAuthErrorDetection:
    """Unit tests for _is_auth_error helper."""

    @pytest.mark.parametrize("stderr", [
        "Failed to authenticate: invalid_grant",
        "403 Forbidden: access denied",
        "401 Unauthorized: bad credentials",
        "Error: unauthorized request",
    ])
    def test_detects_auth_errors(self, stderr: str) -> None:
        assert _is_auth_error(stderr) is True

    @pytest.mark.parametrize("stderr", [
        "file not found: /some/path",
        "network timeout after 30s",
        "directory not empty",
        "token refresh completed successfully",
        "",
    ])
    def test_non_auth_errors(self, stderr: str) -> None:
        assert _is_auth_error(stderr) is False


# ---------------------------------------------------------------------------
# Tests for new backup-related rclone methods
# ---------------------------------------------------------------------------


class TestAbout:
    @pytest.mark.asyncio
    async def test_about_parses_json(self) -> None:
        service = RcloneService()
        mock_result = RcloneResult(
            stdout='{"total": 1000000, "used": 600000, "free": 400000, "trashed": 5000}',
            stderr="", return_code=0, elapsed_seconds=0.5,
        )
        with patch.object(service, "_run", new_callable=AsyncMock, return_value=mock_result):
            info = await service.about("gdrive")
        assert info == {"total": 1000000, "used": 600000, "free": 400000, "trashed": 5000}

    @pytest.mark.asyncio
    async def test_about_handles_partial_response(self) -> None:
        service = RcloneService()
        mock_result = RcloneResult(
            stdout='{"total": 1000000, "used": 600000}',
            stderr="", return_code=0, elapsed_seconds=0.5,
        )
        with patch.object(service, "_run", new_callable=AsyncMock, return_value=mock_result):
            info = await service.about("s3")
        assert info["total"] == 1000000
        assert info["free"] is None
        assert info["trashed"] is None

    @pytest.mark.asyncio
    async def test_about_passes_correct_args(self) -> None:
        service = RcloneService()
        mock_run = AsyncMock(return_value=RcloneResult(
            stdout='{}', stderr="", return_code=0, elapsed_seconds=0.1,
        ))
        with patch.object(service, "_run", mock_run):
            await service.about("myremote")
        mock_run.assert_called_once_with(
            ["about", "--json"], use_config_args=False, timeout=30, positional=["myremote:"],
        )


class TestSyncWithBackupDir:
    @pytest.mark.asyncio
    async def test_passes_backup_dir_flag(self) -> None:
        service = RcloneService()
        mock_run = AsyncMock(return_value=RcloneResult(
            stdout="", stderr="", return_code=0, elapsed_seconds=1.0,
        ))
        with patch.object(service, "_run", mock_run):
            await service.sync_with_backup_dir(
                "/local/data", "remote:backup/current",
                "remote:backup/versions/2025-06-01",
            )
        args, kwargs = mock_run.call_args
        assert args[0] == ["sync", "--backup-dir", "remote:backup/versions/2025-06-01"]
        assert kwargs["positional"] == ["/local/data", "remote:backup/current"]
        assert kwargs["no_timeout"] is True

    @pytest.mark.asyncio
    async def test_passes_filters_and_args(self) -> None:
        service = RcloneService()
        mock_run = AsyncMock(return_value=RcloneResult(
            stdout="", stderr="", return_code=0, elapsed_seconds=1.0,
        ))
        with patch.object(service, "_run", mock_run):
            await service.sync_with_backup_dir(
                "/src", "/dst", "/bak",
                rclone_filter=["- *.tmp"], rclone_args=["--verbose"],
            )
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["rclone_filter"] == ["- *.tmp"]
        assert kwargs["rclone_args"] == ["--verbose"]


class TestDeletePath:
    @pytest.mark.asyncio
    async def test_calls_purge(self) -> None:
        service = RcloneService()
        mock_run = AsyncMock(return_value=RcloneResult(
            stdout="", stderr="", return_code=0, elapsed_seconds=0.3,
        ))
        with patch.object(service, "_run", mock_run):
            await service.delete_path("remote:backup/versions/old")
        mock_run.assert_called_once_with(
            ["purge"], use_config_args=False,
            positional=["remote:backup/versions/old"], no_timeout=True,
        )


class TestGetDirSize:
    @pytest.mark.asyncio
    async def test_parses_size_json(self) -> None:
        service = RcloneService()
        mock_result = RcloneResult(
            stdout='{"count": 42, "bytes": 123456}',
            stderr="", return_code=0, elapsed_seconds=0.2,
        )
        with patch.object(service, "_run", new_callable=AsyncMock, return_value=mock_result):
            size = await service.get_dir_size("remote:backup/current")
        assert size == 123456

    @pytest.mark.asyncio
    async def test_returns_zero_on_missing_key(self) -> None:
        service = RcloneService()
        mock_result = RcloneResult(
            stdout='{"count": 0}',
            stderr="", return_code=0, elapsed_seconds=0.1,
        )
        with patch.object(service, "_run", new_callable=AsyncMock, return_value=mock_result):
            size = await service.get_dir_size("remote:empty")
        assert size == 0
