#!/usr/bin/env python3
"""Generate the TUI's API contract fixtures from the backend's Pydantic models.

Run from the repository root:

    PYTHONPATH=<env with pydantic>:<repo root> python3 tui/test/fixtures/gen_fixtures.py

For every response model the TUI decodes, this builds a representative
instance (every optional field set) and writes its ``model_dump_json()`` to
``responses/<Model>.json``. For every request model the TUI sends, it writes
the model's JSON schema to ``requests/<Model>.schema.json``.

The Go tests in tui/test/contract decode these files into the TUI's types.
Because the JSON comes from the backend's own models, a renamed or re-typed
field on either side fails the tests instead of silently decoding to zero.
Re-run this script whenever backend/api/schemas.py changes and commit the
result.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.api import schemas as s  # noqa: E402

T1 = datetime(2026, 9, 27, 8, 30, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 9, 27, 9, 45, 30, tzinfo=timezone.utc)

PROFILE_BASE: dict[str, Any] = dict(
    id=7,
    slug="docs",
    name="Dokumente Übersicht",
    local_dir="/home/user/Dokumente",
    remote_dir="gdrive:Backup/Dokumente",
    debounce_seconds=9,
    pull_interval_minutes=15,
    rclone_filter=["- *.tmp"],
    rclone_args=["--transfers", "4"],
    backup_dir="/home/user/.backup",
    max_retries=4,
    enabled=True,
    created_at=T1,
    updated_at=T2,
    sync_mode=s.SyncMode.TWO_WAY,
    bwlimit="08:00,512k 19:00,10M 23:00,off",
    sync_window=s.SyncWindow(days=[0, 1, 2, 3, 4], start="22:00", end="06:00"),
)

PROGRESS = s.SyncProgress(
    bytes=21188608, total_bytes=90000000, speed=1048576.5, eta_seconds=65, files_done=1, files_total=3,
    checks=4, total_checks=9,
    current_files=[s.ProgressFileResponse(name="Fotos/Urlaub ä.jpg", size=30000000, bytes=6942720, percentage=23)],
)


def responses() -> dict[str, BaseModel]:
    """Representative instances of every response model the TUI decodes."""
    return {
        # remote_accessible stays null: GET /health never contacts remotes
        # (GET /health/remotes does), and clients must not read null as down.
        "HealthResponse": s.HealthResponse(
            status="ok", rclone_installed=True, remote_accessible=None,
            uptime_seconds=3600.5, database_ok=True, version="0.9.0",
        ),
        "BrowseResponse": s.BrowseResponse(
            current="gdrive:Backup",
            parent="gdrive:",
            entries=[s.DirEntry(name="Dokumente Übersicht", path="gdrive:Backup/Dokumente Übersicht")],
        ),
        "RemoteHealthResponse": s.RemoteHealthResponse(
            remotes=[s.RemoteHealth(remote="gdrive", accessible=False, profiles=["docs"], auth_error=True)],
        ),
        "SyncStatusResponse": s.SyncStatusResponse(
            state=s.SyncState.ERROR, last_sync=T1, current_job_id=42,
            files_processed=12, errors=3, pending_changes=5,
            intervals_paused=True, paused_at=T2,
            last_error="Sync stopped: it would delete 120 files (limit 50)",
            resync_required=True, user_paused=True, progress=PROGRESS,
            outside_sync_window=True, next_window_start=T2, waiting_for_window=True,
        ),
        "SyncStartResponse": s.SyncStartResponse(
            job_id=42, state=s.SyncState.SYNCING, note="Outside this profile's sync window.",
        ),
        "PauseAllResponse": s.PauseAllResponse(
            changed=["docs"], unchanged=["photos"], still_paused={"docs": "Restored the local side."},
        ),
        "TrashListResponse": s.TrashListResponse(
            side=s.TrashSide.REMOTE, total_files=7, total_bytes=123456, truncated=True,
            entries=[s.TrashEntry(
                id="2026-09-27T08-30-00Z/Berichte/ä.txt", folder="2026-09-27T08-30-00Z", path="Berichte/ä.txt",
                size=1024, modified=T1, trashed_at=T2,
            )],
        ),
        "TrashActionResponse": s.TrashActionResponse(
            done=["2026-09-27T08-30-00Z/a.txt"],
            failed=[s.TrashItemError(id="2026-09-27T08-30-00Z/b.txt", code="target_newer", message="newer")],
        ),
        "SyncStopResponse": s.SyncStopResponse(state=s.SyncState.IDLE, message="Sync stopped"),
        "SyncPreviewResponse": s.SyncPreviewResponse(
            push=s.SyncPreviewCounts(deletes=120, replaces=3, creates=4, exceeds_max_delete=True),
            pull=s.SyncPreviewCounts(deletes=2, replaces=3, creates=120, exceeds_max_delete=False),
            excluded=5, max_delete=25, error="partial listing",
            sync_mode=s.SyncMode.TWO_WAY,
            two_way=s.TwoWayPreview(
                local=s.SyncPreviewCounts(deletes=1, replaces=2, creates=3, exceeds_max_delete=False),
                remote=s.SyncPreviewCounts(deletes=60, replaces=4, creates=5, exceeds_max_delete=True),
                conflicts=2, resync=True, resync_required=True, error="bisync dry run failed",
            ),
        ),
        "DiffResponse": s.DiffResponse(
            files=[
                s.FileDiff(
                    path="docs/ä.txt", category=s.ChangeCategory.MODIFIED_BOTH,
                    local_size=1024, remote_size=2048, local_mod_time=T1,
                    remote_mod_time=T2, is_conflict=True, manual_flag=True,
                ),
            ],
            summary=s.DiffSummary(
                local_only=1, remote_only=2, modified_local=3, modified_remote=4,
                modified_both=5, manual=6, total=21,
            ),
            pagination=s.DiffPagination(offset=10, limit=5, total=21, has_more=True),
            error="rclone warning",
        ),
        "SelectiveSyncResponse": s.SelectiveSyncResponse(
            job_id=43, total=3, succeeded=2, failed=1,
            errors=[s.FileError(path="x.txt", error="permission denied")],
        ),
        "ResumeIntervalsResponse": s.ResumeIntervalsResponse(detail="Intervals resumed"),
        "ErrorResponse": s.ErrorResponse(
            detail="Remotes with these names exist already: gdrive", code="name_clash",
            details={"names": ["gdrive"]},
        ),
        "SyncJobResponse": s.SyncJobResponse(
            id=42, direction=s.JobDirection.TWO_WAY, started_at=T1, finished_at=T2,
            status=s.JobStatus.COMPLETED, files_changed=17, conflicts=1, errors=2,
            profile_slug="docs", profile_name="Dokumente",
        ),
        "FileChangeResponse": s.FileChangeResponse(
            id=9, job_id=42, file_path="docs/report.pdf",
            action=s.FileChangeAction.MODIFIED, size_bytes=4096, side=s.FileSide.REMOTE,
        ),
        "ConflictResponse": s.ConflictResponse(
            id=5, job_id=42, file_path="docs/plan.odt", local_modified=T1,
            remote_modified=T2, resolved=True, resolution=s.ConflictResolution.KEEP_BOTH,
            profile_slug="docs", profile_name="Dokumente",
            local_kept_as="docs/plan.local-conflict1.odt", remote_kept_as="docs/plan.odt",
        ),
        "RemoteResponse": s.RemoteResponse(
            name="gdrive", type="drive", last_verified=T1, provider_id="drive",
            editable=False, reconnectable=True, auth_error=True,
        ),
        "RemoteTestResponse": s.RemoteTestResponse(success=False, latency_ms=87, error="timeout", auth_error=True),
        "RemoteConfigResponse": s.RemoteConfigResponse(
            name="box", type="sftp", provider_id="sftp",
            fields=[
                s.RemoteConfigField(name="host", value="files.example.com", is_set=True, secret=False),
                s.RemoteConfigField(name="pass", value="", is_set=True, secret=True),
            ],
            other_keys=["md5sum_command"],
        ),
        "ImportPreviewResponse": s.ImportPreviewResponse(
            remotes=[
                s.ImportCandidate(name="gdrive", type="drive", exists=True, problems=[], keys=["token"]),
                s.ImportCandidate(
                    name="evil", type="sftp", exists=False,
                    problems=["'ssh' runs a program on this machine and is not imported"], keys=["ssh"],
                ),
            ],
            errors=["This rclone.conf is encrypted."],
        ),
        "ImportRemotesResponse": s.ImportRemotesResponse(imported=["gdrive-imported"]),
        "RemoteStorageInfoResponse": s.RemoteStorageInfoResponse(
            total_bytes=1000, used_bytes=400, free_bytes=600, trashed_bytes=20, supported=True,
        ),
        "RemoteDependenciesResponse": s.RemoteDependenciesResponse(
            profiles=[s.RemoteDependencyProfile(slug="docs", name="Dokumente")],
            backup_targets=[
                s.RemoteDependencyBackupTarget(profile_slug="docs", target_name="Nightly", target_id=3),
            ],
        ),
        "LogEntryResponse": s.LogEntryResponse(timestamp=T1, level="WARNING", message="disk almost full"),
        "ProviderResponse": s.ProviderResponse(
            id="s3", display_name="Amazon S3", icon="s3", auth_type=s.AuthType.KEY,
            fields=[
                s.ProviderFieldResponse(
                    name="secret_access_key", label="Secret access key",
                    field_type=s.FieldType.PASSWORD, required=True, help_text="From the IAM console",
                    options=["standard", "off"], default="standard",
                ),
            ],
            default_name="s3", setup_guide="Create an access key first.",
        ),
        "AuthorizeResponse": s.AuthorizeResponse(
            session_id="sess-123", auth_url="https://accounts.example.com/o/oauth2/auth?state=sess-123",
            redirect_uri="http://127.0.0.1:8000/wizard/oauth/callback",
        ),
        "OAuthRedirectResponse": s.OAuthRedirectResponse(
            redirect_uri="http://127.0.0.1:8000/wizard/oauth/callback",
        ),
        "WizardSessionResponse": s.WizardSessionResponse(
            session_id="sess-123", status=s.WizardSessionStatus.FAILED,
            auth_url="https://accounts.example.com/o/oauth2/auth?state=sess-123",
            error="Failed to exchange authorization code for token",
        ),
        "TestRemoteResponse": s.TestRemoteResponse(success=False, error="bad credentials", auth_error=True),
        "TestSyncResponse": s.TestSyncResponse(
            success=False, steps=[{"step": "upload", "ok": True}], error="download failed",
        ),
        "NotificationConfigResponse": s.NotificationConfigResponse(
            channels={
                "desktop": s.ChannelConfig(enabled=False, min_severity="error"),
                "webhook": s.ChannelConfig(
                    enabled=True, min_severity="warning",
                    webhook=s.WebhookSettingsView(
                        url="http://192.168.1.5:8123/api/webhook/omni", allow_http=True,
                        headers=[s.WebhookHeaderView(name="Authorization", value_set=True)],
                    ),
                ),
                "ntfy": s.ChannelConfig(
                    enabled=True, min_severity="info",
                    ntfy=s.NtfySettingsView(
                        server="https://ntfy.sh", topic="omnisync-nas", allow_http=False,
                        username="nas", token_set=False, password_set=True,
                    ),
                ),
                "email": s.ChannelConfig(
                    enabled=False, min_severity="error",
                    email=s.EmailSettingsView(
                        host="smtp.example.com", port=465, security="tls", username="nas",
                        password_set=True, from_addr="nas@example.com", to=["a@example.com", "b@example.com"],
                    ),
                ),
            },
        ),
        "ChannelStatusResponse": s.ChannelStatusResponse(
            channels={
                "desktop": s.ChannelStatusInfo(
                    available=True, detection_method="notify-send", host_os="linux",
                    missing_dependencies=["libnotify"], permission_status="granted",
                ),
            },
        ),
        "NotificationHistoryResponse": s.NotificationHistoryResponse(
            items=[
                s.NotificationLogEntry(
                    id=11, event_type="sync_failed", severity="error", title="Sync failed",
                    body="Push of docs failed", timestamp=T1.isoformat(),
                    channels_delivered=["desktop"],
                ),
            ],
            total=31,
        ),
        "TestNotificationResponse": s.TestNotificationResponse(
            success=True, channels_delivered=["desktop"], errors={"webpush": "no subscribers"},
        ),
        "ProfileResponse": s.ProfileResponse(**PROFILE_BASE),
        "ProfileStatusResponse": s.ProfileStatusResponse(
            **PROFILE_BASE, state=s.SyncState.PULLING, last_sync=T1, current_job_id=42,
            files_processed=12, errors=1, pending_changes=5, intervals_paused=True, paused_at=T2,
            last_error="Sync stopped: it would delete 120 files (limit 50)", max_delete=25,
            resync_required=True, user_paused=True, progress=PROGRESS,
            outside_sync_window=True, next_window_start=T2, waiting_for_window=True,
        ),
        "GlobalConfigResponse": s.GlobalConfigResponse(log_level="DEBUG", history_days=30),
        "AggregateStatusResponse": s.AggregateStatusResponse(
            overall_state=s.SyncState.PUSHING, total_pending_changes=8,
            paused_profiles=[
                s.PausedProfileSummary(
                    slug="docs", name="Dokumente", pending_changes=8, paused_at=T2, user_paused=True,
                ),
            ],
            profiles_summary=[
                s.ProfileSummary(
                    slug="docs", name="Dokumente", state=s.SyncState.PUSHING, last_sync=T1,
                    pending_changes=8, intervals_paused=True,
                    last_error="Local folder '/home/user/Dokumente' is missing or not mounted.",
                    resync_required=True, user_paused=True, progress=PROGRESS,
                ),
            ],
        ),
        "BackupTargetResponse": s.BackupTargetResponse(
            id=3, profile_id=7, name="Nightly", target_path="b2:backups/docs",
            target_type=s.BackupTargetType.CUSTOM_REMOTE, remote_name="b2",
            retention_days=30, keep_last=5, frequency_hours=24, backup_mode=s.BackupMode.ARCHIVE,
            enabled=True, encrypted=True, verify_after_backup=True,
            overdue=True, last_liveness_ok=False, last_liveness_error="unreachable",
            last_backup_at=T1, last_backup_status="failed",
            last_verify_status="failed", last_verify_message="1 file(s) differ from the folder",
            next_scheduled_at=T2, created_at=T1, updated_at=T2,
        ),
        "BackupJobResponse": s.BackupJobResponse(
            id=77, target_id=3, started_at=T1, finished_at=T2,
            status=s.BackupJobStatus.COMPLETED, direction="backup", size_bytes=123456,
            snapshot_id="2026-09-27T08-30-00", error_message="partial",
            verify_status="verified", verify_message="12 file(s) match the folder",
        ),
        "SnapshotResponse": s.SnapshotResponse(
            snapshot_id="2026-09-27T08-30-00", created_at=T1, size_bytes=123456, status="completed",
        ),
        "SnapshotFilesResponse": s.SnapshotFilesResponse(
            snapshot_id="2026-09-27T08-30-00", path="Berichte", search="ü",
            entries=[
                s.SnapshotFileEntry(path="Berichte/Übersicht", name="Übersicht", is_dir=True, size=2048,
                                    file_count=3),
                s.SnapshotFileEntry(path="Berichte/a.txt", name="a.txt", is_dir=False, size=12, mod_time=T1),
            ],
            total=42, offset=10, limit=2, snapshot_files=99,
        ),
        "RestorePreviewResponse": s.RestorePreviewResponse(
            snapshot_id="2026-09-27T08-30-00", restore_scope=s.RestoreScope.BOTH, exact=True,
            sides=[
                s.RestorePreviewSide(side="local", path="/home/user/Dokumente", added=1, replaced=2, removed=3,
                                     unchanged=4, added_examples=["a.txt"], replaced_examples=["b.txt"],
                                     removed_examples=["c.txt"]),
            ],
        ),
    }


REQUEST_MODELS = [
    "ProfileCreateRequest",
    "ProfileUpdateRequest",
    "SyncStartRequest",
    "ResyncRequest",
    "SelectiveSyncRequest",
    "ConflictResolveRequest",
    "AuthorizeRequest",
    "CreateRemoteRequest",
    "ReconnectRemoteRequest",
    "UpdateRemoteRequest",
    "ImportConfigRequest",
    "ImportRemotesRequest",
    "TestRemoteRequest",
    "TestSyncRequest",
    "BackupTargetCreateRequest",
    "BackupTargetUpdateRequest",
    "RestoreRequest",
    "RestoreFilesRequest",
    "NotificationConfigUpdateRequest",
    "TestNotificationRequest",
    "GlobalConfigUpdateRequest",
    "TrashActionRequest",
]


def main() -> None:
    resp_dir = HERE / "responses"
    req_dir = HERE / "requests"
    resp_dir.mkdir(exist_ok=True)
    req_dir.mkdir(exist_ok=True)

    for name, model in responses().items():
        data = json.loads(model.model_dump_json())
        (resp_dir / f"{name}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    for name in REQUEST_MODELS:
        schema = getattr(s, name).model_json_schema()
        (req_dir / f"{name}.schema.json").write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")

    print(f"wrote {len(responses())} response fixtures and {len(REQUEST_MODELS)} request schemas to {HERE}")


if __name__ == "__main__":
    main()
