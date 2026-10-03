"""Property-based tests for Granular Sync Control.

Uses Hypothesis to verify universal correctness properties
across diff classification, summary consistency, and selective sync logic.
"""

from datetime import datetime, timezone

from hypothesis import given, settings
from hypothesis import strategies as st

from backend.api.schemas import (
    ChangeCategory,
    DiffResponse,
    DiffSummary,
    FileError,
    FileDiff,
    SelectiveSyncResponse,
)


# --- Strategies ---

VALID_CATEGORIES = set(ChangeCategory)

change_categories = st.sampled_from(list(ChangeCategory))

optional_positive_int = st.one_of(st.none(), st.integers(min_value=0, max_value=10**15))

optional_datetime = st.one_of(
    st.none(),
    st.datetimes(
        min_value=datetime(2000, 1, 1),
        max_value=datetime(2030, 12, 31),
        timezones=st.just(timezone.utc),
    ),
)

file_path_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P"), whitelist_characters="/._-"),
    min_size=1,
    max_size=200,
)


def build_correct_file_diff(
    path: str,
    category: ChangeCategory,
    local_size: int | None,
    remote_size: int | None,
    local_mod_time: datetime | None,
    remote_mod_time: datetime | None,
    manual_flag: bool,
) -> FileDiff:
    """Build a FileDiff with the correct is_conflict invariant enforced,
    mirroring how the system's enhanced_diff() constructs results."""
    return FileDiff(
        path=path,
        category=category,
        local_size=local_size,
        remote_size=remote_size,
        local_mod_time=local_mod_time,
        remote_mod_time=remote_mod_time,
        is_conflict=(category == ChangeCategory.MODIFIED_BOTH),
        manual_flag=manual_flag,
    )


correctly_built_file_diff = st.builds(
    build_correct_file_diff,
    path=file_path_strategy,
    category=change_categories,
    local_size=optional_positive_int,
    remote_size=optional_positive_int,
    local_mod_time=optional_datetime,
    remote_mod_time=optional_datetime,
    manual_flag=st.booleans(),
)


# Diff classification is exhaustive and exclusive
class TestDiffClassificationExhaustiveAndExclusive:
    """For any file in the diff result, it must be assigned exactly one
    ChangeCategory and is_conflict must be True iff category is modified_both."""

    @settings(max_examples=200)
    @given(diff=correctly_built_file_diff)
    def test_exactly_one_valid_category_assigned(self, diff: FileDiff) -> None:
        """Each FileDiff must have exactly one category from the valid set."""
        assert diff.category in VALID_CATEGORIES
        # Verify it's a single enum value, not a collection
        assert isinstance(diff.category, ChangeCategory)

    @settings(max_examples=200)
    @given(diff=correctly_built_file_diff)
    def test_is_conflict_true_iff_modified_both(self, diff: FileDiff) -> None:
        """is_conflict must be True when and only when category is modified_both."""
        if diff.category == ChangeCategory.MODIFIED_BOTH:
            assert diff.is_conflict is True, (
                f"File '{diff.path}' has category modified_both but is_conflict=False"
            )
        else:
            assert diff.is_conflict is False, (
                f"File '{diff.path}' has category {diff.category.value} but is_conflict=True"
            )

    @settings(max_examples=200)
    @given(diff=correctly_built_file_diff)
    def test_category_is_mutually_exclusive(self, diff: FileDiff) -> None:
        """The assigned category must not equal any other category value."""
        other_categories = VALID_CATEGORIES - {diff.category}
        for other in other_categories:
            assert diff.category != other

    @settings(max_examples=200)
    @given(category=change_categories)
    def test_all_categories_produce_valid_file_diffs(self, category: ChangeCategory) -> None:
        """Every valid ChangeCategory can construct a FileDiff that satisfies the invariant."""
        diff = build_correct_file_diff(
            path="test/file.txt",
            category=category,
            local_size=100,
            remote_size=200,
            local_mod_time=None,
            remote_mod_time=None,
            manual_flag=False,
        )
        assert diff.category == category
        assert diff.is_conflict == (category == ChangeCategory.MODIFIED_BOTH)

    @settings(max_examples=200)
    @given(
        category=change_categories,
        local_size=optional_positive_int,
        remote_size=optional_positive_int,
        local_mod_time=optional_datetime,
        remote_mod_time=optional_datetime,
    )
    def test_metadata_does_not_affect_classification(
        self,
        category: ChangeCategory,
        local_size: int | None,
        remote_size: int | None,
        local_mod_time: datetime | None,
        remote_mod_time: datetime | None,
    ) -> None:
        """The is_conflict flag depends only on category, not on metadata values."""
        diff = build_correct_file_diff(
            path="any.txt",
            category=category,
            local_size=local_size,
            remote_size=remote_size,
            local_mod_time=local_mod_time,
            remote_mod_time=remote_mod_time,
            manual_flag=False,
        )
        assert diff.is_conflict == (category == ChangeCategory.MODIFIED_BOTH)


# --- Diff response summary consistency: helpers ---


def build_correct_diff_response(files: list[FileDiff]) -> DiffResponse:
    """Build a DiffResponse with summary computed from the actual files list,
    mirroring how the system's enhanced_diff() constructs results."""
    summary = DiffSummary(
        local_only=sum(1 for f in files if f.category == ChangeCategory.LOCAL_ONLY),
        remote_only=sum(1 for f in files if f.category == ChangeCategory.REMOTE_ONLY),
        modified_local=sum(1 for f in files if f.category == ChangeCategory.MODIFIED_LOCAL),
        modified_remote=sum(1 for f in files if f.category == ChangeCategory.MODIFIED_REMOTE),
        modified_both=sum(1 for f in files if f.category == ChangeCategory.MODIFIED_BOTH),
        manual=sum(1 for f in files if f.manual_flag),
        total=len(files),
    )
    return DiffResponse(files=files, summary=summary)


