"""Which local folders a profile, backup target or sync test may use.

OmniSync's data directory holds rclone.conf with the OAuth tokens of every
remote, the API token, the Web Push private key and the database. A profile
or local backup target pointing at it (or at a folder inside or above it)
would copy those secrets to a remote or into a backup, so such paths are
always refused.

When ``OMNISYNC_BROWSE_ROOTS`` is set, new local paths must also lie inside
one of those roots (the folders the directory picker shows). Without it the
picker falls back to the home folder and /sync, but other paths stay allowed.

All checks compare real paths, so a symlink cannot hide where a path leads.
"""

from __future__ import annotations

import os
from pathlib import Path

# Each entry: (environment variable, default, whether it names a file whose
# folder is the data directory, or the directory itself).
_DATA_LOCATIONS: tuple[tuple[str, str, bool], ...] = (
    ("OMNISYNC_DB_PATH", "/data/omnisync/omnisync.db", True),
    ("OMNISYNC_CONFIG_PATH", "/data/omnisync/config.toml", True),
    ("OMNISYNC_RCLONE_CONFIG", "/data/omnisync/rclone.conf", True),
    ("OMNISYNC_API_TOKEN_FILE", "/data/omnisync/api-token", True),
    ("OMNISYNC_LOG_PATH", "/data/omnisync/omnisync.log", True),
    ("OMNISYNC_VAPID_DIR", "/data/omnisync/vapid", False),
    ("OMNISYNC_BISYNC_DIR", "/data/omnisync/bisync", False),
)


def _real(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.realpath(os.path.expanduser(os.fspath(path))))


def data_dirs() -> list[Path]:
    """The folders holding OmniSync's own files (secrets, database, state)."""
    dirs: list[Path] = []
    for env, default, is_file in _DATA_LOCATIONS:
        value = os.environ.get(env, "").strip() or default
        folder = _real(os.path.dirname(value) if is_file else value)
        if folder not in dirs:
            dirs.append(folder)
    return dirs


def browse_roots() -> list[Path]:
    """Folders the directory picker may list: the user's home and /sync by default.

    OMNISYNC_BROWSE_ROOTS (os.pathsep-separated) replaces the defaults.
    """
    configured = os.environ.get("OMNISYNC_BROWSE_ROOTS", "")
    raw = [p for p in configured.split(os.pathsep) if p.strip()] or [os.path.expanduser("~"), "/sync"]
    return [_real(p.strip()) for p in raw]


def configured_browse_roots() -> list[Path] | None:
    """The roots from OMNISYNC_BROWSE_ROOTS, or None when it is not set."""
    if not os.environ.get("OMNISYNC_BROWSE_ROOTS", "").strip(os.pathsep + " "):
        return None
    return browse_roots()


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def check_not_data_dir(value: str, field: str) -> str:
    """Raise ValueError when ``value`` is, lies inside or contains a data directory."""
    path = _real(value)
    for data in data_dirs():
        # A data directory of "/" would rule out every folder; only refuse it itself.
        if path == data or (data in path.parents and data.parent != data):
            raise ValueError(
                f"{field} may not be OmniSync's data directory {data} or a folder inside it: "
                "it holds the remote credentials and the API token"
            )
        if path in data.parents:
            raise ValueError(
                f"{field} may not contain OmniSync's data directory {data}: "
                "it holds the remote credentials and the API token"
            )
    return value


def check_in_browse_roots(value: str, field: str) -> str:
    """Raise ValueError when OMNISYNC_BROWSE_ROOTS is set and ``value`` is outside it."""
    roots = configured_browse_roots()
    if roots is None:
        return value
    path = _real(value)
    if not any(_within(path, root) for root in roots):
        raise ValueError(
            f"{field} must be inside the allowed folders ({', '.join(str(r) for r in roots)}). "
            "Set OMNISYNC_BROWSE_ROOTS to allow other folders."
        )
    return value


def same_path(a: str, b: str) -> bool:
    """Whether two local paths lead to the same place."""
    return _real(a) == _real(b)


async def warn_about_stored_paths(session_factory) -> None:
    """Log stored profiles and local backup targets the checks above would refuse now.

    Stored values keep working (only new values are checked), except that
    restores into the data directory are refused; the warning tells the
    admin which ones to move.
    """
    import logging

    from sqlalchemy import select

    from backend.db.models import BackupTarget, SyncProfile

    log = logging.getLogger(__name__)
    async with session_factory() as session:
        profiles = (await session.execute(select(SyncProfile))).scalars().all()
        targets = (await session.execute(
            select(BackupTarget).where(BackupTarget.target_type == "local")
        )).scalars().all()
    stored = [(f"Profile '{p.slug}'", "local_dir", p.local_dir) for p in profiles]
    stored += [(f"Backup target '{t.name}'", "target_path", t.target_path) for t in targets]
    for owner, field, path in stored:
        for check in (check_not_data_dir, check_in_browse_roots):
            try:
                check(path, field)
            except ValueError as exc:
                log.warning("%s was saved before this check and keeps working, but %s", owner, exc)
