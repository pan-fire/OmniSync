"""A remote's credentials: OAuth token refresh, reachability checks and rclone authorize."""

from __future__ import annotations

import asyncio
import configparser
import json
import re

import httpx

from backend.exceptions import RcloneAuthError, RcloneError
from backend.services import remote_auth
from backend.services.rclone.common import logger
from backend.services.rclone.errors import _is_rate_limit_error
from backend.services.rclone.process import RcloneBase

AUTH_URL_PATTERN = re.compile(r'(https?://[^\s"\'<>]+)')


def parse_auth_url(output: str) -> str | None:
    """Extract the auth URL from rclone authorize stderr output."""
    for line in output.splitlines():
        match = AUTH_URL_PATTERN.search(line)
        if match:
            url = match.group(1).rstrip('.,;:)')
            if url.startswith("http"):
                return url
    return None


def parse_auth_token(output: str) -> str | None:
    """Extract the JSON token from rclone authorize stdout output.

    rclone outputs the token as a JSON block like:
    Paste the following into your remote machine --->
    {"access_token":"...","token_type":"Bearer",...}
    <---End paste
    """
    for line in output.splitlines():
        line = line.strip()
        if line.startswith('{') and 'access_token' in line:
            try:
                json.loads(line)  # validate it's valid JSON
                return line
            except json.JSONDecodeError:
                continue
    return None


