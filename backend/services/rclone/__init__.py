"""Rclone subprocess wrapper service for OmniSync.

RcloneService and its helpers are split by area into the modules of
this package:

  service.py   RcloneService itself, assembled from the mixins below
  process.py   running rclone: commands, flags, environment-defined
               remotes, streaming output, stopping it (RcloneBase)
  sync.py      sync, bisync and single-file transfers
  check.py     rclone check: the output parser, diffs and verification
  listing.py   listings, file heads, sizes and storage usage
  config.py    locked updates of rclone.conf and the remotes in it
  auth.py      OAuth token refresh, remote checks, rclone authorize
  probe.py     the round-trip sync test of a new profile
  recorder.py  the JSON log: live progress and what a transfer changed
  errors.py    failure classification and secret redaction
  common.py    shared constants and helpers

The names other modules use are importable from the package itself.
"""

from backend.services.rclone.auth import AUTH_URL_PATTERN, parse_auth_token, parse_auth_url
from backend.services.rclone.check import CheckOutput
from backend.services.rclone.common import (
    BISYNC_STOP_GRACE,
    CHECK_TIMEOUT,
    DEFAULT_TIMEOUT,
    MAX_RECORDED_CHANGES,
    PARTIAL_FILTER,
    PARTIAL_NAME,
    SENTINEL_FILE,
    STREAM_LINE_LIMIT,
    TRASH_DIR,
    TRASH_FILTER,
    generate_conflict_rename,
)
from backend.services.rclone.config import config_file_lock
from backend.services.rclone.errors import (
    AUTH_ERROR_PHRASES,
    RATE_LIMIT_PHRASES,
    _is_auth_error as _is_auth_error,
    _is_rate_limit_error as _is_rate_limit_error,
    classify_failure,
    readable_stderr,
    redact_secrets,
)
from backend.services.rclone.bisync_names import (
    bisync_names_fit,
    bisync_session_name,
    needs_short_names,
)
from backend.services.rclone.process import (
    BISYNC_REMOTE_PREFIX,
    ENV_REMOTE_PREFIX,
    RESERVED_NAME_MESSAGE,
    RESERVED_REMOTE_PREFIXES,
    STATS_ARGS,
    RcloneResult,
    define_env_remote,
    is_reserved_remote_name,
    json_log_args,
    process_env,
    without_flag,
)
from backend.services.rclone.recorder import (
    BISYNC_SIDES,
    MAX_PROGRESS_FILES,
    BisyncRecorder,
    ChangeRecorder,
    FileChangeRecord,
    ProgressFile,
    TransferProgress,
    parse_stats,
)
from backend.services.rclone.service import RcloneService

__all__ = [
    "AUTH_ERROR_PHRASES",
    "AUTH_URL_PATTERN",
    "BISYNC_REMOTE_PREFIX",
    "BISYNC_SIDES",
    "BISYNC_STOP_GRACE",
    "CHECK_TIMEOUT",
    "DEFAULT_TIMEOUT",
    "ENV_REMOTE_PREFIX",
    "MAX_PROGRESS_FILES",
    "MAX_RECORDED_CHANGES",
    "PARTIAL_FILTER",
    "PARTIAL_NAME",
    "RATE_LIMIT_PHRASES",
    "RESERVED_NAME_MESSAGE",
    "RESERVED_REMOTE_PREFIXES",
    "SENTINEL_FILE",
    "STATS_ARGS",
    "STREAM_LINE_LIMIT",
    "TRASH_DIR",
    "TRASH_FILTER",
    "BisyncRecorder",
    "ChangeRecorder",
    "CheckOutput",
    "FileChangeRecord",
    "ProgressFile",
    "RcloneResult",
    "RcloneService",
    "TransferProgress",
    "bisync_names_fit",
    "bisync_session_name",
    "classify_failure",
    "config_file_lock",
    "define_env_remote",
    "generate_conflict_rename",
    "is_reserved_remote_name",
    "json_log_args",
    "needs_short_names",
    "parse_auth_token",
    "parse_auth_url",
    "parse_stats",
    "process_env",
    "readable_stderr",
    "redact_secrets",
    "without_flag",
]
