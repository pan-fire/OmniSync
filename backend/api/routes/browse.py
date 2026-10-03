"""Directory browsing endpoints for OmniSync.

Provides local filesystem and remote directory listing so the UI
can offer a directory picker instead of requiring manual path entry.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Query

from backend.api.errors import SEE_LOG, api_error
from backend.api.schemas import BrowseResponse, DirEntry
from backend.services.path_guard import browse_roots
from backend.services.provider_registry import validate_remote_name
from backend.services.rclone import RcloneService
from backend.services.sync_engine import remote_join

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/browse", tags=["browse"])

_rclone_service: RcloneService | None = None


def set_rclone_service(service: RcloneService) -> None:
    """Set the rclone service reference."""
    global _rclone_service
    _rclone_service = service


@router.get("/local", response_model=BrowseResponse)
async def browse_local(
    path: str = Query("~", description="Directory path to list (within the allowed roots)"),
) -> BrowseResponse:
    """List subdirectories of a local path.

    Only returns directories (not files) since we're picking a sync folder.
    Resolves ~ to the user's home directory.
    """
    # Resolve ~ and normalize (symlinks too, so a link cannot escape a root)
    resolved = Path(os.path.expanduser(path)).resolve()
    roots = browse_roots()
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise api_error(
            403, "path_not_allowed",
            "Browsing is limited to: " + ", ".join(str(r) for r in roots)
            + ". Set OMNISYNC_BROWSE_ROOTS to allow other folders.",
        )

    if not resolved.is_dir():
        raise api_error(404, "directory_not_found", "Directory not found")

    entries: list[DirEntry] = []
    try:
        for item in sorted(resolved.iterdir()):
            # Only directories, skip hidden dirs starting with .
            if item.is_dir() and not item.name.startswith("."):
                entries.append(DirEntry(name=item.name, path=str(item)))
    except PermissionError:
        logger.warning("browse_local: permission denied for %s", resolved)
        raise api_error(403, "permission_denied", "Permission denied for this directory")

    # Parent directory, unless that would leave the allowed roots
    parent = str(resolved.parent) if resolved not in roots else None

    return BrowseResponse(
        current=str(resolved),
        parent=parent,
        entries=entries,
    )


@router.get("/remote", response_model=BrowseResponse)
async def browse_remote(
    path: str = Query("", description="Remote path like 'gdrive:' or 'gdrive:backup/docs'"),
) -> BrowseResponse:
    """List subdirectories on a remote.

    Uses the rclone service to list directories via provider API or rclone lsd.
    """
    if _rclone_service is None:
        raise api_error(503, "service_unavailable", "rclone service not available")

    if not path or ":" not in path:
        raise api_error(
            422, "invalid_remote_path",
            "Path must include remote name, e.g. 'gdrive:' or 'gdrive:backup'",
        )

    remote_name, remote_path = path.split(":", 1)
    if not validate_remote_name(remote_name):
        raise api_error(
            422, "invalid_remote_name",
            "Invalid remote name: use letters, digits, '_' and '-', not starting with '-'",
        )

    try:
        dirs = await _list_remote_dirs(remote_name, remote_path)
    except Exception as e:
        logger.warning("browse_remote failed for '%s': %s", path, e)
        raise api_error(500, "rclone_failed", f"Could not list the remote folder. {SEE_LOG}")

    # Build parent path
    if remote_path:
        parent_parts = remote_path.rstrip("/").rsplit("/", 1)
        parent = f"{remote_name}:{parent_parts[0]}" if len(parent_parts) > 1 else f"{remote_name}:"
    else:
        parent = None

    return BrowseResponse(
        current=path,
        parent=parent,
        entries=dirs,
    )


async def _list_remote_dirs(remote_name: str, remote_path: str) -> list[DirEntry]:
    """List directories on a remote using rclone lsd or provider API."""
    assert _rclone_service is not None

    full_remote = f"{remote_name}:{remote_path}" if remote_path else f"{remote_name}:"

    # Try rclone lsd first — works for all providers
    try:
        # After "--": no part of the caller's path can be read as an option.
        result = await _rclone_service._run(
            ["lsd", "--max-depth", "1"],
            use_config_args=False,
            timeout=15,
            positional=[full_remote],
        )
    except Exception as e:
        logger.warning("rclone lsd failed for '%s': %s", full_remote, e)
        return []

    entries: list[DirEntry] = []
    for line in result.stdout.strip().splitlines():
        # rclone lsd output format: "          -1 2024-01-15 10:30:45        -1 dirname"
        parts = line.strip().split()
        if len(parts) >= 4:
            dirname = " ".join(parts[4:])  # handle spaces in dir names
            # gdrive: + docs -> gdrive:docs, not gdrive:/docs: on SFTP and
            # local-backed remotes a leading / is the file system root.
            dir_path = remote_join(full_remote, dirname)
            entries.append(DirEntry(name=dirname, path=dir_path))

    return sorted(entries, key=lambda e: e.name)
