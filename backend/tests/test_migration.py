"""Tests for backup_dir → BackupTarget migration logic."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from backend.db.models import (
    BackupMode,
    BackupTarget,
    BackupTargetType,
    Base,
    SyncProfile,
)
from backend.services.migration import (
    _extract_remote_name,
    _infer_target_type,
    migrate_backup_dir_to_targets,
)


@pytest_asyncio.fixture
async def db_factory():
    """Provide an async_sessionmaker backed by a fresh in-memory SQLite DB."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _make_profile(
    name: str = "Test",
    slug: str = "test",
    backup_dir: str | None = None,
) -> SyncProfile:
    now = datetime.now(timezone.utc)
    return SyncProfile(
        name=name,
        slug=slug,
        local_dir="/sync/local",
        remote_dir="remote:data",
        backup_dir=backup_dir,
        created_at=now,
        updated_at=now,
    )


# ─── Unit tests for helper functions ─────────────────────────────────


class TestInferTargetType:
    def test_local_path(self) -> None:
        assert _infer_target_type("/backups/daily") == BackupTargetType.LOCAL

    def test_relative_local_path(self) -> None:
        assert _infer_target_type("backups/daily") == BackupTargetType.LOCAL

    def test_remote_path(self) -> None:
        assert _infer_target_type("gdrive:backups/daily") == BackupTargetType.REMOTE

    def test_remote_with_colon_only(self) -> None:
        assert _infer_target_type("remote:") == BackupTargetType.REMOTE


class TestExtractRemoteName:
    def test_local_path_returns_none(self) -> None:
        assert _extract_remote_name("/backups/local") is None

    def test_remote_path_returns_name(self) -> None:
        assert _extract_remote_name("gdrive:backups/daily") == "gdrive"

    def test_remote_colon_only(self) -> None:
        assert _extract_remote_name("remote:") == "remote"

    def test_multiple_colons(self) -> None:
        assert _extract_remote_name("a:b:c") == "a"


# ─── Property-based tests for helpers ────────────────────────────────


@given(path=st.text(min_size=1, max_size=200).filter(lambda s: ":" not in s))
@settings(max_examples=30)
def test_local_paths_never_inferred_as_remote(path: str) -> None:
    assert _infer_target_type(path) == BackupTargetType.LOCAL
    assert _extract_remote_name(path) is None


@given(
    remote=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=1,
        max_size=30,
    ),
    path=st.text(min_size=0, max_size=100),
)
@settings(max_examples=30)
def test_remote_paths_always_inferred_as_remote(remote: str, path: str) -> None:
    full = f"{remote}:{path}"
    assert _infer_target_type(full) == BackupTargetType.REMOTE
    assert _extract_remote_name(full) == remote


# ─── Integration tests for migrate_backup_dir_to_targets ─────────────


@pytest.mark.asyncio
async def test_local_backup_dir_creates_local_target(db_factory) -> None:
    """Profile with a local backup_dir creates a LOCAL BackupTarget."""
    async with db_factory() as session:
        profile = _make_profile(backup_dir="/backups/daily")
        session.add(profile)
        await session.commit()

    await migrate_backup_dir_to_targets(db_factory)

    async with db_factory() as session:
        stmt = select(SyncProfile).options(selectinload(SyncProfile.backup_targets))
        row = (await session.execute(stmt)).scalar_one()
        assert row.backup_dir is None
        assert len(row.backup_targets) == 1
        t = row.backup_targets[0]
        assert t.name == "Legacy backup"
        assert t.target_type == BackupTargetType.LOCAL.value
        assert t.target_path == "/backups/daily"
        assert t.remote_name is None
        assert t.retention_days == 7
        assert t.frequency_hours == 24
        assert t.backup_mode == BackupMode.MIRROR.value
        assert t.enabled is True


