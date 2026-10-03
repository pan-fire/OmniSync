"""Running rclone: commands and flags, the process environment, streaming output, stopping it."""

from __future__ import annotations

import asyncio
import configparser
import os
import re
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.exceptions import RcloneError
from backend.services.rclone.common import DEFAULT_TIMEOUT, STREAM_LINE_LIMIT, logger
from backend.services.rclone.errors import _command_line, classify_failure
from backend.services.rclone.recorder import ChangeRecorder


@dataclass
class RcloneResult:
    """Result of an rclone subprocess execution."""

    stdout: str
    stderr: str
    return_code: int
    elapsed_seconds: float

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "return_code": self.return_code,
            "elapsed_seconds": self.elapsed_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RcloneResult:
        """Deserialize from dictionary."""
        return cls(
            stdout=data["stdout"],
            stderr=data["stderr"],
            return_code=data["return_code"],
            elapsed_seconds=data["elapsed_seconds"],
        )


# Flags that set rclone's verbosity; rclone refuses -v together with any of them.
_VERBOSITY_FLAGS = ("-v", "-vv", "-vvv", "-q", "--verbose", "--quiet", "--log-level")


# Live progress: a stats line every second, logged at NOTICE so a profile
# that sets --log-level NOTICE still reports progress.
STATS_ARGS = ("--stats", "1s", "--stats-log-level", "NOTICE")


def json_log_args(rclone_args: list[str] | None) -> list[str]:
    """Flags that make rclone log every transfer and delete as JSON on stderr.

    ``-v`` (INFO) is added only when the profile's own flags do not set the
    log level already: rclone exits with "Can't set -v and --log-level".
    With a profile level above INFO, no changes are recorded. rclone also
    logs its stats every second (see TransferProgress); the recorders keep
    only the latest of them, so memory stays flat on long runs.
    """
    args = ["--use-json-log", *STATS_ARGS]
    if not any(a.split("=", 1)[0] in _VERBOSITY_FLAGS for a in (rclone_args or [])):
        args.append("-v")
    return args


