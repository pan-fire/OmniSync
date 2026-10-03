"""The sync lock of a profile: an asyncio.Lock that knows whether an operation waits for it."""

from __future__ import annotations

import asyncio
from typing import Literal


class SyncLock(asyncio.Lock):
    """One rclone operation per profile at a time, and whether one waits for its turn.

    asyncio.Lock does not say whether anyone waits for it; an operation that
    starts in the background (SyncEngine.launch(), a backup or restore the
    API starts) must not jump that queue, nor wait in it: it is refused
    while the lock is held or wanted (see is_busy). ``waiting`` counts the
    acquire() calls that have not returned yet. One that finds the lock free
    returns without suspending, so it is never seen waiting.
    """

    def __init__(self) -> None:
        super().__init__()
        self._waiting = 0

    async def acquire(self) -> Literal[True]:
        self._waiting += 1
        try:
            return await super().acquire()
        finally:
            self._waiting -= 1

    @property
    def waiting(self) -> int:
        """How many acquire() calls wait for the lock now."""
        return self._waiting

    def busy(self) -> bool:
        """Held, or wanted by an operation that waits for it."""
        return self.locked() or self._waiting > 0


def is_busy(lock: asyncio.Lock) -> bool:
    """Whether an operation holds or waits for ``lock`` (a plain asyncio.Lock: holds only)."""
    return lock.busy() if isinstance(lock, SyncLock) else lock.locked()
