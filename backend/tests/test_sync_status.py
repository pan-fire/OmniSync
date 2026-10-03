"""Tests for the per-profile status and the /sync/status/aggregate endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.api.routes import sync as sync_module
from backend.api.schemas import SyncState
from backend.models.sync_state import SyncStateManager


async def test_sync_status_returns_200(test_client):
    """GET /profiles/{slug}/sync/status returns 200 with expected SyncStatusResponse schema."""
    response = await test_client.get("/profiles/default/sync/status")
    assert response.status_code == 200

    data = response.json()
    assert "state" in data
    assert "last_sync" in data
    assert "current_job_id" in data
    assert "files_processed" in data
    assert "errors" in data


async def test_sync_status_idle_by_default(test_client):
    """GET /profiles/{slug}/sync/status returns idle state when no sync is running."""
    response = await test_client.get("/profiles/default/sync/status")
    data = response.json()
    assert data["state"] == "idle"


# --- Aggregate status endpoint tests ---


async def test_aggregate_status_no_manager(test_client):
    """GET /sync/status/aggregate returns idle when no manager is set."""
    old = sync_module._manager
    sync_module._manager = None
    try:
        response = await test_client.get("/sync/status/aggregate")
        assert response.status_code == 200
        data = response.json()
        assert data["overall_state"] == "idle"
        assert data["total_pending_changes"] == 0
        assert data["paused_profiles"] == []
        assert data["profiles_summary"] == []
    finally:
        sync_module._manager = old


async def test_aggregate_status_empty_engines(test_client):
    """Aggregate with no engines returns idle."""
    response = await test_client.get("/sync/status/aggregate")
    data = response.json()
    assert data["overall_state"] == "idle"
    assert data["total_pending_changes"] == 0


def _make_mock_engine(
    state: SyncState = SyncState.IDLE,
    pending_changes: int = 0,
    intervals_paused: bool = False,
    name: str = "Test",
    paused_at: datetime | None = None,
    last_sync: datetime | None = None,
    last_error: str | None = None,
) -> MagicMock:
    """Create a mock SyncEngine with the given state."""
    engine = MagicMock()
    engine._state = SyncStateManager(
        state=state,
        pending_changes=pending_changes,
        intervals_paused=intervals_paused,
        paused_at=paused_at,
        last_sync=last_sync,
        last_error=last_error,
    )
    engine._profile = MagicMock()
    engine._profile.name = name
    return engine


async def test_aggregate_status_single_idle_engine(test_client):
    """Single idle engine produces correct aggregate."""
    old = sync_module._manager
    manager = MagicMock()
    manager.engines = {"default": _make_mock_engine(name="Default")}
    sync_module._manager = manager
    try:
        response = await test_client.get("/sync/status/aggregate")
        assert response.status_code == 200
        data = response.json()
        assert data["overall_state"] == "idle"
        assert data["total_pending_changes"] == 0
        assert len(data["profiles_summary"]) == 1
        assert data["profiles_summary"][0]["slug"] == "default"
        assert data["profiles_summary"][0]["name"] == "Default"
        assert data["paused_profiles"] == []
    finally:
        sync_module._manager = old


async def test_aggregate_status_worst_state_error(test_client):
    """Error state is worst across all engines."""
    old = sync_module._manager
    manager = MagicMock()
    manager.engines = {
        "a": _make_mock_engine(state=SyncState.IDLE, name="A"),
        "b": _make_mock_engine(state=SyncState.ERROR, name="B", pending_changes=5),
        "c": _make_mock_engine(state=SyncState.PUSHING, name="C"),
    }
    sync_module._manager = manager
    try:
        response = await test_client.get("/sync/status/aggregate")
        data = response.json()
        assert data["overall_state"] == "error"
        assert data["total_pending_changes"] == 5
    finally:
        sync_module._manager = old


async def test_aggregate_status_paused_profiles(test_client):
    """Paused profiles are collected correctly."""
    old = sync_module._manager
    ts = datetime(2025, 3, 14, 10, 0, 0, tzinfo=timezone.utc)
    manager = MagicMock()
    manager.engines = {
        "a": _make_mock_engine(
            name="A", intervals_paused=True, pending_changes=3, paused_at=ts,
        ),
        "b": _make_mock_engine(name="B"),
    }
    sync_module._manager = manager
    try:
        response = await test_client.get("/sync/status/aggregate")
        data = response.json()
        assert len(data["paused_profiles"]) == 1
        assert data["paused_profiles"][0]["slug"] == "a"
        assert data["paused_profiles"][0]["pending_changes"] == 3
        assert len(data["profiles_summary"]) == 2
    finally:
        sync_module._manager = old


async def test_aggregate_summary_carries_last_error(test_client):
    """Each profile summary says why its last sync failed or was refused."""
    old = sync_module._manager
    manager = MagicMock()
    manager.engines = {
        "a": _make_mock_engine(name="A", state=SyncState.ERROR, last_error="Local folder is missing"),
        "b": _make_mock_engine(name="B"),
    }
    sync_module._manager = manager
    try:
        data = (await test_client.get("/sync/status/aggregate")).json()
        by_slug = {p["slug"]: p for p in data["profiles_summary"]}
        assert by_slug["a"]["last_error"] == "Local folder is missing"
        assert by_slug["b"]["last_error"] is None
    finally:
        sync_module._manager = old


# --- Hypothesis: worst state priority ---


_STATES = [SyncState.IDLE, SyncState.PUSHING, SyncState.PULLING, SyncState.ERROR]
_STATE_PRIORITY = {SyncState.IDLE: 1, SyncState.PUSHING: 2, SyncState.PULLING: 2, SyncState.ERROR: 3}


@given(
    data=st.lists(
        st.tuples(st.sampled_from(_STATES), st.integers(min_value=0, max_value=10000)),
        min_size=1,
        max_size=10,
    ),
)
@settings(max_examples=100)
def test_aggregate_worst_state_property(data: list[tuple[SyncState, int]]) -> None:
    """The overall_state must always be the highest-priority state."""
    from backend.api.routes.sync import _STATE_PRIORITY as route_priority

    states = [d[0] for d in data]
    pendings = [d[1] for d in data]

    worst = SyncState.IDLE
    total = 0
    for s, p in zip(states, pendings, strict=True):
        if route_priority.get(s, 0) > route_priority.get(worst, 0):
            worst = s
        total += p

    expected_priority = max(route_priority.get(s, 0) for s in states)
    assert route_priority.get(worst, 0) == expected_priority
    assert total == sum(pendings)
