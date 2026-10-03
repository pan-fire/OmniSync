"""Retention: deleting snapshots older than a target's retention, except the newest keep_last."""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

from backend.api.schemas import BackupMode
from backend.db.models import BackupTarget
from backend.services.sync_engine import remote_join
from backend.services.backup_service.common import logger
from backend.services.backup_service.base import BackupBase
from backend.services.backup_service.mirror import MANIFESTS_DIR


class RetentionMixin(BackupBase):
    """cleanup_old_snapshots() for mirror and archive targets."""

    # ── Retention ────────────────────────────────────────────────────

    async def cleanup_old_snapshots(self, target: BackupTarget) -> int:
        """Delete snapshots older than retention_days, except the newest keep_last. Returns count deleted.

        However old, the newest ``keep_last`` snapshots (at least the newest
        one) stay: a target whose backups stopped or keep failing must not
        lose its last good snapshots to age. A mirror snapshot T needs only
        the versions newer than T and current/, so deleting only the oldest
        snapshots never breaks a snapshot that is kept.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=target.retention_days)
        keep_last = max(target.keep_last or 1, 1)
        if target.backup_mode == BackupMode.MIRROR.value:
            deleted = await self._cleanup_mirror(self.storage_root(target), cutoff, keep_last)
        else:
            deleted = await self._cleanup_archives(target, cutoff, keep_last)
        if deleted:
            logger.info("Cleaned up %d expired snapshot(s) for target %d", deleted, target.id)
        return deleted

    async def _cleanup_mirror(self, root: str, cutoff: datetime, keep_last: int) -> int:
        manifests, versions = await self._mirror_index(root)
        snapshot_ids = sorted(set(manifests) | set(versions))
        # Counted among the backups that completed (they have a manifest); a
        # version folder a failed run left behind is no restore point. A
        # target backed up only before manifests existed counts its versions.
        restorable = manifests or snapshot_ids
        # Fewer than keep_last of them: every snapshot is kept.
        oldest_kept = restorable[-keep_last] if len(restorable) >= keep_last else ""
        deleted = 0
        for snapshot_id in snapshot_ids:
            if snapshot_id >= oldest_kept:
                break
            created_at = self._parse_timestamp(snapshot_id)
            if created_at is None or created_at >= cutoff:
                continue
            try:
                if snapshot_id in versions:
                    await self._rclone.delete_path(remote_join(root, f"versions/{snapshot_id}") + "/")
                if snapshot_id in manifests:
                    await self._rclone._run(
                        ["deletefile"], use_config_args=False,
                        positional=[remote_join(root, f"{MANIFESTS_DIR}/{snapshot_id}.json")],
                    )
                deleted += 1
            except Exception as exc:
                logger.warning("Failed to delete snapshot %s: %s", snapshot_id, exc)
        return deleted

    async def _cleanup_archives(self, target: BackupTarget, cutoff: datetime, keep_last: int) -> int:
        deleted = 0
        # Newest first: the newest keep_last archives are kept.
        for snap in (await self._list_archive_snapshots(target))[keep_last:]:
            if snap.created_at >= cutoff:
                continue
            try:
                if self._plain_local(target):
                    file_path = os.path.join(target.target_path, snap.snapshot_id)
                    if os.path.exists(file_path):
                        os.remove(file_path)
                else:
                    await self._rclone._run(
                        ["deletefile"],
                        use_config_args=False,
                        positional=[remote_join(self.storage_root(target), snap.snapshot_id)],
                    )
                deleted += 1
            except Exception as exc:
                logger.warning("Failed to delete snapshot %s: %s", snap.snapshot_id, exc)
        return deleted
