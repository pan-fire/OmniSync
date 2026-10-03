"""Tests for SyncStateManager pause/resume state management."""

from __future__ import annotations

from datetime import datetime, timezone

from hypothesis import given, settings
from hypothesis import strategies as st

from backend.models.sync_state import SyncStateManager


class TestSetPaused:
    def test_sets_paused_flag(self) -> None:
        state = SyncStateManager()
        assert state.intervals_paused is False
        state.set_paused()
        assert state.intervals_paused is True

    def test_sets_paused_at_timestamp(self) -> None:
        state = SyncStateManager()
        before = datetime.now(timezone.utc)
        state.set_paused()
        after = datetime.now(timezone.utc)
        assert state.paused_at is not None
        assert before <= state.paused_at <= after

    def test_idempotent_does_not_change_paused_at(self) -> None:
        state = SyncStateManager()
        state.set_paused()
        first_ts = state.paused_at
        state.set_paused()
        assert state.paused_at is first_ts  # exact same object


class TestSetResumed:
    def test_clears_both_fields(self) -> None:
        state = SyncStateManager()
        state.set_paused()
        state.set_resumed()
        assert state.intervals_paused is False
        assert state.paused_at is None

    def test_idempotent_when_not_paused(self) -> None:
        state = SyncStateManager()
        state.set_resumed()
        assert state.intervals_paused is False
        assert state.paused_at is None


class TestToStatusResponse:
    def test_includes_pause_fields_default(self) -> None:
        state = SyncStateManager()
        resp = state.to_status_response()
        assert resp.intervals_paused is False
        assert resp.paused_at is None

    def test_includes_pause_fields_when_paused(self) -> None:
        state = SyncStateManager()
        state.set_paused()
        resp = state.to_status_response()
        assert resp.intervals_paused is True
        assert resp.paused_at is not None


class TestPauseResumeInvariant:
    """Property-based tests for pause/resume state consistency."""

    @given(st.lists(st.booleans(), min_size=1, max_size=50))
    @settings(max_examples=200)
    def test_invariant_paused_implies_paused_at(self, actions: list[bool]) -> None:
        """After any sequence of set_paused/set_resumed calls:
        intervals_paused == True ⟹ paused_at is not None
        intervals_paused == False ⟹ paused_at is None
        """
        state = SyncStateManager()
        for action in actions:
            if action:
                state.set_paused()
            else:
                state.set_resumed()

        if state.intervals_paused:
            assert state.paused_at is not None
        else:
            assert state.paused_at is None
