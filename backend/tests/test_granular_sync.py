"""Unit tests for granular sync control API endpoints."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.api.routes import sync as sync_module
from backend.api.schemas import (
    ChangeCategory,
    DiffResponse,
    DiffSummary,
    FileAction,
    FileDiff,
    FileError,
    JobStatus,
    SelectiveSyncItem,
    SelectiveSyncResponse,
)
from backend.exceptions import InvalidFilePathsError, NoCachedDiffError, SyncBusyError


def _get_engine():
    """The mock engine the test_client fixture runs for profile "default"."""
    return next(iter(sync_module._manager.engines.values()))


class TestDiffEndpoint:
    """POST /profiles/{slug}/diff — Req 1.5"""

    @pytest.mark.asyncio
    async def test_returns_correct_diff_response_shape(self, test_client) -> None:
        """POST /profiles/{slug}/diff returns correct DiffResponse shape."""
        engine = _get_engine()
        assert engine is not None
        engine.enhanced_diff = AsyncMock(return_value=DiffResponse(
            files=[
                FileDiff(
                    path="test.txt",
                    category=ChangeCategory.LOCAL_ONLY,
                    local_size=100,
                    is_conflict=False,
                    manual_flag=False,
                ),
            ],
            summary=DiffSummary(local_only=1, total=1),
            error=None,
        ))

        resp = await test_client.post("/profiles/default/diff")

        assert resp.status_code == 200
        data = resp.json()
        assert "files" in data
        assert "summary" in data
        assert "error" in data
        assert len(data["files"]) == 1
        file_entry = data["files"][0]
        assert file_entry["path"] == "test.txt"
        assert file_entry["category"] == "local_only"
        assert file_entry["local_size"] == 100
        assert file_entry["is_conflict"] is False
        assert data["summary"]["local_only"] == 1
        assert data["summary"]["total"] == 1
        assert data["error"] is None


class TestProfileRoutes:
    """The profile-scoped routes keep the request and response formats — Req 1.6, 5.1"""

    @pytest.mark.asyncio
    async def test_sync_check_returns_old_format(self, test_client) -> None:
        """POST /profiles/{slug}/sync/check still returns old SyncCheckResponse format."""
        engine = _get_engine()
        assert engine is not None
        from backend.api.schemas import SyncCheckResponse

        engine.check_diff = AsyncMock(return_value=SyncCheckResponse(
            has_changes=True,
            local_only=["new_file.txt"],
            remote_only=["deleted.txt"],
            differ=["changed.txt"],
            error=None,
        ))

        resp = await test_client.post("/profiles/default/sync/check")

        assert resp.status_code == 200
        data = resp.json()
        assert "local_only" in data
        assert "remote_only" in data
        assert "differ" in data
        assert "has_changes" in data
        assert "error" in data
        assert data["local_only"] == ["new_file.txt"]
        assert data["remote_only"] == ["deleted.txt"]
        assert data["differ"] == ["changed.txt"]
        assert data["has_changes"] is True

    @pytest.mark.asyncio
    async def test_sync_start_still_works_with_old_format(self, test_client) -> None:
        """POST /profiles/{slug}/sync/start still works with old request format."""
        engine = _get_engine()
        assert engine is not None
        launch = MagicMock(job_id=42)
        launch.wait_started = AsyncMock(return_value=False)
        engine.launch = AsyncMock(return_value=launch)
        engine.get_status = AsyncMock(return_value=engine._state.to_status_response())

        resp = await test_client.post(
            "/profiles/default/sync/start",
            json={"direction": "push"},
        )

        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert "state" in data
        assert data["job_id"] == 42

    @pytest.mark.asyncio
    async def test_profile_without_engine_is_404(self, test_client) -> None:
        """A profile route for a profile that is not running names it."""
        resp = await test_client.post("/profiles/other/sync/check")

        assert resp.status_code == 404
        assert "other" in resp.json()["detail"]


# The single-engine /sync/* routes acted on an arbitrary first engine.
LEGACY_ROUTES = [
    ("GET", "/sync/status", "/profiles/{slug}/sync/status"),
    ("POST", "/sync/start", "/profiles/{slug}/sync/start"),
    ("POST", "/sync/stop", "/profiles/{slug}/sync/stop"),
    ("POST", "/sync/check", "/profiles/{slug}/sync/check"),
    ("POST", "/sync/diff?offset=0&limit=10", "/profiles/{slug}/diff"),
    ("POST", "/sync/selective", "/profiles/{slug}/sync/selective"),
    ("POST", "/sync/resume-intervals", "/profiles/{slug}/sync/resume-intervals"),
    ("GET", "/sync/manual-flags", "/profiles/{slug}/manual-flags"),
    ("DELETE", "/sync/manual-flags/a/b.txt", "/profiles/{slug}/manual-flags/{path}"),
]


class TestLegacyRoutesAreGone:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("method", "path", "replacement"), LEGACY_ROUTES)
    async def test_legacy_route_is_410_and_touches_no_engine(
        self, test_client, method: str, path: str, replacement: str,
    ) -> None:
        engine = _get_engine()
        engine.push = AsyncMock()
        engine.pull = AsyncMock()
        engine.launch = AsyncMock()
        engine.stop_current_sync = AsyncMock()
        engine.check_diff = AsyncMock()
        engine.enhanced_diff = AsyncMock()
        engine.selective_sync = AsyncMock()
        engine.resume_intervals = AsyncMock()
        engine.clear_manual_flag = AsyncMock()

        body = {"direction": "push"} if path == "/sync/start" else None
        resp = await test_client.request(method, path, json=body)

        assert resp.status_code == 410
        body = resp.json()
        assert body["code"] == "route_removed"
        assert replacement in body["details"]["replacement"]
        assert replacement in body["detail"]
        for mock in (engine.push, engine.pull, engine.launch, engine.stop_current_sync, engine.check_diff,
                     engine.enhanced_diff, engine.selective_sync, engine.resume_intervals,
                     engine.clear_manual_flag):
            mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_aggregate_status_is_kept(self, test_client) -> None:
        resp = await test_client.get("/sync/status/aggregate")
        assert resp.status_code == 200
        assert resp.json()["profiles_summary"][0]["slug"] == "default"


class TestSelectiveSyncEndpoint:
    """POST /profiles/{slug}/sync/selective — Req 2.5, 2.6, 2.7, 3.1"""

    @pytest.mark.asyncio
    async def test_valid_items_returns_202_running(self, test_client) -> None:
        """POST /profiles/{slug}/sync/selective with valid items starts them: 202, running."""
        engine = _get_engine()
        assert engine is not None
        engine.launch_selective = AsyncMock(return_value=SelectiveSyncResponse(
            job_id=1, status=JobStatus.RUNNING, total=1, succeeded=0, failed=0, errors=[],
        ))

        resp = await test_client.post(
            "/profiles/default/sync/selective",
            json={"items": [{"path": "file.txt", "action": "push"}]},
        )

        assert resp.status_code == 202
        data = resp.json()
        assert data["job_id"] == 1
        assert data["status"] == "running"
        assert data["total"] == 1
        assert data["failed"] == 0
        assert data["errors"] == []

    @pytest.mark.asyncio
    async def test_creates_sync_job_record(self, test_client) -> None:
        """POST /profiles/{slug}/sync/selective creates SyncJob record (returns job_id)."""
        engine = _get_engine()
        assert engine is not None
        engine.launch_selective = AsyncMock(return_value=SelectiveSyncResponse(
            job_id=99, status=JobStatus.RUNNING, total=2, succeeded=0, failed=0, errors=[],
        ))

        resp = await test_client.post(
            "/profiles/default/sync/selective",
            json={"items": [
                {"path": "a.txt", "action": "push"},
                {"path": "b.txt", "action": "pull"},
            ]},
        )

        assert resp.status_code == 202
        data = resp.json()
        assert data["job_id"] == 99
        engine.launch_selective.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalid_paths_returns_400(self, test_client) -> None:
        """POST /profiles/{slug}/sync/selective with invalid paths returns 400."""
        engine = _get_engine()
        assert engine is not None
        engine.launch_selective = AsyncMock(
            side_effect=InvalidFilePathsError(["bad.txt"]),
        )

        resp = await test_client.post(
            "/profiles/default/sync/selective",
            json={"items": [{"path": "bad.txt", "action": "push"}]},
        )

        assert resp.status_code == 400
        data = resp.json()
        assert data["code"] == "invalid_paths" and "bad.txt" in data["detail"]
        assert "bad.txt" in data["details"]["invalid_paths"]

    @pytest.mark.asyncio
    async def test_no_cached_diff_returns_409(self, test_client) -> None:
        """POST /profiles/{slug}/sync/selective without cached diff returns 409."""
        engine = _get_engine()
        assert engine is not None
        engine.launch_selective = AsyncMock(
            side_effect=NoCachedDiffError("No diff"),
        )

        resp = await test_client.post(
            "/profiles/default/sync/selective",
            json={"items": [{"path": "file.txt", "action": "push"}]},
        )

        assert resp.status_code == 409
        data = resp.json()
        assert "No diff cached. Run POST /profiles/default/diff first." in data["detail"]

    @pytest.mark.asyncio
    async def test_batch_multiple_items_accepted(self, test_client) -> None:
        """Batch request with multiple items accepted — Req 3.1."""
        engine = _get_engine()
        assert engine is not None
        engine.launch_selective = AsyncMock(return_value=SelectiveSyncResponse(
            job_id=5, status=JobStatus.RUNNING, total=3, succeeded=0, failed=0, errors=[],
        ))

        items = [
            {"path": "file1.txt", "action": "push"},
            {"path": "file2.txt", "action": "push"},
            {"path": "file3.txt", "action": "pull"},
        ]
        resp = await test_client.post(
            "/profiles/default/sync/selective",
            json={"items": items},
        )

        assert resp.status_code == 202
        data = resp.json()
        assert (data["status"], data["total"]) == ("running", 3)


    @pytest.mark.asyncio
    async def test_busy_profile_returns_409(self, test_client) -> None:
        engine = _get_engine()
        assert engine is not None
        engine.launch_selective = AsyncMock(side_effect=SyncBusyError())

        resp = await test_client.post(
            "/profiles/default/sync/selective", json={"items": [{"path": "a.txt", "action": "push"}]},
        )
        assert (resp.status_code, resp.json()["code"]) == (409, "sync_busy")

    @pytest.mark.asyncio
    async def test_result_is_followed_by_job_id(self, test_client) -> None:
        engine = _get_engine()
        assert engine is not None
        result = SelectiveSyncResponse(
            job_id=7, status=JobStatus.FAILED, total=2, succeeded=1, failed=1,
            errors=[FileError(path="b.txt", error="Copying the file failed. The OmniSync log has the details.")],
        )
        engine.selective_result = MagicMock(side_effect=lambda job_id: result if job_id == 7 else None)

        resp = await test_client.get("/profiles/default/sync/selective/7")
        assert resp.status_code == 200
        assert resp.json()["status"] == "failed" and resp.json()["errors"][0]["path"] == "b.txt"

        resp = await test_client.get("/profiles/default/sync/selective/12345")
        assert (resp.status_code, resp.json()["code"]) == (404, "job_not_found")


class TestManualFlagsEndpoints:
    """GET /profiles/{slug}/manual-flags and DELETE /profiles/{slug}/manual-flags/{path} — Req 7.4, 7.5"""

    @pytest.mark.asyncio
    async def test_get_manual_flags_returns_flag_list(self, test_client) -> None:
        """GET /profiles/{slug}/manual-flags returns flag list — Req 7.5."""
        engine = _get_engine()
        assert engine is not None
        engine.get_manual_flags = AsyncMock(
            return_value=["file1.txt", "file2.txt"],
        )

        resp = await test_client.get("/profiles/default/manual-flags")

        assert resp.status_code == 200
        data = resp.json()
        assert "flags" in data
        assert data["flags"] == ["file1.txt", "file2.txt"]

    @pytest.mark.asyncio
    async def test_delete_manual_flag_returns_204(self, test_client) -> None:
        """DELETE /profiles/{slug}/manual-flags/{path} returns 204 — Req 7.4."""
        engine = _get_engine()
        assert engine is not None
        engine.clear_manual_flag = AsyncMock(return_value=True)

        resp = await test_client.delete("/profiles/default/manual-flags/file1.txt")

        assert resp.status_code == 204
        engine.clear_manual_flag.assert_awaited_once_with("file1.txt")

    @pytest.mark.asyncio
    async def test_delete_unknown_flag_returns_404(self, test_client) -> None:
        """DELETE /profiles/{slug}/manual-flags/{path} for unknown path returns 404."""
        engine = _get_engine()
        assert engine is not None
        engine.clear_manual_flag = AsyncMock(return_value=False)

        resp = await test_client.delete("/profiles/default/manual-flags/nonexistent.txt")

        assert resp.status_code == 404
        data = resp.json()
        assert "not found" in data["detail"].lower()


# ---------------------------------------------------------------------------
# Integration tests: selective_sync pending_changes update (Task 1.4)
# ---------------------------------------------------------------------------


def _make_file(path: str, category: ChangeCategory = ChangeCategory.LOCAL_ONLY) -> FileDiff:
    return FileDiff(
        path=path,
        category=category,
        is_conflict=(category == ChangeCategory.MODIFIED_BOTH),
        manual_flag=False,
    )


def _build_diff_response(files: list[FileDiff]) -> DiffResponse:
    summary = DiffSummary(total=len(files))
    for f in files:
        if f.category == ChangeCategory.LOCAL_ONLY:
            summary.local_only += 1
        elif f.category == ChangeCategory.REMOTE_ONLY:
            summary.remote_only += 1
        elif f.category == ChangeCategory.MODIFIED_LOCAL:
            summary.modified_local += 1
        elif f.category == ChangeCategory.MODIFIED_REMOTE:
            summary.modified_remote += 1
        elif f.category == ChangeCategory.MODIFIED_BOTH:
            summary.modified_both += 1
        if f.manual_flag:
            summary.manual += 1
    return DiffResponse(files=files, summary=summary)


def listing_as_diffed(engine):
    """A mock lsjson_paths: each side holds exactly the files the cached diff saw there.

    The engine re-checks the cached diff before per-file actions (DS-12).
    """
    async def lsjson_paths(root, paths):
        diff = {f.path: f for f in engine._state.cached_diff.files} if engine._state.cached_diff else {}
        absent = ChangeCategory.REMOTE_ONLY if root == engine._profile.local_dir else ChangeCategory.LOCAL_ONLY
        return {
            p.lstrip("/"): {"Path": p.lstrip("/"), "IsDir": False}
            for p in paths if p in diff and diff[p].category != absent
        }
    return lsjson_paths


@pytest.fixture
async def sync_engine(tmp_path):
    """Create a real SyncEngine with mocked rclone and DB for integration tests."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from backend.models.profile_config import ProfileConfig
    from backend.services.sync_engine import SyncEngine
    from backend.services.rclone import RcloneService

    profile = ProfileConfig(
        profile_id=1,
        slug="test",
        name="Test",
        local_dir=str(tmp_path),  # per-file actions never write into a missing folder
        remote_dir="remote:backup",
    )

    rclone = AsyncMock(spec=RcloneService)
    rclone.copy_files = AsyncMock()
    # every copied file arrives (the engine checks with existing_paths)
    rclone.existing_paths = AsyncMock(side_effect=lambda root, paths: {x.lstrip('/') for x in paths})
    rclone.move_file = AsyncMock()

    engine_db = create_async_engine("sqlite+aiosqlite:///:memory:")
    db_factory = async_sessionmaker(engine_db, expire_on_commit=False)

    engine = SyncEngine(profile, rclone, db_factory)
    engine._rclone = rclone
    rclone.lsjson_paths = AsyncMock(side_effect=listing_as_diffed(engine))
    engine._engine_db = engine_db  # type: ignore[attr-defined]  # test-only cleanup ref
    yield engine
    await engine_db.dispose()


