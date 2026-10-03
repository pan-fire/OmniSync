"""BackupService: backup scheduling, execution, retention and restores, assembled from its mixins."""

from __future__ import annotations

from backend.services.backup_service.archive import ArchiveMixin
from backend.services.backup_service.browse import BrowseMixin
from backend.services.backup_service.execution import ExecutionMixin
from backend.services.backup_service.jobs import JobsMixin
from backend.services.backup_service.liveness import LivenessMixin
from backend.services.backup_service.mirror import MirrorMixin
from backend.services.backup_service.restore import RestoreMixin
from backend.services.backup_service.retention import RetentionMixin
from backend.services.backup_service.scheduling import SchedulingMixin
from backend.services.backup_service.snapshots import SnapshotsMixin
from backend.services.backup_service.verify import VerifyMixin


class BackupService(
    SchedulingMixin,
    JobsMixin,
    ExecutionMixin,
    MirrorMixin,
    ArchiveMixin,
    VerifyMixin,
    LivenessMixin,
    RetentionMixin,
    SnapshotsMixin,
    RestoreMixin,
    BrowseMixin,
):
    """Manages backup scheduling, execution, retention cleanup, and restores."""

    # Here rather than in archive.py: it names BackupService itself.
    @staticmethod
    def _check_archive_id(snapshot_id: str) -> None:
        name = snapshot_id.removeprefix("backup-").removesuffix(".tar.gz")
        if f"backup-{name}.tar.gz" != snapshot_id or BackupService._parse_timestamp(name) is None:
            raise ValueError(f"Invalid snapshot id '{snapshot_id}'")
