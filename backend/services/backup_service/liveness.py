"""Whether a backup target is reachable, and the pre-rename ("gsync") leftovers in it."""

from __future__ import annotations

import asyncio
import json
import os

from backend.api.schemas import BackupTargetType
from backend.db.models import BackupTarget
from backend.exceptions import RcloneAuthError, RcloneError
from backend.services.rclone import redact_secrets
from backend.services.backup_service.common import SEE_LOG, logger
from backend.services.backup_service.base import BackupBase

# Pre-rename ("gsync") leftovers in users' backup targets. Exact names only —
# never patterns — and only as direct children of BackupTarget.target_path.
# Current '.omnisync-*' names are in use and must never appear here.
#
# Only genuinely disposable transients are deleted. The pre-restore directories
# are NOT markers: they are the rclone --backup-dir destinations holding the
# only copy of files a restore overwrote or deleted, so they are reported and
# deliberately left in place for the user to deal with.
LEGACY_MARKER_FILES = (".gsync-liveness-check",)
LEGACY_PRESERVED_DIRS = (".gsync-pre-restore", ".gsync-pre-restore-remote")

# Executable invariant: a preserved name must never become deletable.
assert not set(LEGACY_MARKER_FILES) & set(LEGACY_PRESERVED_DIRS)


class LivenessMixin(BackupBase):
    """Checks a target before a backup; reports and removes legacy markers."""

    async def location_has_data(self, target_path: str) -> bool:
        """Whether a target location holds anything (backups, encrypted or not); a missing one does not."""
        names = await self._rclone.list_top_level(target_path)
        return any(n not in (".omnisync-liveness-check", *LEGACY_MARKER_FILES) for n in names)

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

        # Reachable — opportunistically drop pre-rename markers. Called
        # outside the try above so it can never turn a live target into
        # an unreachable one; it swallows its own failures anyway.
        try:
            await self.purge_legacy_markers(target)
        except Exception as exc:  # Liveness must stay a total function
            logger.debug("Legacy marker purge failed for target: %s", exc)
        return True, None

    # ── Legacy cleanup ───────────────────────────────────────────────

    async def purge_legacy_markers(self, target: BackupTarget) -> int:
        """Remove leftover pre-rename '.gsync-*' transients from a target.

        Deletes only the disposable liveness marker, and only on local
        targets — that marker was never written to a remote (see
        check_liveness). Pre-restore safety backups are reported but never
        touched: they hold the user's own overwritten files. Best-effort;
        returns the number of files removed.
        """
        key = (target.id, target.target_path)
        target_type = target.target_type
        if key in self._purged_targets:
            return 0

        try:
            if target_type == BackupTargetType.LOCAL.value:
                # Plain values only — the ORM instance stays on the event loop.
                removed = await asyncio.to_thread(self._purge_legacy_local, key[1])
            else:
                removed = await self._report_legacy_remote(key[1], key[0])
        except Exception as exc:
            # Not memoized: a transient failure should be retried next time.
            logger.debug("Legacy marker purge skipped for target %s: %s", key[0], exc)
            return 0

        self._purged_targets.add(key)
        if removed:
            logger.info(
                "Removed %d legacy '.gsync-*' marker(s) from target %s", removed, key[0]
            )
        return removed

    def _purge_legacy_local(self, target_path: str) -> int:
        """Remove legacy transients from a local filesystem target."""
        root = os.path.normpath(target_path)
        if not os.path.isabs(root) or root == os.sep:
            logger.debug("Refusing legacy purge for degenerate root %r", root)
            return 0
        if set(root.split(os.sep)) & set(LEGACY_PRESERVED_DIRS):
            logger.warning(
                "Refusing legacy purge: target root %s is inside a pre-restore backup", root
            )
            return 0

        removed = 0
        for name in LEGACY_MARKER_FILES:
            if name in LEGACY_PRESERVED_DIRS:  # Invariant, enforced not assumed
                continue
            path = os.path.normpath(os.path.join(root, name))
            if os.path.dirname(path) != root or not os.path.lexists(path):
                continue
            # Regular files only: never follow a symlink, never touch a dir.
            if os.path.islink(path) or not os.path.isfile(path):
                logger.debug("Skipping legacy marker %s: not a regular file", path)
                continue
            try:
                os.remove(path)
                removed += 1
                logger.debug("Removed legacy marker %s", path)
            except Exception as exc:
                logger.debug("Could not remove legacy marker %s: %s", path, exc)

        for name in LEGACY_PRESERVED_DIRS:
            path = os.path.normpath(os.path.join(root, name))
            if os.path.dirname(path) == root and os.path.lexists(path):
                self._warn_preserved(path)
        return removed

    async def _report_legacy_remote(self, target_path: str, target_id: int) -> int:
        """Report pre-rename safety backups on a remote target.

        Deletes nothing: the liveness marker only ever existed on local
        targets, so a remote delete round trip would be dead work.
        """
        root = target_path.rstrip("/")
        if not root:
            logger.debug("Refusing legacy scan for empty remote root (target %s)", target_id)
            return 0

        # Deliberately not RcloneService.lsjson(): that passes --recursive
        # and would walk every snapshot under the target.
        try:
            result = await self._rclone._run(
                ["lsjson", "--max-depth", "1"],
                use_config_args=False,
                timeout=15,
                positional=[root],
            )
            entries: list[dict] = json.loads(result.stdout)
        except Exception as exc:
            logger.debug("Legacy scan failed for target %s: %s", target_id, exc)
            return 0

        present = {str(item.get("Path", "")).rstrip("/") for item in entries}
        sep = "" if root.endswith(":") else "/"
        for name in LEGACY_PRESERVED_DIRS:
            if name in present:
                self._warn_preserved(f"{root}{sep}{name}")
        return 0

    @staticmethod
    def _warn_preserved(path: str) -> None:
        """Flag a pre-rename safety backup, which is never auto-deleted."""
        logger.warning(
            "Pre-rename safety backup %s left in place: it holds files a previous "
            "restore overwrote or deleted. Delete it yourself once you no longer "
            "need to undo that restore.",
            path,
        )
