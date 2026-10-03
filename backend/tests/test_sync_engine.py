"""Unit and property tests for the sync engine service.

Feature: project-foundation, Property 4: Sync state consistency
Feature: project-foundation, Property 5: Debounce coalesces rapid events
Feature: project-foundation, Property 6: Exponential backoff delay calculation
Feature: diff-ux-feedback, _remove_resolved_from_diff correctness
"""

from __future__ import annotations

import asyncio
from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from hypothesis import given, settings
from hypothesis import strategies as st

from backend.api.schemas import (
    ChangeCategory,
    DiffResponse,
    DiffSummary,
    FileDiff,
    SyncDirection,
    SyncState,
)
from backend.models.profile_config import ProfileConfig
from backend.models.sync_state import SyncStateManager
from backend.services.rclone import SENTINEL_FILE
from backend.services.sync_engine import SyncEngine, calculate_backoff_delay


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

directions = st.sampled_from([SyncDirection.PUSH, SyncDirection.PULL])
job_ids = st.integers(min_value=1, max_value=100_000)


# ---------------------------------------------------------------------------
# Property 4: Sync state consistency
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(direction=directions, job_id=job_ids)
def test_sync_state_consistency(direction: SyncDirection, job_id: int) -> None:
    """Feature: project-foundation, Property 4: Sync state consistency

    For any sync operation (push or pull), the sync engine state should be
    "pushing" or "pulling" (matching the direction) while the operation is
    in progress, and should return to "idle" after the operation completes
    successfully.

    **Validates: Requirements 4.4, 4.5**
    """
    manager = SyncStateManager()

    # Initially idle
    assert manager.state == SyncState.IDLE

    # Determine expected syncing state
    expected_syncing = (
        SyncState.PUSHING if direction == SyncDirection.PUSH else SyncState.PULLING
    )

    # Set syncing
    manager.set_syncing(expected_syncing, job_id)
    assert manager.state == expected_syncing
    assert manager.current_job_id == job_id

    # Complete — back to idle
    manager.set_idle()
    assert manager.state == SyncState.IDLE
    assert manager.current_job_id is None
    assert manager.last_sync is not None


# ---------------------------------------------------------------------------
# Property 5: Debounce coalesces rapid events
# ---------------------------------------------------------------------------


class ManualTimers:
    """Stands in for threading.Timer (SyncEngine.timer_factory) on a manual clock.

    Timers fire only from advance(), in the calling thread, so the debounce
    is tested without sleeping and without races.
    """

    def __init__(self) -> None:
        self.now = Fraction(0)  # exact: no float drift at the window's edge
        self.timers: list[_ManualTimer] = []

    def __call__(self, delay: float, fn) -> _ManualTimer:
        assert delay == int(delay)  # debounce_seconds is whole seconds
        timer = _ManualTimer(self.now + delay, fn)
        self.timers.append(timer)
        return timer

    def advance(self, seconds: Fraction | int) -> None:
        self.now += seconds
        for timer in list(self.timers):
            if timer.started and not timer.cancelled and not timer.fired and timer.due <= self.now:
                timer.fired = True
                timer.fn()

    @property
    def pending(self) -> list[_ManualTimer]:
        return [t for t in self.timers if t.started and not t.cancelled and not t.fired]


class _ManualTimer:
    def __init__(self, due: float, fn) -> None:
        self.due, self.fn = due, fn
        self.started = self.cancelled = self.fired = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


def _watched_engine(debounce_seconds: int, sync_mode: str = "mirror") -> tuple[SyncEngine, ManualTimers, list[str]]:
    """An engine whose watcher runs on a manual clock; returns the syncs it starts."""
    profile = ProfileConfig(
        profile_id=1, slug="p", name="P", local_dir="/sync/p", remote_dir="r:p",
        debounce_seconds=debounce_seconds, sync_mode=sync_mode,
    )
    engine = SyncEngine(profile, AsyncMock(), MagicMock())
    timers = ManualTimers()
    engine.timer_factory = timers
    started: list[str] = []

    async def push(automatic: bool = False) -> int:
        started.append("push")
        return 1

    async def two_way_sync(automatic: bool = False) -> int:
        started.append("two_way")
        return 1

    engine.push = push  # type: ignore[method-assign]
    engine.two_way_sync = two_way_sync  # type: ignore[method-assign]
    return engine, timers, started