@pytest.mark.asyncio
async def test_remote_backup_dir_creates_remote_target(db_factory) -> None:
    """Profile with a remote backup_dir creates a REMOTE BackupTarget."""
    async with db_factory() as session:
        profile = _make_profile(
            name="Remote", slug="remote", backup_dir="gdrive:backups/daily"
        )
        session.add(profile)
        await session.commit()

    await migrate_backup_dir_to_targets(db_factory)

    async with db_factory() as session:
        stmt = select(SyncProfile).options(selectinload(SyncProfile.backup_targets))
        row = (await session.execute(stmt)).scalar_one()
        assert row.backup_dir is None
        t = row.backup_targets[0]
        assert t.target_type == BackupTargetType.REMOTE.value
        assert t.target_path == "gdrive:backups/daily"
        assert t.remote_name == "gdrive"


@pytest.mark.asyncio
async def test_null_backup_dir_skipped(db_factory) -> None:
    """Profiles with no backup_dir are not migrated."""
    async with db_factory() as session:
        session.add(_make_profile(backup_dir=None))
        await session.commit()

    await migrate_backup_dir_to_targets(db_factory)

    async with db_factory() as session:
        targets = (await session.execute(select(BackupTarget))).scalars().all()
        assert targets == []


@pytest.mark.asyncio
async def test_empty_backup_dir_skipped(db_factory) -> None:
    """Profiles with empty-string backup_dir are not migrated."""
    async with db_factory() as session:
        session.add(_make_profile(backup_dir=""))
        await session.commit()

    await migrate_backup_dir_to_targets(db_factory)

    async with db_factory() as session:
        targets = (await session.execute(select(BackupTarget))).scalars().all()
        assert targets == []


@pytest.mark.asyncio
async def test_idempotency(db_factory) -> None:
    """Running migration twice doesn't create duplicate targets."""
    async with db_factory() as session:
        session.add(_make_profile(backup_dir="/backups/daily"))
        await session.commit()

    await migrate_backup_dir_to_targets(db_factory)
    await migrate_backup_dir_to_targets(db_factory)

    async with db_factory() as session:
        targets = (await session.execute(select(BackupTarget))).scalars().all()
        assert len(targets) == 1


