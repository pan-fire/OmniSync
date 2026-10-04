"""The restore points of a target: mirror snapshots (one per manifest) and archives."""

from __future__ import annotations

from backend.api.schemas import BackupMode, SnapshotResponse
from backend.db.models import BackupTarget
from backend.exceptions import RcloneError
from backend.services.sync_engine import remote_join
from backend.services.backup_service.base import BackupBase
from backend.services.backup_service.mirror import MANIFESTS_DIR


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
        if snapshots:
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
        """One snapshot per backup with a manifest.

        A version folder without a manifest (left by a run that failed
        before writing it) is no restore point, but still a layer that
        rebuilds the older snapshots.
        """
        try:
            manifests, _ = await self._mirror_index(self.storage_root(target))
        except RcloneError:
            return []
        snapshots = [
            SnapshotResponse(snapshot_id=m, created_at=ts, status="available")
            for m in manifests if (ts := self._parse_timestamp(m)) is not None
        ]
        return sorted(snapshots, key=lambda s: s.created_at, reverse=True)

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
