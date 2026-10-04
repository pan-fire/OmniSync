"""The state every part of the backup service shares, and where a target's backups live.

BackupService (service.py) is assembled from mixins, one per concern, each
in its own module and derived from BackupBase. All instance state lives
here, set in BackupBase.__init__. For the type checker only, BackupBase
also declares the methods one mixin calls on another (see the
TYPE_CHECKING block); at runtime they come from the mixins through
BackupService's method resolution order.
"""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.logging_setup import register_secret
from backend.api.schemas import BackupTargetType, RestoreScope, SnapshotResponse
from backend.db.models import BackupJob, BackupTarget, SyncProfile
from backend.services.notification_dispatcher import NotificationDispatcher
from backend.services.rclone import RcloneService, define_env_remote
from backend.services.sync_engine import transfer_tuning_args
from backend.services.sync_engine_manager import SyncEngineManager
from backend.services.backup_service.common import SnapshotFile, _BackupOutcome

if TYPE_CHECKING:
    from backend.services.backup_service.restore import _MirrorPlan


def crypt_root(target_id: int, target_path: str, obscured_password: str) -> str:
    """The rclone root of an encrypted target: a crypt remote over ``target_path``.

    The remote (``omnisync_backup_crypt_<target id>:``) is defined only in
    the environment of the rclone processes that name it, so the passphrase
    never appears on a command line, where any local user could read it in
    the process list (obscured is reversible). The path is passed as it is:
    commas, colons and quotes in it, e.g. ``gdrive:Backups``, need no
    escaping. File and folder names are encrypted (rclone's defaults:
    standard name encryption, folder names encrypted, no salt), so the backup
    can be read with any rclone that has a crypt remote with the same
    settings and passphrase.
    """
    register_secret(obscured_password)  # masked in the log wherever it shows up
    return define_env_remote(target_id, {"type": "crypt", "remote": target_path, "password": obscured_password})


