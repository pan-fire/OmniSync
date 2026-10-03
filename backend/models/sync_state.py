"""In-memory sync state tracking for OmniSync."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from backend.api.schemas import DiffResponse, ProgressFileResponse, SyncProgress, SyncState, SyncStatusResponse
from backend.services.sync_window import next_window_start, window_open

if TYPE_CHECKING:
    from backend.services.rclone import ChangeRecorder

_RUNNING_STATES = (SyncState.PUSHING, SyncState.PULLING, SyncState.SYNCING)


@dataclass
class SyncStateManager:
    """Tracks the current sync engine state in memory. No persistence needed."""

    state: SyncState = SyncState.IDLE
    current_job_id: int | None = None
    last_sync: datetime | None = None
    files_processed: int = 0
    errors: int = 0
    pending_changes: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cached_diff: DiffResponse | None = None  # In-memory diff cache
    intervals_paused: bool = False
    paused_at: datetime | None = None
    last_error: str | None = None  # why the last sync failed or was refused
    resync_required: bool = False  # two-way: paused until a confirmed resync
    # The user paused automatic syncing ("Pause" / "Pause all"); kept apart
    # from intervals_paused (paused by the engine itself), see SyncEngine.
    user_paused: bool = False
    # The recorder of the running push, pull or two-way sync: its latest
    # rclone stats are the live progress.
    recorder: ChangeRecorder | None = None
    # The profile's sync window (see backend.services.sync_window), and
    # whether an automatic sync waits for it to open.
    sync_window: dict | None = None
    waiting_for_window: bool = False

    @property
    def auto_paused(self) -> bool:
        """Automatic syncing is held, by the engine or by the user."""
        return self.intervals_paused or self.user_paused

    def set_syncing(self, direction: SyncState, job_id: int) -> None:
        """Transition to a syncing state (PUSHING or PULLING)."""
        self.state = direction
        self.current_job_id = job_id
        self.recorder = None

    def set_idle(self, files_processed: int = 0) -> None:
        """Transition back to idle after a successful sync."""
        self.state = SyncState.IDLE
        self.current_job_id = None
        self.recorder = None
        self.last_sync = datetime.now(timezone.utc)
        self.files_processed += files_processed
        self.pending_changes = 0  # sync completed, clear pending
        self.cached_diff = None  # clear diff cache after bulk sync
        self.last_error = None

    def set_error(self, message: str | None = None) -> None:
        """Transition to error state (retries exhausted, or a sync refused)."""
        self.state = SyncState.ERROR
        self.current_job_id = None
        self.recorder = None
        self.errors += 1
        self.last_error = message

    def set_stopped(self, message: str) -> None:
        """The running operation was stopped: back to idle, with the reason in last_error."""
        self.state = SyncState.IDLE
        self.current_job_id = None
        self.recorder = None
        self.last_error = message

    def set_paused(self) -> None:
        """Enter paused state. Idempotent — safe to call if already paused."""
        if not self.intervals_paused:
            self.intervals_paused = True
            if self.paused_at is None:
                self.paused_at = datetime.now(timezone.utc)

    def set_resumed(self) -> None:
        """Exit paused state."""
        self.intervals_paused = False
        if not self.user_paused:
            self.paused_at = None

    def set_user_paused(self, paused: bool) -> None:
        """Pause (or resume) automatic syncing for the user; idempotent."""
        self.user_paused = paused
        if paused and self.paused_at is None:
            self.paused_at = datetime.now(timezone.utc)
        elif not paused and not self.intervals_paused:
            self.paused_at = None

    def progress(self) -> SyncProgress | None:
        """The running sync's latest rclone stats, if any."""
        recorder = self.recorder
        stats = recorder.progress if recorder is not None and self.state in _RUNNING_STATES else None
        if stats is None:
            return None
        return SyncProgress(
            bytes=stats.bytes, total_bytes=stats.total_bytes, speed=stats.speed,
            eta_seconds=stats.eta_seconds, files_done=stats.files_done, files_total=stats.files_total,
            checks=stats.checks, total_checks=stats.total_checks,
            current_files=[
                ProgressFileResponse(name=f.name, size=f.size, bytes=f.bytes, percentage=f.percentage)
                for f in stats.current
            ],
        )

    def to_status_response(self) -> SyncStatusResponse:
        """Convert current state to API response model."""
        outside = not window_open(self.sync_window)
        return SyncStatusResponse(
            state=self.state,
            last_sync=self.last_sync,
            current_job_id=self.current_job_id,
            files_processed=self.files_processed,
            errors=self.errors,
            pending_changes=self.pending_changes,
            intervals_paused=self.auto_paused,
            user_paused=self.user_paused,
            paused_at=self.paused_at,
            last_error=self.last_error,
            resync_required=self.resync_required,
            progress=self.progress(),
            outside_sync_window=outside,
            next_window_start=next_window_start(self.sync_window) if outside else None,
            waiting_for_window=self.waiting_for_window and outside,
        )

    def cache_diff(self, diff: DiffResponse) -> None:
        """Store the latest diff result for validation by selective sync."""
        self.cached_diff = diff

    def get_cached_paths(self) -> set[str]:
        """Return set of file paths from the cached diff."""
        if self.cached_diff is None:
            return set()
        return {f.path for f in self.cached_diff.files}

    def clear_diff_cache(self) -> None:
        """Clear cached diff (called after bulk sync completes)."""
        self.cached_diff = None
