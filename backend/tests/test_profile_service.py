"""ProfileService against a real (in-memory) database: CRUD, slugs and the folder-overlap rules.

The HTTP layer on top is tested in test_profiles.py.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.schemas import ProfileCreateRequest, ProfileUpdateRequest, SyncMode
from backend.db.models import BackupTarget, Base
from backend.exceptions import ProfileConflictError, ProfileNotFoundError
from backend.services.profile_service import ProfileService


@pytest.fixture
def service(test_db_factory) -> ProfileService:
    return ProfileService(test_db_factory)


def req(name: str, local_dir: str, remote_dir: str, **kw) -> ProfileCreateRequest:
    return ProfileCreateRequest(name=name, local_dir=local_dir, remote_dir=remote_dir, **kw)


class TestCreate:
    async def test_stores_every_setting(self, service: ProfileService) -> None:
        profile = await service.create(req(
            "My Documents", "/srv/docs", "gd:docs",
            debounce_seconds=9, pull_interval_minutes=15, max_retries=5,
            rclone_filter=["- *.tmp"], rclone_args=["--transfers", "2"],
            sync_mode=SyncMode.MIRROR,
        ))
        assert profile.id is not None and profile.slug == "my-documents"
        assert (profile.local_dir, profile.remote_dir) == ("/srv/docs", "gd:docs")
        assert (profile.debounce_seconds, profile.pull_interval_minutes, profile.max_retries) == (9, 15, 5)
        assert json.loads(profile.rclone_filter) == ["- *.tmp"]
        assert json.loads(profile.rclone_args) == ["--transfers", "2"]
        assert profile.sync_mode == "mirror" and profile.enabled is True
        assert profile.created_at == profile.updated_at

        stored = await service.get_by_slug("my-documents")
        assert stored.id == profile.id and json.loads(stored.rclone_filter) == ["- *.tmp"]

    async def test_new_profiles_default_to_two_way(self, service: ProfileService) -> None:
        assert (await service.create(req("A", "/a", "r:a"))).sync_mode == "two_way"

    async def test_same_names_get_numbered_slugs(self, service: ProfileService) -> None:
        slugs = [(await service.create(req("Photos", f"/p{i}", f"r:p{i}"))).slug for i in range(3)]
        assert slugs == ["photos", "photos-2", "photos-3"]

    @pytest.mark.parametrize(("local_dir", "remote_dir", "field"), [
        ("/srv/docs", "r:elsewhere", "local_dir"),  # the same folder
        ("/srv/docs/", "r:elsewhere", "local_dir"),  # trailing slash
        ("/srv/docs/sub", "r:elsewhere", "local_dir"),  # inside
        ("/srv", "r:elsewhere", "local_dir"),  # around
        ("/srv/docs/../docs", "r:elsewhere", "local_dir"),
        ("/elsewhere", "gd:docs/inner", "remote_dir"),
    ])
    async def test_overlapping_folders_are_refused(
        self, service: ProfileService, local_dir: str, remote_dir: str, field: str,
    ) -> None:
        await service.create(req("Docs", "/srv/docs", "gd:docs"))
        with pytest.raises(ProfileConflictError) as info:
            await service.create(req("Other", local_dir, remote_dir))
        assert info.value.field == field and info.value.conflicting_slug == "docs"
        assert [p.slug for p in await service.get_all()] == ["docs"]

    async def test_siblings_with_a_common_prefix_do_not_overlap(self, service: ProfileService) -> None:
        await service.create(req("Docs", "/srv/docs", "gd:docs"))
        await service.create(req("Docs2", "/srv/docs2", "gd:docs2"))
        assert len(await service.get_all()) == 2

    async def test_a_disabled_profile_does_not_block_its_folders(self, service: ProfileService) -> None:
        await service.create(req("Docs", "/srv/docs", "gd:docs"))
        await service.set_enabled("docs", False)
        assert (await service.create(req("Again", "/srv/docs", "gd:docs"))).slug == "again"

    async def test_backup_targets_inside_the_folders_are_refused(self, service: ProfileService, test_db_factory) -> None:
        owner = await service.create(req("Owner", "/srv/owner", "gd:owner"))
        now = datetime.now(timezone.utc)
        async with test_db_factory() as session:
            session.add(BackupTarget(
                profile_id=owner.id, name="Nightly", target_path="/backups/nightly", target_type="local",
                backup_mode="archive", created_at=now, updated_at=now,
            ))
            await session.commit()

        with pytest.raises(ProfileConflictError) as info:
            await service.create(req("Backups", "/backups", "gd:backups"))
        assert info.value.field == "local_dir" and "Nightly" in str(info.value)


class TestRead:
    async def test_get_all_is_sorted_by_name(self, service: ProfileService) -> None:
        for name in ("beta", "Alpha", "gamma"):
            await service.create(req(name, f"/{name}", f"r:{name}"))
        assert [p.name for p in await service.get_all()] == ["Alpha", "beta", "gamma"]

    async def test_unknown_slug(self, service: ProfileService) -> None:
        with pytest.raises(ProfileNotFoundError) as info:
            await service.get_by_slug("nope")
        assert info.value.slug == "nope"


class TestUpdate:
    async def test_partial_update_changes_only_the_given_fields(self, service: ProfileService) -> None:
        created = await service.create(req("Docs", "/srv/docs", "gd:docs", rclone_args=["--fast-list"]))

        updated = await service.update("docs", ProfileUpdateRequest(
            debounce_seconds=30, rclone_filter=["- *.bak"], sync_mode=SyncMode.MIRROR,
        ))

        assert updated.debounce_seconds == 30
        assert json.loads(updated.rclone_filter) == ["- *.bak"]
        assert json.loads(updated.rclone_args) == ["--fast-list"]  # untouched
        assert updated.sync_mode == "mirror"
        assert updated.slug == "docs" and updated.local_dir == "/srv/docs"
        assert updated.updated_at >= created.updated_at.replace(tzinfo=None)

    async def test_args_update_is_stored_as_json(self, service: ProfileService) -> None:
        await service.create(req("Docs", "/srv/docs", "gd:docs"))
        updated = await service.update("docs", ProfileUpdateRequest(rclone_args=["--bwlimit", "1M"]))
        assert json.loads(updated.rclone_args) == ["--bwlimit", "1M"]

    async def test_rename_regenerates_a_unique_slug(self, service: ProfileService) -> None:
        await service.create(req("Docs", "/srv/docs", "gd:docs"))
        await service.create(req("Photos", "/srv/photos", "gd:photos"))

        assert (await service.update("photos", ProfileUpdateRequest(name="Docs"))).slug == "docs-2"
        # keeping its own name keeps its slug (it does not collide with itself)
        assert (await service.update("docs", ProfileUpdateRequest(name="Docs"))).slug == "docs"
        with pytest.raises(ProfileNotFoundError):
            await service.get_by_slug("photos")

    async def test_moving_onto_another_profiles_folder_is_refused(self, service: ProfileService) -> None:
        await service.create(req("Docs", "/srv/docs", "gd:docs"))
        await service.create(req("Photos", "/srv/photos", "gd:photos"))

        with pytest.raises(ProfileConflictError):
            await service.update("photos", ProfileUpdateRequest(local_dir="/srv/docs/photos"))
        with pytest.raises(ProfileConflictError):
            await service.update("photos", ProfileUpdateRequest(remote_dir="gd:docs"))
        assert (await service.get_by_slug("photos")).local_dir == "/srv/photos"

        # moving within its own folder is fine
        moved = await service.update("photos", ProfileUpdateRequest(local_dir="/srv/photos/2026"))
        assert moved.local_dir == "/srv/photos/2026"

    async def test_a_disabled_profile_may_point_anywhere(self, service: ProfileService) -> None:
        await service.create(req("Docs", "/srv/docs", "gd:docs"))
        await service.create(req("Photos", "/srv/photos", "gd:photos"))
        await service.set_enabled("photos", False)

        moved = await service.update("photos", ProfileUpdateRequest(local_dir="/srv/docs"))
        assert moved.local_dir == "/srv/docs"
        # ... but it cannot be enabled while it overlaps
        with pytest.raises(ProfileConflictError):
            await service.set_enabled("photos", True)
        assert (await service.get_by_slug("photos")).enabled is False

    async def test_unknown_slug(self, service: ProfileService) -> None:
        with pytest.raises(ProfileNotFoundError):
            await service.update("nope", ProfileUpdateRequest(name="X"))


class TestEnableAndDelete:
    async def test_disable_and_enable(self, service: ProfileService) -> None:
        await service.create(req("Docs", "/srv/docs", "gd:docs"))
        assert (await service.set_enabled("docs", False)).enabled is False
        assert (await service.set_enabled("docs", True)).enabled is True
        assert (await service.get_by_slug("docs")).enabled is True

    async def test_delete(self, service: ProfileService) -> None:
        await service.create(req("Docs", "/srv/docs", "gd:docs"))
        await service.delete("docs")
        assert await service.get_all() == []
        # the folders are free again
        await service.create(req("Docs", "/srv/docs", "gd:docs"))

    async def test_unknown_slug(self, service: ProfileService) -> None:
        with pytest.raises(ProfileNotFoundError):
            await service.delete("nope")
        with pytest.raises(ProfileNotFoundError):
            await service.set_enabled("nope", True)


# --- Property-based (multi-sync-profiles 5.2) ---

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Any text, plus names from a tiny alphabet so that different names often
# share a slug ("A b", "a-b", "a  B") and numbered suffixes get exercised.
names = st.one_of(
    st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=80),
    st.text(alphabet="aB -_2", min_size=1, max_size=6),
)


def _valid_request(name: str, i: int, **kw) -> ProfileCreateRequest | None:
    try:
        return req(name, f"/data/p{i}", f"remote:p{i}", **kw)
    except ValidationError:
        return None


@settings(max_examples=300)
@given(name=names)
def test_generated_slug_matches_slug_regex(name: str) -> None:
    """Every name the API accepts gets a slug of lowercase letters/digits joined by single dashes."""
    if _valid_request(name, 0) is None:
        # Refused names are exactly those without an ASCII letter or digit.
        assert ProfileService.generate_slug(name) == "" or any(c in name for c in "\n\r\x00")
        return
    slug = ProfileService.generate_slug(name)
    assert SLUG_RE.match(slug), (name, slug)
    assert ProfileService.generate_slug(slug) == slug  # a slug is its own slug


@settings(max_examples=40)
@given(
    profile_names=st.lists(names, min_size=1, max_size=4),
    debounce=st.integers(min_value=1, max_value=60),
    pull=st.integers(min_value=1, max_value=60),
    retries=st.integers(min_value=1, max_value=10),
    rclone_filter=st.lists(st.sampled_from(["- *.tmp", "+ docs/**", "- .git/**"]), max_size=3),
    mode=st.sampled_from(list(SyncMode)),
)
async def test_create_then_get_roundtrip(
    profile_names: list[str], debounce: int, pull: int, retries: int, rclone_filter: list[str], mode: SyncMode,
) -> None:
    """create() then get_by_slug() returns the same profile; slugs are valid and distinct."""
    requests = [
        r for i, n in enumerate(profile_names)
        if (r := _valid_request(
            n, i, debounce_seconds=debounce, pull_interval_minutes=pull, max_retries=retries,
            rclone_filter=rclone_filter, sync_mode=mode,
        )) is not None
    ]
    assume(requests)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        service = ProfileService(async_sessionmaker(engine, expire_on_commit=False))

        created = [await service.create(r) for r in requests]
        slugs = [p.slug for p in created]
        assert len(set(slugs)) == len(slugs)
        for request, profile in zip(requests, created, strict=True):
            assert SLUG_RE.match(profile.slug)
            # The slug is the name's slug, numbered when an earlier profile has it.
            base = ProfileService.generate_slug(request.name)
            assert profile.slug == base or re.fullmatch(re.escape(base) + r"-\d+", profile.slug)

            stored = await service.get_by_slug(profile.slug)
            assert stored.id == profile.id
            assert (stored.name, stored.local_dir, stored.remote_dir) == (
                request.name, request.local_dir, request.remote_dir,
            )
            assert (stored.debounce_seconds, stored.pull_interval_minutes, stored.max_retries) == (
                debounce, pull, retries,
            )
            assert json.loads(stored.rclone_filter) == rclone_filter
            assert json.loads(stored.rclone_args) == []
            assert stored.sync_mode == mode.value and stored.enabled is True
    finally:
        await engine.dispose()