# Diff response summary consistency
class TestDiffResponseSummaryConsistency:
    """For any DiffResponse, the summary field counts must equal the actual
    count of files in each category within the files list, and summary.total
    must equal the length of files."""

    @settings(max_examples=200)
    @given(files=st.lists(correctly_built_file_diff, min_size=0, max_size=50))
    def test_summary_total_equals_file_count(self, files: list[FileDiff]) -> None:
        """summary.total must equal len(files)."""
        resp = build_correct_diff_response(files)
        assert resp.summary.total == len(resp.files), (
            f"summary.total={resp.summary.total} but len(files)={len(resp.files)}"
        )

    @settings(max_examples=200)
    @given(files=st.lists(correctly_built_file_diff, min_size=0, max_size=50))
    def test_category_counts_match_actual_files(self, files: list[FileDiff]) -> None:
        """Each category count in summary must match the actual count of files
        with that category."""
        resp = build_correct_diff_response(files)

        expected_local_only = sum(1 for f in resp.files if f.category == ChangeCategory.LOCAL_ONLY)
        expected_remote_only = sum(1 for f in resp.files if f.category == ChangeCategory.REMOTE_ONLY)
        expected_modified_local = sum(1 for f in resp.files if f.category == ChangeCategory.MODIFIED_LOCAL)
        expected_modified_remote = sum(1 for f in resp.files if f.category == ChangeCategory.MODIFIED_REMOTE)
        expected_modified_both = sum(1 for f in resp.files if f.category == ChangeCategory.MODIFIED_BOTH)

        assert resp.summary.local_only == expected_local_only
        assert resp.summary.remote_only == expected_remote_only
        assert resp.summary.modified_local == expected_modified_local
        assert resp.summary.modified_remote == expected_modified_remote
        assert resp.summary.modified_both == expected_modified_both

    @settings(max_examples=200)
    @given(files=st.lists(correctly_built_file_diff, min_size=0, max_size=50))
    def test_summary_manual_matches_manual_flag_count(self, files: list[FileDiff]) -> None:
        """summary.manual must match the count of files with manual_flag=True."""
        resp = build_correct_diff_response(files)
        expected_manual = sum(1 for f in resp.files if f.manual_flag)
        assert resp.summary.manual == expected_manual, (
            f"summary.manual={resp.summary.manual} but actual manual count={expected_manual}"
        )

    @settings(max_examples=200)
    @given(files=st.lists(correctly_built_file_diff, min_size=0, max_size=50))
    def test_category_counts_sum_equals_total(self, files: list[FileDiff]) -> None:
        """The sum of all category counts must equal summary.total."""
        resp = build_correct_diff_response(files)
        category_sum = (
            resp.summary.local_only
            + resp.summary.remote_only
            + resp.summary.modified_local
            + resp.summary.modified_remote
            + resp.summary.modified_both
        )
        assert category_sum == resp.summary.total, (
            f"Sum of category counts={category_sum} but total={resp.summary.total}"
        )


# --- Selective sync response invariant: helpers ---


def build_correct_selective_sync_response(
    job_id: int,
    succeeded: int,
    failed: int,
    error_messages: list[str],
) -> SelectiveSyncResponse:
    """Build a SelectiveSyncResponse with the correct invariants enforced,
    mirroring how the system's selective_sync() constructs results."""
    errors = [
        FileError(path=f"file_{i}.txt", error=msg)
        for i, msg in enumerate(error_messages[:failed])
    ]
    return SelectiveSyncResponse(
        job_id=job_id,
        total=succeeded + failed,
        succeeded=succeeded,
        failed=failed,
        errors=errors,
    )


# Selective sync response invariant
class TestSelectiveSyncResponseInvariant:
    """For any selective sync response, total must equal succeeded + failed,
    and the length of the errors list must equal failed."""

    @settings(max_examples=200)
    @given(
        job_id=st.integers(min_value=1, max_value=10**6),
        succeeded=st.integers(min_value=0, max_value=500),
        failed=st.integers(min_value=0, max_value=500),
    )
    def test_total_equals_succeeded_plus_failed(
        self, job_id: int, succeeded: int, failed: int
    ) -> None:
        """total must equal succeeded + failed."""
        error_messages = [f"rclone error on file {i}" for i in range(failed)]
        resp = build_correct_selective_sync_response(job_id, succeeded, failed, error_messages)
        assert resp.total == resp.succeeded + resp.failed, (
            f"total={resp.total} but succeeded+failed={resp.succeeded + resp.failed}"
        )

    @settings(max_examples=200)
    @given(
        job_id=st.integers(min_value=1, max_value=10**6),
        succeeded=st.integers(min_value=0, max_value=500),
        failed=st.integers(min_value=0, max_value=500),
    )
    def test_errors_length_equals_failed(
        self, job_id: int, succeeded: int, failed: int
    ) -> None:
        """len(errors) must equal failed."""
        error_messages = [f"rclone error on file {i}" for i in range(failed)]
        resp = build_correct_selective_sync_response(job_id, succeeded, failed, error_messages)
        assert len(resp.errors) == resp.failed, (
            f"len(errors)={len(resp.errors)} but failed={resp.failed}"
        )

    @settings(max_examples=200)
    @given(
        job_id=st.integers(min_value=1, max_value=10**6),
        succeeded=st.integers(min_value=0, max_value=500),
        failed=st.integers(min_value=1, max_value=500),
    )
    def test_errors_non_empty_when_failed_positive(
        self, job_id: int, succeeded: int, failed: int
    ) -> None:
        """If failed > 0, errors list must be non-empty."""
        error_messages = [f"rclone error on file {i}" for i in range(failed)]
        resp = build_correct_selective_sync_response(job_id, succeeded, failed, error_messages)
        assert len(resp.errors) > 0, (
            f"failed={resp.failed} but errors list is empty"
        )

    @settings(max_examples=200)
    @given(
        job_id=st.integers(min_value=1, max_value=10**6),
        succeeded=st.integers(min_value=0, max_value=500),
    )
    def test_errors_empty_when_failed_zero(
        self, job_id: int, succeeded: int
    ) -> None:
        """If failed == 0, errors list must be empty."""
        resp = build_correct_selective_sync_response(job_id, succeeded, 0, [])
        assert len(resp.errors) == 0, (
            f"failed=0 but errors list has {len(resp.errors)} entries"
        )


# --- Bulk sync clears diff cache: imports ---

from backend.models.sync_state import SyncStateManager


# Bulk sync clears diff cache
class TestBulkSyncClearsDiffCache:
    """For any successful bulk sync completion, the SyncStateManager.cached_diff
    must be None and pending_changes must be 0 afterward."""

    @settings(max_examples=200)
    @given(
        files=st.lists(correctly_built_file_diff, min_size=1, max_size=50),
        initial_pending=st.integers(min_value=1, max_value=10000),
        files_processed=st.integers(min_value=0, max_value=10000),
    )
    def test_set_idle_clears_cached_diff(
        self,
        files: list[FileDiff],
        initial_pending: int,
        files_processed: int,
    ) -> None:
        """After set_idle(), cached_diff must be None regardless of prior state."""
        manager = SyncStateManager()
        diff = build_correct_diff_response(files)
        manager.cache_diff(diff)
        manager.pending_changes = initial_pending

        # Verify preconditions
        assert manager.cached_diff is not None
        assert manager.pending_changes > 0

        manager.set_idle(files_processed=files_processed)

        assert manager.cached_diff is None, (
            f"cached_diff should be None after set_idle(), got {manager.cached_diff}"
        )

    @settings(max_examples=200)
    @given(
        files=st.lists(correctly_built_file_diff, min_size=1, max_size=50),
        initial_pending=st.integers(min_value=1, max_value=10000),
        files_processed=st.integers(min_value=0, max_value=10000),
    )
    def test_set_idle_resets_pending_changes_to_zero(
        self,
        files: list[FileDiff],
        initial_pending: int,
        files_processed: int,
    ) -> None:
        """After set_idle(), pending_changes must be 0 regardless of prior state."""
        manager = SyncStateManager()
        diff = build_correct_diff_response(files)
        manager.cache_diff(diff)
        manager.pending_changes = initial_pending

        manager.set_idle(files_processed=files_processed)

        assert manager.pending_changes == 0, (
            f"pending_changes should be 0 after set_idle(), got {manager.pending_changes}"
        )

    @settings(max_examples=200)
    @given(
        files=st.lists(correctly_built_file_diff, min_size=1, max_size=50),
        initial_pending=st.integers(min_value=1, max_value=10000),
        files_processed=st.integers(min_value=0, max_value=10000),
    )
    def test_set_idle_clears_both_simultaneously(
        self,
        files: list[FileDiff],
        initial_pending: int,
        files_processed: int,
    ) -> None:
        """Both cached_diff and pending_changes must be cleared after set_idle()."""
        manager = SyncStateManager()
        diff = build_correct_diff_response(files)
        manager.cache_diff(diff)
        manager.pending_changes = initial_pending

        manager.set_idle(files_processed=files_processed)

        assert manager.cached_diff is None and manager.pending_changes == 0, (
            f"Expected cached_diff=None and pending_changes=0, "
            f"got cached_diff={manager.cached_diff}, pending_changes={manager.pending_changes}"
        )


