"""Property-based tests for the Remote Setup Wizard.

Uses Hypothesis to verify universal correctness properties
across the provider registry and wizard logic.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.api.schemas import AuthType, FieldType
from backend.tests.auth import AUTH_HEADERS
from backend.services.provider_registry import (
    PROVIDERS,
    ProviderInfo,
    get_providers,
    validate_remote_name,
)
from backend.services.wizard_sessions import WizardSessionManager


# Feature: remote-setup-wizard, Property 1: Provider registry structural completeness
# Validates: Requirements 1.1, 1.4, 1.5, 8.4, 10.2
class TestProviderRegistryStructuralCompleteness:
    """Every provider in the registry must have complete, valid structure."""

    @settings(max_examples=100)
    @given(provider=st.sampled_from(get_providers()))
    def test_provider_has_non_empty_id(self, provider: ProviderInfo) -> None:
        assert isinstance(provider.id, str)
        assert len(provider.id) > 0

    @settings(max_examples=100)
    @given(provider=st.sampled_from(get_providers()))
    def test_provider_has_non_empty_display_name(self, provider: ProviderInfo) -> None:
        assert isinstance(provider.display_name, str)
        assert len(provider.display_name) > 0

    @settings(max_examples=100)
    @given(provider=st.sampled_from(get_providers()))
    def test_provider_has_non_empty_icon(self, provider: ProviderInfo) -> None:
        assert isinstance(provider.icon, str)
        assert len(provider.icon) > 0

    @settings(max_examples=100)
    @given(provider=st.sampled_from(get_providers()))
    def test_provider_has_valid_auth_type(self, provider: ProviderInfo) -> None:
        assert provider.auth_type in (AuthType.KEY, AuthType.OAUTH)

    @settings(max_examples=100)
    @given(provider=st.sampled_from(get_providers()))
    def test_provider_has_non_empty_default_name(self, provider: ProviderInfo) -> None:
        assert isinstance(provider.default_name, str)
        assert len(provider.default_name) > 0

    @settings(max_examples=100)
    @given(provider=st.sampled_from(get_providers()))
    def test_key_based_provider_has_at_least_one_field(self, provider: ProviderInfo) -> None:
        if provider.auth_type == AuthType.KEY:
            assert len(provider.fields) > 0

    @settings(max_examples=100)
    @given(provider=st.sampled_from(get_providers()))
    def test_oauth_provider_has_defined_fields_list(self, provider: ProviderInfo) -> None:
        if provider.auth_type == AuthType.OAUTH:
            assert isinstance(provider.fields, list)

    @settings(max_examples=100)
    @given(provider=st.sampled_from(get_providers()))
    def test_all_fields_have_non_empty_name(self, provider: ProviderInfo) -> None:
        for f in provider.fields:
            assert isinstance(f.name, str)
            assert len(f.name) > 0, f"Field in {provider.id} has empty name"

    @settings(max_examples=100)
    @given(provider=st.sampled_from(get_providers()))
    def test_all_fields_have_non_empty_label(self, provider: ProviderInfo) -> None:
        for f in provider.fields:
            assert isinstance(f.label, str)
            assert len(f.label) > 0, f"Field '{f.name}' in {provider.id} has empty label"

    @settings(max_examples=100)
    @given(provider=st.sampled_from(get_providers()))
    def test_all_fields_have_valid_field_type(self, provider: ProviderInfo) -> None:
        for f in provider.fields:
            assert f.field_type in FieldType, (
                f"Field '{f.name}' in {provider.id} has invalid field_type: {f.field_type}"
            )
            # A select field offers its values, and its default is one of them.
            if f.field_type == FieldType.SELECT:
                assert f.options, f"Select field '{f.name}' in {provider.id} has no options"
                assert not f.default or f.default in f.options

    @settings(max_examples=100)
    @given(provider=st.sampled_from(get_providers()))
    def test_all_fields_have_boolean_required(self, provider: ProviderInfo) -> None:
        for f in provider.fields:
            assert isinstance(f.required, bool), (
                f"Field '{f.name}' in {provider.id} has non-boolean required: {f.required}"
            )

    @settings(max_examples=100)
    @given(provider=st.sampled_from(get_providers()))
    def test_all_fields_have_non_empty_help_text(self, provider: ProviderInfo) -> None:
        for f in provider.fields:
            assert isinstance(f.help_text, str)
            assert len(f.help_text) > 0, (
                f"Field '{f.name}' in {provider.id} has empty help_text"
            )


# Feature: remote-setup-wizard, Property 13: Remote name validation
# Validates: Requirements 8.2
class TestRemoteNameValidation:
    """For any string, validate_remote_name returns True iff it matches ^[a-zA-Z0-9_][a-zA-Z0-9_-]*$ (no leading '-', which rclone would read as a flag)."""

    @settings(max_examples=100)
    @given(name=st.text())
    def test_remote_name_validation_matches_regex(self, name: str) -> None:
        """**Validates: Requirements 8.2**"""
        import re

        pattern = re.compile(r'^[a-zA-Z0-9_][a-zA-Z0-9_-]*$')
        expected = bool(name) and bool(pattern.match(name))
        assert validate_remote_name(name) == expected, (
            f"validate_remote_name({name!r}) returned {validate_remote_name(name)}, expected {expected}"
        )


# Feature: remote-setup-wizard, Property 10: Session ID uniqueness
# Validates: Requirements 7.1
class TestSessionIDUniqueness:
    """For any two wizard sessions created by the session manager (even if
    created for the same provider), their session IDs must be distinct."""

    @settings(max_examples=100)
    @given(
        num_sessions=st.integers(min_value=2, max_value=10),
        provider_id=st.sampled_from(
            ["drive", "dropbox", "onedrive", "s3", "b2", "sftp", "ftp"]
        ),
    )
    @pytest.mark.asyncio
    async def test_all_session_ids_are_unique(
        self, num_sessions: int, provider_id: str
    ) -> None:
        """**Validates: Requirements 7.1**"""
        manager = WizardSessionManager(max_sessions=num_sessions)
        sessions = []
        for _ in range(num_sessions):
            session = await manager.create_session(provider_id)
            sessions.append(session)

        session_ids = [s.session_id for s in sessions]
        assert len(session_ids) == len(set(session_ids)), (
            f"Duplicate session IDs found: {session_ids}"
        )


# Feature: remote-setup-wizard, Property 9: Expired session cleanup
# Validates: Requirements 4.7, 7.2
class TestExpiredSessionCleanup:
    """For any wizard session whose created_at timestamp is more than 10 minutes
    in the past, after cleanup_expired() runs, the session must no longer exist
    in the session manager and any associated subprocess must be terminated."""

    @settings(max_examples=100)
    @given(
        provider_id=st.sampled_from(
            ["drive", "dropbox", "onedrive", "s3", "b2", "sftp", "ftp"]
        ),
        extra_minutes=st.integers(min_value=1, max_value=60),
    )
    @pytest.mark.asyncio
    async def test_expired_sessions_are_cleaned_up(
        self, provider_id: str, extra_minutes: int
    ) -> None:
        """**Validates: Requirements 4.7, 7.2**"""
        manager = WizardSessionManager(max_sessions=5)
        session = await manager.create_session(provider_id)

        # Attach a mock process to verify termination
        mock_process = MagicMock()
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock(return_value=0)
        session.process = mock_process

        # Set timestamps to be expired (more than 10 minutes ago)
        expired_time = datetime.now(timezone.utc) - timedelta(
            minutes=10 + extra_minutes
        )
        session.created_at = expired_time
        session.updated_at = expired_time

        await manager.cleanup_expired()

        assert manager.get_session(session.session_id) is None, (
            f"Session {session.session_id} should have been cleaned up"
        )
        assert manager.active_count == 0, (
            "No sessions should remain after cleanup"
        )
        mock_process.terminate.assert_called_once()


# Feature: remote-setup-wizard, Property 11: Cancel terminates and removes session
# Validates: Requirements 7.3
class TestCancelTerminatesAndRemovesSession:
    """For any active wizard session, calling cancel_session(session_id) must
    remove the session from the session manager. After cancellation,
    get_session(session_id) must return None."""

    @settings(max_examples=100)
    @given(
        provider_id=st.sampled_from(
            ["drive", "dropbox", "onedrive", "s3", "b2", "sftp", "ftp"]
        ),
    )
    @pytest.mark.asyncio
    async def test_cancel_removes_session(
        self, provider_id: str
    ) -> None:
        """**Validates: Requirements 7.3**"""
        manager = WizardSessionManager(max_sessions=5)
        session = await manager.create_session(provider_id)
        session_id = session.session_id

        # Verify session exists before cancellation
        assert manager.get_session(session_id) is not None

        await manager.cancel_session(session_id)

        assert manager.get_session(session_id) is None, (
            f"Session {session_id} should be None after cancellation"
        )


# Feature: remote-setup-wizard, Property 6: Rclone authorize output parsing
# Validates: Requirements 4.2, 4.5
class TestRcloneAuthorizeOutputParsing:
    """For any string containing a URL matching http(s)://... in rclone authorize
    output format, the auth URL parser must extract a valid URL. For any string
    containing a JSON token block {"access_token":...}, the token parser must
    extract valid JSON that can be deserialized."""

    @settings(max_examples=100)
    @given(
        prefix=st.text(max_size=50),
        suffix=st.text(max_size=50),
        scheme=st.sampled_from(["http", "https"]),
        domain=st.from_regex(r'[a-z][a-z0-9]{2,15}\.[a-z]{2,4}', fullmatch=True),
        path=st.from_regex(r'/[a-z0-9/]{0,30}', fullmatch=True),
    )
    def test_auth_url_extracted_from_rclone_output(
        self, prefix: str, suffix: str, scheme: str, domain: str, path: str
    ) -> None:
        """**Validates: Requirements 4.2**"""
        from backend.services.rclone import parse_auth_url

        url = f"{scheme}://{domain}{path}"
        # Simulate rclone authorize output format
        output = f"{prefix}\nIf your browser doesn't open automatically go to the following link: {url}\n{suffix}"
        result = parse_auth_url(output)
        assert result is not None, f"Failed to extract URL from output containing {url}"
        assert result.startswith("http"), f"Extracted URL doesn't start with http: {result}"
        assert domain in result, f"Extracted URL doesn't contain domain {domain}: {result}"

    @settings(max_examples=100)
    @given(
        prefix=st.text(max_size=50),
        suffix=st.text(max_size=50),
        access_token=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-"),
            min_size=5,
            max_size=40,
        ),
        token_type=st.sampled_from(["Bearer", "bearer", "MAC"]),
    )
    def test_auth_token_extracted_from_rclone_output(
        self, prefix: str, suffix: str, access_token: str, token_type: str
    ) -> None:
        """**Validates: Requirements 4.5**"""
        import json as json_mod

        from backend.services.rclone import parse_auth_token

        token_obj = {
            "access_token": access_token,
            "token_type": token_type,
            "expiry": "2024-01-01T00:00:00Z",
        }
        token_json = json_mod.dumps(token_obj)
        # Simulate rclone authorize output format with token block
        output = (
            f"{prefix}\n"
            f"Paste the following into your remote machine --->\n"
            f"{token_json}\n"
            f"<---End paste\n"
            f"{suffix}"
        )
        result = parse_auth_token(output)
        assert result is not None, f"Failed to extract token from output containing {token_json}"
        parsed = json_mod.loads(result)
        assert parsed["access_token"] == access_token
        assert parsed["token_type"] == token_type

    @settings(max_examples=100)
    @given(text=st.text(max_size=200).filter(lambda t: "http" not in t.lower()))
    def test_no_url_returns_none(self, text: str) -> None:
        """**Validates: Requirements 4.2**"""
        from backend.services.rclone import parse_auth_url

        result = parse_auth_url(text)
        assert result is None, f"Should return None for text without URL, got: {result}"

    @settings(max_examples=100)
    @given(text=st.text(max_size=200).filter(lambda t: "access_token" not in t))
    def test_no_token_returns_none(self, text: str) -> None:
        """**Validates: Requirements 4.5**"""
        from backend.services.rclone import parse_auth_token

        result = parse_auth_token(text)
        assert result is None, f"Should return None for text without token, got: {result}"


# Feature: remote-setup-wizard, Property 8: Custom OAuth credentials forwarding
# Validates: Requirements 4.8
class TestCustomOAuthCredentialsForwarding:
    """For any non-empty client_id and client_secret values provided in an
    authorize request, the constructed rclone authorize command arguments
    must contain both values."""

    @settings(max_examples=100)
    @given(
        remote_type=st.sampled_from(["drive", "dropbox", "onedrive"]),
        client_id=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-."),
            min_size=1,
            max_size=50,
        ),
        client_secret=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-."),
            min_size=1,
            max_size=50,
        ),
    )
    @pytest.mark.asyncio
    async def test_oauth_credentials_in_authorize_command(
        self, remote_type: str, client_id: str, client_secret: str
    ) -> None:
        """**Validates: Requirements 4.8**"""
        captured_cmd: list[str] = []

        async def mock_create_subprocess_exec(
            *args: str, **kwargs: object
        ) -> MagicMock:
            captured_cmd.extend(args)
            mock_proc = MagicMock()
            mock_proc.stdout = AsyncMock()
            mock_proc.stderr = AsyncMock()
            mock_proc.pid = 12345
            return mock_proc

        from unittest.mock import patch

        from backend.services.rclone import RcloneService

        service = RcloneService()

        extra_args = [
            f"--drive-client-id={client_id}",
            f"--drive-client-secret={client_secret}",
        ]

        with patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess_exec):
            await service.authorize(remote_type, extra_args=extra_args)

        cmd_str = " ".join(captured_cmd)
        assert client_id in cmd_str, (
            f"client_id '{client_id}' not found in command: {captured_cmd}"
        )
        assert client_secret in cmd_str, (
            f"client_secret '{client_secret}' not found in command: {captured_cmd}"
        )
        assert "rclone" in captured_cmd[0]
        assert "authorize" in captured_cmd
        assert remote_type in captured_cmd


# Feature: remote-setup-wizard, Property 12: Duplicate remote name rejection
# Validates: Requirements 6.3
class TestDuplicateRemoteNameRejection:
    """For any remote name that already exists in the rclone remote list,
    create_remote() with that name must raise a ValueError and must not
    create or overwrite the existing remote."""

    @settings(max_examples=100)
    @given(
        remote_name=st.from_regex(r'[a-zA-Z][a-zA-Z0-9_-]{0,19}', fullmatch=True),
        remote_type=st.sampled_from(["drive", "s3", "dropbox", "sftp", "b2", "ftp", "onedrive"]),
    )
    @pytest.mark.asyncio
    async def test_duplicate_name_raises_value_error(
        self, remote_name: str, remote_type: str
    ) -> None:
        """**Validates: Requirements 6.3**"""
        from unittest.mock import patch

        from backend.api.schemas import RemoteResponse
        from backend.services.rclone import RcloneService

        service = RcloneService()

        # Mock list_remotes to return a list containing the duplicate name
        existing_remotes = [RemoteResponse(name=remote_name, type=remote_type)]

        with patch.object(
            service, "list_remotes", new_callable=AsyncMock, return_value=existing_remotes
        ):
            with patch.object(service, "_run", new_callable=AsyncMock) as mock_run:
                with pytest.raises(ValueError, match="already exists"):
                    await service.create_remote(
                        name=remote_name,
                        remote_type=remote_type,
                        params={"key": "value"},
                    )
                # Verify _run was never called (remote was not created)
                mock_run.assert_not_called()


# Feature: remote-setup-wizard, Property 7: Rclone error propagation
# Validates: Requirements 3.5, api-security R9
class TestRcloneErrorPropagation:
    """For any non-zero exit code and any non-empty stderr string from an
    rclone subprocess, the wizard API response must have a non-2xx status
    code and a generic string detail that does not contain the stderr
    message; the stderr message goes to the server log instead."""

    @settings(max_examples=100)
    @given(
        exit_code=st.integers(min_value=1, max_value=255),
        stderr_msg=st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N", "P", "Z"),
                blacklist_characters="\x00",
            ),
            min_size=1,
            max_size=100,
        ),
        remote_name=st.from_regex(r"[a-zA-Z][a-zA-Z0-9_-]{0,9}", fullmatch=True),
        provider_id=st.sampled_from(["s3", "b2", "sftp", "ftp"]),
    )
    @pytest.mark.asyncio
    async def test_rclone_error_propagates_to_api_response(
        self,
        exit_code: int,
        stderr_msg: str,
        remote_name: str,
        provider_id: str,
    ) -> None:
        """**Validates: Requirements 3.5**"""
        import logging

        from httpx import ASGITransport, AsyncClient


        from backend.api.routes import wizard
        from backend.exceptions import RcloneError
        from backend.main import app

        # Create a mock rclone service whose create_remote raises RcloneError
        mock_rclone = AsyncMock()
        mock_rclone.create_remote = AsyncMock(
            side_effect=RcloneError(
                f"rclone failed (exit {exit_code}): {stderr_msg}"
            )
        )

        records: list[logging.LogRecord] = []

        class _Collect(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = _Collect(level=logging.DEBUG)
        wizard_logger = logging.getLogger("backend.api.routes.wizard")
        wizard_logger.addHandler(handler)
        original_service = wizard._rclone_service
        wizard._rclone_service = mock_rclone
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test", headers=AUTH_HEADERS
            ) as client:
                response = await client.post(
                    "/wizard/create",
                    json={
                        "name": remote_name,
                        "provider_id": provider_id,
                        # The required fields, so the request reaches rclone.
                        "params": {f.name: "x" for f in PROVIDERS[provider_id].fields if f.required},
                    },
                )

            # Must be non-2xx
            assert response.status_code >= 400, (
                f"Expected non-2xx status for rclone error, got {response.status_code}"
            )

            # The client gets a fixed message; rclone's stderr is logged.
            detail = response.json().get("detail", "")
            assert isinstance(detail, str)
            assert detail == "Failed to create the remote. The OmniSync log has the details."
            logged = "\n".join(r.getMessage() for r in records)
            assert stderr_msg in logged, (
                f"stderr message {stderr_msg!r} not found in the log: {logged!r}"
            )
        finally:
            wizard_logger.removeHandler(handler)
            wizard._rclone_service = original_service