def _file_event(name: str = "a.txt") -> SimpleNamespace:
    return SimpleNamespace(src_path=f"/sync/p/{name}", event_type="modified")


async def _settle() -> None:
    """Let the loop run what the timer scheduled with run_coroutine_threadsafe."""
    for _ in range(5):
        await asyncio.sleep(0)


@settings(max_examples=100, deadline=None)
@given(
    gaps_ms=st.lists(st.integers(min_value=0, max_value=999), min_size=0, max_size=49),
    debounce_seconds=st.integers(min_value=1, max_value=60),
)
def test_debounce_coalesces_rapid_events(gaps_ms: list[int], debounce_seconds: int) -> None:
    """Feature: project-foundation, Property 5: Debounce coalesces rapid events

    For any burst of file system events, each arriving within the debounce
    window of the previous one, the engine starts exactly one sync, a full
    debounce window after the last event.

    **Validates: Requirements 4.2**
    """
    async def run() -> None:
        engine, timers, started = _watched_engine(debounce_seconds)
        engine._loop = asyncio.get_running_loop()

        engine._on_file_change(_file_event())
        for gap in gaps_ms:
            timers.advance(Fraction(gap * debounce_seconds, 1000))  # still inside the window
            engine._on_file_change(_file_event())
            await _settle()
        assert started == []
        assert len(timers.pending) == 1

        timers.advance(debounce_seconds - Fraction(1, 1000))
        await _settle()
        assert started == []  # not before the window after the last event

        timers.advance(Fraction(1, 1000))
        await _settle()
        assert started == ["push"]
        assert timers.pending == []

    asyncio.run(run())


async def test_debounce_starts_one_sync_per_separate_burst() -> None:
    engine, timers, started = _watched_engine(debounce_seconds=5)
    engine._loop = asyncio.get_running_loop()

    for _burst in range(3):
        for name in ("a.txt", "b.txt", "c.txt"):
            engine._on_file_change(_file_event(name))
            timers.advance(1)
        timers.advance(5)
        await _settle()

    assert started == ["push"] * 3
    # each fired 5 s after its burst's last event (at 2, 10 and 18 s)
    assert [t.due for t in timers.timers if t.fired] == [7, 15, 23]


async def test_debounced_sync_of_a_two_way_profile_is_a_two_way_sync() -> None:
    engine, timers, started = _watched_engine(debounce_seconds=2, sync_mode="two_way")
    engine._loop = asyncio.get_running_loop()

    engine._on_file_change(_file_event())
    timers.advance(2)
    await _settle()

    assert started == ["two_way"]


async def test_omnisync_own_files_do_not_arm_the_debounce() -> None:
    engine, timers, started = _watched_engine(debounce_seconds=1)
    engine._loop = asyncio.get_running_loop()

    for path in (".omnisync-test-123", ".omnisync-trash/x.txt", SENTINEL_FILE, "big.iso.partial"):
        engine._on_file_change(_file_event(path))
    timers.advance(10)
    await _settle()

    assert timers.timers == [] and started == []


def test_debounce_fires_without_a_loop_is_skipped() -> None:
    engine, timers, started = _watched_engine(debounce_seconds=1)
    engine._on_file_change(_file_event())  # never started: no loop to run the sync on
    timers.advance(1)
    assert started == [] and timers.pending == []


