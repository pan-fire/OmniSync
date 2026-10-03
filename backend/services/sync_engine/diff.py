"""Comparing the two sides: status, check, preview and the cached enhanced diff."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from backend.api.schemas import (
    ChangeCategory,
    DiffResponse,
    DiffSummary,
    FileDiff,
    SyncCheckResponse,
    SyncMode,
    SyncPreviewCounts,
    SyncPreviewResponse,
    SyncStatusResponse,
)
from backend.db.models import ManualFlag
from backend.services.sync_engine.common import logger
from backend.services.sync_engine.reporting import ReportingMixin


class DiffMixin(ReportingMixin):
    """Diffs, their cache, the pending count and the manual flags they leave out."""

    async def get_status(self) -> SyncStatusResponse:
        """Return current sync state."""
        return self._state.to_status_response()

    async def check_diff(self, timeout: float | None = None) -> SyncCheckResponse:
        """Compare local and remote directories without transferring files."""
        result = await self._rclone.check_diff(
            self._profile.local_dir, self._profile.remote_dir,
            rclone_filter=self._profile.rclone_filter, rclone_args=self._profile.rclone_args, timeout=timeout,
        )
        response = SyncCheckResponse(**result)
        if response.error:
            # A failed comparison says nothing about pending changes; callers
            # decide (the startup check pauses).
            return response
        paths = [*response.local_only, *response.remote_only, *response.differ]
        not_pending = self._skipped | set(await self.get_manual_flags()) if paths else set()
        pending = sum(1 for p in paths if p not in not_pending)
        self._state.pending_changes = pending
        self._pause_if_needed(pending)
        return response

    async def preview_sync(self) -> SyncPreviewResponse:
        """Count what a push and a pull (and, for a two-way profile, the next
        two-way sync) would delete, replace and create now.

        Side-effect free: unlike check_diff() and enhanced_diff() it leaves
        pending_changes, the cached diff, the conflict records and the pause
        state alone, and it takes no lock (rclone only compares). Files a
        bulk sync excludes (manual flags, unresolved conflicts of the cached
        diff) are left out of the counts, as the sync leaves them alone.
        """
        config = self._profile
        max_delete = self.max_delete
        mode = SyncMode(config.sync_mode)
        two_way = await self._two_way_preview() if config.two_way else None
        result = await self._rclone.check_diff(
            config.local_dir, config.remote_dir,
            rclone_filter=config.rclone_filter, rclone_args=config.rclone_args,
        )
        check = SyncCheckResponse(**result)
        if check.error:
            return SyncPreviewResponse(max_delete=max_delete, error=check.error, sync_mode=mode, two_way=two_way)

        excluded = set(await self.get_manual_flags())
        diff = self._state.cached_diff
        if diff is not None:
            excluded.update(f.path for f in diff.files if f.is_conflict)
        local_only = [p for p in check.local_only if p not in excluded]
        remote_only = [p for p in check.remote_only if p not in excluded]
        differ = [p for p in check.differ if p not in excluded]
        skipped = len(check.local_only) + len(check.remote_only) + len(check.differ)
        skipped -= len(local_only) + len(remote_only) + len(differ)

        def counts(deletes: int, creates: int) -> SyncPreviewCounts:
            return SyncPreviewCounts(
                deletes=deletes, replaces=len(differ), creates=creates,
                exceeds_max_delete=max_delete is not None and deletes > max_delete,
            )

        return SyncPreviewResponse(
            push=counts(deletes=len(remote_only), creates=len(local_only)),
            pull=counts(deletes=len(local_only), creates=len(remote_only)),
            excluded=skipped, max_delete=max_delete, sync_mode=mode, two_way=two_way,
        )

    @staticmethod
    def _parse_rclone_modtime(modtime_str: str) -> datetime:
        """Parse an rclone ModTime string into a timezone-aware UTC datetime.

        rclone lsjson returns ModTime like "2024-01-15T14:30:22.000000000".
        We truncate nanosecond precision to microseconds for fromisoformat(),
        then ensure the result is UTC-aware for comparison with last_sync.
        """
        # Truncate nanoseconds: keep up to 6 fractional digits
        if "." in modtime_str:
            base, frac = modtime_str.split(".", 1)
            # Strip any trailing timezone info from frac
            tz_suffix = ""
            for tz_char in ("Z", "+", "-"):
                if tz_char in frac:
                    idx = frac.index(tz_char)
                    tz_suffix = frac[idx:]
                    frac = frac[:idx]
                    break
            frac = frac[:6]
            modtime_str = f"{base}.{frac}{tz_suffix}"

        # Replace trailing Z with +00:00 for fromisoformat
        modtime_str = modtime_str.replace("Z", "+00:00")

        dt = datetime.fromisoformat(modtime_str)
        # If naive, assume UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    @staticmethod
    def _classify_differ(
        local_mod: datetime,
        remote_mod: datetime,
        last_sync: datetime | None,
    ) -> ChangeCategory:
        """Classify a file that differs on both sides using modification times.

        Uses last_sync as the baseline:
        - Both modified after last_sync → modified_both (conflict)
        - Only local newer → modified_local
        - Only remote newer → modified_remote
        - No last_sync → fall back to direct comparison
        """
        if last_sync is not None:
            local_newer = local_mod > last_sync
            remote_newer = remote_mod > last_sync
            if local_newer and remote_newer:
                return ChangeCategory.MODIFIED_BOTH
            if local_newer:
                return ChangeCategory.MODIFIED_LOCAL
            if remote_newer:
                return ChangeCategory.MODIFIED_REMOTE
            # Neither is newer than last_sync but content differs —
            # treat as modified_both (conflict) to be safe
            return ChangeCategory.MODIFIED_BOTH
        else:
            # No last_sync — fall back to direct ModTime comparison
            if local_mod > remote_mod:
                return ChangeCategory.MODIFIED_LOCAL
            if remote_mod > local_mod:
                return ChangeCategory.MODIFIED_REMOTE
            return ChangeCategory.MODIFIED_BOTH

    async def enhanced_diff(self) -> DiffResponse:
        """Two-phase diff: check --combined + lsjson metadata enrichment.

        1. Run rclone check --combined to get local_only/remote_only/differ
        2. Run rclone lsjson on local and remote to get Size/ModTime metadata
        3. Load manual flags from DB
        4. Classify each file, build FileDiff entries
        5. Build summary, cache result, return DiffResponse
        """
        config = self._profile

        # Phase 1: Get change detection via existing check_diff
        check_result = await self._rclone.check_diff(
            config.local_dir, config.remote_dir,
            rclone_filter=config.rclone_filter, rclone_args=config.rclone_args,
        )
        check = SyncCheckResponse(**check_result)

        # A failed check fails the diff. The previous cached diff is dropped
        # too: per-file actions must never run on a stale picture.
        if check.error:
            self._state.clear_diff_cache()
            return DiffResponse(error=check.error)

        # Phase 2: Get metadata via lsjson. Without it every differing file
        # would look one-sided, so a failed listing fails the diff as well.
        try:
            local_entries = await self._rclone.lsjson(config.local_dir, rclone_filter=config.rclone_filter)
            remote_entries = await self._rclone.lsjson(config.remote_dir, rclone_filter=config.rclone_filter)
        except Exception as exc:
            logger.warning("Diff listing failed for '%s': %s", config.slug, exc)
            self._state.clear_diff_cache()
            return DiffResponse(error=f"Could not list files for the diff: {exc}")

        # Build lookup dicts: path → {Size, ModTime}
        local_meta: dict[str, dict] = {
            e["Path"]: e for e in local_entries if not e.get("IsDir", False)
        }
        remote_meta: dict[str, dict] = {
            e["Path"]: e for e in remote_entries if not e.get("IsDir", False)
        }

        # Load this profile's manual flags from DB
        manual_flag_paths = set(await self.get_manual_flags())

        last_sync = self._state.last_sync
        files: list[FileDiff] = []

        # Process local_only files
        for path in check.local_only:
            meta = local_meta.get(path, {})
            local_mod = None
            if "ModTime" in meta:
                try:
                    local_mod = self._parse_rclone_modtime(meta["ModTime"])
                except (ValueError, TypeError):
                    pass
            files.append(FileDiff(
                path=path,
                category=ChangeCategory.LOCAL_ONLY,
                local_size=meta.get("Size"),
                remote_size=None,
                local_mod_time=local_mod,
                remote_mod_time=None,
                is_conflict=False,
                manual_flag=path in manual_flag_paths,
            ))

        # Process remote_only files
        for path in check.remote_only:
            meta = remote_meta.get(path, {})
            remote_mod = None
            if "ModTime" in meta:
                try:
                    remote_mod = self._parse_rclone_modtime(meta["ModTime"])
                except (ValueError, TypeError):
                    pass
            files.append(FileDiff(
                path=path,
                category=ChangeCategory.REMOTE_ONLY,
                local_size=None,
                remote_size=meta.get("Size"),
                local_mod_time=None,
                remote_mod_time=remote_mod,
                is_conflict=False,
                manual_flag=path in manual_flag_paths,
            ))

        # Process differ files — classify using ModTime + last_sync
        for path in check.differ:
            l_meta = local_meta.get(path, {})
            r_meta = remote_meta.get(path, {})

            local_mod = None
            if "ModTime" in l_meta:
                try:
                    local_mod = self._parse_rclone_modtime(l_meta["ModTime"])
                except (ValueError, TypeError):
                    pass

            remote_mod = None
            if "ModTime" in r_meta:
                try:
                    remote_mod = self._parse_rclone_modtime(r_meta["ModTime"])
                except (ValueError, TypeError):
                    pass

            # Classify based on timestamps
            if local_mod is not None and remote_mod is not None:
                category = self._classify_differ(local_mod, remote_mod, last_sync)
            elif local_mod is not None:
                category = ChangeCategory.MODIFIED_LOCAL
            elif remote_mod is not None:
                category = ChangeCategory.MODIFIED_REMOTE
            else:
                category = ChangeCategory.MODIFIED_BOTH

            files.append(FileDiff(
                path=path,
                category=category,
                local_size=l_meta.get("Size"),
                remote_size=r_meta.get("Size"),
                local_mod_time=local_mod,
                remote_mod_time=remote_mod,
                is_conflict=(category == ChangeCategory.MODIFIED_BOTH),
                manual_flag=path in manual_flag_paths,
            ))

        # Conflict records follow the full picture (skipped files included).
        await self._record_conflicts(files)

        # Skip decisions last as long as the resolution: a skipped file that
        # still differs stays out of the diff; one that no longer differs is
        # forgotten.
        present = {f.path for f in files}
        self._skipped &= present
        files = [f for f in files if f.path not in self._skipped]

        summary = self._summarize(files)
        response = DiffResponse(files=files, summary=summary)

        # Cache result and update pending changes
        self._state.cache_diff(response)
        self._state.pending_changes = self._pending_count(files)
        self._pause_if_needed(self._state.pending_changes)

        return response

    @staticmethod
    def _summarize(files: list[FileDiff]) -> DiffSummary:
        """Per-category counts of a diff."""
        summary = DiffSummary(total=len(files))
        for f in files:
            if f.category == ChangeCategory.LOCAL_ONLY:
                summary.local_only += 1
            elif f.category == ChangeCategory.REMOTE_ONLY:
                summary.remote_only += 1
            elif f.category == ChangeCategory.MODIFIED_LOCAL:
                summary.modified_local += 1
            elif f.category == ChangeCategory.MODIFIED_REMOTE:
                summary.modified_remote += 1
            elif f.category == ChangeCategory.MODIFIED_BOTH:
                summary.modified_both += 1
            if f.manual_flag:
                summary.manual += 1
        return summary

    def _pending_count(self, files: list[FileDiff]) -> int:
        """Differences that block resuming: neither manually flagged nor skipped."""
        return sum(1 for f in files if not f.manual_flag and f.path not in self._skipped)

    async def get_manual_flags(self) -> list[str]:
        """Return the file paths this profile has flagged for manual handling."""
        async with self._db_session_factory() as session:
            result = await session.execute(
                select(ManualFlag.file_path).where(ManualFlag.profile_id == self.profile_id)
            )
            return list(result.scalars().all())

    async def clear_manual_flag(self, file_path: str) -> bool:
        """Remove this profile's manual flag for a specific file path.

        Returns True if the flag was found and deleted, False if not found.
        """
        async with self._db_session_factory() as session:
            result = await session.execute(
                select(ManualFlag).where(
                    ManualFlag.profile_id == self.profile_id,
                    ManualFlag.file_path == file_path,
                )
            )
            flag = result.scalar_one_or_none()
            if flag is None:
                return False
            await session.delete(flag)
            await session.commit()
            return True

    def _remove_resolved_from_diff(self, resolved_paths: list[str]) -> None:
        """Remove resolved file paths from cached diff and update pending_changes.

        This is the same logic already used by the skip handler, extracted for reuse
        by push, pull, and keep_both handlers.
        """
        resolved_set = set(resolved_paths)
        diff = self._state.cached_diff
        if diff is None:
            return

        remaining_files = [f for f in diff.files if f.path not in resolved_set]
        self._state.cached_diff = DiffResponse(files=remaining_files, summary=self._summarize(remaining_files))
        self._state.pending_changes = self._pending_count(remaining_files)