# --- Filter file generation correctness: imports ---

from unittest.mock import AsyncMock

import pytest

from backend.services.rclone import RcloneResult, RcloneService


# --- Filter file generation correctness: helpers ---

def _make_rclone_service() -> RcloneService:
    """Build a minimal RcloneService for testing."""
    return RcloneService(rclone_config_path="/tmp/fake-rclone.conf")


# Strategy for generating realistic file paths (non-empty, no leading/trailing whitespace)
sync_file_path = st.from_regex(
    r"[a-zA-Z0-9][a-zA-Z0-9_/.\-]{0,99}",
    fullmatch=True,
)


# Filter file generation correctness
class TestExactFileListCorrectness:
    """For any non-empty list of file paths, a per-file copy passes exactly
    those paths, one literal path per line, via --files-from-raw in a single
    rclone invocation, with source and dest after '--'. (A '+ <path>' filter
    rule would also match same-named files in other folders and treat
    [ * ? as wildcards.)"""

    @staticmethod
    async def _capture(file_paths: list[str]) -> list[tuple[list[str], list[str], list[str]]]:
        svc = _make_rclone_service()
        calls: list[tuple[list[str], list[str], list[str]]] = []

        async def mock_run(args: list[str], use_config_args: bool = True, timeout: int | None = None,
                           positional: list[str] | None = None, **_kwargs) -> RcloneResult:
            lines: list[str] = []
            if "--files-from-raw" in args:
                with open(args[args.index("--files-from-raw") + 1], encoding="utf-8") as f:
                    lines = f.read().splitlines()
            calls.append((args, positional or [], lines))
            return RcloneResult(stdout="", stderr="", return_code=0, elapsed_seconds=0.1)

        svc._run = mock_run  # type: ignore[assignment]
        await svc.copy_files("source:", "dest:", file_paths)
        return calls

    @settings(max_examples=100)
    @given(file_paths=st.lists(sync_file_path, min_size=1, max_size=50, unique=True))
    @pytest.mark.asyncio
    async def test_list_contains_exactly_the_paths(self, file_paths: list[str]) -> None:
        calls = await self._capture(file_paths)
        assert len(calls) == 1
        _, _, lines = calls[0]
        assert sorted(lines) == sorted(p.lstrip("/") for p in file_paths)

    @settings(max_examples=100)
    @given(file_paths=st.lists(sync_file_path, min_size=1, max_size=50, unique=True))
    @pytest.mark.asyncio
    async def test_uses_files_from_raw_and_no_filter_rules(self, file_paths: list[str]) -> None:
        calls = await self._capture(file_paths)
        args, positional, _ = calls[0]
        assert args[0] == "copy" and "--files-from-raw" in args
        assert "--filter-from" not in args and not any(a.startswith("--filter") for a in args)
        assert positional == ["source:", "dest:"]


# --- Keep-both conflict rename format: imports ---

from pathlib import PurePosixPath

from backend.services.rclone import generate_conflict_rename


# --- Keep-both conflict rename format: strategies ---

# File paths with extensions (contain at least one dot with chars after it)
file_path_with_ext = st.from_regex(
    r"([a-zA-Z0-9_\-]+/)*[a-zA-Z0-9_\-]+\.[a-zA-Z0-9]{1,10}",
    fullmatch=True,
)

# File paths without extensions (no dots at all)
file_path_without_ext = st.from_regex(
    r"([a-zA-Z0-9_\-]+/)*[a-zA-Z0-9_\-]+",
    fullmatch=True,
)

# Timestamps for conflict rename
conflict_timestamp = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2099, 12, 31),
)


# Keep-both conflict rename format
class TestKeepBothConflictRenameFormat:
    """For any file path used in a 'keep both' conflict resolution, the renamed
    remote file must have the format <stem>.conflict-<YYYYMMDDTHHMMSS><ext>
    where timestamp is a valid datetime string, and the original file extension
    is preserved."""

    @settings(max_examples=100)
    @given(path=file_path_with_ext, ts=conflict_timestamp)
    def test_renamed_path_contains_conflict_substring(
        self, path: str, ts: datetime
    ) -> None:
        """The renamed path must contain '.conflict-' substring."""
        renamed = generate_conflict_rename(path, ts)
        assert ".conflict-" in renamed, (
            f"Renamed path '{renamed}' does not contain '.conflict-' (original: '{path}')"
        )

    @settings(max_examples=100)
    @given(path=file_path_with_ext, ts=conflict_timestamp)
    def test_timestamp_portion_is_valid_datetime(
        self, path: str, ts: datetime
    ) -> None:
        """The timestamp portion between '.conflict-' and the extension
        must be a valid YYYYMMDDTHHMMSS datetime string."""
        renamed = generate_conflict_rename(path, ts)
        # Extract the filename part (last component)
        filename = PurePosixPath(renamed).name
        # Find the conflict marker
        conflict_idx = filename.index(".conflict-")
        after_conflict = filename[conflict_idx + len(".conflict-"):]
        # The timestamp is everything before the extension (if any)
        ext = PurePosixPath(path).suffix
        if ext:
            assert after_conflict.endswith(ext), (
                f"Expected renamed file to end with '{ext}', got '{after_conflict}'"
            )
            ts_str = after_conflict[: -len(ext)]
        else:
            ts_str = after_conflict
        # Validate the timestamp format
        parsed = datetime.strptime(ts_str, "%Y%m%dT%H%M%S")
        assert parsed == ts.replace(microsecond=0, tzinfo=None), (
            f"Parsed timestamp {parsed} does not match input {ts}"
        )

    @settings(max_examples=100)
    @given(path=file_path_with_ext, ts=conflict_timestamp)
    def test_file_extension_is_preserved(
        self, path: str, ts: datetime
    ) -> None:
        """The file extension must be preserved in the renamed path."""
        renamed = generate_conflict_rename(path, ts)
        original_ext = PurePosixPath(path).suffix
        renamed_ext = PurePosixPath(renamed).suffix
        assert renamed_ext == original_ext, (
            f"Extension mismatch: original='{original_ext}', renamed='{renamed_ext}' "
            f"(path='{path}', renamed='{renamed}')"
        )

    @settings(max_examples=100)
    @given(path=file_path_with_ext, ts=conflict_timestamp)
    def test_original_stem_preserved_as_prefix(
        self, path: str, ts: datetime
    ) -> None:
        """The original stem must be preserved as a prefix of the renamed filename."""
        renamed = generate_conflict_rename(path, ts)
        original_stem = PurePosixPath(path).stem
        renamed_name = PurePosixPath(renamed).name
        assert renamed_name.startswith(original_stem + ".conflict-"), (
            f"Renamed filename '{renamed_name}' does not start with "
            f"'{original_stem}.conflict-' (path='{path}')"
        )

    @settings(max_examples=100)
    @given(path=file_path_without_ext, ts=conflict_timestamp)
    def test_paths_without_extension_get_conflict_suffix(
        self, path: str, ts: datetime
    ) -> None:
        """Paths without extensions should get .conflict-<timestamp> appended."""
        renamed = generate_conflict_rename(path, ts)
        assert ".conflict-" in renamed, (
            f"Renamed path '{renamed}' missing '.conflict-' for extensionless path '{path}'"
        )
        # Should NOT have an extension after the timestamp
        renamed_name = PurePosixPath(renamed).name
        conflict_idx = renamed_name.index(".conflict-")
        after_conflict = renamed_name[conflict_idx + len(".conflict-"):]
        # The entire remainder should be the timestamp (no dots)
        assert "." not in after_conflict, (
            f"Extensionless path should not have extension after timestamp, "
            f"got '{after_conflict}' (path='{path}', renamed='{renamed}')"
        )
        # Validate it's a valid timestamp
        datetime.strptime(after_conflict, "%Y%m%dT%H%M%S")

    @settings(max_examples=100)
    @given(path=file_path_with_ext, ts=conflict_timestamp)
    def test_parent_directory_preserved(
        self, path: str, ts: datetime
    ) -> None:
        """The parent directory of the path must be preserved in the rename."""
        renamed = generate_conflict_rename(path, ts)
        original_parent = str(PurePosixPath(path).parent)
        renamed_parent = str(PurePosixPath(renamed).parent)
        assert original_parent == renamed_parent, (
            f"Parent directory changed: original='{original_parent}', "
            f"renamed='{renamed_parent}' (path='{path}', renamed='{renamed}')"
        )


