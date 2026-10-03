"""OmniSync's rclone.conf: locked read-modify-write updates and the remotes in it."""

from __future__ import annotations

import asyncio
import configparser
import io
import os
import threading
from collections.abc import Callable
from pathlib import Path

from backend.api.schemas import RemoteResponse
from backend.exceptions import RcloneError
from backend.services import remote_auth
from backend.services.rclone.common import _require_remote_name, logger
from backend.services.rclone.process import RcloneBase

# OmniSync's read-modify-write cycles of an rclone.conf (token refresh,
# creating and deleting a remote) hold the lock of that file, so two of them
# cannot interleave and drop each other's change. They run in worker
# threads: a caller cancelled while waiting cannot leave the lock held, and
# the event loop never blocks on it. rclone processes that save a refreshed
# token themselves do not take it; rclone writes its config atomically, and
# OmniSync refreshes tokens before starting rclone (_ensure_fresh_token), so
# that is rare.
_config_locks: dict[str, threading.Lock] = {}
_config_locks_guard = threading.Lock()


def config_file_lock(path: str) -> threading.Lock:
    """The lock serialising OmniSync's updates of the rclone config at ``path``."""
    key = os.path.realpath(path)
    with _config_locks_guard:
        return _config_locks.setdefault(key, threading.Lock())


