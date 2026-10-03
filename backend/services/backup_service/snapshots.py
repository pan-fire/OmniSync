"""The restore points of a target: mirror snapshots (manifests, legacy versions) and archives."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from backend.api.schemas import BackupMode, SnapshotResponse
from backend.db.models import BackupJob, BackupJobStatus, BackupTarget
from backend.exceptions import RcloneError
from backend.services.sync_engine import remote_join
from backend.services.backup_service.common import LATEST_SNAPSHOT
from backend.services.backup_service.base import BackupBase
from backend.services.backup_service.mirror import MANIFESTS_DIR

# Snapshot kinds (SnapshotResponse.kind)
KIND_FULL = "full"      # restores the tree exactly as it was right after that backup
KIND_LEGACY = "legacy"  # pre-manifest mirror version: files as they were before that backup


class SnapshotsMixin(BackupBase):
    """Lists a target's snapshots, newest first."""

    # ── Snapshots ────────────────────────────────────────────────────

    async def list_snapshots(self, target: BackupTarget) -> list[SnapshotResponse]:
        """List available restore points for a target, newest first.

        The newest one carries ``latest``: the most recent backup, which is
        restorable like any other.
        """
        if target.backup_mode == BackupMode.MIRROR.value:
            snapshots = await self._list_mirror_snapshots(target)
        else:
            snapshots = await self._list_archive_snapshots(target)
        if snapshots and snapshots[0].kind == KIND_FULL:
            snapshots[0].latest = True
        return snapshots

    async def _mirror_index(self, root: str) -> tuple[list[str], list[str]]:
        """(snapshot ids that have a manifest, version folder names), oldest first."""
        versions = sorted(
            v for v in await self._rclone.list_dirs(remote_join(root, "versions"))
            if self._parse_timestamp(v) is not None
        )
        names = await self._rclone.list_top_level(remote_join(root, MANIFESTS_DIR))
        manifests = sorted(
            n.removesuffix(".json") for n in names
            if n.endswith(".json") and self._parse_timestamp(n.removesuffix(".json")) is not None
        )
        return manifests, versions

    async def _list_mirror_snapshots(self, target: BackupTarget) -> list[SnapshotResponse]:
        """One snapshot per backup with a manifest, plus pre-manifest versions.

        A target backed up only before manifests existed also lists its
        latest backup (current/) as the pseudo snapshot ``current``.
        """
        root = self.storage_root(target)
        try:
            manifests, versions = await self._mirror_index(root)
        except RcloneError:
            return []

        snapshots = [
            SnapshotResponse(snapshot_id=m, created_at=ts, status="available", kind=KIND_FULL)
            for m in manifests if (ts := self._parse_timestamp(m)) is not None
        ]
        snapshots += [
            SnapshotResponse(snapshot_id=v, created_at=ts, status="available", kind=KIND_LEGACY)
            for v in versions if v not in set(manifests) and (ts := self._parse_timestamp(v)) is not None
        ]
        if not manifests:
            latest = await self._latest_backup_time(target)
            try:
                has_current = bool(await self._rclone.list_top_level(remote_join(root, "current")))
            except RcloneError:
                has_current = False
            if latest is not None and has_current:
                snapshots.append(SnapshotResponse(
                    snapshot_id=LATEST_SNAPSHOT, created_at=latest, status="available", kind=KIND_FULL,
                ))
        # A full snapshot sorts before a legacy one of the same second.
        return sorted(snapshots, key=lambda s: (s.created_at, s.kind == KIND_FULL), reverse=True)

    async def _latest_backup_time(self, target: BackupTarget) -> datetime | None:
        """When the newest completed backup of this target ran, from the job history."""
        async with self._db() as session:
            job = (await session.execute(
                select(BackupJob).where(
                    BackupJob.target_id == target.id,
                    BackupJob.direction == "backup",
                    BackupJob.status == BackupJobStatus.COMPLETED.value,
                ).order_by(BackupJob.started_at.desc()).limit(1)
            )).scalar_one_or_none()
        if job is None:
            return None
        stamp = self._parse_timestamp(job.snapshot_id or "")
        if stamp is not None:
            return stamp
        when = job.finished_at or job.started_at
        return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when

    async def _list_archive_snapshots(self, target: BackupTarget) -> list[SnapshotResponse]:
        """List tar.gz files as snapshots."""
        try:
            items = await self._rclone.lsjson(self.storage_root(target))
        except RcloneError:
            return []

        snapshots = []
        for item in items:
            path = item.get("Path", "")
            if path.startswith("backup-") and path.endswith(".tar.gz"):
                # Extract timestamp from filename like "backup-2025-06-01T14-00-00.tar.gz"
                ts_str = path.removeprefix("backup-").removesuffix(".tar.gz")
                created_at = self._parse_timestamp(ts_str)
                if created_at:
                    snapshots.append(SnapshotResponse(
                        snapshot_id=path,
                        created_at=created_at,
                        size_bytes=item.get("Size"),
                        status="available",
                    ))
        return sorted(snapshots, key=lambda s: s.created_at, reverse=True)