class TestSelectiveSyncPendingChanges:
    """Integration tests: selective_sync updates pending_changes correctly."""

    @pytest.mark.asyncio
    async def test_push_decrements_pending_changes(self, sync_engine) -> None:
        """After push action: pending_changes decremented, file removed from cached_diff."""
        engine = sync_engine
        async with engine._engine_db.begin() as conn:
            from backend.db.models import Base
            await conn.run_sync(Base.metadata.create_all)

        files = [
            _make_file("a.txt", ChangeCategory.LOCAL_ONLY),
            _make_file("b.txt", ChangeCategory.REMOTE_ONLY),
            _make_file("c.txt", ChangeCategory.MODIFIED_LOCAL),
        ]
        engine._state.cached_diff = _build_diff_response(files)
        engine._state.pending_changes = 3

        items = [SelectiveSyncItem(path="a.txt", action=FileAction.PUSH)]
        result = await engine.selective_sync(items)

        assert result.succeeded == 1
        assert result.failed == 0
        assert engine._state.pending_changes == 2
        assert len(engine._state.cached_diff.files) == 2
        remaining_paths = {f.path for f in engine._state.cached_diff.files}
        assert "a.txt" not in remaining_paths

    @pytest.mark.asyncio
    async def test_pull_decrements_pending_changes(self, sync_engine) -> None:
        """After pull action: pending_changes decremented, file removed from cached_diff."""
        engine = sync_engine
        async with engine._engine_db.begin() as conn:
            from backend.db.models import Base
            await conn.run_sync(Base.metadata.create_all)

        files = [
            _make_file("a.txt", ChangeCategory.REMOTE_ONLY),
            _make_file("b.txt", ChangeCategory.LOCAL_ONLY),
        ]
        engine._state.cached_diff = _build_diff_response(files)
        engine._state.pending_changes = 2

        items = [SelectiveSyncItem(path="a.txt", action=FileAction.PULL)]
        result = await engine.selective_sync(items)

        assert result.succeeded == 1
        assert engine._state.pending_changes == 1
        remaining_paths = {f.path for f in engine._state.cached_diff.files}
        assert "a.txt" not in remaining_paths
        assert "b.txt" in remaining_paths

    @pytest.mark.asyncio
    async def test_keep_both_decrements_pending_changes(self, sync_engine) -> None:
        """After keep_both action: pending_changes decremented, file removed from cached_diff."""
        engine = sync_engine
        async with engine._engine_db.begin() as conn:
            from backend.db.models import Base
            await conn.run_sync(Base.metadata.create_all)

        files = [
            _make_file("conflict.txt", ChangeCategory.MODIFIED_BOTH),
            _make_file("other.txt", ChangeCategory.LOCAL_ONLY),
        ]
        engine._state.cached_diff = _build_diff_response(files)
        engine._state.pending_changes = 2

        items = [SelectiveSyncItem(path="conflict.txt", action=FileAction.KEEP_BOTH)]
        result = await engine.selective_sync(items)

        assert result.succeeded == 1
        assert engine._state.pending_changes == 1
        remaining_paths = {f.path for f in engine._state.cached_diff.files}
        assert "conflict.txt" not in remaining_paths

    @pytest.mark.asyncio
    async def test_skip_continues_to_work(self, sync_engine) -> None:
        """Regression check: skip action still updates pending_changes correctly."""
        engine = sync_engine
        async with engine._engine_db.begin() as conn:
            from backend.db.models import Base
            await conn.run_sync(Base.metadata.create_all)

        files = [
            _make_file("a.txt", ChangeCategory.LOCAL_ONLY),
            _make_file("b.txt", ChangeCategory.REMOTE_ONLY),
        ]
        engine._state.cached_diff = _build_diff_response(files)
        engine._state.pending_changes = 2

        items = [SelectiveSyncItem(path="a.txt", action=FileAction.SKIP)]
        result = await engine.selective_sync(items)

        assert result.succeeded == 1
        assert engine._state.pending_changes == 1
        remaining_paths = {f.path for f in engine._state.cached_diff.files}
        assert "a.txt" not in remaining_paths

    @pytest.mark.asyncio
    async def test_partial_push_failure_only_removes_none(self, sync_engine) -> None:
        """When push fails, no files are removed from cache (batch operation)."""
        engine = sync_engine
        async with engine._engine_db.begin() as conn:
            from backend.db.models import Base
            await conn.run_sync(Base.metadata.create_all)

        engine._rclone.copy_files = AsyncMock(side_effect=Exception("rclone error"))

        files = [
            _make_file("a.txt", ChangeCategory.LOCAL_ONLY),
            _make_file("b.txt", ChangeCategory.LOCAL_ONLY),
        ]
        engine._state.cached_diff = _build_diff_response(files)
        engine._state.pending_changes = 2

        items = [
            SelectiveSyncItem(path="a.txt", action=FileAction.PUSH),
            SelectiveSyncItem(path="b.txt", action=FileAction.PUSH),
        ]
        result = await engine.selective_sync(items)

        assert result.failed == 2
        assert result.succeeded == 0
        # Cache unchanged — push failed so files remain
        assert engine._state.pending_changes == 2
        assert len(engine._state.cached_diff.files) == 2


