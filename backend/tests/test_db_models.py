"""Property-based tests for database ORM models: job lifecycle, file change
recording and the SyncJob JSON round-trip.
"""

import json
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from backend.db.models import Base, BackupJob, BackupTarget, FileChange, SyncJob, SyncProfile


# --- Hypothesis strategies ---

directions = st.sampled_from(["push", "pull"])
statuses = st.sampled_from(["running", "completed", "failed"])
actions = st.sampled_from(["created", "modified", "deleted"])
non_negative_ints = st.integers(min_value=0, max_value=10_000)

# Datetimes that are JSON-safe (no microseconds lost, UTC-aware)
safe_datetimes = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2099, 12, 31),
    timezones=st.just(timezone.utc),
)

optional_datetimes = st.one_of(st.none(), safe_datetimes)

# File paths: printable strings that look like paths (non-empty, no NUL bytes)
file_paths = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S"), blacklist_characters="\x00"),
    min_size=1,
    max_size=200,
)

optional_size_bytes = st.one_of(st.none(), st.integers(min_value=0, max_value=10_000_000))


def syncjob_field_dict(
    direction: str,
    started_at: datetime,
    finished_at: datetime | None,
    status: str,
    files_changed: int,
    conflicts: int,
    errors: int,
) -> dict:
    """Build a dict of SyncJob column values (excluding auto-generated id)."""
    return {
        "direction": direction,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat() if finished_at is not None else None,
        "status": status,
        "files_changed": files_changed,
        "conflicts": conflicts,
        "errors": errors,
    }


# --- Property test ---


@settings(max_examples=100)
@given(
    direction=directions,
    started_at=safe_datetimes,
    finished_at=optional_datetimes,
    status=statuses,
    files_changed=non_negative_ints,
    conflicts=non_negative_ints,
    errors=non_negative_ints,
)
def test_syncjob_json_round_trip(
    direction: str,
    started_at: datetime,
    finished_at: datetime | None,
    status: str,
    files_changed: int,
    conflicts: int,
    errors: int,
) -> None:
    """SyncJob JSON round-trip.

    For any valid SyncJob data, serializing to JSON then deserializing back
    should produce an equivalent SyncJob record.
    """
    # Build the field dict
    original = syncjob_field_dict(
        direction, started_at, finished_at, status, files_changed, conflicts, errors
    )

    # Serialize to JSON string
    json_str = json.dumps(original)

    # Deserialize back
    restored = json.loads(json_str)

    # Assert all fields match
    assert restored["direction"] == direction
    assert restored["started_at"] == started_at.isoformat()
    assert restored["status"] == status
    assert restored["files_changed"] == files_changed
    assert restored["conflicts"] == conflicts
    assert restored["errors"] == errors

    if finished_at is not None:
        assert restored["finished_at"] == finished_at.isoformat()
    else:
        assert restored["finished_at"] is None

    # Also verify we can construct a SyncJob ORM instance from the restored data
    job = SyncJob(
        direction=restored["direction"],
        started_at=datetime.fromisoformat(restored["started_at"]),
        finished_at=(
            datetime.fromisoformat(restored["finished_at"])
            if restored["finished_at"] is not None
            else None
        ),
        status=restored["status"],
        files_changed=restored["files_changed"],
        conflicts=restored["conflicts"],
        errors=restored["errors"],
    )

    assert job.direction == direction
    assert job.started_at == started_at
    assert job.finished_at == finished_at
    assert job.status == status
    assert job.files_changed == files_changed
    assert job.conflicts == conflicts
    assert job.errors == errors


# --- Async DB helper ---