class AuthMixin(RcloneBase):
    """Keeps OAuth tokens fresh, checks that a remote answers, starts rclone authorize."""

    async def _ensure_fresh_token(self, remote_name: str) -> None:
        """Pre-refresh an OAuth token if it's expired or close to expiry.

        Reads the token from the rclone config, checks the expiry field,
        and refreshes via httpx if needed. This prevents rclone from
        attempting an interactive token refresh.

        A remote without a client_id (made with rclone's built-in app) is
        left to rclone, which refreshes it with its own app: OmniSync has
        no built-in credentials to refresh it with.
        """
        cfg = configparser.RawConfigParser()
        cfg.optionxform = str  # type: ignore[assignment]  # preserve key casing
        try:
            cfg.read(self.rclone_config_path)
        except Exception:
            return

        if not cfg.has_section(remote_name):
            return

        remote_type = cfg.get(remote_name, "type", fallback="")
        token_str = cfg.get(remote_name, "token", fallback="")

        if not token_str or remote_type not in ("drive", "dropbox", "onedrive"):
            return
        if not cfg.get(remote_name, "client_id", fallback=""):
            return

        try:
            token = json.loads(token_str)
        except json.JSONDecodeError:
            return

        # Check if token is expired or expires within 5 minutes
        expiry_str = token.get("expiry", "")
        if expiry_str:
            try:
                from datetime import datetime, timezone, timedelta
                # Parse rclone expiry format: 2026-03-10T14:30:00.000Z
                expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                if expiry > now + timedelta(minutes=5):
                    # Token still valid, no refresh needed
                    return
                logger.info("_ensure_fresh_token: '%s' token expires at %s, refreshing", remote_name, expiry_str)
            except (ValueError, TypeError):
                # Can't parse expiry, try refresh anyway
                pass

        # Token is expired or unparseable — refresh it
        await self._refresh_token(remote_name, remote_type, token, cfg)

    async def check_installed(self) -> bool:
        """Verify rclone binary is available."""
        try:
            result = await self._run(["version"], use_config_args=False)
            return result.return_code == 0
        except (RcloneError, FileNotFoundError, OSError):
            return False

    async def check_remote(self, remote: str) -> bool:
        """Verify remote accessibility by calling the provider API directly.

        Reads the token from the rclone config file and makes a lightweight
        API call to the provider. This bypasses the rclone binary entirely,
        avoiding issues where rclone hangs in Docker environments (e.g. Go
        binary DNS/TLS issues in slim containers).

        Falls back to rclone lsd if the remote type is unknown or has no token.
        """
        logger.info("check_remote: testing '%s' with config '%s'", remote, self.rclone_config_path)

        # Read the remote config
        config = configparser.RawConfigParser()
        config.optionxform = str  # type: ignore[assignment]  # preserve key casing
        try:
            config.read(self.rclone_config_path)
        except Exception as e:
            logger.warning("check_remote: failed to read config: %s", e)
            return False

        if not config.has_section(remote):
            logger.warning("check_remote: section '%s' not found in config", remote)
            return False

        remote_type = config.get(remote, "type", fallback="")
        token_str = config.get(remote, "token", fallback="")

        # Try Python-based API check for known providers with tokens
        if token_str and remote_type in ("drive", "dropbox", "onedrive"):
            return await self._check_remote_via_api(remote, remote_type, token_str, config)

        # Fallback to rclone lsd for unknown providers or key-based auth
        return await self._check_remote_via_rclone(remote)

    async def _check_remote_via_api(
        self, remote: str, remote_type: str, token_str: str, config: configparser.RawConfigParser
    ) -> bool:
        """Check remote by calling the provider API directly with httpx."""
        try:
            token = json.loads(token_str)
        except json.JSONDecodeError:
            logger.warning("check_remote: invalid token JSON for '%s'", remote)
            return False

        access_token = token.get("access_token", "")
        refresh_token = token.get("refresh_token", "")

        if not access_token:
            logger.warning("check_remote: no access_token for '%s'", remote)
            return False

        # Provider-specific API endpoints for a lightweight check
        api_checks: dict[str, str] = {
            "drive": "https://www.googleapis.com/drive/v3/about?fields=user",
            "dropbox": "https://api.dropboxapi.com/2/users/get_current_account",
            "onedrive": "https://graph.microsoft.com/v1.0/me/drive",
        }

        url = api_checks.get(remote_type, "")
        if not url:
            return await self._check_remote_via_rclone(remote)

        headers = {"Authorization": f"Bearer {access_token}"}

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                if remote_type == "dropbox":
                    # Dropbox needs a POST with empty body
                    resp = await client.post(url, headers=headers, content=b"null")
                else:
                    resp = await client.get(url, headers=headers)

                if resp.status_code == 200:
                    logger.info("check_remote: '%s' API check OK", remote)
                    remote_auth.clear_auth_failed(remote)
                    return True

                if resp.status_code == 401 and not config.get(remote, "client_id", fallback=""):
                    # rclone's built-in app: only rclone can refresh the
                    # token, so let rclone check the remote.
                    logger.info("check_remote: '%s' token expired, checking with rclone", remote)
                    return await self._check_remote_via_rclone(remote)

                if resp.status_code == 401 and refresh_token:
                    # Token expired, try refresh
                    logger.info("check_remote: '%s' token expired, refreshing", remote)
                    new_token = await self._refresh_token(remote, remote_type, token, config)
                    if new_token:
                        remote_auth.clear_auth_failed(remote)
                        return True

                logger.warning(
                    "check_remote: '%s' API returned %d: %s",
                    remote, resp.status_code, resp.text[:200]
                )
                # Refused credentials (not a rate limit sent as 403): the
                # remote needs a reconnect.
                if resp.status_code in (401, 403) and not _is_rate_limit_error(resp.text):
                    remote_auth.mark_auth_failed(remote)
                return False

            except httpx.TimeoutException:
                logger.warning("check_remote: '%s' API call timed out", remote)
                return False
            except Exception as e:
                logger.warning("check_remote: '%s' API call failed: %s", remote, e)
                return False

    async def _refresh_token(
        self, remote: str, remote_type: str, token: dict, config: configparser.RawConfigParser
    ) -> bool:
        """Refresh an expired OAuth token and update the config file.

        Uses the remote's own app credentials from rclone.conf. A remote
        without a client_id is not refreshed here (False): rclone refreshes
        it with its built-in app.
        """
        from backend.services.oauth import OAUTH_CONFIGS

        oauth_config = OAUTH_CONFIGS.get(remote_type)
        if not oauth_config:
            return False

        client_id = config.get(remote, "client_id", fallback="")
        client_secret = config.get(remote, "client_secret", fallback="")

        refresh_token = token.get("refresh_token", "")
        if not refresh_token or not client_id:
            return False

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        # Public clients (Dropbox PKCE apps, Azure public clients) send none:
        # an empty client_secret is an invalid_client error at some providers.
        if client_secret:
            data["client_secret"] = client_secret

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(oauth_config.token_url, data=data)
                resp.raise_for_status()
                new_data = resp.json()
            except Exception as e:
                logger.warning("check_remote: token refresh failed for '%s': %s", remote, e)
                return False

        # Update token in config file
        from backend.services.oauth import _compute_expiry
        new_token = {
            "access_token": new_data["access_token"],
            "token_type": new_data.get("token_type", "Bearer"),
            "expiry": _compute_expiry(new_data.get("expires_in")),
            "refresh_token": new_data.get("refresh_token", refresh_token),
        }

        try:
            # Re-read under the lock: a remote created or deleted meanwhile
            # stays so (for a deleted one, set() fails and nothing is written).
            await self._update_config(lambda cfg: cfg.set(remote, "token", json.dumps(new_token)))
            logger.info("check_remote: refreshed token for '%s'", remote)
        except Exception as e:
            logger.warning("check_remote: failed to save refreshed token: %s", e)

        return True

    async def _check_remote_via_rclone(self, remote: str) -> bool:
        """Fallback: check remote using rclone lsd command."""
        try:
            result = await self._run(
                [
                    "lsd", "--max-depth", "0",
                    "--contimeout", "5s",
                    "--timeout", "10s",
                    "--low-level-retries", "1",
                    "--retries", "1",
                ],
                use_config_args=False,
                timeout=15,
                positional=[f"{remote}:"],
            )
            logger.info("check_remote (rclone): '%s' returned code %d", remote, result.return_code)
            remote_auth.clear_auth_failed(remote)
            return result.return_code == 0
        except RcloneError as e:
            logger.warning("check_remote (rclone) failed for '%s': %s", remote, e)
            if isinstance(e, RcloneAuthError):
                remote_auth.mark_auth_failed(remote)
            return False

    async def authorize(
        self, remote_type: str, extra_args: list[str] | None = None
    ) -> asyncio.subprocess.Process:
        """Start rclone authorize <type> and return the running process.

        The caller (WizardSessionManager) reads stdout for auth_url and token.
        Does NOT use _run() because it's a long-running interactive process.
        """
        cmd = self._base_cmd() + ["authorize", remote_type, "--auth-no-open-browser"]
        if extra_args:
            cmd.extend(extra_args)

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return process