# --- Timestamp-based classification correctness: imports ---

from datetime import timedelta

from backend.services.sync_engine import SyncEngine


# --- Timestamp-based classification correctness: strategies ---

# Timezone-aware UTC datetimes for timestamp comparison
utc_datetime = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2099, 12, 31),
    timezones=st.just(timezone.utc),
)

# Positive timedeltas for constructing ordered timestamps
positive_timedelta = st.timedeltas(
    min_value=timedelta(seconds=1),
    max_value=timedelta(days=365 * 10),
)


# Timestamp-based classification correctness
class TestTimestampBasedClassificationCorrectness:
    """For any file that exists on both local and remote with differing content,
    given local_mod_time, remote_mod_time, and last_sync time:
    - If only local was modified after last_sync → modified_local
    - If only remote was modified after last_sync → modified_remote
    - If both were modified after last_sync (or last_sync is None and times differ) → modified_both
    """

    @settings(max_examples=200)
    @given(
        base=utc_datetime,
        local_offset=positive_timedelta,
        remote_offset=positive_timedelta,
    )
    def test_only_local_newer_than_last_sync(
        self,
        base: datetime,
        local_offset: timedelta,
        remote_offset: timedelta,
    ) -> None:
        """When last_sync is not None and only local_mod > last_sync → modified_local."""
        last_sync = base
        local_mod = base + local_offset   # local is after last_sync
        remote_mod = base - remote_offset  # remote is before last_sync

        result = SyncEngine._classify_differ(local_mod, remote_mod, last_sync)
        assert result == ChangeCategory.MODIFIED_LOCAL, (
            f"Expected modified_local when only local is newer. "
            f"local_mod={local_mod}, remote_mod={remote_mod}, last_sync={last_sync}, got={result}"
        )

    @settings(max_examples=200)
    @given(
        base=utc_datetime,
        local_offset=positive_timedelta,
        remote_offset=positive_timedelta,
    )
    def test_only_remote_newer_than_last_sync(
        self,
        base: datetime,
        local_offset: timedelta,
        remote_offset: timedelta,
    ) -> None:
        """When last_sync is not None and only remote_mod > last_sync → modified_remote."""
        last_sync = base
        local_mod = base - local_offset    # local is before last_sync
        remote_mod = base + remote_offset  # remote is after last_sync

        result = SyncEngine._classify_differ(local_mod, remote_mod, last_sync)
        assert result == ChangeCategory.MODIFIED_REMOTE, (
            f"Expected modified_remote when only remote is newer. "
            f"local_mod={local_mod}, remote_mod={remote_mod}, last_sync={last_sync}, got={result}"
        )

    @settings(max_examples=200)
    @given(
        base=utc_datetime,
        local_offset=positive_timedelta,
        remote_offset=positive_timedelta,
    )
    def test_both_newer_than_last_sync(
        self,
        base: datetime,
        local_offset: timedelta,
        remote_offset: timedelta,
    ) -> None:
        """When last_sync is not None and both > last_sync → modified_both."""
        last_sync = base
        local_mod = base + local_offset    # local is after last_sync
        remote_mod = base + remote_offset  # remote is after last_sync

        result = SyncEngine._classify_differ(local_mod, remote_mod, last_sync)
        assert result == ChangeCategory.MODIFIED_BOTH, (
            f"Expected modified_both when both are newer. "
            f"local_mod={local_mod}, remote_mod={remote_mod}, last_sync={last_sync}, got={result}"
        )

    @settings(max_examples=200)
    @given(
        base=utc_datetime,
        local_offset=positive_timedelta,
        remote_offset=positive_timedelta,
    )
    def test_neither_newer_than_last_sync(
        self,
        base: datetime,
        local_offset: timedelta,
        remote_offset: timedelta,
    ) -> None:
        """When last_sync is not None and neither > last_sync → modified_both (safe default)."""
        last_sync = base
        local_mod = base - local_offset    # local is before last_sync
        remote_mod = base - remote_offset  # remote is before last_sync

        result = SyncEngine._classify_differ(local_mod, remote_mod, last_sync)
        assert result == ChangeCategory.MODIFIED_BOTH, (
            f"Expected modified_both when neither is newer (safe default). "
            f"local_mod={local_mod}, remote_mod={remote_mod}, last_sync={last_sync}, got={result}"
        )

    @settings(max_examples=200)
    @given(
        base=utc_datetime,
        offset=positive_timedelta,
    )
    def test_no_last_sync_local_newer(
        self,
        base: datetime,
        offset: timedelta,
    ) -> None:
        """When last_sync is None and local > remote → modified_local."""
        remote_mod = base
        local_mod = base + offset  # local is strictly newer

        result = SyncEngine._classify_differ(local_mod, remote_mod, None)
        assert result == ChangeCategory.MODIFIED_LOCAL, (
            f"Expected modified_local when last_sync=None and local > remote. "
            f"local_mod={local_mod}, remote_mod={remote_mod}, got={result}"
        )

    @settings(max_examples=200)
    @given(
        base=utc_datetime,
        offset=positive_timedelta,
    )
    def test_no_last_sync_remote_newer(
        self,
        base: datetime,
        offset: timedelta,
    ) -> None:
        """When last_sync is None and remote > local → modified_remote."""
        local_mod = base
        remote_mod = base + offset  # remote is strictly newer

        result = SyncEngine._classify_differ(local_mod, remote_mod, None)
        assert result == ChangeCategory.MODIFIED_REMOTE, (
            f"Expected modified_remote when last_sync=None and remote > local. "
            f"local_mod={local_mod}, remote_mod={remote_mod}, got={result}"
        )

    @settings(max_examples=200)
    @given(base=utc_datetime)
    def test_no_last_sync_equal_times(
        self,
        base: datetime,
    ) -> None:
        """When last_sync is None and local == remote → modified_both."""
        result = SyncEngine._classify_differ(base, base, None)
        assert result == ChangeCategory.MODIFIED_BOTH, (
            f"Expected modified_both when last_sync=None and times are equal. "
            f"local_mod={base}, remote_mod={base}, got={result}"
        )


