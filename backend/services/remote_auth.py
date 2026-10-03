"""Which remotes the provider last refused to sign in to.

A connection test (POST /remotes/{name}/test, /wizard/test), a remote
health check (GET /health/remotes) or a sync that ends in an rclone
authentication error marks its remote; the next success, and a reconnect
or a credential change, clears it. The web UI and the TUI then offer to
reconnect (OAuth) or edit the credentials of that remote.

The state lives in memory: after a restart it is rebuilt by the next test,
health check or sync. That is enough for a hint, and needs no database
column.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

_failed: dict[str, datetime] = {}
_lock = threading.Lock()


def mark_auth_failed(name: str) -> None:
    """Record that the provider refused the credentials of remote ``name``."""
    with _lock:
        _failed[name] = datetime.now(timezone.utc)


def clear_auth_failed(name: str) -> None:
    """Forget an auth failure of ``name`` (it worked again, or was reconnected)."""
    with _lock:
        _failed.pop(name, None)


def auth_failed(name: str) -> bool:
    """Whether the last sign-in of remote ``name`` was refused."""
    with _lock:
        return name in _failed


def reset() -> None:
    """Forget every failure (tests)."""
    with _lock:
        _failed.clear()
