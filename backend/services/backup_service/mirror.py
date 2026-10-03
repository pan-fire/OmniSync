"""Mirror backups: rclone sync into current/ with versions/, and the manifest of each backup."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile

from backend.db.models import BackupTarget, SyncProfile
from backend.exceptions import RcloneAuthError, RcloneError
from backend.services.sync_engine import remote_join
from backend.services.backup_service.common import BACKUP_FILTER, RestoreRefused, _BackupOutcome, logger
from backend.services.backup_service.base import BackupBase

# Mirror layout: <target>/current/ (the latest backup), <target>/versions/<T>/
# (what the backup at T replaced or deleted) and <target>/manifests/<T>.json
# (every file, with its size, right after the backup at T). A manifest makes
# snapshot T exactly restorable; versions/ alone cannot tell which files were
# added after T.
MANIFESTS_DIR = "manifests"
MANIFEST_FORMAT = 1


class MirrorMixin(BackupBase):
    """Runs a mirror backup and reads and writes its manifests."""

    async def _execute_mirror(
        self, target: BackupTarget, profile: SyncProfile, timestamp: str,
    ) -> _BackupOutcome:
        """Run mirror backup using rclone sync --backup-dir, then record its manifest (and verify)."""
        root = self.storage_root(target)
        source = profile.local_dir
        dest = remote_join(root, "current") + "/"
        backup_dir = remote_join(root, f"versions/{timestamp}") + "/"

        await self._rclone.sync_with_backup_dir(source, dest, backup_dir, rclone_filter=BACKUP_FILTER,
                                                rclone_args=self.transfer_args(profile))
        # The trash folder is excluded (lsjson never lists it): a restore
        # never writes into the destination's trash.
        try:
            entries = await self._rclone.lsjson(remote_join(root, "current"))
        except RcloneAuthError:
            raise
        except RcloneError as exc:
            if "directory not found" not in str(exc).lower():
                raise
            entries = []  # an empty folder: rclone created no current/
        files = [{"path": e["Path"], "size": e.get("Size"), "mtime": e.get("ModTime")}
                 for e in entries if not e.get("IsDir")]
        await self._write_manifest(root, timestamp, files)
        outcome = _BackupOutcome(sum(max(f["size"] or 0, 0) for f in files), timestamp)
        if target.verify_after_backup:
            outcome.verify_status, outcome.verify_message = await self._verify_mirror(
                target, source, remote_join(root, "current"), len(files),
            )
        return outcome

    async def _write_manifest(self, root: str, snapshot_id: str, files: list[dict]) -> None:
        """Store <root>/manifests/<snapshot_id>.json: the file list right after that backup."""
        manifest = {"format": MANIFEST_FORMAT, "snapshot_id": snapshot_id, "files": files}
        temp_dir = await asyncio.to_thread(tempfile.mkdtemp, prefix="omnisync-manifest-")
        try:
            path = os.path.join(temp_dir, f"{snapshot_id}.json")

            def dump() -> None:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(manifest, fh)

            await asyncio.to_thread(dump)
            await self._rclone.copyto(path, remote_join(root, f"{MANIFESTS_DIR}/{snapshot_id}.json"))
        finally:
            await asyncio.to_thread(shutil.rmtree, temp_dir, True)

    async def _read_manifest(self, root: str, snapshot_id: str) -> dict[str, int | None]:
        """path -> size of every file in snapshot ``snapshot_id``."""
        return {path: f.get("size") for path, f in (await self._read_manifest_files(root, snapshot_id)).items()}

    async def _read_manifest_files(self, root: str, snapshot_id: str) -> dict[str, dict]:
        """path -> manifest entry (size, and mtime in manifests written since it was recorded)."""
        result = await self._rclone._run(
            ["cat"], use_config_args=False, positional=[remote_join(root, f"{MANIFESTS_DIR}/{snapshot_id}.json")],
        )
        try:
            data = json.loads(result.stdout)
            files = {str(f["path"]): f for f in data["files"]}
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("The manifest of snapshot %s is damaged: %s", snapshot_id, exc)
            raise RestoreRefused(f"The manifest of snapshot {snapshot_id} is damaged; nothing was restored.")
        if data.get("format") != MANIFEST_FORMAT:
            raise RestoreRefused(f"The manifest of snapshot {snapshot_id} has an unknown format; nothing was restored.")
        return files
