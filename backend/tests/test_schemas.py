"""Tests for Pydantic schema validation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from backend.api.schemas import (
    AggregateStatusResponse,
    BackupJobResponse,
    BackupJobStatus,
    BackupMode,
    BackupTargetCreateRequest,
    BackupTargetResponse,
    BackupTargetType,
    BackupTargetUpdateRequest,
    ChannelConfig,
    ChannelConfigUpdate,
    DiffPagination,
    DiffResponse,
    DiffSummary,
    FileDiff,
    ChangeCategory,
    GlobalConfigResponse,
    NotificationConfigResponse,
    PausedProfileSummary,
    ProfileCreateRequest,
    ProfileStatusResponse,
    ProfileSummary,
    PushSubscriptionRequest,
    RemoteDependenciesResponse,
    RemoteDependencyBackupTarget,
    RemoteDependencyProfile,
    RemoteStorageInfoResponse,
    RestoreRequest,
    RestoreScope,
    ResumeIntervalsResponse,
    SnapshotResponse,
    SyncDirection,
    SyncMode,
    SyncStartRequest,
    SyncState,
    SyncStatusResponse,
    TestNotificationResponse as NotificationTestResponse,  # aliased: pytest would collect "Test*"
)


class TestSyncStatusResponse:
    def test_defaults_paused_false(self) -> None:
        resp = SyncStatusResponse(state=SyncState.IDLE)
        assert resp.intervals_paused is False
        assert resp.paused_at is None

    def test_serializes_paused_fields(self) -> None:
        ts = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        resp = SyncStatusResponse(
            state=SyncState.IDLE,
            intervals_paused=True,
            paused_at=ts,
        )
        data = resp.model_dump()
        assert data["intervals_paused"] is True
        assert data["paused_at"] == ts


class TestSyncStartRequest:
    def test_force_defaults_false(self) -> None:
        req = SyncStartRequest(direction=SyncDirection.PUSH)
        assert req.force is False

    def test_force_true(self) -> None:
        req = SyncStartRequest(direction=SyncDirection.PULL, force=True)
        assert req.force is True


class TestResumeIntervalsResponse:
    def test_round_trips_detail(self) -> None:
        resp = ResumeIntervalsResponse(detail="Intervals resumed successfully.")
        assert resp.detail == "Intervals resumed successfully."
        data = resp.model_dump()
        assert data["detail"] == "Intervals resumed successfully."


# --- Backup target schemas ---

_NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _valid_create_kwargs() -> dict:
    return {
        "name": "Daily backup",
        "target_path": "/backups/daily",
        "target_type": BackupTargetType.LOCAL,
    }


class TestBackupTargetCreateRequest:
    def test_minimal_valid(self) -> None:
        req = BackupTargetCreateRequest(**_valid_create_kwargs())
        assert req.name == "Daily backup"
        assert req.retention_days == 7
        assert req.frequency_hours == 24
        assert req.backup_mode == BackupMode.MIRROR
        assert req.enabled is True
        assert req.remote_name is None

    def test_all_fields(self) -> None:
        req = BackupTargetCreateRequest(
            name="Cloud mirror",
            target_path="gdrive:backups",
            target_type=BackupTargetType.REMOTE,
            remote_name="gdrive",
            retention_days=30,
            frequency_hours=6,
            backup_mode=BackupMode.ARCHIVE,
            enabled=False,
        )
        assert req.target_type == BackupTargetType.REMOTE
        assert req.remote_name == "gdrive"
        assert req.backup_mode == BackupMode.ARCHIVE
        assert req.enabled is False

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BackupTargetCreateRequest(
                name="", target_path="/b", target_type=BackupTargetType.LOCAL
            )

    def test_name_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BackupTargetCreateRequest(
                name="x" * 256, target_path="/b", target_type=BackupTargetType.LOCAL
            )

    def test_retention_days_too_low(self) -> None:
        with pytest.raises(ValidationError):
            BackupTargetCreateRequest(**{**_valid_create_kwargs(), "retention_days": 0})

    def test_retention_days_too_high(self) -> None:
        with pytest.raises(ValidationError):
            BackupTargetCreateRequest(**{**_valid_create_kwargs(), "retention_days": 366})

    def test_frequency_hours_too_low(self) -> None:
        with pytest.raises(ValidationError):
            BackupTargetCreateRequest(**{**_valid_create_kwargs(), "frequency_hours": 0})

    def test_frequency_hours_too_high(self) -> None:
        with pytest.raises(ValidationError):
            BackupTargetCreateRequest(**{**_valid_create_kwargs(), "frequency_hours": 8761})

    def test_empty_target_path_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BackupTargetCreateRequest(
                name="ok", target_path="", target_type=BackupTargetType.LOCAL
            )


class TestBackupTargetUpdateRequest:
    def test_all_none_valid(self) -> None:
        req = BackupTargetUpdateRequest()
        assert req.name is None
        assert req.retention_days is None
        assert req.enabled is None

    def test_partial_update(self) -> None:
        req = BackupTargetUpdateRequest(retention_days=30, enabled=False)
        assert req.retention_days == 30
        assert req.enabled is False
        assert req.name is None

    def test_retention_days_bounds(self) -> None:
        with pytest.raises(ValidationError):
            BackupTargetUpdateRequest(retention_days=0)
        with pytest.raises(ValidationError):
            BackupTargetUpdateRequest(retention_days=366)

    def test_frequency_hours_bounds(self) -> None:
        with pytest.raises(ValidationError):
            BackupTargetUpdateRequest(frequency_hours=0)
        with pytest.raises(ValidationError):
            BackupTargetUpdateRequest(frequency_hours=8761)


class TestBackupTargetResponse:
    def test_serializes_all_fields(self) -> None:
        resp = BackupTargetResponse(
            id=1,
            profile_id=10,
            name="Test",
            target_path="/backup",
            target_type=BackupTargetType.LOCAL,
            remote_name=None,
            retention_days=7,
            keep_last=3,
            frequency_hours=24,
            backup_mode=BackupMode.MIRROR,
            enabled=True,
            created_at=_NOW,
            updated_at=_NOW,
        )
        data = resp.model_dump()
        assert data["keep_last"] == 3 and data["overdue"] is False
        assert data["target_type"] == "local"
        assert data["backup_mode"] == "mirror"
        assert data["last_liveness_ok"] is None
        assert data["last_backup_at"] is None
        assert data["next_scheduled_at"] is None


class TestBackupJobResponse:
    def test_minimal(self) -> None:
        resp = BackupJobResponse(
            id=1,
            target_id=5,
            started_at=_NOW,
            status=BackupJobStatus.RUNNING,
            direction="backup",
        )
        assert resp.finished_at is None
        assert resp.size_bytes is None
        assert resp.snapshot_id is None
        assert resp.error_message is None

    def test_completed_job(self) -> None:
        resp = BackupJobResponse(
            id=2,
            target_id=5,
            started_at=_NOW,
            finished_at=_NOW,
            status=BackupJobStatus.COMPLETED,
            direction="backup",
            size_bytes=1024,
            snapshot_id="snap-001",
        )
        assert resp.status == BackupJobStatus.COMPLETED
        assert resp.size_bytes == 1024


class TestSnapshotResponse:
    def test_round_trip(self) -> None:
        resp = SnapshotResponse(
            snapshot_id="snap-001",
            created_at=_NOW,
            size_bytes=2048,
            status="available",
        )
        data = resp.model_dump()
        assert data["snapshot_id"] == "snap-001"
        assert data["status"] == "available"


class TestRestoreRequest:
    def test_valid(self) -> None:
        req = RestoreRequest(
            snapshot_id="snap-001", restore_scope=RestoreScope.BOTH
        )
        assert req.restore_scope == RestoreScope.BOTH

    def test_all_scopes(self) -> None:
        for scope in RestoreScope:
            req = RestoreRequest(snapshot_id="x", restore_scope=scope)
            assert req.restore_scope == scope


class TestRemoteStorageInfoResponse:
    def test_all_nullable(self) -> None:
        resp = RemoteStorageInfoResponse()
        assert resp.total_bytes is None
        assert resp.used_bytes is None
        assert resp.free_bytes is None
        assert resp.trashed_bytes is None
        assert resp.supported is True

    def test_with_values(self) -> None:
        resp = RemoteStorageInfoResponse(
            total_bytes=100_000, used_bytes=60_000, free_bytes=40_000
        )
        assert resp.total_bytes == 100_000


class TestRemoteDependenciesResponse:
    def test_empty(self) -> None:
        resp = RemoteDependenciesResponse()
        assert resp.profiles == []
        assert resp.backup_targets == []

    def test_with_deps(self) -> None:
        resp = RemoteDependenciesResponse(
            profiles=[RemoteDependencyProfile(slug="default", name="Default")],
            backup_targets=[
                RemoteDependencyBackupTarget(
                    profile_slug="default", target_name="Daily", target_id=1
                )
            ],
        )
        assert len(resp.profiles) == 1
        assert resp.backup_targets[0].target_id == 1


# --- Property-based tests ---


@given(name=st.text(min_size=1, max_size=255))
@settings(max_examples=50)
def test_name_field_roundtrip(name: str) -> None:
    req = BackupTargetCreateRequest(
        name=name, target_path="/b", target_type=BackupTargetType.LOCAL
    )
    assert req.name == name


@given(days=st.integers(min_value=1, max_value=365))
@settings(max_examples=50)
def test_retention_days_roundtrip(days: int) -> None:
    req = BackupTargetCreateRequest(
        **{**_valid_create_kwargs(), "retention_days": days}
    )
    assert req.retention_days == days
    data = req.model_dump()
    reparsed = BackupTargetCreateRequest(**data)
    assert reparsed.retention_days == days


@given(hours=st.integers(min_value=1, max_value=8760))
@settings(max_examples=50)
def test_frequency_hours_roundtrip(hours: int) -> None:
    req = BackupTargetCreateRequest(
        **{**_valid_create_kwargs(), "frequency_hours": hours}
    )
    assert req.frequency_hours == hours


@given(days=st.integers().filter(lambda d: d < 1 or d > 365))
@settings(max_examples=30)
def test_retention_days_out_of_bounds_rejected(days: int) -> None:
    with pytest.raises(ValidationError):
        BackupTargetCreateRequest(**{**_valid_create_kwargs(), "retention_days": days})


# --- DiffPagination schema tests ---


class TestDiffPagination:
    def test_defaults(self) -> None:
        p = DiffPagination()
        assert p.offset == 0
        assert p.limit == 0
        assert p.total == 0
        assert p.has_more is False

    def test_has_more_true(self) -> None:
        p = DiffPagination(offset=0, limit=100, total=3000, has_more=True)
        assert p.has_more is True
        assert p.total == 3000

    def test_round_trip(self) -> None:
        p = DiffPagination(offset=200, limit=100, total=500, has_more=True)
        data = p.model_dump()
        reparsed = DiffPagination(**data)
        assert reparsed == p


class TestDiffResponsePagination:
    def test_pagination_none_by_default(self) -> None:
        resp = DiffResponse()
        assert resp.pagination is None

    def test_pagination_field_present(self) -> None:
        pag = DiffPagination(offset=0, limit=100, total=200, has_more=True)
        resp = DiffResponse(pagination=pag)
        assert resp.pagination is not None
        assert resp.pagination.has_more is True

    def test_backward_compat_no_pagination(self) -> None:
        resp = DiffResponse(
            files=[FileDiff(path="a.txt", category=ChangeCategory.LOCAL_ONLY)],
            summary=DiffSummary(local_only=1, total=1),
        )
        data = resp.model_dump()
        assert data["pagination"] is None
        assert len(data["files"]) == 1


# --- Aggregate status schema tests ---


class TestPausedProfileSummary:
    def test_minimal(self) -> None:
        s = PausedProfileSummary(slug="default", name="Default")
        assert s.pending_changes == 0
        assert s.paused_at is None

    def test_with_fields(self) -> None:
        ts = datetime(2025, 3, 14, 10, 0, 0, tzinfo=timezone.utc)
        s = PausedProfileSummary(slug="obs", name="Obsidian", pending_changes=42, paused_at=ts)
        assert s.pending_changes == 42
        assert s.paused_at == ts


class TestProfileSummary:
    def test_defaults(self) -> None:
        s = ProfileSummary(slug="x", name="X", state=SyncState.IDLE)
        assert s.pending_changes == 0
        assert s.intervals_paused is False
        assert s.last_sync is None

    def test_full(self) -> None:
        ts = datetime(2025, 3, 14, 10, 0, 0, tzinfo=timezone.utc)
        s = ProfileSummary(
            slug="obs", name="Obsidian", state=SyncState.ERROR,
            last_sync=ts, pending_changes=100, intervals_paused=True,
        )
        assert s.state == SyncState.ERROR
        assert s.intervals_paused is True


class TestAggregateStatusResponse:
    def test_defaults(self) -> None:
        r = AggregateStatusResponse()
        assert r.overall_state == SyncState.IDLE
        assert r.total_pending_changes == 0
        assert r.paused_profiles == []
        assert r.profiles_summary == []

    def test_full(self) -> None:
        r = AggregateStatusResponse(
            overall_state=SyncState.ERROR,
            total_pending_changes=3045,
            paused_profiles=[PausedProfileSummary(slug="a", name="A", pending_changes=3)],
            profiles_summary=[ProfileSummary(slug="a", name="A", state=SyncState.IDLE, pending_changes=3)],
        )
        assert r.overall_state == SyncState.ERROR
        assert r.total_pending_changes == 3045
        assert len(r.paused_profiles) == 1
        assert len(r.profiles_summary) == 1


# --- Property-based tests for DiffPagination ---


@given(
    offset=st.integers(min_value=0, max_value=100_000),
    limit=st.integers(min_value=1, max_value=500),
    total=st.integers(min_value=0, max_value=100_000),
)
@settings(max_examples=100)
def test_diff_pagination_has_more_consistency(offset: int, limit: int, total: int) -> None:
    has_more = (offset + limit) < total
    p = DiffPagination(offset=offset, limit=limit, total=total, has_more=has_more)
    assert p.has_more == ((offset + limit) < total)


# --- Notification schemas (host-notifications 3.2) ---


class TestChannelConfig:
    def test_defaults(self) -> None:
        cfg = ChannelConfig()
        assert cfg.enabled is True
        assert cfg.min_severity == "warning"

    @pytest.mark.parametrize("severity", ["debug", "info", "warning", "error"])
    def test_every_severity_accepted(self, severity: str) -> None:
        assert ChannelConfig(min_severity=severity).min_severity == severity

    @pytest.mark.parametrize("severity", ["", "critical", "WARNING", "warn", " info", "info\n", "debugx"])
    def test_invalid_severity_rejected(self, severity: str) -> None:
        with pytest.raises(ValidationError):
            ChannelConfig(min_severity=severity)

    @pytest.mark.parametrize("severity", ["critical", "Error"])
    def test_partial_update_validates_severity_too(self, severity: str) -> None:
        with pytest.raises(ValidationError):
            ChannelConfigUpdate(min_severity=severity)
        assert ChannelConfigUpdate().min_severity is None


class TestNotificationConfigResponse:
    def test_serializes_channel_dict(self) -> None:
        resp = NotificationConfigResponse(channels={
            "webpush": ChannelConfig(),
            "host_native": {"enabled": False, "min_severity": "error"},
        })
        assert resp.model_dump(exclude_none=True) == {"channels": {  # as GET /notifications/config sends it
            "webpush": {"enabled": True, "min_severity": "warning"},
            "host_native": {"enabled": False, "min_severity": "error"},
        }}

    def test_invalid_channel_entry_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NotificationConfigResponse(channels={"webpush": {"min_severity": "loud"}})


class TestPushSubscriptionRequest:
    BASE = "https://fcm.googleapis.com/fcm/send/"

    def test_endpoint_at_max_length_accepted(self) -> None:
        endpoint = self.BASE + "a" * (2048 - len(self.BASE))
        assert len(endpoint) == 2048
        req = PushSubscriptionRequest(endpoint=endpoint, keys={"p256dh": "k", "auth": "a"})
        assert req.endpoint == endpoint
        assert req.keys == {"p256dh": "k", "auth": "a"}

    def test_endpoint_over_max_length_rejected(self) -> None:
        endpoint = self.BASE + "a" * (2049 - len(self.BASE))
        with pytest.raises(ValidationError) as info:
            PushSubscriptionRequest(endpoint=endpoint, keys={})
        assert info.value.errors()[0]["type"] == "string_too_long"


class TestTestNotificationResponse:
    def test_errors_is_channel_to_code(self) -> None:
        resp = NotificationTestResponse(
            success=False, channels_delivered=["webpush"], errors={"host_native": "unavailable"},
        )
        assert resp.model_dump() == {
            "success": False, "channels_delivered": ["webpush"], "errors": {"host_native": "unavailable"},
        }

    def test_errors_is_required(self) -> None:
        with pytest.raises(ValidationError):
            NotificationTestResponse(success=True, channels_delivered=[])  # type: ignore[call-arg]

    @pytest.mark.parametrize("errors", [["webpush"], {"webpush": ["failed"]}, {"webpush": None}, "failed"])
    def test_errors_must_map_strings_to_strings(self, errors) -> None:
        with pytest.raises(ValidationError):
            NotificationTestResponse(success=False, channels_delivered=[], errors=errors)


# --- Profile schemas (multi-sync-profiles 3.2) ---


class TestProfileCreateRequest:
    REQUIRED = {"name": "Work Docs", "local_dir": "/home/me/Docs", "remote_dir": "gdrive:Docs"}

    @pytest.mark.parametrize("missing", ["name", "local_dir", "remote_dir"])
    def test_required_fields(self, missing: str) -> None:
        body = {k: v for k, v in self.REQUIRED.items() if k != missing}
        with pytest.raises(ValidationError) as info:
            ProfileCreateRequest(**body)
        assert [(e["loc"], e["type"]) for e in info.value.errors()] == [((missing,), "missing")]

    @pytest.mark.parametrize("empty", ["name", "local_dir", "remote_dir"])
    def test_required_fields_may_not_be_empty(self, empty: str) -> None:
        with pytest.raises(ValidationError):
            ProfileCreateRequest(**{**self.REQUIRED, empty: ""})

    def test_optional_fields_use_defaults(self) -> None:
        req = ProfileCreateRequest(**self.REQUIRED)
        assert req.model_dump() == {
            **self.REQUIRED,
            "debounce_seconds": 5,
            "pull_interval_minutes": 5,
            "rclone_filter": [],
            "rclone_args": [],
            "max_retries": 3,
            "sync_mode": SyncMode.TWO_WAY,
            "bwlimit": None,
            "sync_window": None,
        }


class TestProfileStatusResponse:
    NOW = datetime(2026, 5, 6, 7, 8, 9, tzinfo=timezone.utc)

    def _profile(self, **kw) -> ProfileStatusResponse:
        return ProfileStatusResponse(
            id=1, slug="docs", name="Docs", local_dir="/d", remote_dir="r:d",
            created_at=self.NOW, updated_at=self.NOW, **kw,
        )

    def test_status_defaults(self) -> None:
        resp = self._profile()
        assert resp.state == SyncState.IDLE
        assert (resp.last_sync, resp.current_job_id, resp.last_error, resp.max_delete) == (None, None, None, None)
        assert (resp.files_processed, resp.errors, resp.pending_changes) == (0, 0, 0)
        assert resp.intervals_paused is False and resp.resync_required is False

    @pytest.mark.parametrize("state", list(SyncState))
    def test_serializes_sync_state_as_its_value(self, state: SyncState) -> None:
        resp = self._profile(state=state.value, last_sync=self.NOW, current_job_id=4, pending_changes=2)
        assert resp.state is state
        data = resp.model_dump(mode="json")
        assert data["state"] == state.value
        assert data["last_sync"] == "2026-05-06T07:08:09Z"
        assert (data["current_job_id"], data["pending_changes"], data["slug"]) == (4, 2, "docs")

    def test_unknown_state_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._profile(state="sleeping")


class TestGlobalConfigResponse:
    def test_only_global_fields(self) -> None:
        assert GlobalConfigResponse().model_dump() == {"log_level": "INFO", "history_days": 90}

    def test_per_profile_fields_are_dropped(self) -> None:
        resp = GlobalConfigResponse(
            log_level="DEBUG", local_dir="/sync", remote_dir="r:x", debounce_seconds=3, rclone_args=["--x"],
        )
        assert resp.model_dump() == {"log_level": "DEBUG", "history_days": 90}
