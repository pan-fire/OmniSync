"""Every documented error answer of the backup routes, and what each one leaves unchanged.

Through HTTP against the app wired by test_client: the database is real
(test_db_factory), the BackupService is test_services' spec'd mock, so each
test makes it fail the way the real one does and checks the answer (status,
stable ``code``, no rclone or exception text) and that nothing was started,
scheduled, written or deleted. The real service's side of these refusals is
in test_backup_safety_integration.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from backend.api.routes import backups as backup_routes
from backend.api.schemas import BackupMode, BackupTargetType, RestoreScope
from backend.db.models import BackupJob, BackupJobStatus, BackupTarget, SyncProfile
from backend.exceptions import RcloneError, SyncBusyError
from backend.main import app
from backend.services.backup_service import BackupRunning, RestoreRefused, SnapshotFile

# Text that must never reach a client: what rclone or the OS said.
LEAK = "rclone said: token=fake-secret-1234 at /srv/private/rclone.conf"
SNAPSHOT = "2026-01-02T03-04-05"


@pytest.fixture
def svc(test_services):
    """The backup service the routes use; schedule lookups answer "not scheduled"."""
    service = test_services.backup_service
    service.get_next_run_time.return_value = None
    service.remote_names.return_value = {"gdrive"}
    return service


async def add_profile(factory, slug: str, local_dir: str = "/sync/docs") -> int:
    now = datetime.now(timezone.utc)
    async with factory() as session:
        profile = SyncProfile(
            slug=slug, name=slug.title(), local_dir=local_dir, remote_dir=f"gdrive:{slug}",
            created_at=now, updated_at=now,
        )
        session.add(profile)
        await session.commit()
        return profile.id


async def add_target(factory, profile_id: int, path: str = "/backups/docs", name: str = "Nightly") -> int:
    now = datetime.now(timezone.utc)
    async with factory() as session:
        target = BackupTarget(
            profile_id=profile_id, name=name, target_path=path,
            target_type=BackupTargetType.LOCAL.value, retention_days=7, frequency_hours=24,
            backup_mode=BackupMode.MIRROR.value, enabled=True, created_at=now, updated_at=now,
        )
        session.add(target)
        await session.commit()
        return target.id


async def add_job(factory, target_id: int, status: str = BackupJobStatus.COMPLETED.value,
                  direction: str = "backup") -> BackupJob:
    async with factory() as session:
        job = BackupJob(target_id=target_id, started_at=datetime.now(timezone.utc), status=status,
                        direction=direction)
        session.add(job)
        await session.commit()
        return job


async def target_row(factory, target_id: int) -> BackupTarget | None:
    async with factory() as session:
        return await session.get(BackupTarget, target_id)


async def count_targets(factory) -> int:
    async with factory() as session:
        return (await session.execute(select(func.count()).select_from(BackupTarget))).scalar_one()


def error(resp, status: int, code: str) -> dict:
    """The error envelope of ``resp``, after checking its status and code and that nothing leaked."""
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert body["code"] == code, body
    assert {"detail", "code"} <= set(body) <= {"detail", "code", "details", "request_id"}
    assert "fake-secret" not in resp.text and "/srv/private" not in resp.text
    return body


@pytest.fixture
async def docs(test_db_factory) -> tuple[int, int]:
    """(profile id, target id) of profile 'docs' with one local mirror target."""
    profile_id = await add_profile(test_db_factory, "docs")
    return profile_id, await add_target(test_db_factory, profile_id)


# ── Authentication ───────────────────────────────────────────────────


@pytest.mark.parametrize(("method", "path", "body"), [
    ("POST", "/profiles/docs/backups/{t}/run", None),
    ("POST", "/profiles/docs/backups/{t}/restore", {"snapshot_id": SNAPSHOT, "restore_scope": "local_only"}),
    ("POST", "/profiles/docs/backups/{t}/restore-files", {"snapshot_id": SNAPSHOT, "paths": ["a.txt"]}),
    ("DELETE", "/profiles/docs/backups/{t}?confirm=true", None),
    ("PUT", "/profiles/docs/backups/{t}", {"name": "Renamed"}),
    ("GET", "/profiles/docs/backups/{t}/snapshots/" + SNAPSHOT + "/files", None),
])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong-token"}, {"Authorization": "Basic x"}])
async def test_no_backup_action_without_the_api_token(test_client, test_db_factory, svc, docs, method, path, body,
                                                     headers):
    """Without the right token a request is refused before it reaches the service or the database."""
    _, target_id = docs
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as bare:
        resp = await bare.request(method, path.format(t=target_id), json=body)
    assert resp.status_code == 401
    assert resp.json()["code"] in ("token_missing", "token_invalid")
    assert resp.headers["www-authenticate"] == "Bearer"
    for call in (svc.start_backup, svc.start_restore, svc.start_restore_files, svc.snapshot_files):
        call.assert_not_awaited()
    svc.unschedule_target.assert_not_called()
    target = await target_row(test_db_factory, target_id)
    assert target is not None and target.name == "Nightly"


# ── Wiring ───────────────────────────────────────────────────────────


async def test_unwired_service_or_database_is_503(test_client, monkeypatch):
    """Before startup wired the routes (or after shutdown) the routes answer 503, never crash."""
    monkeypatch.setattr(backup_routes, "_backup_service", None)
    error(await test_client.get("/profiles/docs/backups"), 503, "service_unavailable")
    monkeypatch.undo()
    monkeypatch.setattr(backup_routes, "_db_factory", None)
    error(await test_client.get("/profiles/docs/backups/1/jobs/1"), 503, "service_unavailable")


# ── Not found, and no reach into another profile ─────────────────────


async def test_unknown_profile_is_404(test_client, svc):
    for resp in (
        await test_client.get("/profiles/nope/backups"),
        await test_client.post("/profiles/nope/backups", json={
            "name": "x", "target_path": "/backups/x", "target_type": "local"}),
        await test_client.post("/profiles/nope/backups/1/run"),
    ):
        error(resp, 404, "profile_not_found")
    svc.start_backup.assert_not_awaited()


async def test_a_target_is_only_reachable_through_its_own_profile(test_client, test_db_factory, svc, docs):
    """Another profile's slug never reaches a target: no read, edit, run, restore or delete through it."""
    _, target_id = docs
    await add_profile(test_db_factory, "photos", local_dir="/sync/photos")
    job = await add_job(test_db_factory, target_id)
    base = f"/profiles/photos/backups/{target_id}"
    restore = {"snapshot_id": SNAPSHOT, "restore_scope": "local_only"}
    for resp in (
        await test_client.get(base),
        await test_client.put(base, json={"name": "Hijacked"}),
        await test_client.delete(base, params={"confirm": "true"}),
        await test_client.post(f"{base}/run"),
        await test_client.get(f"{base}/jobs/{job.id}"),
        await test_client.get(f"{base}/snapshots"),
        await test_client.get(f"{base}/snapshots/{SNAPSHOT}/files"),
        await test_client.post(f"{base}/restore", json=restore),
        await test_client.post(f"{base}/restore/preview", json=restore),
        await test_client.post(f"{base}/restore-files", json={"snapshot_id": SNAPSHOT, "paths": ["a"]}),
    ):
        error(resp, 404, "backup_target_not_found")
    target = await target_row(test_db_factory, target_id)
    assert target is not None and target.name == "Nightly"
    for call in (svc.start_backup, svc.start_restore, svc.start_restore_files, svc.snapshot_files,
                 svc.restore_preview, svc.list_snapshots):
        call.assert_not_awaited()
    svc.unschedule_target.assert_not_called()


