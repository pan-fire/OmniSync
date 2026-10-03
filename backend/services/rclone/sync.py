"""Transfers: rclone sync and bisync, and copying, moving and deleting single files."""

from __future__ import annotations

import os
import signal
import tempfile
import time

from backend.api.schemas import SyncDirection
from backend.services.rclone.bisync_names import rebase, short_bisync_roots
from backend.services.rclone.common import BISYNC_STOP_GRACE, PARTIAL_FILTER, SENTINEL_FILE, TRASH_FILTER, logger
from backend.services.rclone.errors import classify_failure
from backend.services.rclone.recorder import BisyncRecorder, ChangeRecorder
from backend.services.rclone.process import RcloneBase, RcloneResult, json_log_args, without_flag


class SyncMixin(RcloneBase):
    """Commands that change files: whole-folder syncs and single-file operations."""

    async def sync(
        self, source: str, dest: str, direction: SyncDirection,
        exclude_filter_path: str | None = None,
        rclone_filter: list[str] | None = None,
        rclone_args: list[str] | None = None,
        backup_dir: str | None = None,
        max_delete: int | None = None,
        recorder: ChangeRecorder | None = None,
    ) -> RcloneResult:
        """Run rclone sync with configured filters and args.

        For OAuth remotes, refreshes the token via httpx before invoking
        rclone, so the binary never attempts an interactive token refresh
        (which hangs in non-interactive Docker environments).

        rclone logs each transfer as JSON; ``recorder`` (if given) receives
        what was created, modified and deleted, also when the sync fails
        part-way.

        Args:
            source: Source path (local or remote).
            dest: Destination path (local or remote).
            direction: Push or pull.
            exclude_filter_path: Optional path to a filter file with exclude
                rules (e.g. ``- /path/to/file``). Appended as
                ``--filter-from <path>`` to the rclone command.
            rclone_filter / rclone_args: The profile's own filters and flags.
            backup_dir: Where rclone moves every file it would delete or
                overwrite (``--backup-dir``), on the destination's remote.
            max_delete: Stop with an error instead of deleting more files
                than this (``--max-delete``).
        """
        # Pre-refresh token for OAuth remotes to prevent rclone from hanging
        remote_spec = dest if direction == SyncDirection.PUSH else source
        remote_name = remote_spec.split(":")[0] if ":" in remote_spec else ""
        if remote_name:
            await self._ensure_fresh_token(remote_name)

        # The trash folder lives inside the destination, so it must be
        # excluded (rclone refuses an overlapping --backup-dir otherwise).
        # A killed run's leftover .partial files are neither copied nor deleted.
        args = ["sync", f"--filter={TRASH_FILTER}", f"--filter={PARTIAL_FILTER}"]
        if exclude_filter_path:
            args.extend(["--filter-from", exclude_filter_path])
        if backup_dir:
            args.extend(["--backup-dir", backup_dir])
        if max_delete is not None:
            args.extend(["--max-delete", str(max_delete)])
        logger.info("Starting %s sync: %s -> %s", direction.value, source, dest)
        return await self._run_logged(args, recorder if recorder is not None else ChangeRecorder(max_rows=0),
                                      rclone_filter=rclone_filter, rclone_args=rclone_args,
                                      positional=[source, dest])

    async def bisync(
        self, local: str, remote: str, *,
        workdir: str, filters_file: str, recorder: BisyncRecorder,
        rclone_args: list[str] | None = None,
        backup_dirs: tuple[str, str] | None = None,
        resync: bool = False, dry_run: bool = False, check_access: bool = True,
        short_names: int | None = None,
    ) -> RcloneResult:
        """Run ``rclone bisync local remote`` (Path1 is always the local folder).

        ``short_names`` (a profile id): run on that profile's short-name
        remotes instead of the real paths (see bisync_names.py), for paths
        too long for bisync's file names. Both paths and the backup dirs,
        which must stay on the remote of their path, are then given through
        them; the remotes are defined only in the rclone process's
        environment, and ``recorder`` maps them back in messages.

        The flags, as verified with rclone 1.75.1:

        - ``--workdir``: the listings of the last successful run, per profile.
        - ``--filters-file``: the trash exclusion plus the profile's rules.
          bisync stores its MD5 next to it and refuses to run ("filters file
          has changed (must run --resync)") once the content changes.
        - ``--check-access --check-filename .omnisync-check``: abort unless
          the sync marker is on both sides (also during --resync).
        - ``--resilient --recover``: retry after minor errors and recover
          from an interrupted run (SIGINT, crash) without --resync.
        - ``--conflict-resolve newer --conflict-loser num --conflict-suffix
          local-conflict,remote-conflict --suffix-keep-extension``: a file
          changed on both sides keeps both versions; the newer keeps the
          name, the other becomes e.g. ``plan.local-conflict1.odt``.
        - ``--backup-dir1/--backup-dir2``: replaced and deleted files go to
          each side's trash. The trash lives inside the synced folders,
          which bisync accepts only because the filters exclude it
          (otherwise: "destination and parameter to --backup-dir mustn't
          overlap").
        - ``--max-delete 100``: bisync's own delete guard counts a
          PERCENTAGE of each side's files (default 50); OmniSync applies its
          absolute limit before the run instead (see SyncEngine), so the
          profile's --max-delete is not passed on.
        - ``--resync --resync-mode newer``: the first run and every explicit
          resync: copies both ways, the newer version wins where a file
          differs (the older one goes to the trash), nothing is deleted.

        A stop sends SIGINT: bisync then shuts down gracefully and keeps
        its listings, so the next run (--recover) continues. SIGTERM makes
        rclone 1.75.1 abort a running transfer with a critical error that
        needs --resync.
        """
        remote_name = remote.split(":")[0] if ":" in remote else ""
        if remote_name:
            await self._ensure_fresh_token(remote_name)
        profile_args = without_flag(rclone_args or [], "--max-delete")
        paths = (local, remote)
        if short_names is not None:
            paths = short_bisync_roots(short_names, local, remote)
            if backup_dirs is not None:
                backup_dirs = (rebase(backup_dirs[0], local, paths[0]), rebase(backup_dirs[1], remote, paths[1]))
            recorder.real_roots = {paths[0]: local, paths[1]: remote}
        args = [
            "bisync",
            "--workdir", workdir,
            "--filters-file", filters_file,
            "--resilient", "--recover",
            "--create-empty-src-dirs",
            "--conflict-resolve", "newer",
            "--conflict-loser", "num",
            "--conflict-suffix", "local-conflict,remote-conflict",
            "--suffix-keep-extension",
            "--max-delete", "100",
            "--color", "NEVER",
        ]
        if check_access:
            args += ["--check-access", "--check-filename", SENTINEL_FILE]
        if backup_dirs is not None:
            args += ["--backup-dir1", backup_dirs[0], "--backup-dir2", backup_dirs[1]]
        if resync:
            args += ["--resync", "--resync-mode", "newer"]
        if dry_run:
            args.append("--dry-run")
        cmd = self._build_command([*args, *json_log_args(profile_args)], rclone_args=profile_args,
                                  positional=list(paths))
        logger.info("Starting two-way sync%s: %s <-> %s%s", " (resync)" if resync else "", local, remote,
                    f" (as {paths[0]} <-> {paths[1]})" if short_names is not None else "")
        start = time.monotonic()
        rc = await self._stream(cmd, lambda _line: None, recorder.feed, timeout=None,
                                stop_signal=signal.SIGINT, stop_grace=BISYNC_STOP_GRACE)
        stderr = recorder.failure_text()
        if rc != 0:
            raise classify_failure(rc, stderr)
        return RcloneResult(stdout="", stderr=stderr, return_code=0, elapsed_seconds=time.monotonic() - start)

    async def copy_files(
        self, source: str, dest: str, file_paths: list[str],
        recorder: ChangeRecorder | None = None,
        backup_dir: str | None = None,
        ignore_times: bool = False,
        rclone_args: list[str] | None = None,
    ) -> RcloneResult:
        """Copy exactly these files (paths relative to source) to dest.

        Uses ``--files-from-raw``: each line is one literal path. A filter
        rule such as ``+ notes.txt`` would instead match every notes.txt at
        any depth and treat ``[`` ``*`` ``?`` as wildcards.

        With ``recorder``, rclone logs as JSON and the recorder receives what
        was created and replaced. With ``backup_dir``, replaced files are
        moved there first. (--files-from-raw cannot be combined with filter
        rules; rclone 1.75 accepts a backup dir inside dest with it, since
        only the listed files are considered.) ``ignore_times`` copies even
        when size and modification time already match (``--ignore-times``),
        for a file whose content differs regardless. ``rclone_args``: extra
        flags such as the profile's bandwidth limit; never filter flags,
        which rclone refuses next to --files-from-raw.
        """
        list_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        try:
            for path in file_paths:
                list_file.write(path.lstrip("/") + "\n")
            list_file.close()

            args = ["copy", "--files-from-raw", list_file.name]
            if backup_dir:
                args += ["--backup-dir", backup_dir]
            if ignore_times:
                args.append("--ignore-times")
            if recorder is not None:
                return await self._run_logged(args, recorder, rclone_args=rclone_args, positional=[source, dest])
            return await self._run(args, rclone_args=rclone_args, positional=[source, dest], no_timeout=True)
        finally:
            os.unlink(list_file.name)

    async def copyto(self, source: str, dest: str, rclone_args: list[str] | None = None) -> RcloneResult:
        """Copy one file to an exact destination path (rclone copyto), with extra flags (``rclone_args``)."""
        return await self._run(["copyto"], rclone_args=rclone_args, positional=[source, dest], no_timeout=True)

    async def move_file(
        self, source: str, dest: str
    ) -> RcloneResult:
        """Rename/move a single file using rclone moveto.

        Used for conflict 'keep_both' to rename remote file.
        """
        return await self._run(
            ["moveto"],
            use_config_args=False,
            positional=[source, dest],
            no_timeout=True,
        )

    async def delete_file(self, path: str) -> RcloneResult:
        """Delete one file (rclone deletefile); a folder is refused by rclone."""
        return await self._run(["deletefile"], use_config_args=False, positional=[path], no_timeout=True)

    async def sync_with_backup_dir(
        self,
        source: str,
        dest: str,
        backup_dir: str,
        rclone_filter: list[str] | None = None,
        rclone_args: list[str] | None = None,
    ) -> RcloneResult:
        """Run rclone sync with --backup-dir for versioned mirror backups.

        Changed/deleted files are moved from dest → backup_dir before overwriting.
        """
        # A first mirror of a large folder can take hours: no wall-clock limit.
        return await self._run(
            ["sync", "--backup-dir", backup_dir],
            rclone_filter=rclone_filter, rclone_args=rclone_args,
            positional=[source, dest], no_timeout=True,
        )

    async def delete_path(self, path: str) -> RcloneResult:
        """Run 'rclone purge <path>' to delete a directory and all its contents."""
        return await self._run(["purge"], use_config_args=False, positional=[path], no_timeout=True)
