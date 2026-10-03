"""Safety rails of the sync engine: the startup check, sync markers, empty-side checks and the delete limit."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from backend.api.schemas import SyncDirection
from backend.services.rclone import SENTINEL_FILE, TRASH_DIR
from backend.services.sync_engine.common import TRASH_STAMP_FORMAT, _write_text, logger, remote_join
from backend.services.sync_engine.reporting import ReportingMixin

# Files a single sync may delete before rclone stops with an error. A
# profile's own --max-delete in rclone_args takes precedence.
DEFAULT_MAX_DELETE = int(os.environ.get("OMNISYNC_MAX_DELETE", "50"))

# The startup check runs before the API serves requests, so it is bounded
# more tightly than a diff the user asks for; a timeout pauses the profile.
STARTUP_CHECK_TIMEOUT = float(os.environ.get("OMNISYNC_STARTUP_CHECK_TIMEOUT", "120"))


def effective_max_delete(rclone_args: list[str]) -> int | None:
    """The delete limit a bulk sync with these profile rclone_args runs with.

    The profile's own --max-delete wins (the last one, as rclone reads it);
    otherwise DEFAULT_MAX_DELETE applies. None means no limit (a negative
    value, as rclone treats it) or a value rclone would not accept.
    """
    value: str | None = None
    for i, arg in enumerate(rclone_args):
        if arg == "--max-delete":
            value = rclone_args[i + 1] if i + 1 < len(rclone_args) else ""
        elif arg.startswith("--max-delete="):
            value = arg.split("=", 1)[1]
    if value is None:
        return DEFAULT_MAX_DELETE if DEFAULT_MAX_DELETE >= 0 else None
    try:
        limit = int(value)
    except ValueError:
        return None
    return limit if limit >= 0 else None


# The profile flags that only tune how files are transferred (bandwidth,
# parallelism, retries, timeouts, chunk sizes, how files are compared). They
# apply to every transfer OmniSync makes for the profile, including those
# that name their files: per-file pushes and pulls, conflict resolutions,
# backups and restores. The profile's other flags choose which files a sync
# sees or what it may skip or delete (--exclude, --max-age, --update,
# --max-delete, ...): rclone refuses filter flags next to --files-from-raw,
# and --update or --ignore-existing would silently skip a file the user
# chose to copy, so those are left out there.
TUNING_VALUE_FLAGS = frozenset({
    "--bwlimit", "--transfers", "--checkers", "--tpslimit", "--tpslimit-burst", "--max-transfer",
    "--low-level-retries", "--retries", "--retries-sleep", "--contimeout", "--timeout",
    "--multi-thread-streams", "--buffer-size", "--drive-chunk-size", "--onedrive-chunk-size",
    "--s3-chunk-size", "--s3-upload-concurrency", "--b2-chunk-size",
})
TUNING_BOOL_FLAGS = frozenset({"--fast-list", "--checksum", "--drive-acknowledge-abuse"})


def transfer_tuning_args(rclone_args: list[str], bwlimit: str | None) -> list[str]:
    """The tuning flags of a profile's rclone_args (see TUNING_VALUE_FLAGS), plus its bandwidth limit.

    For transfers of chosen files, backups and restores; push, pull and
    two-way runs pass every flag (SafetyMixin._transfer_args).
    """
    out: list[str] = []
    take_value = False
    for arg in rclone_args:
        if take_value:
            take_value = False
            out.append(arg)
            continue
        name, has_value, _ = arg.partition("=")
        if name in TUNING_VALUE_FLAGS:
            out.append(arg)
            take_value = not has_value
        elif name in TUNING_BOOL_FLAGS:
            out.append(arg)
    if bwlimit:
        out += ["--bwlimit", bwlimit]
    return out


class SafetyMixin(ReportingMixin):
    """Checks a sync must pass before it changes files, and the limits it runs with."""

    async def _startup_check(self) -> None:
        """Run a diff check at startup to detect unsynced changes.

        Runs (awaited) before the scheduler starts so that the pull job can
        be paused if differences are found. Fails closed: if the check
        errors or times out (auth, rate limit, unreadable files, rclone
        exit codes), intervals are paused and last_error says why, since
        "could not compare" must never be taken for "in sync".
        """
        logger.info("Running startup diff check...")
        try:
            result = await self.check_diff(timeout=STARTUP_CHECK_TIMEOUT)
            error = result.error
        except Exception as exc:
            result, error = None, str(exc) or type(exc).__name__
        if error:
            message = (
                f"Startup check could not compare local and remote: {error} "
                "Automatic syncing is paused so offline changes are not overwritten. "
                "Run a diff once the problem is fixed, or resume."
            )
            logger.error("Profile '%s': %s", self._profile.slug, message)
            self._state.set_error(message)
            self._pause(reason=message)
            await self._emit_notification("startup_failure", error=message)
            return
        assert result is not None
        total = len(result.local_only) + len(result.remote_only) + len(result.differ)
        if total > 0:
            logger.info(
                "Startup check found %d unsynced changes: "
                "%d local-only, %d remote-only, %d differ (%d pending)",
                total, len(result.local_only), len(result.remote_only), len(result.differ),
                self._state.pending_changes,
            )
        else:
            logger.info("Startup check: local and remote are in sync")

    async def _preflight(self, direction: SyncDirection) -> tuple[str | None, bool]:
        """Decide whether a bulk sync is safe to run.

        Returns (refusal reason or None, whether this is the profile's first
        sync, i.e. neither side has the sync marker yet). Raises RcloneError
        when the remote cannot be listed.
        """
        config = self._profile
        if not os.path.isdir(config.local_dir):
            return (f"Local folder '{config.local_dir}' is missing or not mounted.", False)
        try:
            local_entries = [e for e in os.listdir(config.local_dir) if e != TRASH_DIR]
        except OSError as exc:
            return (f"Local folder '{config.local_dir}' cannot be read: {exc.strerror}.", False)
        remote_entries = await self._rclone.list_top_level(config.remote_dir)

        local_marked = SENTINEL_FILE in local_entries
        remote_marked = SENTINEL_FILE in remote_entries
        if local_marked != remote_marked:
            missing = "remote folder" if local_marked else "local folder"
            return (
                f"The sync marker {SENTINEL_FILE} is missing from the {missing}. That usually "
                "means the folder was replaced, emptied or is not mounted. Check it, then copy "
                f"{SENTINEL_FILE} over from the other side to confirm it is the right folder.",
                False,
            )

        source, dest = (local_entries, remote_entries) if direction == SyncDirection.PUSH else (remote_entries, local_entries)
        source_items = [e for e in source if e != SENTINEL_FILE]
        dest_items = [e for e in dest if e != SENTINEL_FILE]
        if not source_items and dest_items:
            side = "local folder" if direction == SyncDirection.PUSH else "remote folder"
            return (
                f"Refusing to {direction.value}: the {side} is empty while the other side has "
                f"{len(dest_items)} item(s), which the sync would delete.",
                False,
            )
        return (None, not local_marked and not remote_marked)

    async def _write_sentinels(self) -> None:
        """Mark both folders as belonging to this profile (first successful sync)."""
        config = self._profile
        local_marker = os.path.join(config.local_dir, SENTINEL_FILE)
        await asyncio.to_thread(
            _write_text,
            local_marker,
            f"OmniSync sync marker for profile '{config.slug}'.\n"
            "OmniSync refuses to sync if this file exists on only one side.\n",
        )
        await self._rclone.copyto(local_marker, remote_join(config.remote_dir, SENTINEL_FILE))
        logger.info("Profile '%s': sync markers written on both sides", config.slug)

    def _backup_dir(self, dest: str) -> str:
        """Timestamped folder (inside dest's trash) for files this sync replaces or deletes."""
        stamp = datetime.now(timezone.utc).strftime(TRASH_STAMP_FORMAT)
        return remote_join(dest, f"{TRASH_DIR}/{stamp}")

    def _max_delete(self) -> int | None:
        """The default delete bound, unless the profile sets --max-delete itself."""
        args = self._profile.rclone_args
        if any(a == "--max-delete" or a.startswith("--max-delete=") for a in args):
            return None
        return DEFAULT_MAX_DELETE

    @property
    def _transfer_args(self) -> list[str]:
        """The profile's rclone flags plus its bandwidth limit, for push, pull and two-way runs."""
        args = list(self._profile.rclone_args)
        if self._profile.bwlimit:
            args += ["--bwlimit", self._profile.bwlimit]
        return args

    @property
    def _copy_args(self) -> list[str]:
        """The flags for copies of chosen files (per-file actions, conflict resolutions).

        The tuning subset of _transfer_args, with the bandwidth limit: see
        transfer_tuning_args.
        """
        return transfer_tuning_args(self._profile.rclone_args, self._profile.bwlimit)

    @property
    def max_delete(self) -> int | None:
        """The delete limit this profile's bulk syncs run with (None: no limit)."""
        return effective_max_delete(self._profile.rclone_args)

    async def _preflight_two_way(self, resync: bool) -> tuple[str | None, bool]:
        """Decide whether a two-way run is safe; (refusal or None, neither side marked).

        Same rails as a push or pull: the local folder must exist, and the
        sync marker must be on both sides or on neither (first run). A
        normal run also refuses when one side is empty and the other is not
        (an emptied or unmounted folder would otherwise delete everything
        on the other side); a resync only copies, so it may fill an empty
        side. Raises RcloneError when the remote cannot be listed.
        """
        config = self._profile
        if not os.path.isdir(config.local_dir):
            return (f"Local folder '{config.local_dir}' is missing or not mounted.", False)
        try:
            local_entries = [e for e in os.listdir(config.local_dir) if e != TRASH_DIR]
        except OSError as exc:
            return (f"Local folder '{config.local_dir}' cannot be read: {exc.strerror}.", False)
        remote_entries = await self._rclone.list_top_level(config.remote_dir)

        local_marked = SENTINEL_FILE in local_entries
        remote_marked = SENTINEL_FILE in remote_entries
        if local_marked != remote_marked:
            missing = "remote folder" if local_marked else "local folder"
            return (
                f"The sync marker {SENTINEL_FILE} is missing from the {missing}. That usually "
                "means the folder was replaced, emptied or is not mounted. Check it, then copy "
                f"{SENTINEL_FILE} over from the other side to confirm it is the right folder.",
                False,
            )
        if not resync:
            local_items = [e for e in local_entries if e != SENTINEL_FILE]
            remote_items = [e for e in remote_entries if e != SENTINEL_FILE]
            for empty, other, items in (("local", "remote", remote_items), ("remote", "local", local_items)):
                if items and not (local_items if empty == "local" else remote_items):
                    return (
                        f"Refusing to sync: the {empty} folder is empty while the {other} folder has "
                        f"{len(items)} item(s), which a two-way sync would delete there. If the {empty} "
                        f"folder was emptied on purpose, delete the files on the {other} side too, or "
                        "use Push or Pull.",
                        False,
                    )
        return (None, not local_marked and not remote_marked)