# ---------------------------------------------------------------------------
# Property 6: Exponential backoff delay calculation
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    attempt=st.integers(min_value=1, max_value=20),
    base_delay=st.floats(min_value=0.1, max_value=60.0, allow_nan=False, allow_infinity=False),
)
def test_exponential_backoff_delay(attempt: int, base_delay: float) -> None:
    """Feature: project-foundation, Property 6: Exponential backoff delay calculation

    For any retry attempt number n (1 through max_retries), the backoff delay
    should equal base_delay * 2^(n-1), producing a strictly increasing sequence
    of delays.

    **Validates: Requirements 4.6**
    """
    delay = calculate_backoff_delay(attempt, base_delay)
    expected = base_delay * (2 ** (attempt - 1))

    assert delay == expected

    # Verify strictly increasing: delay at attempt n+1 > delay at attempt n
    if attempt > 1:
        prev_delay = calculate_backoff_delay(attempt - 1, base_delay)
        assert delay > prev_delay


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestSyncStateManager:
    """Unit tests for SyncStateManager."""

    def test_initial_state_is_idle(self) -> None:
        manager = SyncStateManager()
        assert manager.state == SyncState.IDLE
        assert manager.current_job_id is None
        assert manager.last_sync is None
        assert manager.files_processed == 0
        assert manager.errors == 0

    def test_set_error_increments_error_count(self) -> None:
        manager = SyncStateManager()
        manager.set_error()
        assert manager.state == SyncState.ERROR
        assert manager.errors == 1
        manager.set_error()
        assert manager.errors == 2

    def test_set_idle_accumulates_files_processed(self) -> None:
        manager = SyncStateManager()
        manager.set_syncing(SyncState.PUSHING, 1)
        manager.set_idle(files_processed=5)
        assert manager.files_processed == 5

        manager.set_syncing(SyncState.PULLING, 2)
        manager.set_idle(files_processed=3)
        assert manager.files_processed == 8

    def test_to_status_response(self) -> None:
        manager = SyncStateManager()
        resp = manager.to_status_response()
        assert resp.state == SyncState.IDLE
        assert resp.current_job_id is None
        assert resp.files_processed == 0
        assert resp.errors == 0


class TestCalculateBackoffDelay:
    """Unit tests for calculate_backoff_delay."""

    def test_first_attempt(self) -> None:
        assert calculate_backoff_delay(1, 1.0) == 1.0

    def test_second_attempt(self) -> None:
        assert calculate_backoff_delay(2, 1.0) == 2.0

    def test_third_attempt(self) -> None:
        assert calculate_backoff_delay(3, 1.0) == 4.0

    def test_custom_base_delay(self) -> None:
        assert calculate_backoff_delay(1, 5.0) == 5.0
        assert calculate_backoff_delay(2, 5.0) == 10.0
        assert calculate_backoff_delay(3, 5.0) == 20.0


# ---------------------------------------------------------------------------
# Helpers for _remove_resolved_from_diff tests
# ---------------------------------------------------------------------------


def _make_engine() -> SyncEngine:
    """Create a SyncEngine with mock dependencies for unit testing."""
    from backend.models.profile_config import ProfileConfig
    profile = ProfileConfig(
        profile_id=1,
        slug="test",
        name="Test",
        local_dir="/tmp/test-local",
        remote_dir="remote:backup",
    )
    rclone = AsyncMock()
    db_factory = AsyncMock()
    return SyncEngine(profile, rclone, db_factory)


def _make_file_diff(path: str, category: ChangeCategory, manual_flag: bool = False) -> FileDiff:
    return FileDiff(
        path=path,
        category=category,
        is_conflict=(category == ChangeCategory.MODIFIED_BOTH),
        manual_flag=manual_flag,
    )


def _build_diff(files: list[FileDiff]) -> DiffResponse:
    """Build a DiffResponse with correct summary from a list of FileDiff."""
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


# ---------------------------------------------------------------------------
# _remove_resolved_from_diff unit tests
# ---------------------------------------------------------------------------


