"""Cross-profile sync status: GET /sync/status/aggregate summarises every running profile.

Everything that acts on one profile lives under /profiles/{slug}/.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from backend.api.schemas import (
    AggregateStatusResponse,
    PausedProfileSummary,
    ProfileSummary,
    SyncState,
)
from backend.services.sync_engine_manager import SyncEngineManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync")

_manager: SyncEngineManager | None = None


def set_manager(manager: SyncEngineManager | None) -> None:
    """Set the engine manager reference (called by main.py)."""
    global _manager
    _manager = manager


_STATE_PRIORITY = {
    SyncState.IDLE: 1,
    SyncState.PULLING: 2,
    SyncState.PUSHING: 2,
    SyncState.SYNCING: 2,
    SyncState.ERROR: 3,
}


@router.get("/status/aggregate", response_model=AggregateStatusResponse)
async def get_aggregate_status() -> AggregateStatusResponse:
    """Aggregated status across all running sync engines."""
    if _manager is None:
        return AggregateStatusResponse()

    worst = SyncState.IDLE
    total_pending = 0
    paused: list[PausedProfileSummary] = []
    summaries: list[ProfileSummary] = []

    for slug, engine in _manager.engines.items():
        st = engine._state
        profile_name = engine._profile.name

        if _STATE_PRIORITY.get(st.state, 0) > _STATE_PRIORITY.get(worst, 0):
            worst = st.state
        total_pending += st.pending_changes

        if st.auto_paused:
            paused.append(PausedProfileSummary(
                slug=slug, name=profile_name,
                pending_changes=st.pending_changes, paused_at=st.paused_at, user_paused=st.user_paused,
            ))

        summaries.append(ProfileSummary(
            slug=slug, name=profile_name, state=st.state,
            last_sync=st.last_sync, pending_changes=st.pending_changes,
            intervals_paused=st.auto_paused, last_error=st.last_error,
            resync_required=st.resync_required, user_paused=st.user_paused, progress=st.progress(),
        ))

    return AggregateStatusResponse(
        overall_state=worst,
        total_pending_changes=total_pending,
        paused_profiles=paused,
        profiles_summary=summaries,
    )