async def test_a_job_is_only_found_under_its_own_target(test_client, test_db_factory, docs):
    profile_id, target_id = docs
    other = await add_target(test_db_factory, profile_id, "/backups/other", name="Weekly")
    job = await add_job(test_db_factory, other)
    assert (await test_client.get(f"/profiles/docs/backups/{other}/jobs/{job.id}")).status_code == 200
    error(await test_client.get(f"/profiles/docs/backups/{target_id}/jobs/{job.id}"), 404, "backup_job_not_found")
    error(await test_client.get(f"/profiles/docs/backups/{target_id}/jobs/999"), 404, "backup_job_not_found")


# ── Creating and editing targets ─────────────────────────────────────


async def test_create_outside_the_browse_roots_is_refused(test_client, test_db_factory, svc, docs, monkeypatch,
                                                          tmp_path: Path):
    monkeypatch.setenv("OMNISYNC_BROWSE_ROOTS", str(tmp_path / "allowed"))
    resp = await test_client.post("/profiles/docs/backups", json={
        "name": "Outside", "target_path": str(tmp_path / "elsewhere"), "target_type": "local"})
    error(resp, 422, "path_not_allowed")
    assert await count_targets(test_db_factory) == 1
    svc.schedule_target.assert_not_called()


async def test_create_on_an_unconfigured_remote_is_refused(test_client, test_db_factory, svc, docs):
    resp = await test_client.post("/profiles/docs/backups", json={
        "name": "Cloud", "target_path": "dropbox:Backups", "target_type": "remote"})
    body = error(resp, 422, "remote_not_configured")
    assert "dropbox" in body["detail"]
    assert await count_targets(test_db_factory) == 1