class TestRemoveResolvedFromDiff:
    """Unit tests for SyncEngine._remove_resolved_from_diff()."""

    def test_removes_subset_from_diff(self) -> None:
        """Removing 2 paths from a 5-file diff leaves 3 with correct counts."""
        engine = _make_engine()
        files = [
            _make_file_diff("a.txt", ChangeCategory.LOCAL_ONLY),
            _make_file_diff("b.txt", ChangeCategory.REMOTE_ONLY),
            _make_file_diff("c.txt", ChangeCategory.MODIFIED_LOCAL),
            _make_file_diff("d.txt", ChangeCategory.MODIFIED_REMOTE),
            _make_file_diff("e.txt", ChangeCategory.MODIFIED_BOTH),
        ]
        engine._state.cached_diff = _build_diff(files)
        engine._state.pending_changes = 5

        engine._remove_resolved_from_diff(["a.txt", "c.txt"])

        assert engine._state.pending_changes == 3
        diff = engine._state.cached_diff
        assert diff is not None
        remaining_paths = {f.path for f in diff.files}
        assert remaining_paths == {"b.txt", "d.txt", "e.txt"}
        assert diff.summary.total == 3
        assert diff.summary.local_only == 0
        assert diff.summary.remote_only == 1
        assert diff.summary.modified_local == 0
        assert diff.summary.modified_remote == 1
        assert diff.summary.modified_both == 1

    def test_removing_all_files_sets_pending_zero(self) -> None:
        """Removing all files sets pending_changes = 0."""
        engine = _make_engine()
        files = [
            _make_file_diff("a.txt", ChangeCategory.LOCAL_ONLY),
            _make_file_diff("b.txt", ChangeCategory.REMOTE_ONLY),
        ]
        engine._state.cached_diff = _build_diff(files)
        engine._state.pending_changes = 2

        engine._remove_resolved_from_diff(["a.txt", "b.txt"])

        assert engine._state.pending_changes == 0
        assert engine._state.cached_diff is not None
        assert len(engine._state.cached_diff.files) == 0

    def test_removing_nonexistent_paths_is_noop(self) -> None:
        """Removing paths not in cache is a no-op (no crash)."""
        engine = _make_engine()
        files = [_make_file_diff("a.txt", ChangeCategory.LOCAL_ONLY)]
        engine._state.cached_diff = _build_diff(files)
        engine._state.pending_changes = 1

        engine._remove_resolved_from_diff(["nonexistent.txt"])

        assert engine._state.pending_changes == 1
        assert len(engine._state.cached_diff.files) == 1

    def test_noop_when_cached_diff_is_none(self) -> None:
        """Calling with cached_diff = None is a no-op."""
        engine = _make_engine()
        engine._state.cached_diff = None
        engine._state.pending_changes = 0

        engine._remove_resolved_from_diff(["a.txt"])

        assert engine._state.cached_diff is None
        assert engine._state.pending_changes == 0

    def test_manual_flag_count_preserved(self) -> None:
        """Manual flag count is recalculated correctly after removal."""
        engine = _make_engine()
        files = [
            _make_file_diff("a.txt", ChangeCategory.LOCAL_ONLY, manual_flag=True),
            _make_file_diff("b.txt", ChangeCategory.REMOTE_ONLY, manual_flag=True),
            _make_file_diff("c.txt", ChangeCategory.MODIFIED_LOCAL),
        ]
        engine._state.cached_diff = _build_diff(files)
        engine._state.pending_changes = 3

        engine._remove_resolved_from_diff(["a.txt"])

        # manually flagged files never count as pending (they never block resume)
        assert engine._state.pending_changes == 1
        diff = engine._state.cached_diff
        assert diff is not None
        assert diff.summary.manual == 1
        assert diff.summary.total == 2


# ---------------------------------------------------------------------------
# Hypothesis: _remove_resolved_from_diff invariant
# ---------------------------------------------------------------------------

# Strategy: generate a list of FileDiff with random categories
_categories = st.sampled_from(list(ChangeCategory))
_file_diff_st = st.builds(
    _make_file_diff,
    path=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="._-/"),
        min_size=1, max_size=30,
    ),
    category=_categories,
    manual_flag=st.booleans(),
)


@settings(max_examples=200)
@given(
    files=st.lists(_file_diff_st, min_size=0, max_size=20, unique_by=lambda f: f.path),
    remove_fraction=st.floats(min_value=0.0, max_value=1.0),
)
def test_remove_resolved_invariant(files: list[FileDiff], remove_fraction: float) -> None:
    """Hypothesis: after removing any subset, pending_changes counts the remaining unflagged files."""
    engine = _make_engine()
    engine._state.cached_diff = _build_diff(files)
    engine._state.pending_changes = len(files)

    # Pick a random subset to remove
    n_remove = int(len(files) * remove_fraction)
    paths_to_remove = [f.path for f in files[:n_remove]]

    engine._remove_resolved_from_diff(paths_to_remove)

    diff = engine._state.cached_diff
    assert diff is not None
    assert engine._state.pending_changes == sum(1 for f in diff.files if not f.manual_flag)
    assert diff.summary.total == len(diff.files)

    # Verify summary counts are consistent
    cat_sum = (
        diff.summary.local_only
        + diff.summary.remote_only
        + diff.summary.modified_local
        + diff.summary.modified_remote
        + diff.summary.modified_both
    )
    assert cat_sum == diff.summary.total
