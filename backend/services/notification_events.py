"""Notification event model, enums, and factory functions for OmniSync."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from pydantic import BaseModel, Field


class NotificationSeverity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class NotificationEventType(str, Enum):
    SYNC_COMPLETED = "sync_completed"
    SYNC_FAILED = "sync_failed"
    AUTH_ERROR = "auth_error"
    REMOTE_UNREACHABLE = "remote_unreachable"
    CONFLICT_DETECTED = "conflict_detected"
    INTERVALS_PAUSED = "intervals_paused"
    ENGINE_CRASH = "engine_crash"
    STARTUP_FAILURE = "startup_failure"
    STARTUP_SUCCESS = "startup_success"
    SUBSYSTEM_DEGRADED = "subsystem_degraded"
    RCLONE_UNAVAILABLE = "rclone_unavailable"
    BACKUP_COMPLETED = "backup_completed"
    BACKUP_FAILED = "backup_failed"
    BACKUP_TARGET_UNREACHABLE = "backup_target_unreachable"
    BACKUP_OVERDUE = "backup_overdue"
    BACKUP_RESTORE_COMPLETED = "backup_restore_completed"
    BACKUP_RESTORE_FAILED = "backup_restore_failed"
    BACKUP_VERIFY_FAILED = "backup_verify_failed"
    TEST = "test"


class NotificationEvent(BaseModel):
    severity: NotificationSeverity
    event_type: NotificationEventType
    title: str = Field(max_length=200)
    body: str = Field(max_length=2000)
    timestamp: datetime
    profile_slug: str | None = None
    profile_name: str | None = None


# Severity ordering for threshold comparison (strictly monotonic)
SEVERITY_ORDER: dict[NotificationSeverity, int] = {
    NotificationSeverity.DEBUG: 0,
    NotificationSeverity.INFO: 1,
    NotificationSeverity.WARNING: 2,
    NotificationSeverity.ERROR: 3,
}


# --- Factory functions ---


def _titled(title: str, profile_name: str | None) -> str:
    """``title — profile`` (when a profile is known), within the 200-char limit."""
    if profile_name:
        title = f"{title} — {profile_name}"
    return title[:200]


# What a sync direction is called in a title. Mirror directions keep the
# historic "Sync push" / "Sync pull" wording.
_DIRECTION_TITLES: dict[str, str] = {
    "two-way": "Two-way sync",
    "two_way": "Two-way sync",
    "resync": "Two-way resync",
    "startup check": "Startup check",
}


def _direction_title(direction: str) -> str:
    return _DIRECTION_TITLES.get(direction, f"Sync {direction}")


# rclone failure texts that mean the remote could not be reached at all
# (DNS, TCP, TLS), as opposed to a refusal or a failure on some files.
NETWORK_ERROR_PHRASES: tuple[str, ...] = (
    "no such host",
    "connection refused",
    "network is unreachable",
    "no route to host",
    "i/o timeout",
    "tls handshake timeout",
    "connection reset by peer",
    "temporary failure in name resolution",
    "dial tcp",
    "couldn't connect",
)

# The prefix rclone.classify_failure gives an RcloneAuthError.
AUTH_ERROR_PREFIX = "rclone authentication error"


def classify_failure_text(error: str) -> str | None:
    """'auth', 'network' or None for the text of a failed rclone run.

    Lets a failure that reaches the notification hooks only as text (e.g.
    after the retries, or from the startup check) become the specific
    event: an auth error or an unreachable remote, instead of a generic
    sync failure.
    """
    lower = error.lower()
    if AUTH_ERROR_PREFIX in lower:
        return "auth"
    if any(phrase in lower for phrase in NETWORK_ERROR_PHRASES):
        return "network"
    return None


def sync_completed_event(
    direction: str, files: int, *, conflicts: int = 0, warnings: list[str] | None = None,
    profile_name: str | None = None, profile_slug: str | None = None,
) -> NotificationEvent:
    """``warnings``: one line per kind of file the sync left alone or treated
    specially (names.describe); they make the event a warning."""
    if direction in ("two-way", "two_way", "resync"):
        body = f"Synced {files} change(s) in both directions."
        if conflicts:
            body += f" {conflicts} file(s) changed on both sides were kept in two versions."
    else:
        body = f"Successfully synced {files} file(s)."
    title = f"{_direction_title(direction)} completed"
    if warnings:
        title += " with warnings"
        body += " Not everything was synced as it is; see the job's warnings: " + " ".join(
            f"{line}." for line in warnings)
    return NotificationEvent(
        severity=NotificationSeverity.WARNING if warnings else NotificationSeverity.INFO,
        event_type=NotificationEventType.SYNC_COMPLETED,
        title=_titled(title, profile_name),
        body=body[:2000],
        timestamp=datetime.now(timezone.utc),
        profile_slug=profile_slug,
        profile_name=profile_name,
    )


def sync_failed_event(
    direction: str, error: str, *, attempts: int | None = None,
    profile_name: str | None = None, profile_slug: str | None = None,
) -> NotificationEvent:
    """A failed or refused sync. ``attempts`` (when retried) is said in the body."""
    body = f"Failed after {attempts} attempt(s): {error[:1900]}" if attempts else error[:2000]
    return NotificationEvent(
        severity=NotificationSeverity.ERROR,
        event_type=NotificationEventType.SYNC_FAILED,
        title=_titled(f"{_direction_title(direction)} failed", profile_name),
        body=body,
        timestamp=datetime.now(timezone.utc),
        profile_slug=profile_slug,
        profile_name=profile_name,
    )


def auth_error_event(error: str, *, profile_name: str | None = None, profile_slug: str | None = None) -> NotificationEvent:
    return NotificationEvent(
        severity=NotificationSeverity.ERROR,
        event_type=NotificationEventType.AUTH_ERROR,
        title=_titled("Authentication failed", profile_name),
        body=f"OAuth token error: {error[:1950]}",
        timestamp=datetime.now(timezone.utc),
        profile_slug=profile_slug,
        profile_name=profile_name,
    )


def remote_unreachable_event(
    remote: str, error: str, *, profile_name: str | None = None, profile_slug: str | None = None,
) -> NotificationEvent:
    return NotificationEvent(
        severity=NotificationSeverity.ERROR,
        event_type=NotificationEventType.REMOTE_UNREACHABLE,
        title=_titled(f"Remote '{remote[:120]}' unreachable", profile_name),
        body=f"Cannot connect to remote: {error[:1950]}",
        timestamp=datetime.now(timezone.utc),
        profile_slug=profile_slug,
        profile_name=profile_name,
    )


def conflict_detected_event(
    count: int, *, kept_both: bool = False,
    profile_name: str | None = None, profile_slug: str | None = None,
) -> NotificationEvent:
    """Files changed on both sides.

    ``kept_both``: a two-way sync already kept both versions (one under a
    conflict name); otherwise the files wait for a decision.
    """
    if kept_both:
        body = (
            f"{count} file(s) were changed on both sides. Both versions were kept, "
            "one under a conflict name: review them on the Conflicts page."
        )
    else:
        body = (
            f"{count} file(s) modified on both local and remote. They are not synced "
            "until you choose which version to keep."
        )
    return NotificationEvent(
        severity=NotificationSeverity.WARNING,
        event_type=NotificationEventType.CONFLICT_DETECTED,
        title=_titled("Sync conflicts detected", profile_name),
        body=body,
        timestamp=datetime.now(timezone.utc),
        profile_slug=profile_slug,
        profile_name=profile_name,
    )


def intervals_paused_event(pending: int, *, profile_name: str | None = None, profile_slug: str | None = None) -> NotificationEvent:
    return NotificationEvent(
        severity=NotificationSeverity.WARNING,
        event_type=NotificationEventType.INTERVALS_PAUSED,
        title=_titled("Sync intervals paused", profile_name),
        body=f"Automatic sync paused due to {pending} unresolved difference(s).",
        timestamp=datetime.now(timezone.utc),
        profile_slug=profile_slug,
        profile_name=profile_name,
    )


def resync_required_event(
    reason: str, *, profile_name: str | None = None, profile_slug: str | None = None,
) -> NotificationEvent:
    """A two-way profile paused until the user confirms a resync.

    An error, not a warning: nothing syncs in either direction until the
    user acts. It reuses the intervals_paused type, since that is what
    happened to the profile.
    """
    return NotificationEvent(
        severity=NotificationSeverity.ERROR,
        event_type=NotificationEventType.INTERVALS_PAUSED,
        title=_titled("Two-way sync needs a resync", profile_name),
        body=reason[:2000],
        timestamp=datetime.now(timezone.utc),
        profile_slug=profile_slug,
        profile_name=profile_name,
    )


def engine_crash_event(
    exc_type: str, message: str, *, component: str = "Sync engine",
    profile_name: str | None = None, profile_slug: str | None = None,
) -> NotificationEvent:
    return NotificationEvent(
        severity=NotificationSeverity.ERROR,
        event_type=NotificationEventType.ENGINE_CRASH,
        title=_titled(f"{component[:80]} crash", profile_name),
        body=f"{exc_type}: {message[:1950]}",
        timestamp=datetime.now(timezone.utc),
        profile_slug=profile_slug,
        profile_name=profile_name,
    )


def startup_failure_event(
    subsystem: str, error: str, *, profile_name: str | None = None, profile_slug: str | None = None,
) -> NotificationEvent:
    return NotificationEvent(
        severity=NotificationSeverity.ERROR,
        event_type=NotificationEventType.STARTUP_FAILURE,
        title=f"Startup failure: {subsystem}"[:200],
        body=f"Failed to initialize {subsystem[:80]}: {error[:1900]}",
        timestamp=datetime.now(timezone.utc),
        profile_slug=profile_slug,
        profile_name=profile_name,
    )


def startup_success_event() -> NotificationEvent:
    return NotificationEvent(
        severity=NotificationSeverity.INFO,
        event_type=NotificationEventType.STARTUP_SUCCESS,
        title="OmniSync started",
        body="All subsystems initialized successfully.",
        timestamp=datetime.now(timezone.utc),
    )


def subsystem_degraded_event(subsystem: str, detail: str) -> NotificationEvent:
    return NotificationEvent(
        severity=NotificationSeverity.WARNING,
        event_type=NotificationEventType.SUBSYSTEM_DEGRADED,
        title=f"Subsystem degraded: {subsystem}",
        body=detail[:2000],
        timestamp=datetime.now(timezone.utc),
    )


def rclone_unavailable_event(detail: str) -> NotificationEvent:
    return NotificationEvent(
        severity=NotificationSeverity.ERROR,
        event_type=NotificationEventType.RCLONE_UNAVAILABLE,
        title="rclone unavailable",
        body=detail[:2000],
        timestamp=datetime.now(timezone.utc),
    )


def backup_completed_event(
    target_name: str,
    backup_mode: str,
    size_bytes: int,
    *,
    profile_name: str | None = None,
    profile_slug: str | None = None,
) -> NotificationEvent:
    title = f"Backup completed — {target_name}"
    if profile_name:
        title = f"Backup completed — {profile_name}/{target_name}"
    return NotificationEvent(
        severity=NotificationSeverity.INFO,
        event_type=NotificationEventType.BACKUP_COMPLETED,
        title=title[:200],
        body=f"Backup ({backup_mode}) finished. Size: {size_bytes:,} bytes.",
        timestamp=datetime.now(timezone.utc),
        profile_slug=profile_slug,
        profile_name=profile_name,
    )


def backup_failed_event(
    target_name: str,
    error: str,
    *,
    profile_name: str | None = None,
    profile_slug: str | None = None,
) -> NotificationEvent:
    title = f"Backup failed — {target_name}"
    if profile_name:
        title = f"Backup failed — {profile_name}/{target_name}"
    return NotificationEvent(
        severity=NotificationSeverity.ERROR,
        event_type=NotificationEventType.BACKUP_FAILED,
        title=title[:200],
        body=f"Backup error: {error[:1950]}",
        timestamp=datetime.now(timezone.utc),
        profile_slug=profile_slug,
        profile_name=profile_name,
    )


def backup_target_unreachable_event(
    target_name: str,
    error: str,
    *,
    profile_name: str | None = None,
    profile_slug: str | None = None,
) -> NotificationEvent:
    title = f"Backup target unreachable — {target_name}"
    if profile_name:
        title = f"Backup target unreachable — {profile_name}/{target_name}"
    return NotificationEvent(
        severity=NotificationSeverity.WARNING,
        event_type=NotificationEventType.BACKUP_TARGET_UNREACHABLE,
        title=title[:200],
        body=f"Cannot reach backup target: {error[:1950]}",
        timestamp=datetime.now(timezone.utc),
        profile_slug=profile_slug,
        profile_name=profile_name,
    )


def backup_overdue_event(
    target_name: str,
    frequency_hours: int,
    last_completed_at: datetime | None = None,
    *,
    profile_name: str | None = None,
    profile_slug: str | None = None,
) -> NotificationEvent:
    title = f"Backup overdue — {target_name}"
    if profile_name:
        title = f"Backup overdue — {profile_name}/{target_name}"
    if last_completed_at is None:
        since = "No backup of this target has completed yet"
    else:
        since = f"The last backup completed at {last_completed_at.strftime('%Y-%m-%d %H:%M UTC')}"
    return NotificationEvent(
        severity=NotificationSeverity.WARNING,
        event_type=NotificationEventType.BACKUP_OVERDUE,
        title=title[:200],
        body=(f"{since}, although it is set to back up every {frequency_hours}h. "
              "Check its backup history for failed or skipped runs."),
        timestamp=datetime.now(timezone.utc),
        profile_slug=profile_slug,
        profile_name=profile_name,
    )


def backup_restore_completed_event(
    target_name: str,
    snapshot_id: str,
    restore_scope: str,
    *,
    profile_name: str | None = None,
    profile_slug: str | None = None,
) -> NotificationEvent:
    title = f"Restore completed — {target_name}"
    if profile_name:
        title = f"Restore completed — {profile_name}/{target_name}"
    return NotificationEvent(
        severity=NotificationSeverity.INFO,
        event_type=NotificationEventType.BACKUP_RESTORE_COMPLETED,
        title=title[:200],
        body=f"Restored snapshot '{snapshot_id}' (scope: {restore_scope}).",
        timestamp=datetime.now(timezone.utc),
        profile_slug=profile_slug,
        profile_name=profile_name,
    )


def backup_restore_failed_event(
    target_name: str,
    snapshot_id: str,
    error: str,
    *,
    profile_name: str | None = None,
    profile_slug: str | None = None,
) -> NotificationEvent:
    title = f"Restore failed — {target_name}"
    if profile_name:
        title = f"Restore failed — {profile_name}/{target_name}"
    return NotificationEvent(
        severity=NotificationSeverity.ERROR,
        event_type=NotificationEventType.BACKUP_RESTORE_FAILED,
        title=title[:200],
        body=f"Failed to restore snapshot '{snapshot_id}': {error[:1900]}",
        timestamp=datetime.now(timezone.utc),
        profile_slug=profile_slug,
        profile_name=profile_name,
    )


def backup_verify_failed_event(
    target_name: str,
    snapshot_id: str,
    summary: str,
    *,
    profile_name: str | None = None,
    profile_slug: str | None = None,
) -> NotificationEvent:
    """A backup completed, but comparing it with the folder afterwards found a problem."""
    title = f"Backup verification failed — {target_name}"
    if profile_name:
        title = f"Backup verification failed — {profile_name}/{target_name}"
    return NotificationEvent(
        severity=NotificationSeverity.ERROR,
        event_type=NotificationEventType.BACKUP_VERIFY_FAILED,
        title=title[:200],
        body=f"Backup '{snapshot_id}' does not match the folder: {summary[:1850]}",
        timestamp=datetime.now(timezone.utc),
        profile_slug=profile_slug,
        profile_name=profile_name,
    )


def test_event() -> NotificationEvent:
    return NotificationEvent(
        severity=NotificationSeverity.INFO,
        event_type=NotificationEventType.TEST,
        title="OmniSync test notification",
        body="This is a test notification from OmniSync.",
        timestamp=datetime.now(timezone.utc),
    )


# Map event types to their factory functions for completeness checks
_EVENT_FACTORIES: dict[NotificationEventType, Callable[..., NotificationEvent]] = {
    NotificationEventType.SYNC_COMPLETED: sync_completed_event,
    NotificationEventType.SYNC_FAILED: sync_failed_event,
    NotificationEventType.AUTH_ERROR: auth_error_event,
    NotificationEventType.REMOTE_UNREACHABLE: remote_unreachable_event,
    NotificationEventType.CONFLICT_DETECTED: conflict_detected_event,
    NotificationEventType.INTERVALS_PAUSED: intervals_paused_event,
    NotificationEventType.ENGINE_CRASH: engine_crash_event,
    NotificationEventType.STARTUP_FAILURE: startup_failure_event,
    NotificationEventType.STARTUP_SUCCESS: startup_success_event,
    NotificationEventType.SUBSYSTEM_DEGRADED: subsystem_degraded_event,
    NotificationEventType.RCLONE_UNAVAILABLE: rclone_unavailable_event,
    NotificationEventType.BACKUP_COMPLETED: backup_completed_event,
    NotificationEventType.BACKUP_FAILED: backup_failed_event,
    NotificationEventType.BACKUP_TARGET_UNREACHABLE: backup_target_unreachable_event,
    NotificationEventType.BACKUP_OVERDUE: backup_overdue_event,
    NotificationEventType.BACKUP_RESTORE_COMPLETED: backup_restore_completed_event,
    NotificationEventType.BACKUP_RESTORE_FAILED: backup_restore_failed_event,
    NotificationEventType.BACKUP_VERIFY_FAILED: backup_verify_failed_event,
    NotificationEventType.TEST: test_event,
}