async def test_create_when_rclone_cannot_list_remotes_is_503_without_its_output(test_client, test_db_factory, svc,
                                                                                docs, caplog):
    """rclone failing is a 503 whose answer says nothing of what rclone printed; the log has it."""
    svc.remote_names.side_effect = RcloneError(LEAK)
    resp = await test_client.post("/profiles/docs/backups", json={
        "name": "Cloud", "target_path": "gdrive:Backups", "target_type": "remote"})
    error(resp, 503, "rclone_unavailable")
    assert await count_targets(test_db_factory) == 1
    assert any(r.exc_info for r in caplog.records if r.name == "backend.api.routes.backups")


async def test_create_when_the_passphrase_cannot_be_stored_creates_nothing(test_client, test_db_factory, svc, docs):
    """A target that would be written unencrypted although a passphrase was asked for is never created."""
    svc.obscure_passphrase.side_effect = OSError(LEAK)
    resp = await test_client.post("/profiles/docs/backups", json={
        "name": "Vault", "target_path": "/backups/vault", "target_type": "local",
        "encryption_passphrase": "correct horse battery"})
    error(resp, 503, "rclone_unavailable")
    assert await count_targets(test_db_factory) == 1
    svc.schedule_target.assert_not_called()


@pytest.mark.parametrize("path", ["/backups/docs", "/backups/docs/inner", "/backups", "/sync/docs/backup", "/sync"])
async def test_create_overlapping_a_target_or_a_synced_folder_is_refused(test_client, test_db_factory, svc, docs,
                                                                         path):
    """Two targets in one folder (or one inside the other) would rotate and later delete each other's files."""
    resp = await test_client.post("/profiles/docs/backups", json={
        "name": "Again", "target_path": path, "target_type": "local"})
    error(resp, 400, "path_overlap")
    assert await count_targets(test_db_factory) == 1


async def test_create_inside_another_profiles_folder_is_refused_and_named(test_client, test_db_factory, svc, docs):
    """A backup inside any synced folder, not only this profile's, would be synced (and deleted) with it."""
    await add_profile(test_db_factory, "photos", local_dir="/sync/photos")
    resp = await test_client.post("/profiles/docs/backups", json={
        "name": "Inside", "target_path": "/sync/photos/backup", "target_type": "local"})
    body = error(resp, 400, "path_overlap")
    assert "photos" in body["detail"] and "local_dir" in body["detail"]
    assert await count_targets(test_db_factory) == 1


async def test_a_disabled_target_is_created_but_not_scheduled(test_client, svc, docs):
    resp = await test_client.post("/profiles/docs/backups", json={
        "name": "Manual", "target_path": "/backups/manual", "target_type": "local", "enabled": False})
    assert resp.status_code == 201, resp.text
    assert resp.json()["enabled"] is False
    svc.schedule_target.assert_not_called()