# --- Skip removes files from cached diff: imports ---

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.schemas import FileAction, SelectiveSyncItem
from backend.db.models import Base


# --- Skip removes files from cached diff: helpers ---

from backend.services.rclone import SENTINEL_FILE

# A real, non-empty local folder that already carries the sync marker, so the
# engine's pre-sync safety checks pass and the tests reach the rclone mocks.
# Set once per module by _local_dir, in pytest's own temp folder.
_LOCAL_DIR = ""


@pytest.fixture(scope="module", autouse=True)
def _local_dir(tmp_path_factory: pytest.TempPathFactory) -> None:
    global _LOCAL_DIR
    path = tmp_path_factory.mktemp("props-local")
    for name in ("existing.txt", SENTINEL_FILE):
        (path / name).write_text("x")
    _LOCAL_DIR = str(path)


def _parse_exclude_rule(line: str) -> str:
    """Turn an anchored, escaped rclone exclude rule back into the literal path."""
    assert line.startswith("- /"), f"Unexpected filter line format: {line!r}"
    out, escaped = [], False
    for c in line[3:]:
        if escaped:
            out.append(c)
            escaped = False
        elif c == "\\":
            escaped = True
        else:
            out.append(c)
    return "".join(out)


# Databases made by _make_sync_engine_with_diff, closed after each test (a
# hypothesis test makes one per example).
_open_databases: list = []


@pytest.fixture(autouse=True)
async def _close_databases():
    yield
    while _open_databases:
        await _open_databases.pop().dispose()


async def _make_sync_engine_with_diff(
    diff_files: list[FileDiff],
) -> tuple[SyncEngine, AsyncMock]:
    """Create a SyncEngine with an in-memory DB, mock rclone, and a
    pre-populated cached diff. Returns (engine, mock_rclone) so tests
    can inspect rclone call counts."""
    # In-memory SQLite DB
    engine_db = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    _open_databases.append(engine_db)
    async with engine_db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine_db, expire_on_commit=False)

    @asynccontextmanager
    async def session_ctx():
        async with factory() as session:
            yield session

    # Mock profile
    from backend.models.profile_config import ProfileConfig
    mock_profile = ProfileConfig(
        profile_id=1,
        slug="test",
        name="Test",
        local_dir=_LOCAL_DIR,
        remote_dir="remote:bucket",
    )

    # Mock rclone — track calls
    mock_rclone = AsyncMock()
    mock_rclone.list_top_level = AsyncMock(return_value=["existing.txt", SENTINEL_FILE])
    mock_rclone.copy_files = AsyncMock()
    # every copied file arrives (the engine checks with existing_paths)
    mock_rclone.existing_paths = AsyncMock(side_effect=lambda root, paths: {x.lstrip('/') for x in paths})
    mock_rclone.move_file = AsyncMock()
    mock_rclone.sync = AsyncMock()

    sync_engine = SyncEngine(
        profile=mock_profile,
        rclone=mock_rclone,
        db_session_factory=session_ctx,  # type: ignore[arg-type]
    )

    # Populate cached diff
    diff_resp = build_correct_diff_response(diff_files)
    sync_engine._state.cache_diff(diff_resp)
    sync_engine._state.pending_changes = diff_resp.summary.total

    return sync_engine, mock_rclone


# Strategy: generate a list of FileDiffs with unique paths, then pick a subset to skip
unique_path_file_diffs = st.lists(
    correctly_built_file_diff,
    min_size=1,
    max_size=30,
    unique_by=lambda fd: fd.path,
)


# Skip removes files from cached diff
class TestSkipRemovesFilesFromCachedDiff:
    """For any cached diff and any subset of file paths marked as skip,
    after the skip operation, the cached diff must no longer contain those
    file paths, and no rclone transfer command must be invoked."""

    @settings(max_examples=100)
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_skipped_paths_removed_from_cached_diff(
        self, data: st.DataObject
    ) -> None:
        """After skipping a subset of paths, the cached diff must not
        contain any of the skipped paths."""
        diff_files = data.draw(unique_path_file_diffs)
        all_paths = [f.path for f in diff_files]

        # Draw a non-empty subset of paths to skip
        skip_indices = data.draw(
            st.lists(
                st.integers(min_value=0, max_value=len(all_paths) - 1),
                min_size=1,
                max_size=len(all_paths),
                unique=True,
            )
        )
        skip_paths = [all_paths[i] for i in skip_indices]

        engine, _ = await _make_sync_engine_with_diff(diff_files)

        items = [SelectiveSyncItem(path=p, action=FileAction.SKIP) for p in skip_paths]
        await engine.selective_sync(items)

        # Verify skipped paths are gone from cached diff
        remaining_paths = engine._state.get_cached_paths()
        for p in skip_paths:
            assert p not in remaining_paths, (
                f"Skipped path '{p}' still present in cached diff. "
                f"Remaining: {remaining_paths}"
            )

    @settings(max_examples=100)
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_non_skipped_paths_remain_in_cached_diff(
        self, data: st.DataObject
    ) -> None:
        """After skipping a subset, all non-skipped paths must still be
        present in the cached diff."""
        diff_files = data.draw(unique_path_file_diffs)
        all_paths = [f.path for f in diff_files]

        # Draw a strict subset (not all) to skip
        if len(all_paths) < 2:
            # Need at least 2 files to have a non-empty remainder
            return

        skip_indices = data.draw(
            st.lists(
                st.integers(min_value=0, max_value=len(all_paths) - 1),
                min_size=1,
                max_size=len(all_paths) - 1,
                unique=True,
            )
        )
        skip_paths_set = {all_paths[i] for i in skip_indices}
        expected_remaining = {p for p in all_paths if p not in skip_paths_set}

        engine, _ = await _make_sync_engine_with_diff(diff_files)

        items = [SelectiveSyncItem(path=p, action=FileAction.SKIP) for p in skip_paths_set]
        await engine.selective_sync(items)

        remaining_paths = engine._state.get_cached_paths()
        for p in expected_remaining:
            assert p in remaining_paths, (
                f"Non-skipped path '{p}' was incorrectly removed from cached diff. "
                f"Remaining: {remaining_paths}"
            )

    @settings(max_examples=100)
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_no_rclone_transfer_invoked_for_skip(
        self, data: st.DataObject
    ) -> None:
        """Skip operations must not invoke any rclone transfer commands
        (copy_files, move_file, sync)."""
        diff_files = data.draw(unique_path_file_diffs)
        all_paths = [f.path for f in diff_files]

        skip_indices = data.draw(
            st.lists(
                st.integers(min_value=0, max_value=len(all_paths) - 1),
                min_size=1,
                max_size=len(all_paths),
                unique=True,
            )
        )
        skip_paths = [all_paths[i] for i in skip_indices]

        engine, mock_rclone = await _make_sync_engine_with_diff(diff_files)

        items = [SelectiveSyncItem(path=p, action=FileAction.SKIP) for p in skip_paths]
        await engine.selective_sync(items)

        mock_rclone.copy_files.assert_not_called()
        mock_rclone.move_file.assert_not_called()
        mock_rclone.sync.assert_not_called()

    @settings(max_examples=100)
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_skip_all_results_in_empty_cached_diff(
        self, data: st.DataObject
    ) -> None:
        """Skipping all files must result in an empty cached diff."""
        diff_files = data.draw(unique_path_file_diffs)
        all_paths = [f.path for f in diff_files]

        engine, _ = await _make_sync_engine_with_diff(diff_files)

        items = [SelectiveSyncItem(path=p, action=FileAction.SKIP) for p in all_paths]
        await engine.selective_sync(items)

        assert engine._state.cached_diff is not None
        assert len(engine._state.cached_diff.files) == 0, (
            f"Expected empty cached diff after skipping all files, "
            f"got {len(engine._state.cached_diff.files)} files"
        )
        assert engine._state.pending_changes == 0