class ConfigMixin(RcloneBase):
    """Listing, creating, changing and deleting remotes in rclone.conf."""

    async def list_remotes(self) -> list[RemoteResponse]:
        """List configured remotes with their types from the config file."""
        result = await self._run(["listremotes"], use_config_args=False)

        # Read types from config file
        cfg = configparser.RawConfigParser()
        cfg.optionxform = str  # type: ignore[assignment]  # preserve key casing
        try:
            cfg.read(self.rclone_config_path)
        except Exception:
            pass

        remotes: list[RemoteResponse] = []
        for line in result.stdout.strip().splitlines():
            name = line.strip().rstrip(":")
            if name:
                remote_type = cfg.get(name, "type", fallback="unknown")
                remotes.append(RemoteResponse(name=name, type=remote_type))
        return remotes

    async def create_remote(
        self, name: str, remote_type: str, params: dict[str, str]
    ) -> None:
        """Create an rclone remote by writing directly to the config file.

        Uses RawConfigParser (no %-interpolation) to safely handle OAuth
        tokens that contain special characters like %, {, }.
        """
        from backend.services.provider_registry import validate_remote_name

        if not validate_remote_name(name):
            raise ValueError(
                "Remote name must contain only alphanumeric characters, hyphens, and underscores"
            )

        # Check uniqueness
        existing = await self.list_remotes()
        if any(r.name == name for r in existing):
            raise ValueError(f"Remote '{name}' already exists")

        def add(config: configparser.RawConfigParser) -> None:
            if config.has_section(name):  # created since the check above
                raise ValueError(f"Remote '{name}' already exists")
            config[name] = {"type": remote_type}
            for key, value in params.items():
                config[name][key] = value

        await self._update_config(add)
        logger.info("Created remote '%s' (type=%s) in %s", name, remote_type, self.rclone_config_path)

        # Verify rclone can parse the new section. Its output holds the
        # remote's secrets, so only success or failure is logged.
        try:
            await self._run(["config", "show", name], use_config_args=False, timeout=10)
        except RcloneError:
            logger.error("rclone cannot read the new remote '%s'; config may be malformed", name)

    def _read_config(self) -> configparser.RawConfigParser:
        """rclone.conf as a RawConfigParser (empty if the file does not exist).

        RawConfigParser has no %-interpolation, which would break JSON
        tokens; keys keep their casing. A malformed file raises, so it is
        never overwritten with less than it held.
        """
        config = configparser.RawConfigParser()
        config.optionxform = str  # type: ignore[assignment]
        config.read(self.rclone_config_path)
        return config

    def _write_config(self, config: configparser.RawConfigParser) -> None:
        """Write rclone.conf readable by the owner only: it holds OAuth tokens and keys.

        The write is atomic, and the previous file is kept as rclone.conf.bak.
        """
        from backend.security import write_secret_file

        buf = io.StringIO()
        config.write(buf)
        write_secret_file(Path(self.rclone_config_path), buf.getvalue(), keep_backup=True)

    async def _update_config(self, change: Callable[[configparser.RawConfigParser], object]) -> None:
        """Read rclone.conf, apply ``change`` and write it back, holding the file's lock.

        Runs in a worker thread (see config_file_lock). If ``change``
        raises, nothing is written.
        """
        def update() -> None:
            with config_file_lock(self.rclone_config_path):
                config = self._read_config()
                change(config)
                self._write_config(config)

        await asyncio.to_thread(update)

    async def obscure(self, value: str) -> str:
        """Return rclone's obscured form of a password (what rclone.conf expects).

        The value is passed on stdin, never on the command line, so it does
        not show up in the process list.
        """
        proc = await asyncio.create_subprocess_exec(
            "rclone", "obscure", "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(value.encode()), timeout=10)
        if proc.returncode != 0:
            raise RcloneError(f"rclone obscure failed (exit {proc.returncode})")
        return stdout.decode().strip()

    async def delete_remote(self, name: str) -> None:
        """Remove a remote's section from rclone.conf.

        Done here rather than with ``rclone config delete`` (which does the
        same) so it holds the config lock and keeps rclone.conf.bak.
        """
        existing = await self.list_remotes()
        if not any(r.name == name for r in existing):
            raise ValueError(f"Remote '{name}' not found")

        def remove(config: configparser.RawConfigParser) -> None:
            if not config.remove_section(name):
                raise ValueError(f"Remote '{name}' not found")

        await self._update_config(remove)
        remote_auth.clear_auth_failed(name)
        logger.info("Deleted remote '%s' from %s", name, self.rclone_config_path)

    async def remote_section(self, name: str) -> dict[str, str] | None:
        """A copy of the remote's settings in rclone.conf (``type`` included), or None.

        The values include secrets and tokens: callers must not return or
        log them.
        """
        _require_remote_name(name)

        def read() -> dict[str, str] | None:
            config = self._read_config()
            return dict(config.items(name)) if config.has_section(name) else None

        return await asyncio.to_thread(read)

    async def update_remote(
        self, name: str, remote_type: str, set_values: dict[str, str], remove: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        """Change some settings of an existing remote, holding the config lock.

        Sets ``set_values`` and removes the keys in ``remove``; every other
        setting (a token, options the wizard has no field for) stays. Raises
        ValueError if the remote does not exist (anymore) or is no longer of
        ``remote_type``; ``type`` itself is never changed.
        """
        _require_remote_name(name)
        if "type" in set_values or "type" in remove:
            raise ValueError("A remote's type cannot be changed")

        def change(config: configparser.RawConfigParser) -> None:
            if not config.has_section(name):
                raise ValueError(f"Remote '{name}' not found")
            if config.get(name, "type", fallback="") != remote_type:
                raise ValueError(f"Remote '{name}' is not of type {remote_type}")
            for key in remove:
                config.remove_option(name, key)
            for key, value in set_values.items():
                config.set(name, key, value)

        await self._update_config(change)
        logger.info("Updated remote '%s' (changed: %s; removed: %s)", name,
                    ", ".join(sorted(set_values)) or "-", ", ".join(sorted(remove)) or "-")

    async def add_remotes(self, sections: dict[str, dict[str, str]]) -> None:
        """Add several remotes in one locked update: all of them or none.

        Each value maps option to value, ``type`` included, written as given
        (an imported rclone.conf is already obscured). Raises ValueError,
        writing nothing, if a name is invalid or a remote of that name exists.
        """
        for name in sections:
            _require_remote_name(name)

        def add(config: configparser.RawConfigParser) -> None:
            clashes = [n for n in sections if config.has_section(n)]
            if clashes:
                raise ValueError(f"Remote(s) already exist: {', '.join(clashes)}")
            for name, options in sections.items():
                config.add_section(name)
                for key, value in options.items():
                    config.set(name, key, value)

        await self._update_config(add)
        logger.info("Added %d remote(s) to %s: %s", len(sections), self.rclone_config_path, ", ".join(sections))