@pytest.mark.parametrize("change", [
    {"target_type": "remote"},  # a local path on a remote type
    {"target_path": "relative/dir"},
    {"target_type": "custom_remote", "target_path": "gdrive:B", "remote_name": "other"},
])
async def test_update_to_an_inconsistent_location_is_refused(test_client, test_db_factory, svc, docs, change):
    _, target_id = docs
    resp = await test_client.put(f"/profiles/docs/backups/{target_id}", json=change)
    error(resp, 422, "invalid_backup_target")
    target = await target_row(test_db_factory, target_id)
    assert target is not None and (target.target_path, target.target_type) == ("/backups/docs", "local")


async def test_update_moving_onto_another_target_is_refused(test_client, test_db_factory, svc, docs):
    profile_id, target_id = docs
    await add_target(test_db_factory, profile_id, "/backups/weekly", name="Weekly")
    resp = await test_client.put(f"/profiles/docs/backups/{target_id}", json={"target_path": "/backups/weekly/x"})
    error(resp, 400, "path_overlap")
    target = await target_row(test_db_factory, target_id)
    assert target is not None and target.target_path == "/backups/docs"


async def test_passphrase_change_over_existing_backups_is_refused(test_client, test_db_factory, svc, docs):
    """Backups written with the old passphrase would become unreadable and never be cleaned up."""
    _, target_id = docs
    svc.location_has_data.return_value = True
    resp = await test_client.put(f"/profiles/docs/backups/{target_id}",
                                 json={"name": "Renamed", "encryption_passphrase": "a brand new passphrase"})
    error(resp, 409, "backup_location_not_empty")
    target = await target_row(test_db_factory, target_id)
    assert target is not None and target.encryption_password is None and target.name == "Nightly"
    svc.obscure_passphrase.assert_not_awaited()


async def test_passphrase_change_when_the_location_cannot_be_checked_is_503(test_client, test_db_factory, svc, docs):
    """Not knowing whether backups exist is no licence to change the passphrase."""
    _, target_id = docs
    svc.location_has_data.side_effect = RcloneError(LEAK)
    resp = await test_client.put(f"/profiles/docs/backups/{target_id}",
                                 json={"encryption_passphrase": "a brand new passphrase"})
    error(resp, 503, "rclone_unavailable")
    target = await target_row(test_db_factory, target_id)
    assert target is not None and target.encryption_password is None