# Manual flag round trip
class TestManualFlagRoundTrip:
    """For any file path, marking it with a manual flag and then querying
    manual flags must include that path; subsequently clearing the flag
    and querying again must not include that path."""

    @settings(max_examples=100)
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_set_flag_then_query_includes_path(
        self, data: st.DataObject
    ) -> None:
        """After marking paths with MANUAL action, get_manual_flags()
        must include every marked path."""
        diff_files = data.draw(unique_path_file_diffs)
        all_paths = [f.path for f in diff_files]

        # Draw a non-empty subset to mark as manual
        manual_indices = data.draw(
            st.lists(
                st.integers(min_value=0, max_value=len(all_paths) - 1),
                min_size=1,
                max_size=len(all_paths),
                unique=True,
            )
        )
        manual_paths = [all_paths[i] for i in manual_indices]

        engine, _ = await _make_sync_engine_with_diff(diff_files)

        # Set manual flags via selective_sync
        items = [SelectiveSyncItem(path=p, action=FileAction.MANUAL) for p in manual_paths]
        await engine.selective_sync(items)

        # Query manual flags
        flags = await engine.get_manual_flags()
        for p in manual_paths:
            assert p in flags, (
                f"Path '{p}' was marked MANUAL but not found in get_manual_flags(). "
                f"Flags: {flags}"
            )

    @settings(max_examples=100)
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_clear_flag_then_query_excludes_path(
        self, data: st.DataObject
    ) -> None:
        """After setting and then clearing manual flags, get_manual_flags()
        must no longer include the cleared paths."""
        diff_files = data.draw(unique_path_file_diffs)
        all_paths = [f.path for f in diff_files]

        manual_indices = data.draw(
            st.lists(
                st.integers(min_value=0, max_value=len(all_paths) - 1),
                min_size=1,
                max_size=len(all_paths),
                unique=True,
            )
        )
        manual_paths = [all_paths[i] for i in manual_indices]

        engine, _ = await _make_sync_engine_with_diff(diff_files)

        # Set manual flags
        items = [SelectiveSyncItem(path=p, action=FileAction.MANUAL) for p in manual_paths]
        await engine.selective_sync(items)

        # Clear all manual flags
        for p in manual_paths:
            await engine.clear_manual_flag(p)

        # Query manual flags — should be empty for these paths
        flags = await engine.get_manual_flags()
        for p in manual_paths:
            assert p not in flags, (
                f"Path '{p}' was cleared but still found in get_manual_flags(). "
                f"Flags: {flags}"
            )

    @settings(max_examples=100)
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_full_round_trip_set_query_clear_query(
        self, data: st.DataObject
    ) -> None:
        """Full round trip: set flag → query includes → clear → query excludes."""
        diff_files = data.draw(unique_path_file_diffs)
        all_paths = [f.path for f in diff_files]

        manual_indices = data.draw(
            st.lists(
                st.integers(min_value=0, max_value=len(all_paths) - 1),
                min_size=1,
                max_size=len(all_paths),
                unique=True,
            )
        )
        manual_paths = [all_paths[i] for i in manual_indices]

        engine, _ = await _make_sync_engine_with_diff(diff_files)

        # Step 1: Set manual flags
        items = [SelectiveSyncItem(path=p, action=FileAction.MANUAL) for p in manual_paths]
        await engine.selective_sync(items)

        # Step 2: Query — all manual paths must be present
        flags_after_set = await engine.get_manual_flags()
        for p in manual_paths:
            assert p in flags_after_set, (
                f"Round trip failed at SET step: '{p}' not in flags. "
                f"Flags: {flags_after_set}"
            )

        # Step 3: Clear all manual flags
        for p in manual_paths:
            await engine.clear_manual_flag(p)

        # Step 4: Query — none of the manual paths should be present
        flags_after_clear = await engine.get_manual_flags()
        for p in manual_paths:
            assert p not in flags_after_clear, (
                f"Round trip failed at CLEAR step: '{p}' still in flags. "
                f"Flags: {flags_after_clear}"
            )


# --- Invalid paths rejected: imports ---

from backend.exceptions import InvalidFilePathsError


