"""Core sync engine service for OmniSync.

Orchestrates file watching, scheduling, and sync operations via rclone.

SyncEngine is split by concern into the modules of this package:

  engine.py     SyncEngine itself: start, stop and the file watcher
  base.py       the shared instance state (EngineBase)
  runs.py       the sync lock, launch(), stopping a run, the sync window
  pause.py      the interval pause state machine (engine, user, hold)
  safety.py     startup check, sync markers, empty-side checks, delete limit
  diff.py       check, preview, the cached enhanced diff, manual flags
  selective.py  per-file actions on the cached diff
  conflicts.py  conflict records and resolving them
  mirror.py     push and pull (rclone sync)
  two_way.py    two-way sync and resync (rclone bisync)
  trash.py      trash retention
  reporting.py  job records and notifications
  common.py     constants and helpers the modules share

The names other modules use are importable from the package itself.
"""

from backend.services.sync_engine.common import (
    ENGINE_STOPPED,
    RATE_LIMIT_BASE_DELAY,
    STOPPED_BY_USER,
    TRASH_STAMP_FORMAT,
    calculate_backoff_delay,
    filter_escape,
    remote_join,
)
from backend.services.sync_engine.engine import SyncEngine
from backend.services.sync_engine.pause import USER_PAUSE_REASON, store_pause_reason, store_user_pause
from backend.services.sync_engine.runs import SYNC_CRASHED, SYNC_START_WAIT, SyncLaunch
from backend.services.sync_engine.lock import SyncLock, is_busy
from backend.services.sync_engine.safety import (
    DEFAULT_MAX_DELETE,
    STARTUP_CHECK_TIMEOUT,
    effective_max_delete,
    transfer_tuning_args,
)
from backend.services.sync_engine.trash import TRASH_PRUNE_INTERVAL, TRASH_RETENTION_DAYS, expired_trash_folders
from backend.services.sync_engine.two_way import (
    BISYNC_DIR,
    BISYNC_FILTERS_FILE,
    BISYNC_PARTIAL_FILTER,
    BISYNC_STATE_FILE,
    FILTER_FLAGS,
    bisync_workdir,
    filter_flags,
    reset_bisync_state,
)

__all__ = [
    "BISYNC_DIR",
    "BISYNC_FILTERS_FILE",
    "BISYNC_PARTIAL_FILTER",
    "BISYNC_STATE_FILE",
    "DEFAULT_MAX_DELETE",
    "ENGINE_STOPPED",
    "FILTER_FLAGS",
    "RATE_LIMIT_BASE_DELAY",
    "STARTUP_CHECK_TIMEOUT",
    "STOPPED_BY_USER",
    "SYNC_CRASHED",
    "SYNC_START_WAIT",
    "SyncEngine",
    "SyncLaunch",
    "SyncLock",
    "TRASH_PRUNE_INTERVAL",
    "TRASH_RETENTION_DAYS",
    "TRASH_STAMP_FORMAT",
    "USER_PAUSE_REASON",
    "bisync_workdir",
    "calculate_backoff_delay",
    "effective_max_delete",
    "expired_trash_folders",
    "filter_escape",
    "filter_flags",
    "is_busy",
    "remote_join",
    "reset_bisync_state",
    "store_pause_reason",
    "store_user_pause",
    "transfer_tuning_args",
]
