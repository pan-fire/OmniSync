"""Tests for notification event model and factory functions."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from backend.services.notification_events import (
    SEVERITY_ORDER,
    NotificationEvent,
    NotificationEventType,
    NotificationSeverity,
    _EVENT_FACTORIES,
    auth_error_event,
    backup_completed_event,
    backup_failed_event,
    backup_overdue_event,
    backup_restore_completed_event,
    backup_restore_failed_event,
    backup_target_unreachable_event,
    backup_verify_failed_event,
    conflict_detected_event,
    engine_crash_event,
    intervals_paused_event,
    rclone_unavailable_event,
    remote_unreachable_event,
    startup_failure_event,
    startup_success_event,
    subsystem_degraded_event,
    sync_completed_event,
    sync_failed_event,
    test_event as make_test_event,  # aliased: pytest would collect "test_event"
)


class TestNotificationSeverity:
    def test_severity_order_is_strictly_monotonic(self) -> None:
        ordered = sorted(SEVERITY_ORDER.items(), key=lambda x: x[1])
        for i in range(1, len(ordered)):
            assert ordered[i][1] > ordered[i - 1][1]

    def test_severity_order_covers_all_members(self) -> None:
        for sev in NotificationSeverity:
            assert sev in SEVERITY_ORDER

    def test_debug_lt_info_lt_warning_lt_error(self) -> None:
        assert SEVERITY_ORDER[NotificationSeverity.DEBUG] < SEVERITY_ORDER[NotificationSeverity.INFO]
        assert SEVERITY_ORDER[NotificationSeverity.INFO] < SEVERITY_ORDER[NotificationSeverity.WARNING]
        assert SEVERITY_ORDER[NotificationSeverity.WARNING] < SEVERITY_ORDER[NotificationSeverity.ERROR]


class TestFactoryFunctions:
    def test_sync_completed_event(self) -> None:
        e = sync_completed_event("push", 5)
        assert e.severity == NotificationSeverity.INFO
        assert e.event_type == NotificationEventType.SYNC_COMPLETED
        assert "push" in e.title
        assert "5" in e.body

    def test_sync_failed_event(self) -> None:
        e = sync_failed_event("pull", "timeout")
        assert e.severity == NotificationSeverity.ERROR
        assert e.event_type == NotificationEventType.SYNC_FAILED
        assert "pull" in e.title

    def test_auth_error_event(self) -> None:
        e = auth_error_event("token expired")
        assert e.severity == NotificationSeverity.ERROR
        assert e.event_type == NotificationEventType.AUTH_ERROR

    def test_remote_unreachable_event(self) -> None:
        e = remote_unreachable_event("gdrive", "connection refused")
        assert e.severity == NotificationSeverity.ERROR
        assert e.event_type == NotificationEventType.REMOTE_UNREACHABLE
        assert "gdrive" in e.title

    def test_conflict_detected_event(self) -> None:
        e = conflict_detected_event(3)
        assert e.severity == NotificationSeverity.WARNING
        assert e.event_type == NotificationEventType.CONFLICT_DETECTED
        assert "3" in e.body

    def test_intervals_paused_event(self) -> None:
        e = intervals_paused_event(7)
        assert e.severity == NotificationSeverity.WARNING
        assert e.event_type == NotificationEventType.INTERVALS_PAUSED
        assert "7" in e.body

    def test_engine_crash_event(self) -> None:
        e = engine_crash_event("RuntimeError", "scheduler died")
        assert e.severity == NotificationSeverity.ERROR
        assert e.event_type == NotificationEventType.ENGINE_CRASH

    def test_startup_failure_event(self) -> None:
        e = startup_failure_event("database", "connection refused")
        assert e.severity == NotificationSeverity.ERROR
        assert e.event_type == NotificationEventType.STARTUP_FAILURE
        assert "database" in e.title

    def test_startup_success_event(self) -> None:
        e = startup_success_event()
        assert e.severity == NotificationSeverity.INFO
        assert e.event_type == NotificationEventType.STARTUP_SUCCESS

    def test_subsystem_degraded_event(self) -> None:
        e = subsystem_degraded_event("scheduler", "not running")
        assert e.severity == NotificationSeverity.WARNING
        assert e.event_type == NotificationEventType.SUBSYSTEM_DEGRADED

    def test_rclone_unavailable_event(self) -> None:
        e = rclone_unavailable_event("binary not found")
        assert e.severity == NotificationSeverity.ERROR
        assert e.event_type == NotificationEventType.RCLONE_UNAVAILABLE

    def test_test_event(self) -> None:
        e = make_test_event()
        assert e.severity == NotificationSeverity.INFO
        assert e.event_type == NotificationEventType.TEST

    def test_backup_completed_event(self) -> None:
        e = backup_completed_event("Daily", "mirror", 1024, profile_name="Default", profile_slug="default")
        assert e.severity == NotificationSeverity.INFO
        assert e.event_type == NotificationEventType.BACKUP_COMPLETED
        assert "Daily" in e.title
        assert "Default" in e.title
        assert "1,024" in e.body
        assert e.profile_slug == "default"

    def test_backup_completed_event_no_profile(self) -> None:
        e = backup_completed_event("Daily", "archive", 0)
        assert e.severity == NotificationSeverity.INFO
        assert "Daily" in e.title
        assert e.profile_slug is None

    def test_backup_failed_event(self) -> None:
        e = backup_failed_event("Daily", "disk full", profile_name="Default", profile_slug="default")
        assert e.severity == NotificationSeverity.ERROR
        assert e.event_type == NotificationEventType.BACKUP_FAILED
        assert "Daily" in e.title
        assert "disk full" in e.body

    def test_backup_target_unreachable_event(self) -> None:
        e = backup_target_unreachable_event("S3 backup", "connection refused", profile_name="Prod")
        assert e.severity == NotificationSeverity.WARNING
        assert e.event_type == NotificationEventType.BACKUP_TARGET_UNREACHABLE
        assert "S3 backup" in e.title
        assert "connection refused" in e.body

    def test_backup_overdue_event(self) -> None:
        last = datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc)
        e = backup_overdue_event("Daily", 24, last, profile_name="Docs", profile_slug="docs")
        assert e.severity == NotificationSeverity.WARNING
        assert e.event_type == NotificationEventType.BACKUP_OVERDUE
        assert e.title == "Backup overdue — Docs/Daily" and e.profile_slug == "docs"
        assert "2026-09-01 03:00 UTC" in e.body and "every 24h" in e.body
        assert "No backup of this target has completed yet" in backup_overdue_event("Daily", 6, None).body

    def test_backup_restore_completed_event(self) -> None:
        e = backup_restore_completed_event("Daily", "snap-001", "both", profile_name="Default")
        assert e.severity == NotificationSeverity.INFO
        assert e.event_type == NotificationEventType.BACKUP_RESTORE_COMPLETED
        assert "snap-001" in e.body
        assert "both" in e.body

    def test_backup_restore_failed_event(self) -> None:
        e = backup_restore_failed_event("Daily", "snap-001", "checksum mismatch", profile_name="Default")
        assert e.severity == NotificationSeverity.ERROR
        assert e.event_type == NotificationEventType.BACKUP_RESTORE_FAILED
        assert "snap-001" in e.body
        assert "checksum mismatch" in e.body

    def test_backup_verify_failed_event(self) -> None:
        e = backup_verify_failed_event("Daily", "2026-10-02T10-00-00", "1 file(s) differ", profile_name="Default",
                                       profile_slug="default")
        assert e.severity == NotificationSeverity.ERROR
        assert e.event_type == NotificationEventType.BACKUP_VERIFY_FAILED
        assert e.title == "Backup verification failed — Default/Daily"
        assert "2026-10-02T10-00-00" in e.body and "1 file(s) differ" in e.body
        assert len(backup_verify_failed_event("D", "s", "x" * 5000).body) <= 2000

    def test_all_event_types_have_factories(self) -> None:
        """Property P12: Every NotificationEventType member has a factory."""
        for et in NotificationEventType:
            assert et in _EVENT_FACTORIES, f"Missing factory for {et}"


class TestEventModel:
    def test_timestamp_is_utc(self) -> None:
        e = make_test_event()
        assert e.timestamp.tzinfo is not None
        assert e.timestamp.tzinfo == timezone.utc

    def test_title_max_length(self) -> None:
        with pytest.raises(ValidationError):
            NotificationEvent(
                severity=NotificationSeverity.INFO,
                event_type=NotificationEventType.TEST,
                title="x" * 201,
                body="test",
                timestamp=make_test_event().timestamp,
            )

    def test_body_max_length(self) -> None:
        with pytest.raises(ValidationError):
            NotificationEvent(
                severity=NotificationSeverity.INFO,
                event_type=NotificationEventType.TEST,
                title="test",
                body="x" * 2001,
                timestamp=make_test_event().timestamp,
            )

    @given(st.sampled_from(list(NotificationEventType)))
    def test_pbt_all_factories_produce_valid_events(self, event_type: NotificationEventType) -> None:
        """PBT P12: Every factory produces a valid NotificationEvent."""
        factory = _EVENT_FACTORIES[event_type]
        import inspect
        sig = inspect.signature(factory)
        # Build kwargs with dummy values
        kwargs: dict = {}
        for name, param in sig.parameters.items():
            if param.annotation in (str, "str"):
                kwargs[name] = "test_value"
            elif param.annotation in (int, "int"):
                kwargs[name] = 1
        event = factory(**kwargs)
        assert isinstance(event, NotificationEvent)
        assert len(event.title) <= 200
        assert len(event.body) <= 2000
        assert event.timestamp.tzinfo == timezone.utc


class TestEventWording:
    """Titles and bodies of the engine's events, including two-way sync."""

    @pytest.mark.parametrize(("text", "kind"), [
        ("rclone authentication error: invalid_grant", "auth"),
        ("rclone failed (exit 1): dial tcp 1.2.3.4:443: connect: connection refused", "network"),
        ("Failed to copy: Get \"https://x\": dial tcp: lookup x: no such host", "network"),
        ("rclone failed (exit 3): directory not found", None),
        ("rclone was rate-limited by the provider (exit 1): 429", None),
    ])
    def test_classify_failure_text(self, text: str, kind: str | None) -> None:
        from backend.services.notification_events import classify_failure_text
        assert classify_failure_text(text) == kind

    def test_two_way_titles(self) -> None:
        assert sync_completed_event("two-way", 3, profile_name="Docs").title == "Two-way sync completed — Docs"
        assert sync_completed_event("resync", 3).title == "Two-way resync completed"
        assert sync_failed_event("two-way", "x").title == "Two-way sync failed"
        assert sync_completed_event("push", 3).title == "Sync push completed"

    def test_two_way_completed_mentions_conflicts_kept(self) -> None:
        body = sync_completed_event("two-way", 4, conflicts=2).body
        assert "both directions" in body and "2 file(s)" in body

    def test_failed_body_says_retries_only_when_retried(self) -> None:
        assert sync_failed_event("push", "refused").body == "refused"
        assert sync_failed_event("push", "boom", attempts=3).body == "Failed after 3 attempt(s): boom"

    def test_conflicts_kept_both(self) -> None:
        e = conflict_detected_event(2, kept_both=True, profile_name="Docs", profile_slug="docs")
        assert e.severity == NotificationSeverity.WARNING
        assert "Both versions were kept" in e.body
        assert e.profile_slug == "docs"

    def test_resync_required(self) -> None:
        from backend.services.notification_events import resync_required_event
        e = resync_required_event("Two-way sync needs a resync: x. Run Resync.", profile_name="Docs")
        assert e.severity == NotificationSeverity.ERROR
        assert e.event_type == NotificationEventType.INTERVALS_PAUSED
        assert e.title == "Two-way sync needs a resync — Docs"

    def test_long_profile_names_are_cut_to_the_title_limit(self) -> None:
        e = sync_failed_event("two-way", "x", profile_name="n" * 300)
        assert len(e.title) == 200