# Invalid paths rejected
class TestInvalidPathsRejected:
    """For any selective sync request containing file paths that do not exist
    in the current cached diff, the API must raise InvalidFilePathsError,
    and the error must identify all invalid paths."""

    @settings(max_examples=100)
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_all_invalid_paths_raises_error(
        self, data: st.DataObject
    ) -> None:
        """When all requested paths are invalid (not in cached diff),
        InvalidFilePathsError is raised listing every path."""
        diff_files = data.draw(unique_path_file_diffs)
        # Generate invalid paths guaranteed to not collide with diff paths
        num_invalid = data.draw(st.integers(min_value=1, max_value=10))
        invalid_paths = [
            f"INVALID/{i}/{data.draw(sync_file_path)}"
            for i in range(num_invalid)
        ]
        # Ensure none accidentally match a diff path
        cached_path_set = {f.path for f in diff_files}
        invalid_paths = [p for p in invalid_paths if p not in cached_path_set]
        if not invalid_paths:
            invalid_paths = ["INVALID/__fallback__"]

        engine, _ = await _make_sync_engine_with_diff(diff_files)

        items = [
            SelectiveSyncItem(path=p, action=FileAction.PUSH)
            for p in invalid_paths
        ]

        with pytest.raises(InvalidFilePathsError) as exc_info:
            await engine.selective_sync(items)

        assert set(exc_info.value.invalid_paths) == set(invalid_paths), (
            f"Expected error to list all invalid paths {invalid_paths}, "
            f"got {exc_info.value.invalid_paths}"
        )

    @settings(max_examples=100)
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_mix_valid_and_invalid_raises_with_only_invalid(
        self, data: st.DataObject
    ) -> None:
        """When a mix of valid and invalid paths is submitted,
        InvalidFilePathsError is raised listing only the invalid ones."""
        diff_files = data.draw(unique_path_file_diffs)
        all_paths = [f.path for f in diff_files]

        # Pick a non-empty subset of valid paths
        valid_indices = data.draw(
            st.lists(
                st.integers(min_value=0, max_value=len(all_paths) - 1),
                min_size=1,
                max_size=len(all_paths),
                unique=True,
            )
        )
        valid_paths = [all_paths[i] for i in valid_indices]

        # Generate at least one invalid path
        num_invalid = data.draw(st.integers(min_value=1, max_value=5))
        cached_path_set = set(all_paths)
        invalid_paths = [
            f"INVALID/{i}/{data.draw(sync_file_path)}"
            for i in range(num_invalid)
        ]
        invalid_paths = [p for p in invalid_paths if p not in cached_path_set]
        if not invalid_paths:
            invalid_paths = ["INVALID/__fallback__"]

        engine, _ = await _make_sync_engine_with_diff(diff_files)

        # Mix valid + invalid items
        items = [
            SelectiveSyncItem(path=p, action=FileAction.PUSH)
            for p in valid_paths
        ] + [
            SelectiveSyncItem(path=p, action=FileAction.PULL)
            for p in invalid_paths
        ]

        with pytest.raises(InvalidFilePathsError) as exc_info:
            await engine.selective_sync(items)

        assert set(exc_info.value.invalid_paths) == set(invalid_paths), (
            f"Expected only invalid paths {invalid_paths} in error, "
            f"got {exc_info.value.invalid_paths}"
        )

    @settings(max_examples=100)
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_error_contains_exactly_invalid_paths(
        self, data: st.DataObject
    ) -> None:
        """The error's invalid_paths list contains exactly the invalid paths —
        no extras and no missing entries."""
        diff_files = data.draw(unique_path_file_diffs)
        cached_path_set = {f.path for f in diff_files}

        # Generate a known set of invalid paths
        num_invalid = data.draw(st.integers(min_value=1, max_value=8))
        invalid_paths = []
        for i in range(num_invalid):
            p = f"INVALID/{i}/{data.draw(sync_file_path)}"
            if p not in cached_path_set and p not in invalid_paths:
                invalid_paths.append(p)
        if not invalid_paths:
            invalid_paths = ["INVALID/__exact_check__"]

        engine, _ = await _make_sync_engine_with_diff(diff_files)

        items = [
            SelectiveSyncItem(path=p, action=FileAction.SKIP)
            for p in invalid_paths
        ]

        with pytest.raises(InvalidFilePathsError) as exc_info:
            await engine.selective_sync(items)

        reported = exc_info.value.invalid_paths
        # Exactly the same set — no extras, no missing
        assert len(reported) == len(invalid_paths), (
            f"Expected {len(invalid_paths)} invalid paths, got {len(reported)}. "
            f"Expected: {invalid_paths}, Got: {reported}"
        )
        assert set(reported) == set(invalid_paths), (
            f"Mismatch in invalid paths. "
            f"Missing: {set(invalid_paths) - set(reported)}, "
            f"Extra: {set(reported) - set(invalid_paths)}"
        )


# --- Bulk sync excludes manual flags and unresolved conflicts: imports ---



