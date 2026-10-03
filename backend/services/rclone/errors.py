"""Classifying rclone failures (auth, rate limit) and keeping secrets out of messages."""

from __future__ import annotations

import json
import re

from backend.exceptions import RcloneAuthError, RcloneError, RcloneRateLimitError

# Phrases that indicate an authentication error in rclone stderr
AUTH_ERROR_PHRASES = ("invalid_grant", "unauthorized", "403 forbidden", "401 unauthorized")

# Phrases that mean the provider throttled us. Google Drive reports these as
# HTTP 403 (reason rateLimitExceeded / userRateLimitExceeded), so they are
# checked before the auth phrases: a rate limit is retryable, not a login problem.
RATE_LIMIT_PHRASES = (
    "ratelimitexceeded",  # also matches userRateLimitExceeded
    "rate limit exceeded",
    "too many requests",
    "error 429",
    "status 429",
    "429 too many",
)


def _is_rate_limit_error(stderr: str) -> bool:
    """Check if stderr says the provider rate-limited the request."""
    stderr_lower = stderr.lower()
    return any(phrase in stderr_lower for phrase in RATE_LIMIT_PHRASES)


def _is_auth_error(stderr: str) -> bool:
    """Check if stderr contains authentication error phrases.

    A rate limit is never an auth error, even when the provider sends it as
    an HTTP 403 (Google Drive's rateLimitExceeded / userRateLimitExceeded).
    """
    if _is_rate_limit_error(stderr):
        return False
    stderr_lower = stderr.lower()
    return any(phrase in stderr_lower for phrase in AUTH_ERROR_PHRASES)


def readable_stderr(stderr: str, max_lines: int = 20) -> str:
    """Turn rclone stderr (plain or --use-json-log) into short readable text.

    JSON log lines become "<object>: <msg>"; info lines (the transfer log)
    are dropped, since they are not the failure reason.
    """
    out: list[str] = []
    for line in stderr.splitlines():
        if line.startswith("{"):
            try:
                entry = json.loads(line)
            except ValueError:
                out.append(line)
                continue
            if not isinstance(entry, dict) or entry.get("level") not in ("warning", "error", "critical", "notice"):
                continue
            if "stats" in entry:  # the per-second progress block, not a message
                continue
            msg = str(entry.get("msg", "")).strip()
            obj = entry.get("object")
            out.append(f"{obj}: {msg}" if obj else msg)
        elif line.strip():
            out.append(line)
    return "\n".join(out[-max_lines:])


# A password in an on-the-fly connection string (":crypt,...,password='<obscured>':",
# e.g. in a path a user entered). Obscured is not encrypted, so it never goes
# to the log or into an error message. (Encrypted backup targets pass theirs
# through the environment instead: see define_env_remote.)
_CONNECTION_SECRET_RE = re.compile(r"\b(password2?|pass)=('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|[^,:\s]*)")


def redact_secrets(text: str) -> str:
    """``text`` with the passwords of connection strings replaced by ***."""
    return _CONNECTION_SECRET_RE.sub(r"\1=***", text)


def _command_line(cmd: list[str]) -> str:
    """A command for the log, without secrets."""
    return redact_secrets(" ".join(cmd))


def classify_failure(return_code: int, stderr: str) -> RcloneError:
    """The exception for a failed rclone run: rate limit, auth, or generic."""
    stderr = redact_secrets(stderr)
    text = readable_stderr(stderr) or stderr
    if _is_rate_limit_error(stderr):
        return RcloneRateLimitError(f"rclone was rate-limited by the provider (exit {return_code}): {text}")
    if _is_auth_error(stderr):
        return RcloneAuthError(f"rclone authentication error: {text}")
    return RcloneError(f"rclone failed (exit {return_code}): {text}")
