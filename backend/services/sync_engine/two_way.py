"""Two-way syncs (rclone bisync) of the sync engine: state, filters file, resync."""

from __future__ import annotations

import asyncio
import glob
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone

from backend.api.schemas import SyncPreviewCounts, SyncState, TwoWayPreview
from backend.db.models import SyncJob
from backend.exceptions import RcloneAuthError, RcloneError, RcloneRateLimitError
from backend.services.rclone import (
    PARTIAL_FILTER,
    SENTINEL_FILE,
    TRASH_DIR,
    TRASH_FILTER,
    BisyncRecorder,
    bisync_names_fit,
    needs_short_names,
)
from backend.services.sync_engine import common
from backend.services.sync_engine.common import (
    ENGINE_STOPPED,
    RATE_LIMIT_BASE_DELAY,
    TRASH_STAMP_FORMAT,
    _write_text,
    filter_escape,
    logger,
    remote_join,
)
from backend.services.sync_engine.reporting import ReportingMixin

# Two-way sync (rclone bisync) keeps its listings of the last successful run
# per profile in <OMNISYNC_BISYNC_DIR>/<profile id>, next to the filters
# file it was run with and OmniSync's own state file.
BISYNC_DIR = os.environ.get("OMNISYNC_BISYNC_DIR", "/data/omnisync/bisync")
BISYNC_STATE_FILE = "omnisync-state.json"
BISYNC_FILTERS_FILE = "filters.txt"
BISYNC_PARTIAL_FILTER = PARTIAL_FILTER


def bisync_workdir(profile_id: int) -> str:
    """The bisync working directory of a profile."""
    return os.path.join(BISYNC_DIR, str(profile_id))


# Profile flags that change which files a sync sees (see SAFE_RCLONE_FLAGS).
FILTER_FLAGS = frozenset({
    "--exclude", "--include", "--filter", "--exclude-if-present", "--max-size", "--min-size",
    "--max-age", "--min-age", "--max-depth", "--ignore-case-sync", "--skip-links", "--one-file-system",
    "--drive-skip-gdocs",
})
_FLAGS_WITHOUT_VALUE = frozenset({"--ignore-case-sync", "--skip-links", "--one-file-system", "--drive-skip-gdocs"})


def filter_flags(rclone_args: list[str]) -> list[str]:
    """The flags (with their values) of rclone_args that change which files a sync sees."""
    out: list[str] = []
    take_value = False
    for arg in rclone_args:
        if take_value:
            take_value = False
            if not arg.startswith("-"):
                out.append(arg)
                continue
        name = arg.split("=", 1)[0]
        if name in FILTER_FLAGS:
            out.append(arg)
            take_value = "=" not in arg and name not in _FLAGS_WITHOUT_VALUE
    return out


def reset_bisync_state(profile_id: int) -> None:
    """Forget a profile's two-way sync state (switched to mirror, or deleted).

    The next two-way sync of the profile is then a first run: a resync.
    """
    shutil.rmtree(bisync_workdir(profile_id), ignore_errors=True)