async def _make_session() -> tuple[AsyncSession, AsyncEngine]:
    """Create a fresh in-memory SQLite database and return (session, engine)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session = factory()
    return session, engine


# --- Job lifecycle consistency ---


@settings(max_examples=100)
@given(
    direction=directions,
    started_at=safe_datetimes,
    finished_at=safe_datetimes,
    files_changed=non_negative_ints,
    errors=non_negative_ints,
)
def test_job_lifecycle_consistency(
    direction: str,
    started_at: datetime,
    finished_at: datetime,
    files_changed: int,
    errors: int,
) -> None:
    """Job lifecycle consistency.

    For any valid direction and arbitrary completion data, creating a job then
    completing it should result in a record matching all provided fields.
    """
    import asyncio

    async def _run() -> None:
        session, engine = await _make_session()
        try:
            # Create a running job
            job = SyncJob(
                direction=direction,
                started_at=started_at,
                status="running",
                files_changed=0,
                conflicts=0,
                errors=0,
                profile_id=1,  # no FK enforcement here: the profile row is not needed
            )
            session.add(job)
            await session.flush()
            job_id = job.id

            # Complete the job
            job.finished_at = finished_at
            job.status = "completed"
            job.files_changed = files_changed
            job.errors = errors
            await session.flush()

            # Re-query to verify persistence
            session.expire_all()
            loaded = await session.get(SyncJob, job_id)

            assert loaded is not None
            assert loaded.direction == direction
            # SQLite strips timezone info, so compare naive datetimes
            assert loaded.started_at == started_at.replace(tzinfo=None)
            assert loaded.finished_at == finished_at.replace(tzinfo=None)
            assert loaded.status == "completed"
            assert loaded.files_changed == files_changed
            assert loaded.errors == errors
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_run())


# --- File change recording integrity ---


@settings(max_examples=100)
@given(
    direction=directions,
    started_at=safe_datetimes,
    file_path=file_paths,
    action=actions,
    size_bytes=optional_size_bytes,
)
def test_file_change_recording_integrity(
    direction: str,
    started_at: datetime,
    file_path: str,
    action: str,
    size_bytes: int | None,
) -> None:
    """File change recording integrity.

    For any file path, action type, and associated job, recording a file change
    should result in a record that exactly matches the provided data.
    """
    import asyncio

    async def _run() -> None:
        session, engine = await _make_session()
        try:
            # Create parent job (FK requirement)
            job = SyncJob(
                direction=direction,
                started_at=started_at,
                status="running",
                files_changed=0,
                conflicts=0,
                errors=0,
                profile_id=1,  # no FK enforcement here: the profile row is not needed
            )
            session.add(job)
            await session.flush()
            job_id = job.id

            # Record file change
            fc = FileChange(
                job_id=job_id,
                file_path=file_path,
                action=action,
                size_bytes=size_bytes,
            )
            session.add(fc)
            await session.flush()
            fc_id = fc.id

            # Re-query to verify persistence
            session.expire_all()
            loaded = await session.get(FileChange, fc_id)

            assert loaded is not None
            assert loaded.job_id == job_id
            assert loaded.file_path == file_path
            assert loaded.action == action
            assert loaded.size_bytes == size_bytes
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_run())


# --- BackupTarget and BackupJob tests ---


backup_target_types = st.sampled_from(["local", "remote", "custom_remote"])
backup_modes = st.sampled_from(["archive", "mirror"])
backup_job_statuses = st.sampled_from(["running", "completed", "failed", "skipped"])
retention_days_st = st.integers(min_value=1, max_value=365)
frequency_hours_st = st.integers(min_value=1, max_value=8760)


def _make_profile(now: datetime) -> SyncProfile:
    """Create a SyncProfile instance for FK tests."""
    return SyncProfile(
        slug="test-profile",
        name="Test Profile",
        local_dir="/tmp/test",
        remote_dir="remote:test",
        created_at=now,
        updated_at=now,
    )


def _make_target(profile_id: int, now: datetime) -> BackupTarget:
    """Create a BackupTarget instance for tests."""
    return BackupTarget(
        profile_id=profile_id,
        name="Test backup",
        target_path="/tmp/backup",
        target_type="local",
        retention_days=7,
        frequency_hours=24,
        backup_mode="mirror",
        enabled=True,
        created_at=now,
        updated_at=now,
    )


@settings(max_examples=50)
@given(
    target_type=backup_target_types,
    backup_mode=backup_modes,
    retention_days=retention_days_st,
    frequency_hours=frequency_hours_st,
)
def test_backup_target_crud(
    target_type: str,
    backup_mode: str,
    retention_days: int,
    frequency_hours: int,
) -> None:
    """BackupTarget CRUD: create, read, verify all fields persisted."""
    import asyncio

    async def _run() -> None:
        session, engine = await _make_session()
        try:
            now = datetime(2026, 1, 1)
            profile = _make_profile(now)
            session.add(profile)
            await session.flush()

            profile_id = profile.id

            target = BackupTarget(
                profile_id=profile_id,
                name="Nightly backup",
                target_path="/mnt/nas/backup",
                target_type=target_type,
                remote_name="nas" if target_type == "custom_remote" else None,
                retention_days=retention_days,
                frequency_hours=frequency_hours,
                backup_mode=backup_mode,
                enabled=True,
                created_at=now,
                updated_at=now,
            )
            session.add(target)
            await session.flush()
            target_id = target.id

            session.expire_all()
            loaded = await session.get(BackupTarget, target_id)
            assert loaded is not None
            assert loaded.profile_id == profile_id
            assert loaded.name == "Nightly backup"
            assert loaded.target_type == target_type
            assert loaded.backup_mode == backup_mode
            assert loaded.retention_days == retention_days
            assert loaded.frequency_hours == frequency_hours
            assert loaded.enabled is True
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_run())


@settings(max_examples=50)
@given(
    status=backup_job_statuses,
    direction=st.sampled_from(["backup", "restore"]),
    size_bytes=optional_size_bytes,
)
def test_backup_job_crud(
    status: str,
    direction: str,
    size_bytes: int | None,
) -> None:
    """BackupJob CRUD: create, read, verify all fields persisted."""
    import asyncio

    async def _run() -> None:
        session, engine = await _make_session()
        try:
            now = datetime(2026, 1, 1)
            profile = _make_profile(now)
            session.add(profile)
            await session.flush()

            target = _make_target(profile.id, now)
            session.add(target)
            await session.flush()

            target_id_saved = target.id

            job = BackupJob(
                target_id=target_id_saved,
                started_at=now,
                status=status,
                direction=direction,
                size_bytes=size_bytes,
                snapshot_id="2026-01-01T00-00-00" if status == "completed" else None,
            )
            session.add(job)
            await session.flush()
            job_id = job.id

            session.expire_all()
            loaded = await session.get(BackupJob, job_id)
            assert loaded is not None
            assert loaded.target_id == target_id_saved
            assert loaded.status == status
            assert loaded.direction == direction
            assert loaded.size_bytes == size_bytes
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_run())


def test_backup_target_cascade_delete() -> None:
    """Deleting a SyncProfile cascades to BackupTargets and BackupJobs."""
    import asyncio

    async def _run() -> None:
        session, engine = await _make_session()
        try:
            now = datetime(2026, 1, 1)
            profile = _make_profile(now)
            session.add(profile)
            await session.flush()

            target = _make_target(profile.id, now)
            session.add(target)
            await session.flush()

            job = BackupJob(
                target_id=target.id,
                started_at=now,
                status="completed",
                direction="backup",
            )
            session.add(job)
            await session.flush()
            target_id = target.id
            job_id = job.id

            # Delete profile — should cascade
            await session.delete(profile)
            await session.flush()

            assert await session.get(BackupTarget, target_id) is None
            assert await session.get(BackupJob, job_id) is None
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_run())


def test_backup_target_defaults() -> None:
    """BackupTarget default values for retention_days, frequency_hours, enabled."""
    import asyncio

    async def _run() -> None:
        session, engine = await _make_session()
        try:
            now = datetime(2026, 1, 1)
            profile = _make_profile(now)
            session.add(profile)
            await session.flush()

            target = BackupTarget(
                profile_id=profile.id,
                name="Defaults test",
                target_path="/tmp/defaults",
                target_type="local",
                backup_mode="mirror",
                created_at=now,
                updated_at=now,
            )
            session.add(target)
            await session.flush()
            target_id = target.id

            session.expire_all()
            loaded = await session.get(BackupTarget, target_id)
            assert loaded is not None
            assert loaded.retention_days == 7
            assert loaded.frequency_hours == 24
            assert loaded.enabled is True
            assert loaded.last_liveness_ok is None
            assert loaded.last_liveness_error is None
            assert loaded.remote_name is None
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_run())


def test_backup_target_relationship_navigation() -> None:
    """Navigate profile.backup_targets and target.backup_jobs relationships."""
    import asyncio

    async def _run() -> None:
        session, engine = await _make_session()
        try:
            now = datetime(2026, 1, 1)
            profile = _make_profile(now)
            session.add(profile)
            await session.flush()

            target = _make_target(profile.id, now)
            session.add(target)
            await session.flush()

            job = BackupJob(
                target_id=target.id,
                started_at=now,
                status="completed",
                direction="backup",
            )
            session.add(job)
            await session.flush()
            profile_id = profile.id
            target_id = target.id

            session.expire_all()
            loaded_profile = await session.get(SyncProfile, profile_id)
            assert loaded_profile is not None
            targets = await session.run_sync(lambda _: loaded_profile.backup_targets)
            assert len(targets) == 1
            assert targets[0].name == "Test backup"

            loaded_target = await session.get(BackupTarget, target_id)
            assert loaded_target is not None
            jobs = await session.run_sync(lambda _: loaded_target.backup_jobs)
            assert len(jobs) == 1
            assert jobs[0].status == "completed"
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_run())


def test_backup_job_cascade_from_target_delete() -> None:
    """Deleting a BackupTarget cascades to its BackupJobs."""
    import asyncio

    async def _run() -> None:
        session, engine = await _make_session()
        try:
            now = datetime(2026, 1, 1)
            profile = _make_profile(now)
            session.add(profile)
            await session.flush()

            target = _make_target(profile.id, now)
            session.add(target)
            await session.flush()

            job = BackupJob(
                target_id=target.id,
                started_at=now,
                status="completed",
                direction="backup",
            )
            session.add(job)
            await session.flush()
            job_id = job.id

            await session.delete(target)
            await session.flush()

            assert await session.get(BackupJob, job_id) is None
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_run())


# --- NotificationLog / PushSubscription (host-notifications 2.2) and
# --- NotificationLog.profile_slug / SyncProfile.slug (multi-sync-profiles 1.3)


async def test_notification_log_roundtrip(db_session: AsyncSession) -> None:
    """A NotificationLog row reads back with every value it was written with."""
    from backend.db.models import NotificationLog

    ts = datetime(2026, 3, 4, 5, 6, 7)
    rows = [
        NotificationLog(
            event_type="sync_failed", severity="error", title="Push failed — Docs",
            body="rclone exited 1", timestamp=ts,
            channels_delivered=json.dumps(["webpush", "host_native"]), profile_slug="docs",
        ),
        NotificationLog(
            event_type="test", severity="info", title="Test", body="",
            timestamp=ts, channels_delivered=json.dumps([]),
        ),
    ]
    db_session.add_all(rows)
    await db_session.commit()
    ids = [r.id for r in rows]
    db_session.expire_all()

    loaded = (await db_session.execute(
        select(NotificationLog).order_by(NotificationLog.id)
    )).scalars().all()
    assert [r.id for r in loaded] == ids
    first, second = loaded
    assert (first.event_type, first.severity, first.title, first.body) == (
        "sync_failed", "error", "Push failed — Docs", "rclone exited 1",
    )
    assert first.timestamp == ts
    assert json.loads(first.channels_delivered) == ["webpush", "host_native"]
    assert first.profile_slug == "docs"
    # Events outside any profile (e.g. a test notification) have no slug.
    assert second.profile_slug is None
    assert json.loads(second.channels_delivered) == []

    # The history filter on profile_slug finds only that profile's row.
    by_slug = (await db_session.execute(
        select(NotificationLog).where(NotificationLog.profile_slug == "docs")
    )).scalars().all()
    assert [r.id for r in by_slug] == [ids[0]]


async def test_push_subscription_roundtrip(db_session: AsyncSession) -> None:
    from backend.db.models import PushSubscription

    now = datetime(2026, 1, 2, 3, 4, 5)
    endpoint = "https://fcm.googleapis.com/fcm/send/" + "x" * 1900
    sub = PushSubscription(endpoint=endpoint, p256dh_key="BPkey", auth_key="authkey", created_at=now)
    db_session.add(sub)
    await db_session.commit()
    sub_id = sub.id
    db_session.expire_all()

    loaded = await db_session.get(PushSubscription, sub_id)
    assert loaded is not None
    assert (loaded.endpoint, loaded.p256dh_key, loaded.auth_key, loaded.created_at) == (
        endpoint, "BPkey", "authkey", now,
    )


async def test_push_subscription_endpoint_is_unique(db_session: AsyncSession) -> None:
    """The database itself refuses a second row with the same endpoint."""
    from backend.db.models import PushSubscription

    now = datetime(2026, 1, 1)
    endpoint = "https://updates.push.services.mozilla.com/wpush/v2/abc"
    db_session.add(PushSubscription(endpoint=endpoint, p256dh_key="k1", auth_key="a1", created_at=now))
    await db_session.commit()

    db_session.add(PushSubscription(endpoint=endpoint, p256dh_key="k2", auth_key="a2", created_at=now))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    rows = (await db_session.execute(select(PushSubscription))).scalars().all()
    assert [(r.endpoint, r.p256dh_key) for r in rows] == [(endpoint, "k1")]

    # A different endpoint is fine.
    db_session.add(PushSubscription(endpoint=endpoint + "-2", p256dh_key="k3", auth_key="a3", created_at=now))
    await db_session.commit()


async def test_sync_profile_slug_is_unique(db_session: AsyncSession) -> None:
    """Two profiles cannot share a slug, even bypassing ProfileService."""
    now = datetime(2026, 1, 1)
    db_session.add(SyncProfile(
        slug="docs", name="Docs", local_dir="/a", remote_dir="r:a", created_at=now, updated_at=now,
    ))
    await db_session.commit()

    db_session.add(SyncProfile(
        slug="docs", name="Docs again", local_dir="/b", remote_dir="r:b", created_at=now, updated_at=now,
    ))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    rows = (await db_session.execute(select(SyncProfile))).scalars().all()
    assert [(r.slug, r.name) for r in rows] == [("docs", "Docs")]
