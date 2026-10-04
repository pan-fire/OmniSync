"""Whether a backup target is reachable."""

from __future__ import annotations

import asyncio
import os

from backend.api.schemas import BackupTargetType
from backend.db.models import BackupTarget
from backend.exceptions import RcloneAuthError, RcloneError
from backend.services.rclone import redact_secrets
from backend.services.backup_service.common import SEE_LOG, logger
from backend.services.backup_service.base import BackupBase

class LivenessMixin(BackupBase):
    """Checks a target before a backup."""

    async def location_has_data(self, target_path: str) -> bool:
        """Whether a target location holds anything (backups, encrypted or not); a missing one does not."""
        names = await self._rclone.list_top_level(target_path)
        return any(n != ".omnisync-liveness-check" for n in names)

    # ── Liveness ─────────────────────────────────────────────────────

    async def check_liveness(self, target: BackupTarget, *, first_run: bool = False) -> tuple[bool, str | None]:
        """Check if the backup target is reachable. Creates nothing.

        A local target folder must exist and be writable: a missing one is
        usually an unmounted drive, and creating it would put the backup on
        the local disk underneath. A remote target is listed at its own
        path; only a target that never completed a backup (``first_run``)
        may lack its folder, as long as the remote itself answers.

        Returns (True, None) if alive, or (False, error_message) if unreachable.
        """
        path = target.target_path
        try:
            if target.target_type == BackupTargetType.LOCAL.value:
                if not await asyncio.to_thread(os.path.isdir, path):
                    return False, (
                        f"Backup folder '{path}' does not exist or is not mounted. OmniSync does not "
                        "create it, so a backup never lands on the local disk under an unmounted drive: "
                        "mount the drive or create the folder."
                    )
                test_file = os.path.join(path, ".omnisync-liveness-check")

                def write_test() -> None:
                    with open(test_file, "w") as f:
                        f.write("ok")
                    os.remove(test_file)

                await asyncio.to_thread(write_test)
            else:
                try:
                    await self._rclone._run(["lsd"], use_config_args=False, timeout=15, positional=[path])
                except RcloneAuthError:
                    raise
                except RcloneError as exc:
                    if "directory not found" not in str(exc).lower():
                        raise
                    if not first_run:
                        return False, (
                            f"Backup folder '{path}' was not found, although earlier backups were "
                            "written there. Check whether it was moved or deleted."
                        )
                    # A new target: the first backup creates the folder.
                    remote = path.split(":", 1)[0]
                    await self._rclone._run(
                        ["lsd"], use_config_args=False, timeout=15, positional=[f"{remote}:"]
                    )
        except RcloneAuthError as exc:
            logger.warning("Backup target %s: signing in to its remote failed: %s", target.id, redact_secrets(str(exc)))
            return False, f"Signing in to the remote of the backup target failed: reconnect the remote. {SEE_LOG}"
        except Exception as exc:
            # rclone's or the OS's text goes to the log, not to the job or the target.
            logger.warning("Backup target %s is not reachable: %s", target.id, redact_secrets(str(exc)))
            return False, f"The backup target could not be reached. {SEE_LOG}"

        return True, None