# ---------------------------------------------------------------------------
# Pagination tests for POST /profiles/{slug}/diff (Task 3.3)
# ---------------------------------------------------------------------------

from hypothesis import given, settings
from hypothesis import strategies as st


def _build_large_diff(n: int) -> DiffResponse:
    """Build a DiffResponse with n files."""
    files = [_make_file(f"file_{i}.txt") for i in range(n)]
    return _build_diff_response(files)


class TestDiffPagination:
    """POST /profiles/{slug}/diff with offset/limit parameters."""

    @pytest.mark.asyncio
    async def test_no_pagination_returns_all(self, test_client) -> None:
        """No offset/limit returns all files with pagination=None."""
        engine = _get_engine()
        assert engine is not None
        engine.enhanced_diff = AsyncMock(return_value=_build_large_diff(50))

        resp = await test_client.post("/profiles/default/diff")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["files"]) == 50
        assert data["pagination"] is None

    @pytest.mark.asyncio
    async def test_limit_zero_returns_all(self, test_client) -> None:
        """limit=0 is backward compat — returns all files."""
        engine = _get_engine()
        assert engine is not None
        engine.enhanced_diff = AsyncMock(return_value=_build_large_diff(50))

        resp = await test_client.post("/profiles/default/diff?offset=0&limit=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["files"]) == 50
        assert data["pagination"] is None

    @pytest.mark.asyncio
    async def test_paginated_first_page(self, test_client) -> None:
        """First page returns correct slice and pagination metadata."""
        engine = _get_engine()
        assert engine is not None
        engine.enhanced_diff = AsyncMock(return_value=_build_large_diff(250))

        resp = await test_client.post("/profiles/default/diff?offset=0&limit=100")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["files"]) == 100
        pag = data["pagination"]
        assert pag["offset"] == 0
        assert pag["limit"] == 100
        assert pag["total"] == 250
        assert pag["has_more"] is True
        # Summary reflects full diff, not page
        assert data["summary"]["total"] == 250

    @pytest.mark.asyncio
    async def test_paginated_last_page(self, test_client) -> None:
        """Last page has has_more=False."""
        engine = _get_engine()
        assert engine is not None
        engine.enhanced_diff = AsyncMock(return_value=_build_large_diff(250))

        resp = await test_client.post("/profiles/default/diff?offset=200&limit=100")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["files"]) == 50
        pag = data["pagination"]
        assert pag["offset"] == 200
        assert pag["total"] == 250
        assert pag["has_more"] is False

    @pytest.mark.asyncio
    async def test_offset_beyond_total(self, test_client) -> None:
        """offset beyond total returns empty files."""
        engine = _get_engine()
        assert engine is not None
        engine.enhanced_diff = AsyncMock(return_value=_build_large_diff(10))

        resp = await test_client.post("/profiles/default/diff?offset=50&limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["files"]) == 0
        pag = data["pagination"]
        assert pag["total"] == 10
        assert pag["has_more"] is False

    @pytest.mark.asyncio
    async def test_limit_exceeds_500_rejected(self, test_client) -> None:
        """limit > 500 is rejected by validation."""
        resp = await test_client.post("/profiles/default/diff?limit=501")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_negative_offset_rejected(self, test_client) -> None:
        """Negative offset is rejected by validation."""
        resp = await test_client.post("/profiles/default/diff?offset=-1")
        assert resp.status_code == 422


# Hypothesis property-based test for pagination slicing
@given(
    total=st.integers(min_value=0, max_value=5000),
    offset=st.integers(min_value=0, max_value=6000),
    limit=st.integers(min_value=1, max_value=500),
)
@settings(max_examples=200)
def test_pagination_slicing_properties(total: int, offset: int, limit: int) -> None:
    """Pagination slicing invariants hold for any valid inputs."""
    files = list(range(total))  # simulated file list
    page = files[offset : offset + limit]
    has_more = (offset + limit) < total

    assert len(page) <= limit
    assert len(page) == min(limit, max(0, total - offset))
    assert has_more == ((offset + limit) < total)