class BackupBase:
    """Instance state of a BackupService; see BackupService."""

    def __init__(
        self,
        rclone: RcloneService,
        dispatcher: NotificationDispatcher,
        db_session_factory: async_sessionmaker[AsyncSession],
        engine_manager: SyncEngineManager,
    ) -> None:
        self._rclone = rclone
        self._dispatcher = dispatcher
        self._db = db_session_factory
        self._manager = engine_manager
        self._scheduler: AsyncIOScheduler | None = None
        self._running_targets: set[int] = set()
        # Targets whose overdue notification was sent (until they recover).
        self._overdue_notified: set[int] = set()
        # (target id, location, encrypted, snapshot id) -> files of an archive snapshot
        self._archive_lists: OrderedDict[tuple[int, str, bool, str], list[SnapshotFile]] = OrderedDict()
        # Backup and restore runs going on now -> their job id (jobs.py), and
        # whether the service is shutting down (stop_jobs()).
        self._job_tasks: dict[asyncio.Task, int] = {}
        self._stopping = False

    # ── Storage location ─────────────────────────────────────────────

    @staticmethod
    def storage_root(target: BackupTarget) -> str:
        """Where the target's backups are read and written: its path, or a crypt over it.

        Everything that touches backup data (snapshots, manifests, archives,
        retention, restores) goes through this, so encryption is transparent.
        Liveness and the overlap checks look at the plain ``target_path``.
        """
        if target.encryption_password:
            return crypt_root(target.id, target.target_path, target.encryption_password)
        return target.target_path

    @staticmethod
    def _plain_local(target: BackupTarget) -> bool:
        """A local, unencrypted target: archives there are plain files on this machine."""
        return target.target_type == BackupTargetType.LOCAL.value and not target.encryption_password

    async def obscure_passphrase(self, passphrase: str) -> str:
        """rclone's obscured form of a passphrase, as stored in the database.

        Both forms are masked in the log from now on.
        """
        register_secret(passphrase)
        obscured = await self._rclone.obscure(passphrase)
        register_secret(obscured)
        return obscured

    def profile_lock(self, profile_id: int) -> asyncio.Lock:
        """The profile's sync lock, shared with its engine whether or not one runs now.

        A private lock for a disabled profile would let an engine started
        mid-backup (the profile is enabled) sync the folder meanwhile.
        """
        return self._manager.sync_lock(profile_id)

    @staticmethod
    def transfer_args(profile: SyncProfile) -> list[str]:
        """The profile's bandwidth limit and tuning flags, for backup and restore transfers.

        Only the flags that tune transfers (transfer_tuning_args): the
        profile's filters and limits never change what a backup holds or a
        restore writes.
        """
        try:
            args = json.loads(profile.rclone_args) if profile.rclone_args else []
        except (TypeError, ValueError):
            args = []
        if not isinstance(args, list):
            args = []
        return transfer_tuning_args([str(a) for a in args], profile.bwlimit or None)

    @staticmethod
    def _parse_timestamp(ts_str: str) -> datetime | None:
        """Parse a timestamp string like '2025-06-01T14-00-00'."""
        try:
            return datetime.strptime(ts_str, "%Y-%m-%dT%H-%M-%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    if TYPE_CHECKING:
        # Provided by the mixins (see the module docstring).
        # archive.py
        async def _execute_archive(
            self, target: BackupTarget, profile: SyncProfile, timestamp: str,
        ) -> _BackupOutcome: ...
        def _archive_source(self, target: BackupTarget, snapshot_id: str) -> str | list[str]: ...
        async def _restore_archive(
            self,
            target: BackupTarget,
            profile: SyncProfile,
            snapshot_id: str,
            scope: RestoreScope,
        ) -> None: ...
        async def _restore_archive_files(
            self, target: BackupTarget, snapshot_id: str, select_path: Callable[[str], bool], dest: str,
            rclone_args: list[str] | None = None,
        ) -> None: ...
        # execution.py
        async def run_backup(self, target_id: int) -> BackupJob: ...
        async def _backup(self, target_id: int, job_id: int, *, lock_held: bool) -> None: ...
        # jobs.py
        async def _new_job(self, target_id: int, direction: str, snapshot_id: str | None = None) -> BackupJob: ...
        async def _run_job(self, job_id: int, direction: str, body: Callable[[], Awaitable[object]]) -> BackupJob: ...
        async def stop_jobs(self, timeout: float = ...) -> None: ...
        # browse.py
        async def _chosen_files(self, target_id: int, snapshot_id: str, paths: list[str]) -> tuple[int, list[str]]: ...
        async def _restore_files(
            self, target_id: int, snapshot_id: str, paths: list[str], target_dir: str | None, job_id: int,
            *, lock_held: bool,
        ) -> None: ...
        # liveness.py
        async def check_liveness(self, target: BackupTarget, *, first_run: bool = False) -> tuple[bool, str | None]: ...
        # mirror.py
        async def _execute_mirror(
            self, target: BackupTarget, profile: SyncProfile, timestamp: str,
        ) -> _BackupOutcome: ...
        async def _read_manifest(self, root: str, snapshot_id: str) -> dict[str, int | None]: ...
        async def _read_manifest_files(self, root: str, snapshot_id: str) -> dict[str, dict]: ...
        # restore.py
        async def _restore(
            self, target_id: int, snapshot_id: str, scope: RestoreScope, job_id: int, *, lock_held: bool,
        ) -> None: ...
        async def _hold_for_restore(self, profile: SyncProfile, reason: str) -> None: ...
        @staticmethod
        def _restore_sides(profile: SyncProfile, scope: RestoreScope) -> list[tuple[Literal["local", "remote"], str]]: ...
        @classmethod
        def _restore_destinations(cls, profile: SyncProfile, scope: RestoreScope) -> list[str]: ...
        @staticmethod
        def pre_restore_dir(dest: str) -> str: ...
        async def _mirror_plan(
            self, root: str, snapshot_id: str, select: Callable[[str], bool] | None = None,
        ) -> _MirrorPlan: ...
        async def _apply_copy(
            self, plan: _MirrorPlan, snapshot_id: str, dests: list[str], rclone_args: list[str] | None = None,
        ) -> None: ...
        # retention.py
        async def cleanup_old_snapshots(self, target: BackupTarget) -> int: ...
        # service.py
        @staticmethod
        def _check_archive_id(snapshot_id: str) -> None: ...
        # snapshots.py
        async def _mirror_index(self, root: str) -> tuple[list[str], list[str]]: ...
        async def _list_archive_snapshots(self, target: BackupTarget) -> list[SnapshotResponse]: ...
        # verify.py
        async def _verify_mirror(
            self, target: BackupTarget, source: str, current: str, file_count: int,
        ) -> tuple[str, str]: ...
        async def _verify_archive(
            self, target: BackupTarget, temp_path: str, filename: str, size: int,
        ) -> tuple[str, str]: ...