class TwoWayMixin(ReportingMixin):
    """two_way_sync() and resync(): the run plan, bisync with retries, the delete limit."""

    # --- Two-way sync (rclone bisync) ---
    #
    # State lives in the profile's workdir (bisync_workdir): bisync's own
    # listings of the last successful run, the filters file it ran with, and
    # omnisync-state.json ({"pair": [local, remote], "resync_required":
    # reason or null, "names": "paths" | "short"}). "names" is how bisync
    # was given the pair, and so what its listings are named after (see
    # rclone/bisync_names.py): the real paths, or, for paths too long for
    # bisync's file names, the profile's short-name remotes. It is chosen
    # when a pair is synced for the first time or resynced, and kept
    # otherwise (a missing value is "paths", the only form before). A run is
    # one of:
    #   * resync: the first run for this pair of folders, or confirmed by the
    #     user (resync()): the union of both sides, nothing deleted;
    #   * blocked: a resync is required (bisync said so, its listings are
    #     gone although the pair was synced before, or the pair was synced
    #     on its real paths, which bisync can no longer name its files
    #     after): nothing runs, the profile pauses with last_error until the
    #     user confirms a resync;
    #   * flush: the filters changed (profile filters, manual flags or
    #     flags): a normal run with the OLD filters first, so every pending
    #     change and deletion is carried over, then a resync with the new
    #     ones, which can then only add newly included files;
    #   * run: a normal two-way sync.
    # Every normal run first checks the delete limit (see
    # _two_way_delete_refusal).

    @property
    def _workdir(self) -> str:
        return bisync_workdir(self.profile_id)

    @property
    def _pair(self) -> list[str]:
        return [self._profile.local_dir, self._profile.remote_dir]

    def _short_names(self, resync: bool) -> bool:
        """Whether this run gives bisync the short names (see the state above).

        ``resync``: the run is a resync of the pair (first run, explicit,
        or after the pair changed), which chooses the form anew.
        """
        state = self._read_bisync_state()
        if resync or state.get("pair") != self._pair:
            return needs_short_names(self._profile.local_dir, self._profile.remote_dir)
        return state.get("names") == "short"

    def _read_bisync_state(self) -> dict:
        try:
            with open(os.path.join(self._workdir, BISYNC_STATE_FILE), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_bisync_state(self, **changes: object) -> None:
        state = {**self._read_bisync_state(), **changes}
        os.makedirs(self._workdir, exist_ok=True)
        path = os.path.join(self._workdir, BISYNC_STATE_FILE)
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(path + ".tmp", path)

    @staticmethod
    def _resync_message(reason: str) -> str:
        return (
            f"Two-way sync needs a resync: {reason.rstrip('.')}. Automatic syncing is paused. "
            "Check both folders, then run Resync: it copies what is missing on either side, "
            "keeps the newer version where a file differs (the older one goes to the trash) "
            "and deletes nothing."
        )

    def _load_resync_required(self) -> None:
        """At start: a two-way profile that still needs a resync stays paused."""
        state = self._read_bisync_state()
        reason = state.get("resync_required") if state.get("pair") == self._pair else None
        if reason:
            message = self._resync_message(str(reason))
            logger.warning("Profile '%s': %s", self._profile.slug, message)
            self._state.resync_required = True
            self._state.set_error(message)
            self._pause(message)

    def _bisync_filters(self, manual_flags: list[str]) -> str:
        """The filters file content for the next run.

        The trash is excluded (so it may live inside the synced folders),
        manually flagged files are left alone, then the profile's own rules
        follow. With rules, the sync marker is included before them: an
        include-style filter (``+ /Docs/**``, ``- **``) would otherwise hide
        it from --check-access and every run would fail. (Profiles without
        rules keep their filters file unchanged, so they need no resync.)
        The profile's filtering flags are recorded as a comment, so
        changing one (e.g. --exclude, --max-size) also makes the next run a
        resync; other flags (--bwlimit, --transfers, ...) do not.
        """
        lines = [
            "# OmniSync two-way sync filters. Generated: a change makes the next sync a resync.",
            "# filter flags: " + json.dumps(filter_flags(self._profile.rclone_args)),
            TRASH_FILTER,
            # rclone's in-progress files (<name>.<8 hex>.partial), left behind
            # when a run is killed: never carried to the other side.
            BISYNC_PARTIAL_FILTER,
            *(f"- /{filter_escape(p.lstrip('/'))}" for p in sorted(set(manual_flags))),
            *([f"+ /{filter_escape(SENTINEL_FILE)}"] if self._profile.rclone_filter else []),
            *self._profile.rclone_filter,
        ]
        return "\n".join(lines) + "\n"

    def _workdir_files(self, pattern: str) -> list[str]:
        return glob.glob(os.path.join(glob.escape(self._workdir), pattern))

    def _listings_exist(self) -> bool:
        return bool(self._workdir_files("*.path1.lst")) and bool(self._workdir_files("*.path2.lst"))

    def _listed_files(self) -> int | None:
        """The most files one side's last listing holds; None if unknown.

        A run can delete on one side at most the files that side's last
        listing holds, so a delete limit at least this high cannot be hit.
        """
        most = 0
        for path in [*self._workdir_files("*.path1.lst"), *self._workdir_files("*.path2.lst")]:
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    most = max(most, sum(1 for line in fh if line.startswith("-")))
            except OSError:
                return None
        return most

    def _keep_listings_after_stop(self) -> None:
        """After a run OmniSync itself stopped: keep bisync's last good listings.

        A stop sends SIGINT and bisync shuts down gracefully, keeping its
        listings. rclone 1.75.1 sometimes turns a transfer cancelled that
        way into a critical error instead ("chtimes <file>.partial: no such
        file or directory") and renames the listings of the last successful
        run to *.lst-err. Renaming them back leaves the same state a crash
        leaves (last good listings plus *.lst-new), which --recover handles
        on the next run. Only for runs stopped here: after any other
        critical error the listings stay renamed and a resync is required.
        """
        if self._listings_exist():
            return
        errs = [*self._workdir_files("*.path1.lst-err"), *self._workdir_files("*.path2.lst-err")]
        if len(errs) != 2:
            return
        for path in errs:
            os.replace(path, path.removesuffix("-err"))
        logger.info("Profile '%s': kept the listings of the last good two-way sync after the stop",
                    self._profile.slug)

    def _two_way_plan(self, filters: str) -> tuple[str, str | None]:
        """("resync" | "blocked" | "flush" | "run", reason if blocked); see above."""
        state = self._read_bisync_state()
        if state.get("pair") != self._pair:
            return ("resync", None)
        if state.get("resync_required"):
            return ("blocked", str(state["resync_required"]))
        if state.get("names") != "short" and not bisync_names_fit(self._profile.local_dir, self._profile.remote_dir):
            return ("blocked", "the folder paths are too long for the names of rclone's two-way sync records; "
                               "a resync starts new records under short names")
        if not self._listings_exist():
            return ("blocked", "the record of the last two-way sync is missing (an earlier run failed "
                               "or was interrupted in a way rclone could not recover from)")
        try:
            with open(os.path.join(self._workdir, BISYNC_FILTERS_FILE), encoding="utf-8") as fh:
                current = fh.read()
        except OSError:
            return ("blocked", "the filters file of the last two-way sync is missing")
        return ("run", None) if current == filters else ("flush", None)

    def _backup_dirs(self) -> tuple[str, str]:
        """This run's trash folders, one per side, with the same timestamp."""
        stamp = datetime.now(timezone.utc).strftime(TRASH_STAMP_FORMAT)
        return (
            remote_join(self._profile.local_dir, f"{TRASH_DIR}/{stamp}"),
            remote_join(self._profile.remote_dir, f"{TRASH_DIR}/{stamp}"),
        )

    def _clear_workdir(self, patterns: tuple[str, ...]) -> None:
        for pattern in patterns:
            for path in self._workdir_files(pattern):
                try:
                    os.remove(path)
                except OSError as exc:
                    logger.warning("Could not remove %s: %s", path, exc)

    async def _run_two_way(self, explicit_resync: bool) -> int:
        """A two-way run (see above) recorded as one job; returns the job id."""
        config = self._profile
        filters = self._bisync_filters(await self.get_manual_flags())
        plan, reason = self._two_way_plan(filters)
        resyncing = explicit_resync or plan in ("resync", "flush")
        short = self._short_names(resync=explicit_resync or plan == "resync")
        async with self._db_session_factory() as session:
            job = SyncJob(
                direction="resync" if resyncing else "two_way",
                started_at=datetime.now(timezone.utc),
                status="running", files_changed=0, conflicts=0, errors=0,
                profile_id=self.profile_id,
            )
            session.add(job)
            await session.commit()
            job_id = job.id

        self._state.set_syncing(SyncState.SYNCING, job_id)
        self._job_recorded(job_id)
        recorder = BisyncRecorder(max_rows=self.max_recorded_changes)
        self._state.recorder = recorder  # its rclone stats are the live progress
        try:
            return await self._two_way_steps(job_id, recorder, explicit_resync, plan, reason, filters, short)
        except asyncio.CancelledError:
            stop = self._consume_stop()
            message = stop or ENGINE_STOPPED
            logger.warning("Two-way sync for '%s' (job %d): %s", config.slug, job_id, message)
            self._keep_listings_after_stop()
            try:
                await self._record_error(job_id, message, 0)
                conflicts = await self._record_two_way_conflicts(job_id, recorder.conflicts)
                await self._finish_job(job_id, "failed", recorder=recorder, conflicts=conflicts)
            except Exception as exc:  # never mask the stop itself
                logger.warning("Could not record stopped job %d: %s", job_id, exc)
            self._state.set_stopped(message)
            if stop is None:
                raise
            return job_id

    async def _two_way_steps(
        self, job_id: int, recorder: BisyncRecorder, explicit_resync: bool,
        plan: str, reason: str | None, filters: str, short: bool,
    ) -> int:
        """The steps of _run_two_way; ``short``: bisync runs on short names (see _short_names)."""
        config = self._profile
        if plan == "blocked" and not explicit_resync:
            await self._require_resync(job_id, recorder, reason or "", 0)
            return job_id

        resync_now = explicit_resync or plan == "resync"
        try:
            refusal, unmarked = await self._preflight_two_way(resync=resync_now)
        except RcloneError as exc:
            refusal, unmarked = f"Could not list {config.remote_dir}: {exc}", False
        if refusal is not None:
            await self._two_way_failed(job_id, recorder, refusal, 0)
            return job_id

        os.makedirs(self._workdir, exist_ok=True)
        # Only this engine runs bisync for this profile, under the sync lock:
        # a lock file left behind is from a killed run (--recover handles it).
        self._clear_workdir(("*.lck",))
        if resync_now:
            # A resync rewrites the listings; ones from other folders must
            # not make a later check think this pair was synced.
            self._clear_workdir(("*.lst*",))
        if unmarked:
            # --check-access needs the marker on both sides before any run.
            await self._write_sentinels()

        steps = [True] if resync_now else ([False, True] if plan == "flush" else [False])
        for resync in steps:
            if resync:
                await asyncio.to_thread(_write_text, os.path.join(self._workdir, BISYNC_FILTERS_FILE), filters)
            if not await self._bisync_with_retries(job_id, recorder, resync, short):
                return job_id
        if plan == "flush" and not explicit_resync:
            logger.info("Profile '%s': filters changed; synced with the old ones, then resynced", config.slug)

        self._write_bisync_state(pair=self._pair, resync_required=None,
                                 names="short" if short else "paths")
        conflicts = await self._record_two_way_conflicts(job_id, recorder.conflicts)
        await self._finish_job(job_id, "completed", recorder=recorder, conflicts=conflicts)
        self._state.set_idle(files_processed=recorder.total)
        self._state.resync_required = False
        self._skipped.clear()
        self._resume("two-way sync succeeded")
        await self._release_hold()
        logger.info("Two-way sync completed for '%s' (job %d, %d file(s) changed, %d conflict(s))",
                    config.slug, job_id, recorder.total, conflicts)
        direction = "resync" if explicit_resync or plan in ("resync", "flush") else "two-way"
        await self._emit_notification("sync_completed", direction=direction, files=recorder.total, conflicts=conflicts)
        if conflicts:
            await self._emit_notification("conflict_detected", count=conflicts, kept_both=True)
        await self._maybe_prune_trash("local")
        await self._maybe_prune_trash("remote")
        return job_id

    async def _bisync_with_retries(self, job_id: int, recorder: BisyncRecorder, resync: bool, short: bool) -> bool:
        """One bisync step with the profile's retries; False once the failure is recorded."""
        max_retries = self._profile.max_retries
        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            failed = recorder
            try:
                if not resync:
                    probe = BisyncRecorder(max_rows=0)
                    failed = probe
                    refusal = await self._two_way_delete_refusal(probe, short)
                    if refusal is not None:
                        await self._two_way_failed(job_id, recorder, refusal, attempt, pause=True)
                        return False
                    failed = recorder
                self._changing_files()
                await self._rclone.bisync(
                    self._profile.local_dir, self._profile.remote_dir,
                    workdir=self._workdir,
                    filters_file=os.path.join(self._workdir, BISYNC_FILTERS_FILE),
                    recorder=recorder, rclone_args=self._transfer_args,
                    backup_dirs=self._backup_dirs(), resync=resync,
                    short_names=self.profile_id if short else None,
                )
                return True
            except RcloneAuthError as exc:
                await self._two_way_failed(job_id, recorder, str(exc), attempt, event="auth_error")
                return False
            except RcloneError as exc:
                if failed.resync_needed:
                    await self._require_resync(job_id, recorder, failed.critical or str(exc), attempt)
                    return False
                if failed.too_many_deletes:  # bisync's own guard (OmniSync sets it to 100 %)
                    await self._two_way_failed(job_id, recorder, str(exc), attempt, pause=True)
                    return False
                last_error = exc
                logger.warning("Two-way sync attempt %d/%d for '%s' failed: %s",
                               attempt, max_retries, self._profile.slug, exc)
                await self._record_error(job_id, str(exc), attempt)
                if attempt < max_retries:
                    first = RATE_LIMIT_BASE_DELAY if isinstance(exc, RcloneRateLimitError) else 1.0
                    await asyncio.sleep(common.calculate_backoff_delay(attempt, first))
                    if self._stop_retrying():
                        break
        await self._two_way_failed(job_id, recorder, str(last_error), max_retries, record=False, retried=True)
        return False

    async def _two_way_delete_refusal(self, probe: BisyncRecorder, short: bool) -> str | None:
        """Why the next two-way run must not start because of the delete limit, or None.

        bisync's own --max-delete is a percentage of each side's files,
        which cannot express "at most 50 files" on a large tree. OmniSync
        keeps its absolute limit instead (the profile's --max-delete, else
        OMNISYNC_MAX_DELETE) and applies it to each side, BEFORE the run:
        a dry run in a copy of the workdir counts the deletions, and a run
        that would delete more on one side does not start at all. The dry
        run is skipped when no side's last listing holds more files than
        the limit. ``short``: as bisync is run (the dry run must find the
        same listings). Raises RcloneError when the dry run fails.
        """
        limit = self.max_delete
        if limit is None:
            return None
        listed = self._listed_files()
        if listed is not None and listed <= limit:
            return None
        await self._bisync_dry_run(probe, resync=False, filters=None, check_access=True, short=short)
        over = [(side, n) for side in ("local", "remote") if (n := probe.count(side, "deleted")) > limit]
        if not over:
            return None
        where = " and ".join(f"{n} file(s) in the {side} folder" for side, n in over)
        return (
            f"Stopped before changing anything: this two-way sync would delete {where}, more than "
            f"the limit of {limit} per side. Automatic syncing is paused. Check the folder: if the "
            "files were deleted on purpose, raise --max-delete for this profile (or delete them on "
            f"the other side too) and resume; if not, restore them (e.g. from {TRASH_DIR}) first."
        )

    async def _bisync_dry_run(
        self, recorder: BisyncRecorder, resync: bool, filters: str | None, check_access: bool, short: bool,
    ) -> None:
        """bisync --dry-run in a scratch copy of the workdir (no lock file, no listings touched).

        ``filters`` replaces the filters file of the copy (for a resync);
        None keeps the one of the last run. Raises RcloneError on failure.
        """
        with tempfile.TemporaryDirectory(prefix="omnisync-bisync-plan-") as tmp:
            def copy_state() -> None:
                if not os.path.isdir(self._workdir):
                    return
                for name in os.listdir(self._workdir):
                    src = os.path.join(self._workdir, name)
                    if name.endswith(".lck") or name.startswith("omnisync-state") or not os.path.isfile(src):
                        continue
                    shutil.copy2(src, os.path.join(tmp, name))

            await asyncio.to_thread(copy_state)
            filters_file = os.path.join(tmp, BISYNC_FILTERS_FILE)
            if filters is not None:
                await asyncio.to_thread(_write_text, filters_file, filters)
            await self._rclone.bisync(
                self._profile.local_dir, self._profile.remote_dir,
                workdir=tmp, filters_file=filters_file, recorder=recorder,
                rclone_args=self._profile.rclone_args, backup_dirs=self._backup_dirs(),
                resync=resync, dry_run=True, check_access=check_access,
                short_names=self.profile_id if short else None,
            )

    async def _require_resync(self, job_id: int, recorder: BisyncRecorder, reason: str, attempt: int) -> None:
        """Pause the profile until the user confirms a resync (never resync blindly)."""
        message = self._resync_message(reason or "rclone reported that a resync is needed")
        self._write_bisync_state(pair=self._pair, resync_required=reason or message)
        self._state.resync_required = True
        await self._two_way_failed(job_id, recorder, message, attempt, pause=True, event="resync_required")

    async def _two_way_failed(
        self, job_id: int, recorder: BisyncRecorder, message: str, attempt: int,
        pause: bool = False, event: str = "sync_failed", record: bool = True, retried: bool = False,
    ) -> None:
        """Record a failed or refused two-way run and report it.

        ``event``: sync_failed, auth_error, or resync_required (the profile is
        paused until the user confirms a resync). ``retried``: the failure
        is what was left after all the profile's attempts.
        """
        logger.error("Two-way sync for '%s' (job %d): %s", self._profile.slug, job_id, message)
        conflicts = await self._record_two_way_conflicts(job_id, recorder.conflicts)
        if record:
            await self._record_error(job_id, message, attempt)
        await self._finish_job(job_id, "failed", recorder=recorder, conflicts=conflicts)
        self._state.set_error(message)
        if pause:
            self._pause(message)
        if event in ("auth_error", "resync_required"):
            await self._emit_notification(event, error=message)
        else:
            await self._emit_notification(
                "sync_failed", direction="two-way", error=message, attempts=attempt if retried else None,
            )
        if conflicts:  # the files bisync kept in two versions before it failed
            await self._emit_notification("conflict_detected", count=conflicts, kept_both=True)

    async def _two_way_preview(self) -> TwoWayPreview:
        """What the next two-way run would do (dry run; changes nothing, takes no lock)."""
        config = self._profile
        limit = self.max_delete
        filters = self._bisync_filters(await self.get_manual_flags())
        plan, reason = self._two_way_plan(filters)
        if plan == "blocked":
            return TwoWayPreview(resync=True, resync_required=True, error=self._resync_message(reason or ""))
        resync = plan != "run"
        if not os.path.isdir(config.local_dir):
            return TwoWayPreview(resync=resync, error=f"Local folder '{config.local_dir}' is missing or not mounted.")
        try:
            local_marked = os.path.isfile(os.path.join(config.local_dir, SENTINEL_FILE))
            remote_marked = SENTINEL_FILE in await self._rclone.list_top_level(config.remote_dir)
        except RcloneError as exc:
            return TwoWayPreview(resync=resync, error=f"Could not list {config.remote_dir}: {exc}")
        if local_marked != remote_marked:
            side = "remote" if local_marked else "local"
            return TwoWayPreview(resync=resync, error=f"The sync marker {SENTINEL_FILE} is missing from the {side} folder.")
        recorder = BisyncRecorder(max_rows=0)
        try:
            await self._bisync_dry_run(
                recorder, resync=plan == "resync", filters=filters if plan == "resync" else None,
                check_access=local_marked, short=self._short_names(resync=plan == "resync"),
            )
        except RcloneError as exc:
            return TwoWayPreview(resync=resync, error=f"The two-way preview failed: {exc}")

        def counts(side: str) -> SyncPreviewCounts:
            deletes = recorder.count(side, "deleted")
            return SyncPreviewCounts(
                deletes=deletes, replaces=recorder.count(side, "modified"),
                creates=recorder.count(side, "created"),
                exceeds_max_delete=not resync and limit is not None and deletes > limit,
            )

        return TwoWayPreview(
            local=counts("local"), remote=counts("remote"),
            conflicts=recorder.changed_on_both, resync=resync,
        )