async def test_update_of_the_mode_only_does_not_reschedule(test_client, test_db_factory, svc, docs):
    _, target_id = docs
    resp = await test_client.put(f"/profiles/docs/backups/{target_id}", json={"backup_mode": "archive"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["backup_mode"] == "archive"
    target = await target_row(test_db_factory, target_id)
    assert target is not None and target.backup_mode == "archive"
    svc.reschedule_target.assert_not_awaited()


@pytest.mark.parametrize("params", [{}, {"confirm": "false"}])
async def test_delete_needs_confirmation(test_client, test_db_factory, svc, docs, params):
    _, target_id = docs
    error(await test_client.delete(f"/profiles/docs/backups/{target_id}", params=params), 400, "confirmation_required")
    assert await target_row(test_db_factory, target_id) is not None
    svc.unschedule_target.assert_not_called()


# ── Starting runs ────────────────────────────────────────────────────


@pytest.mark.parametrize(("raised", "status", "code"), [
    (BackupRunning("Backup already running for target 1"), 409, "backup_running"),
    (SyncBusyError("A sync, backup or restore of this profile is running or waiting."), 409, "sync_busy"),
    (ValueError("BackupTarget 1 not found"), 404, "backup_target_not_found"),  # deleted meanwhile
])
async def test_backup_start_refusals(test_client, svc, docs, raised, status, code):
    _, target_id = docs
    svc.start_backup.side_effect = raised
    error(await test_client.post(f"/profiles/docs/backups/{target_id}/run"), status, code)


@pytest.mark.parametrize(("raised", "status", "code"), [
    (SyncBusyError("busy"), 409, "sync_busy"),
    (ValueError("BackupTarget 1 not found"), 404, "backup_target_not_found"),
])
async def test_restore_start_refusals(test_client, svc, docs, raised, status, code):
    _, target_id = docs
    svc.start_restore.side_effect = raised
    resp = await test_client.post(f"/profiles/docs/backups/{target_id}/restore",
                                  json={"snapshot_id": SNAPSHOT, "restore_scope": "both"})
    error(resp, status, code)


@pytest.fixture
async def profile_in_data_dir(test_db_factory, monkeypatch, tmp_path: Path) -> int:
    """A target of a profile stored (before the check existed) with OmniSync's data folder as its local folder."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("OMNISYNC_DB_PATH", str(data / "omnisync.db"))
    profile_id = await add_profile(test_db_factory, "legacy", local_dir=str(data))
    return await add_target(test_db_factory, profile_id, str(tmp_path / "backups"))


@pytest.mark.parametrize("scope", ["local_only", "both"])
async def test_restore_never_writes_over_the_data_directory(test_client, svc, profile_in_data_dir, scope):
    """A full restore into OmniSync's own folder would overwrite the credentials and the API token."""
    resp = await test_client.post(f"/profiles/legacy/backups/{profile_in_data_dir}/restore",
                                  json={"snapshot_id": SNAPSHOT, "restore_scope": scope})
    error(resp, 422, "path_not_allowed")
    svc.start_restore.assert_not_awaited()


async def test_a_remote_only_restore_does_not_touch_the_local_folder_so_it_may_run(test_client, test_db_factory, svc,
                                                                                   profile_in_data_dir):
    svc.start_restore.return_value = await add_job(
        test_db_factory, profile_in_data_dir, BackupJobStatus.RUNNING.value, "restore")
    resp = await test_client.post(f"/profiles/legacy/backups/{profile_in_data_dir}/restore",
                                  json={"snapshot_id": SNAPSHOT, "restore_scope": "remote_only"})
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "running" and resp.json()["error_code"] is None
    svc.start_restore.assert_awaited_once_with(profile_in_data_dir, SNAPSHOT, RestoreScope.REMOTE_ONLY)


async def test_restore_files_never_writes_over_the_data_directory(test_client, svc, profile_in_data_dir):
    resp = await test_client.post(f"/profiles/legacy/backups/{profile_in_data_dir}/restore-files",
                                  json={"snapshot_id": SNAPSHOT, "paths": ["a.txt"]})
    error(resp, 422, "path_not_allowed")
    svc.start_restore_files.assert_not_awaited()


@pytest.mark.parametrize("inside", ["", "/sub", "/.."])
async def test_restore_files_never_into_a_backup_target(test_client, test_db_factory, svc, docs, inside):
    """Restored files inside a target folder would be rotated into versions/ and deleted by retention."""
    _, target_id = docs
    profile_id = await add_profile(test_db_factory, "photos", local_dir="/sync/photos")
    await add_target(test_db_factory, profile_id, "/backups/photos", name="Photos")
    resp = await test_client.post(f"/profiles/docs/backups/{target_id}/restore-files", json={
        "snapshot_id": SNAPSHOT, "paths": ["a.txt"], "target_dir": "/backups/photos" + inside})
    body = error(resp, 422, "path_overlap")
    assert "target_dir" in body["detail"]
    svc.start_restore_files.assert_not_awaited()


async def test_restore_files_outside_the_browse_roots_is_refused(test_client, svc, docs, monkeypatch,
                                                                tmp_path: Path):
    _, target_id = docs
    monkeypatch.setenv("OMNISYNC_BROWSE_ROOTS", str(tmp_path / "allowed"))
    resp = await test_client.post(f"/profiles/docs/backups/{target_id}/restore-files", json={
        "snapshot_id": SNAPSHOT, "paths": ["a.txt"], "target_dir": str(tmp_path / "elsewhere")})
    error(resp, 422, "path_not_allowed")
    svc.start_restore_files.assert_not_awaited()


# ── Reading snapshots ────────────────────────────────────────────────


# What the service raises -> the answer (see _snapshot_call).
SNAPSHOT_FAILURES = [
    (ValueError(f"Snapshot '{SNAPSHOT}' not found at the target"), 404, "snapshot_not_found"),
    (RestoreRefused(f"The archive {SNAPSHOT} cannot be read; it may be damaged."), 409, "snapshot_unavailable"),
    (SyncBusyError("busy"), 409, "sync_busy"),
    (RcloneError(LEAK), 502, "rclone_failed"),
]


@pytest.mark.parametrize(("raised", "status", "code"), SNAPSHOT_FAILURES)
async def test_snapshot_read_failures(test_client, svc, docs, raised, status, code):
    """Browsing, previewing and restoring files of a snapshot that cannot be read: one mapping for all three."""
    _, target_id = docs
    base = f"/profiles/docs/backups/{target_id}"
    svc.snapshot_files.side_effect = raised
    svc.restore_preview.side_effect = raised
    svc.start_restore_files.side_effect = raised
    for resp in (
        await test_client.get(f"{base}/snapshots/{SNAPSHOT}/files"),
        await test_client.post(f"{base}/restore/preview", json={"snapshot_id": SNAPSHOT, "restore_scope": "both"}),
        await test_client.post(f"{base}/restore-files", json={"snapshot_id": SNAPSHOT, "paths": ["a.txt"]}),
    ):
        body = error(resp, status, code)
        if isinstance(raised, (ValueError, RestoreRefused)):
            assert body["detail"] == str(raised)  # OmniSync's own words


# Percent-encoded where the client would otherwise normalise the URL.
@pytest.mark.parametrize("snapshot", ["a..b", "..hidden", " ", "%2E%2E", "%2E", "x%0Ay", "x%00y", "x%5Cy"])
async def test_malformed_snapshot_ids_never_reach_the_service(test_client, svc, docs, snapshot):
    _, target_id = docs
    resp = await test_client.get(f"/profiles/docs/backups/{target_id}/snapshots/{snapshot}/files")
    error(resp, 422, "invalid_snapshot_id")
    svc.snapshot_files.assert_not_awaited()


@pytest.mark.parametrize("snapshot", ["../etc", "a/b", "a\\b"])
async def test_snapshot_ids_with_separators_are_refused_in_bodies(test_client, svc, docs, snapshot):
    _, target_id = docs
    base = f"/profiles/docs/backups/{target_id}"
    for resp in (
        await test_client.post(f"{base}/restore", json={"snapshot_id": snapshot, "restore_scope": "both"}),
        await test_client.post(f"{base}/restore-files", json={"snapshot_id": snapshot, "paths": ["a.txt"]}),
    ):
        error(resp, 422, "validation_failed")
    svc.start_restore.assert_not_awaited()
    svc.start_restore_files.assert_not_awaited()


async def test_odd_file_names_are_listed_as_they_are(test_client, svc, docs):
    """Unicode, spaces, a leading '-' and a very long name come back unchanged, folders first."""
    _, target_id = docs
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    long_name = "n" * 240 + ".txt"
    names = ["-rf.txt", "with space.txt", "Ünïcödé/файл.txt", "Ünïcödé/日本.txt", long_name]
    svc.snapshot_files.return_value = [SnapshotFile(p, 3, when) for p in names]
    files = f"/profiles/docs/backups/{target_id}/snapshots/{SNAPSHOT}/files"
    top = (await test_client.get(files)).json()
    assert [(e["name"], e["is_dir"]) for e in top["entries"]] == [
        ("Ünïcödé", True), ("-rf.txt", False), (long_name, False), ("with space.txt", False),
    ]
    inner = (await test_client.get(files, params={"path": "Ünïcödé"})).json()
    assert [e["path"] for e in inner["entries"]] == ["Ünïcödé/файл.txt", "Ünïcödé/日本.txt"]
    found = (await test_client.get(files, params={"search": "  ФАЙЛ "})).json()
    assert [e["path"] for e in found["entries"]] == ["Ünïcödé/файл.txt"]