def without_flag(args: list[str], flag: str) -> list[str]:
    """``args`` without ``flag`` (``--flag value`` and ``--flag=value``)."""
    out: list[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg == flag:
            skip = True
            continue
        if arg.startswith(flag + "="):
            continue
        out.append(arg)
    return out


# Remotes OmniSync defines itself, only in the environment of the rclone
# processes that use them (RCLONE_CONFIG_<NAME>_<OPTION>), never in
# rclone.conf and never on the command line: an encrypted backup target's
# crypt remote carries its (merely obscured, so reversible) passphrase, and a
# command line is readable by every local user through the process list,
# while a process's environment is readable only by its owner. Two kinds:
#   * omnisync_backup_crypt_<target id>: an encrypted backup target;
#   * omnisync_bisync_<profile id>_local / _remote: the short names a
#     two-way sync of long paths runs on (see bisync_names.py).
# Users cannot create or import remotes with these prefixes (rclone matches
# these names case-insensitively, and an environment definition would
# override a rclone.conf section of the same name).
ENV_REMOTE_PREFIX = "omnisync_backup_crypt_"
BISYNC_REMOTE_PREFIX = "omnisync_bisync_"
RESERVED_REMOTE_PREFIXES = (ENV_REMOTE_PREFIX, BISYNC_REMOTE_PREFIX)
_ENV_REMOTE_RE = re.compile(
    rf"(?<![A-Za-z0-9_-])({ENV_REMOTE_PREFIX}\d+|{BISYNC_REMOTE_PREFIX}\d+_(?:local|remote)):", re.IGNORECASE,
)
_env_remotes: dict[str, dict[str, str]] = {}
RESERVED_NAME_MESSAGE = (
    f"names starting with '{ENV_REMOTE_PREFIX}' or '{BISYNC_REMOTE_PREFIX}' are reserved for OmniSync's "
    "own remotes (encrypted backups, two-way syncs of long paths)"
)


def is_reserved_remote_name(name: str) -> bool:
    """Whether ``name`` is reserved for OmniSync's own environment-defined remotes."""
    return name.lower().replace("-", "_").startswith(RESERVED_REMOTE_PREFIXES)


def define_env_remote(suffix: str | int, options: dict[str, str], prefix: str = ENV_REMOTE_PREFIX) -> str:
    """Define remote ``<prefix><suffix>`` for the rclone processes that name it; return ``"<name>:"``.

    ``prefix`` is one of RESERVED_REMOTE_PREFIXES. Defining it again
    replaces its options. Option values are passed as they are (no
    quoting), so commas, colons and quotes in them need no escaping.
    """
    name = f"{prefix}{suffix}"
    if prefix not in RESERVED_REMOTE_PREFIXES or not _ENV_REMOTE_RE.fullmatch(f"{name}:"):
        raise ValueError(f"Invalid environment remote: {name!r}")
    _env_remotes[name] = dict(options)
    return f"{name}:"


def process_env(cmd: list[str]) -> dict[str, str] | None:
    """The environment for running ``cmd``: None (inherit) unless it names an environment-defined remote.

    Then it is this process's environment plus the RCLONE_CONFIG_* variables
    of exactly the remotes ``cmd`` names, so no other rclone process ever
    receives them.
    """
    extra: dict[str, str] = {}
    for arg in cmd:
        for match in _ENV_REMOTE_RE.finditer(arg):
            name = match.group(1).lower()
            for option, value in _env_remotes.get(name, {}).items():
                extra[f"RCLONE_CONFIG_{name.upper()}_{option.upper()}"] = value
    return {**os.environ, **extra} if extra else None


class RcloneBase:
    """The state and the process handling RcloneService's parts share.

    RcloneService (service.py) is assembled from mixins, one per area,
    each derived from this class. For the type checker only, it declares
    the methods one mixin calls on another (see the TYPE_CHECKING block).
    """

    def __init__(self, timeout: int = DEFAULT_TIMEOUT, rclone_config_path: str | None = None) -> None:
        self.timeout = timeout
        self.rclone_config_path = rclone_config_path or os.environ.get(
            "OMNISYNC_RCLONE_CONFIG", "/data/omnisync/rclone.conf"
        )

    def _base_cmd(self) -> list[str]:
        """Return the base rclone command with the --config flag."""
        return ["rclone", "--config", self.rclone_config_path]

    def _build_command(self, base_args: list[str], rclone_filter: list[str] | None = None,
                       rclone_args: list[str] | None = None, positional: list[str] | None = None) -> list[str]:
        """Build full rclone command with optional filters and extra args.

        ``positional`` (source/dest paths) goes after ``--`` so that no path can
        ever be read as an rclone option.
        """
        cmd = self._base_cmd() + base_args

        for f in (rclone_filter or []):
            # One token, so a rule such as "- *.tmp" can never be read as a flag.
            cmd.append(f"--filter={f}")

        cmd.extend(rclone_args or [])
        if positional:
            cmd.append("--")
            cmd.extend(positional)
        return cmd

    async def _run(self, args: list[str], use_config_args: bool = True, timeout: int | None = None,
                   rclone_filter: list[str] | None = None, rclone_args: list[str] | None = None,
                   positional: list[str] | None = None, no_timeout: bool = False) -> RcloneResult:
        """Execute an rclone command and return the result.

        Args:
            args: The rclone subcommand and arguments.
            use_config_args: Whether to append filters/extra args from config.
                             Set to False for config management commands.
            timeout: Per-call timeout override in seconds. Defaults to self.timeout.
            positional: Source/dest paths, placed after ``--``.
            no_timeout: Run without a wall-clock limit (transfers). rclone's own
                --timeout/--contimeout still abort stalled connections.
        """
        effective_timeout: int | None = None if no_timeout else (timeout if timeout is not None else self.timeout)
        if use_config_args:
            cmd = self._build_command(args, rclone_filter=rclone_filter, rclone_args=rclone_args, positional=positional)
        else:
            cmd = self._base_cmd() + args + (["--", *positional] if positional else [])
        logger.info("Running rclone command (timeout=%s): %s",
                    f"{effective_timeout}s" if effective_timeout else "none", _command_line(cmd))

        start = time.monotonic()
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=process_env(cmd),
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=effective_timeout
            )
        except asyncio.TimeoutError:
            await self._terminate(proc)
            raise RcloneError(f"rclone command timed out after {effective_timeout}s")
        except asyncio.CancelledError:
            # The engine was stopped: end rclone cleanly so it removes its
            # partial files instead of leaving them to be synced later.
            await self._terminate(proc)
            raise
        elapsed = time.monotonic() - start

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return_code = proc.returncode or 0

        result = RcloneResult(
            stdout=stdout,
            stderr=stderr,
            return_code=return_code,
            elapsed_seconds=elapsed,
        )

        if return_code != 0:
            raise classify_failure(return_code, stderr)

        return result

    async def _stream(
        self, cmd: list[str],
        on_stdout: Callable[[str], None], on_stderr: Callable[[str], None],
        timeout: float | None,
        stop_signal: int = signal.SIGTERM, stop_grace: float = 10.0,
    ) -> int:
        """Run rclone, handing each stdout/stderr line to a callback; return the exit code.

        Nothing is accumulated here, so memory stays flat however many files
        rclone reports. Only the line's newline is removed: leading and
        trailing spaces belong to file names.
        """
        logger.info("Running rclone command (timeout=%s): %s",
                    f"{timeout}s" if timeout else "none", _command_line(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STREAM_LINE_LIMIT,
            env=process_env(cmd),
        )

        async def pump(stream: asyncio.StreamReader | None, handler: Callable[[str], None]) -> None:
            assert stream is not None
            while True:
                try:
                    raw = await stream.readline()
                except ValueError:
                    # One line longer than the limit: report it as unparseable.
                    handler("\x00<overlong line>")
                    continue
                if not raw:
                    return
                handler(raw.decode("utf-8", errors="replace").removesuffix("\n"))

        try:
            await asyncio.wait_for(
                asyncio.gather(pump(proc.stdout, on_stdout), pump(proc.stderr, on_stderr), proc.wait()),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            await self._terminate(proc, grace=stop_grace, sig=stop_signal)
            raise RcloneError(f"rclone command timed out after {timeout}s")
        except asyncio.CancelledError:
            # Stopped by the user or shutdown: end rclone cleanly so it
            # removes its partial files. Its output is still read meanwhile,
            # so a full pipe never blocks the shutdown.
            drain = asyncio.ensure_future(asyncio.gather(
                pump(proc.stdout, lambda _line: None), pump(proc.stderr, on_stderr), return_exceptions=True,
            ))
            await self._terminate(proc, grace=stop_grace, sig=stop_signal)
            try:
                await asyncio.wait_for(drain, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                drain.cancel()
            raise
        return proc.returncode or 0

    async def _run_logged(
        self, args: list[str], recorder: ChangeRecorder,
        rclone_filter: list[str] | None = None, rclone_args: list[str] | None = None,
        positional: list[str] | None = None,
    ) -> RcloneResult:
        """Run a transfer with --use-json-log, feeding every log line to ``recorder``.

        No wall-clock limit (rclone's own --timeout aborts stalled
        connections). Raises like _run on a non-zero exit.
        """
        cmd = self._build_command([*args, *json_log_args(rclone_args)], rclone_filter=rclone_filter,
                                  rclone_args=rclone_args, positional=positional)
        start = time.monotonic()
        rc = await self._stream(cmd, lambda _line: None, recorder.feed, timeout=None)
        stderr = recorder.failure_text()
        if rc != 0:
            raise classify_failure(rc, stderr)
        return RcloneResult(stdout="", stderr=stderr, return_code=0, elapsed_seconds=time.monotonic() - start)

    @staticmethod
    async def _terminate(
        proc: asyncio.subprocess.Process | None, grace: float = 10.0, sig: int = signal.SIGTERM,
    ) -> None:
        """Signal rclone (SIGTERM), then SIGKILL if it has not exited after ``grace`` seconds."""
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.send_signal(sig)
            try:
                await asyncio.wait_for(proc.wait(), timeout=grace)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        except ProcessLookupError:
            pass

    if TYPE_CHECKING:
        # Provided by the mixins.
        # auth.py
        async def _ensure_fresh_token(self, remote_name: str) -> None: ...
        # config.py
        async def _update_config(self, change: Callable[[configparser.RawConfigParser], object]) -> None: ...