@pytest.mark.asyncio
async def test_profile_with_existing_targets_skipped(db_factory) -> None:
    """If a profile already has backup_targets, migration leaves backup_dir alone."""
    now = datetime.now(timezone.utc)
    async with db_factory() as session:
        profile = _make_profile(backup_dir="/old/path")
        session.add(profile)
        await session.flush()
        # Manually add a target so migration should skip
        session.add(
            BackupTarget(
                profile_id=profile.id,
                name="Manual",
                target_path="/manual/backup",
                target_type=BackupTargetType.LOCAL.value,
                backup_mode=BackupMode.ARCHIVE.value,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    await migrate_backup_dir_to_targets(db_factory)

    async with db_factory() as session:
        stmt = select(SyncProfile).options(selectinload(SyncProfile.backup_targets))
        row = (await session.execute(stmt)).scalar_one()
        # backup_dir should still be the old value — not nulled
        assert row.backup_dir == "/old/path"
        # Only the manually added target should exist
        assert len(row.backup_targets) == 1
        assert row.backup_targets[0].name == "Manual"


@pytest.mark.asyncio
async def test_multiple_profiles_migrated(db_factory) -> None:
    """Multiple profiles with backup_dir are all migrated in one pass."""
    async with db_factory() as session:
        session.add(_make_profile(name="A", slug="a", backup_dir="/backups/a"))
        session.add(
            _make_profile(name="B", slug="b", backup_dir="s3:bucket/b")
        )
        session.add(_make_profile(name="C", slug="c", backup_dir=None))
        await session.commit()

    await migrate_backup_dir_to_targets(db_factory)

    async with db_factory() as session:
        stmt = (
            select(SyncProfile)
            .options(selectinload(SyncProfile.backup_targets))
            .order_by(SyncProfile.slug)
        )
        profiles = (await session.execute(stmt)).scalars().all()
        assert len(profiles) == 3

        # Profile A: local
        assert profiles[0].backup_dir is None
        assert len(profiles[0].backup_targets) == 1
        assert profiles[0].backup_targets[0].target_type == BackupTargetType.LOCAL.value

        # Profile B: remote
        assert profiles[1].backup_dir is None
        assert len(profiles[1].backup_targets) == 1
        assert profiles[1].backup_targets[0].target_type == BackupTargetType.REMOTE.value
        assert profiles[1].backup_targets[0].remote_name == "s3"

        # Profile C: untouched
        assert profiles[2].backup_dir is None
        assert profiles[2].backup_targets == []


@pytest.mark.asyncio
async def test_migration_logs_info(db_factory, caplog) -> None:
    """Migration logs an INFO message for each migrated profile."""
    async with db_factory() as session:
        session.add(_make_profile(backup_dir="/backups/daily"))
        await session.commit()

    with caplog.at_level("INFO", logger="backend.services.migration"):
        await migrate_backup_dir_to_targets(db_factory)

    assert any("Migrated backup_dir" in msg for msg in caplog.messages)
    assert any("Test" in msg for msg in caplog.messages)


# ─── Legacy TOML → profile, with settings the API would refuse ──────


async def _migrate_toml(db_factory, tmp_path, text: str):
    """Run both startup migrations on a legacy config.toml; return (profile, targets, toml)."""
    from backend.services.config import ConfigService
    from backend.services.migration import migrate_legacy_config
    from backend.services.profile_service import ProfileService

    toml = tmp_path / "config.toml"
    toml.write_text(text)
    service = ProfileService(db_factory)
    await migrate_legacy_config(ConfigService(toml), service, db_factory)
    await migrate_backup_dir_to_targets(db_factory)
    (profile,) = await service.get_all()
    async with db_factory() as session:
        targets = (await session.execute(select(BackupTarget))).scalars().all()
    return profile, targets, toml.read_text()


@pytest.mark.asyncio
async def test_invalid_legacy_paths_create_disabled_profile(db_factory, tmp_path, caplog) -> None:
    """A relative local_dir and a remote_dir without 'remote:' no longer abort startup."""
    caplog.set_level("INFO", logger="backend.services.migration")
    profile, targets, toml = await _migrate_toml(
        db_factory, tmp_path,
        'log_level = "INFO"\nlocal_dir = "Sync"\nremote_dir = "gdrive"\nbackup_dir = "old-backups"\n',
    )

    assert profile.name == "Default"
    assert profile.enabled is False
    assert (profile.local_dir, profile.remote_dir) == ("/sync/local", "remote:backup")
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("DISABLED" in m and "'Sync'" in m and "'gdrive'" in m for m in warnings), warnings
    assert any("local_dir 'Sync'" in m for m in warnings)
    assert any("remote_dir 'gdrive'" in m for m in warnings)
    # The relative backup_dir becomes a target that never runs until fixed.
    (target,) = targets
    assert (target.target_path, target.enabled) == ("old-backups", False)
    assert any("backup_dir 'old-backups'" in m for m in warnings)
    # The per-profile keys are gone; the global ones stay.
    assert "local_dir" not in toml and "log_level" in toml


@pytest.mark.asyncio
async def test_refused_rclone_args_create_disabled_profile(db_factory, tmp_path, caplog) -> None:
    profile, _, _ = await _migrate_toml(
        db_factory, tmp_path,
        'local_dir = "/home/me/Sync"\nremote_dir = "gdrive:Sync"\n'
        'rclone_args = ["--config=/etc/shadow"]\nrclone_filter = "not a list"\n',
    )
    assert profile.enabled is False
    assert (profile.local_dir, profile.remote_dir) == ("/home/me/Sync", "gdrive:Sync")
    assert (profile.rclone_args, profile.rclone_filter) == ("[]", "[]")
    warnings = caplog.text
    assert "rclone_args ['--config=/etc/shadow'] is not valid" in warnings
    assert "rclone_filter 'not a list' is not valid" in warnings


@pytest.mark.asyncio
async def test_valid_legacy_config_stays_enabled(db_factory, tmp_path, caplog) -> None:
    caplog.set_level("INFO", logger="backend.services.migration")
    profile, targets, _ = await _migrate_toml(
        db_factory, tmp_path,
        'local_dir = "/home/me/Sync"\nremote_dir = "gdrive:Sync"\nbackup_dir = "/mnt/backups"\n',
    )
    assert profile.enabled is True
    assert [(t.target_path, t.enabled) for t in targets] == [("/mnt/backups", True)]
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


# ─── Legacy TOML → Default profile: idempotency and preserved fields ──


async def _run_legacy_migration(db_factory, toml_path):
    from backend.services.config import ConfigService
    from backend.services.migration import migrate_legacy_config
    from backend.services.profile_service import ProfileService

    service = ProfileService(db_factory)
    await migrate_legacy_config(ConfigService(toml_path), service, db_factory)
    return await service.get_all()


FULL_LEGACY_TOML = """\
log_level = "DEBUG"
local_dir = "/home/me/Sync"
remote_dir = "gdrive:Sync"
debounce_seconds = 12
pull_interval_minutes = 45
rclone_filter = ["- *.tmp", "+ docs/**"]
rclone_args = ["--transfers", "8"]
backup_dir = "/mnt/old-backups"
max_retries = 7

[notifications.channels.webpush]
enabled = false
min_severity = "error"

[notifications.channels.host_native]
enabled = true
min_severity = "info"
"""


@pytest.mark.asyncio
async def test_legacy_migration_preserves_every_profile_field_and_notifications(db_factory, tmp_path) -> None:
    import json

    import toml

    path = tmp_path / "config.toml"
    path.write_text(FULL_LEGACY_TOML)

    (profile,) = await _run_legacy_migration(db_factory, path)

    assert (profile.name, profile.slug, profile.enabled) == ("Default", "default", True)
    assert (profile.local_dir, profile.remote_dir) == ("/home/me/Sync", "gdrive:Sync")
    assert (profile.debounce_seconds, profile.pull_interval_minutes, profile.max_retries) == (12, 45, 7)
    assert json.loads(profile.rclone_filter) == ["- *.tmp", "+ docs/**"]
    assert json.loads(profile.rclone_args) == ["--transfers", "8"]
    assert profile.backup_dir == "/mnt/old-backups"

    # Only the per-profile keys leave config.toml; log_level and the whole
    # [notifications] section stay as they were.
    assert toml.loads(path.read_text()) == {
        "log_level": "DEBUG",
        "notifications": {"channels": {
            "webpush": {"enabled": False, "min_severity": "error"},
            "host_native": {"enabled": True, "min_severity": "info"},
        }},
    }


@pytest.mark.asyncio
async def test_legacy_migration_is_idempotent(db_factory, tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(FULL_LEGACY_TOML)

    (first,) = await _run_legacy_migration(db_factory, path)
    after_first = path.read_text()

    (second,) = await _run_legacy_migration(db_factory, path)
    assert second.id == first.id and second.updated_at == first.updated_at
    assert path.read_text() == after_first

    # Even legacy keys reappearing (an old config restored by hand) create
    # no second profile once profiles exist, and the file is left alone.
    path.write_text(FULL_LEGACY_TOML)
    profiles = await _run_legacy_migration(db_factory, path)
    assert [p.id for p in profiles] == [first.id]
    assert path.read_text() == FULL_LEGACY_TOML


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    None,  # no config.toml at all
    "",
    'log_level = "WARNING"\n\n[notifications.channels.webpush]\nenabled = true\nmin_severity = "warning"\n',
    # per-profile settings without local_dir/remote_dir are not a legacy profile
    'log_level = "INFO"\ndebounce_seconds = 9\n',
])
async def test_no_legacy_keys_means_no_migration(db_factory, tmp_path, text) -> None:
    path = tmp_path / "config.toml"
    if text is not None:
        path.write_text(text)

    assert await _run_legacy_migration(db_factory, path) == []

    if text is None:
        assert not path.exists()
    else:
        assert path.read_text() == text