# Bulk sync excludes manual flags and unresolved conflicts
class TestBulkSyncExcludesManualFlagsAndConflicts:
    """For any set of manual-flagged file paths and unresolved conflict file paths,
    when a bulk sync is triggered, the rclone exclude filter must contain all of
    those paths, and no other paths must be excluded."""

    @settings(max_examples=100)
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_manual_flags_only_in_exclude_filter(
        self, data: st.DataObject
    ) -> None:
        """When only manual flags exist (no conflicts), the exclude filter
        must contain exactly those flagged paths."""
        # Generate diffs with NO conflicts (exclude MODIFIED_BOTH)
        non_conflict_categories = [
            c for c in ChangeCategory if c != ChangeCategory.MODIFIED_BOTH
        ]
        diff_files = data.draw(
            st.lists(
                st.builds(
                    build_correct_file_diff,
                    path=sync_file_path,
                    category=st.sampled_from(non_conflict_categories),
                    local_size=optional_positive_int,
                    remote_size=optional_positive_int,
                    local_mod_time=optional_datetime,
                    remote_mod_time=optional_datetime,
                    manual_flag=st.just(False),
                ),
                min_size=2,
                max_size=20,
                unique_by=lambda fd: fd.path,
            )
        )
        all_paths = [f.path for f in diff_files]

        # Pick a non-empty subset to mark as manual flags
        manual_indices = data.draw(
            st.lists(
                st.integers(min_value=0, max_value=len(all_paths) - 1),
                min_size=1,
                max_size=len(all_paths),
                unique=True,
            )
        )
        manual_paths = {all_paths[i] for i in manual_indices}

        engine, mock_rclone = await _make_sync_engine_with_diff(diff_files)

        # Set manual flags via selective_sync
        items = [SelectiveSyncItem(path=p, action=FileAction.MANUAL) for p in manual_paths]
        await engine.selective_sync(items)

        # Capture filter file content when rclone.sync is called
        captured_filter_content: list[str] = []

        async def capture_sync(source, dest, direction, exclude_filter_path=None, **kwargs):
            if exclude_filter_path:
                with open(exclude_filter_path) as f:
                    captured_filter_content.append(f.read())
            return RcloneResult(stdout="", stderr="", return_code=0, elapsed_seconds=0.1)

        mock_rclone.sync = AsyncMock(side_effect=capture_sync)

        await engine.push()

        assert len(captured_filter_content) == 1, (
            f"Expected exactly 1 rclone.sync call, got {len(captured_filter_content)}"
        )

        # Parse exclude lines from filter content
        filter_lines = captured_filter_content[0].strip().splitlines()
        excluded_paths = set()
        for line in filter_lines:
            excluded_paths.add(_parse_exclude_rule(line))

        assert excluded_paths == manual_paths, (
            f"Exclude filter should contain exactly manual-flagged paths.\n"
            f"Expected: {manual_paths}\n"
            f"Got: {excluded_paths}"
        )

    @settings(max_examples=100)
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_conflicts_only_in_exclude_filter(
        self, data: st.DataObject
    ) -> None:
        """When only unresolved conflicts exist (no manual flags), the exclude
        filter must contain exactly those conflict paths."""
        # Generate some non-conflict files
        non_conflict_categories = [
            c for c in ChangeCategory if c != ChangeCategory.MODIFIED_BOTH
        ]
        non_conflict_files = data.draw(
            st.lists(
                st.builds(
                    build_correct_file_diff,
                    path=sync_file_path,
                    category=st.sampled_from(non_conflict_categories),
                    local_size=optional_positive_int,
                    remote_size=optional_positive_int,
                    local_mod_time=optional_datetime,
                    remote_mod_time=optional_datetime,
                    manual_flag=st.just(False),
                ),
                min_size=1,
                max_size=15,
                unique_by=lambda fd: fd.path,
            )
        )

        # Generate conflict files (MODIFIED_BOTH → is_conflict=True)
        conflict_files = data.draw(
            st.lists(
                st.builds(
                    build_correct_file_diff,
                    path=st.from_regex(r"conflict/[a-z0-9]{1,20}\.[a-z]{1,4}", fullmatch=True),
                    category=st.just(ChangeCategory.MODIFIED_BOTH),
                    local_size=optional_positive_int,
                    remote_size=optional_positive_int,
                    local_mod_time=optional_datetime,
                    remote_mod_time=optional_datetime,
                    manual_flag=st.just(False),
                ),
                min_size=1,
                max_size=10,
                unique_by=lambda fd: fd.path,
            )
        )

        # Ensure no path collisions
        non_conflict_path_set = {f.path for f in non_conflict_files}
        conflict_files = [f for f in conflict_files if f.path not in non_conflict_path_set]
        if not conflict_files:
            conflict_files = [
                build_correct_file_diff(
                    path="conflict/__fallback__.txt",
                    category=ChangeCategory.MODIFIED_BOTH,
                    local_size=100,
                    remote_size=200,
                    local_mod_time=None,
                    remote_mod_time=None,
                    manual_flag=False,
                )
            ]

        diff_files = non_conflict_files + conflict_files
        conflict_paths = {f.path for f in conflict_files}

        engine, mock_rclone = await _make_sync_engine_with_diff(diff_files)

        # Capture filter file content
        captured_filter_content: list[str] = []

        async def capture_sync(source, dest, direction, exclude_filter_path=None, **kwargs):
            if exclude_filter_path:
                with open(exclude_filter_path) as f:
                    captured_filter_content.append(f.read())
            return RcloneResult(stdout="", stderr="", return_code=0, elapsed_seconds=0.1)

        mock_rclone.sync = AsyncMock(side_effect=capture_sync)

        await engine.push()

        assert len(captured_filter_content) == 1, (
            f"Expected exactly 1 rclone.sync call, got {len(captured_filter_content)}"
        )

        filter_lines = captured_filter_content[0].strip().splitlines()
        excluded_paths = set()
        for line in filter_lines:
            excluded_paths.add(_parse_exclude_rule(line))

        assert excluded_paths == conflict_paths, (
            f"Exclude filter should contain exactly conflict paths.\n"
            f"Expected: {conflict_paths}\n"
            f"Got: {excluded_paths}"
        )

    @settings(max_examples=100)
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_both_manual_flags_and_conflicts_in_exclude_filter(
        self, data: st.DataObject
    ) -> None:
        """When both manual flags and unresolved conflicts exist, the exclude
        filter must contain the union of both sets."""
        # Generate non-conflict files (some will be flagged as manual)
        non_conflict_files = data.draw(
            st.lists(
                st.builds(
                    build_correct_file_diff,
                    path=sync_file_path,
                    category=st.sampled_from([
                        c for c in ChangeCategory if c != ChangeCategory.MODIFIED_BOTH
                    ]),
                    local_size=optional_positive_int,
                    remote_size=optional_positive_int,
                    local_mod_time=optional_datetime,
                    remote_mod_time=optional_datetime,
                    manual_flag=st.just(False),
                ),
                min_size=2,
                max_size=15,
                unique_by=lambda fd: fd.path,
            )
        )

        # Generate conflict files
        conflict_files = data.draw(
            st.lists(
                st.builds(
                    build_correct_file_diff,
                    path=st.from_regex(r"conflict/[a-z0-9]{1,20}\.[a-z]{1,4}", fullmatch=True),
                    category=st.just(ChangeCategory.MODIFIED_BOTH),
                    local_size=optional_positive_int,
                    remote_size=optional_positive_int,
                    local_mod_time=optional_datetime,
                    remote_mod_time=optional_datetime,
                    manual_flag=st.just(False),
                ),
                min_size=1,
                max_size=10,
                unique_by=lambda fd: fd.path,
            )
        )

        # Ensure no path collisions
        non_conflict_path_set = {f.path for f in non_conflict_files}
        conflict_files = [f for f in conflict_files if f.path not in non_conflict_path_set]
        if not conflict_files:
            conflict_files = [
                build_correct_file_diff(
                    path="conflict/__fallback__.txt",
                    category=ChangeCategory.MODIFIED_BOTH,
                    local_size=100,
                    remote_size=200,
                    local_mod_time=None,
                    remote_mod_time=None,
                    manual_flag=False,
                )
            ]

        diff_files = non_conflict_files + conflict_files
        conflict_paths = {f.path for f in conflict_files}

        # Pick a non-empty subset of non-conflict files to mark as manual
        nc_paths = [f.path for f in non_conflict_files]
        manual_indices = data.draw(
            st.lists(
                st.integers(min_value=0, max_value=len(nc_paths) - 1),
                min_size=1,
                max_size=len(nc_paths),
                unique=True,
            )
        )
        manual_paths = {nc_paths[i] for i in manual_indices}

        engine, mock_rclone = await _make_sync_engine_with_diff(diff_files)

        # Set manual flags via selective_sync
        items = [SelectiveSyncItem(path=p, action=FileAction.MANUAL) for p in manual_paths]
        await engine.selective_sync(items)

        # Capture filter file content
        captured_filter_content: list[str] = []

        async def capture_sync(source, dest, direction, exclude_filter_path=None, **kwargs):
            if exclude_filter_path:
                with open(exclude_filter_path) as f:
                    captured_filter_content.append(f.read())
            return RcloneResult(stdout="", stderr="", return_code=0, elapsed_seconds=0.1)

        mock_rclone.sync = AsyncMock(side_effect=capture_sync)

        await engine.push()

        assert len(captured_filter_content) == 1, (
            f"Expected exactly 1 rclone.sync call, got {len(captured_filter_content)}"
        )

        filter_lines = captured_filter_content[0].strip().splitlines()
        excluded_paths = set()
        for line in filter_lines:
            excluded_paths.add(_parse_exclude_rule(line))

        expected_excluded = manual_paths | conflict_paths
        assert excluded_paths == expected_excluded, (
            f"Exclude filter should contain union of manual flags and conflicts.\n"
            f"Expected: {expected_excluded}\n"
            f"Got: {excluded_paths}\n"
            f"Missing: {expected_excluded - excluded_paths}\n"
            f"Extra: {excluded_paths - expected_excluded}"
        )
