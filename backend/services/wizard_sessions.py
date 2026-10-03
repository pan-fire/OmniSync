"""Wizard session manager for the Remote Setup Wizard.

Manages in-memory OAuth sessions with subprocess tracking and cleanup.
Sessions are stored in a dict keyed by session ID. This is acceptable
because OmniSync is a single-user localhost tool with max 3 concurrent sessions.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend.exceptions import WizardSessionError

logger = logging.getLogger(__name__)

MAX_SESSIONS = 3
SESSION_EXPIRY_SECONDS = 600  # 10 minutes


@dataclass
class WizardSession:
    """Tracks the state of an in-progress wizard OAuth flow."""

    session_id: str
    provider_id: str
    status: str = "pending"  # "pending", "completed", "failed"
    auth_url: str | None = None
    token: str | None = None
    error: str | None = None
    # Stable code of a failure the web UI explains (OAuthFlowError.code).
    error_code: str | None = None
    process: asyncio.subprocess.Process | None = None
    client_id: str | None = None
    client_secret: str | None = None
    redirect_uri: str | None = None
    # PKCE verifier for the token exchange; never leaves the server.
    code_verifier: str | None = None
    # Set once an authorization code was taken for this session, so a
    # second callback cannot start another exchange.
    code_used: bool = False
    # Reconnect: the existing remote whose token this sign-in replaces
    # (POST /wizard/reconnect); None when it creates a new remote.
    remote_name: str | None = None
    # Reconnect with new app credentials: store client_id/client_secret too.
    store_client: bool = False
    # Settings the sign-in found that go into the remote's config with the
    # token (OneDrive: drive_id and drive_type).
    extra_config: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class WizardSessionManager:
    """Manages wizard sessions with concurrency limits and expiry cleanup."""

    def __init__(
        self,
        max_sessions: int = MAX_SESSIONS,
        expiry_seconds: int = SESSION_EXPIRY_SECONDS,
    ) -> None:
        self._sessions: dict[str, WizardSession] = {}
        self._max_sessions = max_sessions
        self._expiry_seconds = expiry_seconds

    async def create_session(
        self,
        provider_id: str,
        extra_args: list[str] | None = None,
    ) -> WizardSession:
        """Create a new wizard session.

        Performs lazy cleanup of expired sessions first, then checks
        the concurrency limit before creating.

        Raises WizardSessionError if max concurrent sessions exceeded.
        """
        await self.cleanup_expired()

        if len(self._sessions) >= self._max_sessions:
            raise WizardSessionError(
                "Maximum concurrent wizard sessions reached"
            )

        session_id = str(uuid.uuid4())
        session = WizardSession(
            session_id=session_id,
            provider_id=provider_id,
        )
        self._sessions[session_id] = session
        logger.info("Created wizard session %s for provider %s", session_id, provider_id)
        return session

    def get_session(self, session_id: str) -> WizardSession | None:
        """Return a session by ID, or None if not found.

        Triggers lazy cleanup of expired sessions before lookup.
        Note: cleanup_expired is async, so we do synchronous expiry
        check here and defer full cleanup (with process termination)
        to the next create_session or explicit cleanup call.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return None

        # Check if this specific session is expired
        now = datetime.now(timezone.utc)
        elapsed = (now - session.updated_at).total_seconds()
        if elapsed > self._expiry_seconds:
            return None

        return session

    async def cancel_session(self, session_id: str) -> None:
        """Cancel and remove a session, terminating any running subprocess."""
        session = self._sessions.pop(session_id, None)
        if session is None:
            return

        await self._terminate_process(session)
        logger.info("Cancelled wizard session %s", session_id)

    async def cleanup_expired(self) -> None:
        """Remove all sessions that have exceeded the expiry timeout.

        Terminates any associated subprocesses.
        """
        now = datetime.now(timezone.utc)
        expired_ids: list[str] = []

        for sid, session in self._sessions.items():
            elapsed = (now - session.updated_at).total_seconds()
            if elapsed > self._expiry_seconds:
                expired_ids.append(sid)

        for sid in expired_ids:
            session = self._sessions.pop(sid)
            await self._terminate_process(session)
            logger.info("Cleaned up expired wizard session %s", sid)

    @property
    def active_count(self) -> int:
        """Return the number of active (non-expired) sessions."""
        return len(self._sessions)

    async def _terminate_process(self, session: WizardSession) -> None:
        """Terminate the subprocess associated with a session, if any."""
        if session.process is None:
            return

        try:
            session.process.terminate()
            # Give it a moment to exit gracefully
            try:
                await asyncio.wait_for(session.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                session.process.kill()
                logger.warning(
                    "Had to kill process for session %s", session.session_id
                )
        except ProcessLookupError:
            # Process already exited
            pass
