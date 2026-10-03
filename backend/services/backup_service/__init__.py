"""Backup scheduling, execution, retention, and restore service.

BackupService is split by concern into the modules of this package:

  service.py     BackupService itself, assembled from the mixins below
  base.py        the shared instance state, storage location, crypt_root
  scheduling.py  start/stop, interval jobs, catch-up runs, overdue checks
  jobs.py        job records and error codes, background starts, shutdown
  execution.py   run_backup(): refusals, snapshot names, path conflicts
  mirror.py      mirror backups and their manifests
  archive.py     archive backups: tar.gz writing, reading, extracting
  verify.py      verification right after a backup
  liveness.py    target reachability and legacy marker cleanup
  retention.py   deleting expired snapshots
  snapshots.py   listing a target's restore points
  restore.py     full restores and rebuilding mirror snapshots
  browse.py      browsing snapshots, restoring selected files, previews
  common.py      shared constants and types

The names other modules use are importable from the package itself.
"""

from backend.services.backup_service.base import crypt_root
from backend.services.backup_service.browse import (
    ARCHIVE_LIST_CACHE,
    MODTIME_TOLERANCE,
    PREVIEW_EXAMPLES,
    browse_snapshot,
    path_selector,
)
from backend.services.backup_service.common import BACKUP_FILTER, LATEST_SNAPSHOT, LOCK_TIMEOUT, RestoreRefused, SnapshotFile
from backend.services.backup_service.jobs import JOB_ERRORS, BackupRunning, public_job_error
from backend.services.backup_service.execution import CLOCK_WAIT_SECONDS, BackupRefused, backup_paths_overlap
from backend.services.backup_service.archive import PARTIAL_ARCHIVE_PREFIX
from backend.services.backup_service.liveness import LEGACY_MARKER_FILES, LEGACY_PRESERVED_DIRS
from backend.services.backup_service.mirror import MANIFEST_FORMAT, MANIFESTS_DIR
from backend.services.backup_service.restore import PRE_RESTORE_DIR
from backend.services.backup_service.scheduling import (
    CATCH_UP_DELAY,
    CATCH_UP_STAGGER,
    OVERDUE_CHECK_INTERVAL,
    OVERDUE_CHECK_JOB,
    OVERDUE_FACTOR,
    OVERDUE_FIRST_CHECK,
    backup_is_overdue,
    next_backup_time,
)
from backend.services.backup_service.service import BackupService
from backend.services.backup_service.snapshots import KIND_FULL, KIND_LEGACY
from backend.services.backup_service.verify import DECRYPT_TEST_BYTES, VERIFIED, VERIFY_FAILED

__all__ = [
    "ARCHIVE_LIST_CACHE",
    "JOB_ERRORS",
    "BACKUP_FILTER",
    "CATCH_UP_DELAY",
    "CATCH_UP_STAGGER",
    "CLOCK_WAIT_SECONDS",
    "DECRYPT_TEST_BYTES",
    "KIND_FULL",
    "KIND_LEGACY",
    "LATEST_SNAPSHOT",
    "LEGACY_MARKER_FILES",
    "LEGACY_PRESERVED_DIRS",
    "LOCK_TIMEOUT",
    "MANIFESTS_DIR",
    "MANIFEST_FORMAT",
    "MODTIME_TOLERANCE",
    "OVERDUE_CHECK_INTERVAL",
    "OVERDUE_CHECK_JOB",
    "OVERDUE_FACTOR",
    "OVERDUE_FIRST_CHECK",
    "PARTIAL_ARCHIVE_PREFIX",
    "PREVIEW_EXAMPLES",
    "PRE_RESTORE_DIR",
    "VERIFIED",
    "VERIFY_FAILED",
    "BackupRefused",
    "BackupRunning",
    "BackupService",
    "RestoreRefused",
    "SnapshotFile",
    "backup_is_overdue",
    "backup_paths_overlap",
    "browse_snapshot",
    "crypt_root",
    "next_backup_time",
    "path_selector",
    "public_job_error",
]
