"""Read-only rclone commands: listings, file heads, sizes and storage usage."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

from backend.exceptions import RcloneAuthError, RcloneError
from backend.services.rclone.common import TRASH_FILTER, _require_remote_name, logger
from backend.services.rclone.errors import _command_line, classify_failure
from backend.services.rclone.process import RcloneBase, process_env


class ListingMixin(RcloneBase):
    """lsjson, lsf, cat, size and about."""

    async def cat_head(self, path: str, count: int) -> bytes:
        """The first ``count`` bytes of a file (rclone cat --head): reads no more than that."""
        cmd = self._base_cmd() + ["cat", "--head", str(count), "--", path]
        logger.info("Running rclone command: %s", _command_line(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=process_env(cmd),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            await self._terminate(proc)
            raise RcloneError(f"rclone command timed out after {self.timeout}s")
        if proc.returncode:
            raise classify_failure(proc.returncode, stderr.decode("utf-8", errors="replace"))
        return stdout

    async def lsjson(self, path: str, rclone_filter: list[str] | None = None,
                     rclone_args: list[str] | None = None) -> list[dict]:
        """List files with metadata using rclone lsjson --recursive.

        Returns list of {Path, Size, ModTime, IsDir} dicts. The trash folder
        is never listed; ``rclone_filter`` adds the profile's own rules,
        ``rclone_args`` flags (only ones that filter belong here).
        """
        result = await self._run(
            ["lsjson", "--recursive"],
            rclone_filter=[TRASH_FILTER, *(rclone_filter or [])],
            rclone_args=rclone_args,
            positional=[path],
        )
        try:
            entries: list[dict] = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError) as e:
            raise RcloneError(f"Failed to parse lsjson output: {e}")
        return entries

    async def lsjson_paths(self, root: str, file_paths: list[str]) -> dict[str, dict]:
        """Current metadata of exactly these files under root: path -> lsjson entry.

        Paths that do not exist (or are directories) are absent from the
        result; a missing root gives {}. Used to re-check a cached diff
        before acting on it.
        """
        if not file_paths:
            return {}
        list_file = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        try:
            for path in file_paths:
                list_file.write(path.lstrip("/") + "\n")
            list_file.close()
            try:
                result = await self._run(
                    ["lsjson", "-R", "--files-only", "--files-from-raw", list_file.name],
                    use_config_args=False,
                    positional=[root],
                )
            except RcloneAuthError:
                raise
            except RcloneError as exc:
                if "directory not found" in str(exc).lower():
                    return {}
                raise
        finally:
            os.unlink(list_file.name)
        try:
            entries = json.loads(result.stdout)
        except ValueError as exc:
            raise RcloneError(f"Failed to parse lsjson output: {exc}")
        return {e["Path"]: e for e in entries if isinstance(e, dict) and not e.get("IsDir")}

    async def list_dirs(self, path: str) -> list[str]:
        """Names of the folders directly inside ``path``; [] if it does not exist."""
        try:
            result = await self._run(["lsf", "--dirs-only", "--max-depth", "1"],
                                     use_config_args=False, positional=[path])
        except RcloneAuthError:
            raise
        except RcloneError as exc:
            if "directory not found" in str(exc).lower():
                return []
            raise
        return [line.removesuffix("/") for line in result.stdout.split("\n") if line.endswith("/")]

    async def existing_paths(self, root: str, file_paths: list[str]) -> set[str]:
        """Which of these paths (relative to root) exist there as files.

        rclone copy --files-from-raw silently skips listed files that do not
        exist, so callers use this to confirm each file actually arrived.
        """
        list_file = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        try:
            for path in file_paths:
                list_file.write(path.lstrip("/") + "\n")
            list_file.close()
            result = await self._run(
                ["lsf", "-R", "--files-only", "--files-from-raw", list_file.name],
                use_config_args=False,
                positional=[root],
            )
        finally:
            os.unlink(list_file.name)
        # split("\n"), not splitlines(): \r and other separators are legal in names
        return {line for line in result.stdout.split("\n") if line}

    async def existing_items(self, root: str, rules: list[str]) -> set[str]:
        """The files and folders under root that these include rules match.

        ``rules``: ``+ /<escaped path>`` for a file, ``+ /<escaped path>/``
        for a folder; everything else is excluded, so rclone descends only
        into the folders on the way. Returns the listed paths, folders with
        a trailing ``/`` (their parents too); a missing root gives none.
        """
        if not rules:
            return set()
        filter_file = tempfile.NamedTemporaryFile(mode="w", suffix=".filter", delete=False, encoding="utf-8")
        try:
            filter_file.write("".join(f"{rule}\n" for rule in [*rules, "- **"]))
            filter_file.close()
            try:
                result = await self._run(["lsf", "-R", "--filter-from", filter_file.name],
                                         use_config_args=False, positional=[root])
            except RcloneAuthError:
                raise
            except RcloneError as exc:
                if "directory not found" in str(exc).lower():
                    return set()
                raise
        finally:
            os.unlink(filter_file.name)
        return {line for line in result.stdout.split("\n") if line}

    async def list_top_level(self, path: str) -> list[str]:
        """Names directly inside ``path`` (trash excluded); [] if it does not exist."""
        try:
            result = await self._run(
                ["lsf", "--max-depth", "1"],
                rclone_filter=[TRASH_FILTER],
                positional=[path],
            )
        except RcloneAuthError:
            raise
        except RcloneError as exc:
            if "directory not found" in str(exc).lower():
                return []
            raise
        return [line.removesuffix("/") for line in result.stdout.split("\n") if line]

    async def about(self, remote: str) -> dict[str, int | None]:
        """Run 'rclone about <remote>: --json' and return storage info.

        Returns dict with keys: total, used, free, trashed (values in bytes or None).
        Raises RcloneError if the remote is unreachable.
        """
        _require_remote_name(remote)
        result = await self._run(
            ["about", "--json"], use_config_args=False, timeout=30, positional=[f"{remote}:"],
        )
        data = json.loads(result.stdout)
        return {
            "total": data.get("total"),
            "used": data.get("used"),
            "free": data.get("free"),
            "trashed": data.get("trashed"),
        }

    async def get_dir_size(self, path: str) -> int:
        """Run 'rclone size <path> --json' and return total bytes."""
        result = await self._run(
            ["size", "--json"], use_config_args=False, positional=[path], no_timeout=True
        )
        data = json.loads(result.stdout)
        return data.get("bytes", 0)
