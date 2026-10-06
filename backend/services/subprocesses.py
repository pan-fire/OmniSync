"""Child processes run with a time limit, never left running after it."""

from __future__ import annotations

import asyncio


async def communicate_or_kill(
    proc: asyncio.subprocess.Process, timeout: float, input: bytes | None = None,
) -> tuple[bytes, bytes]:
    """``proc.communicate()`` within ``timeout`` seconds.

    ``asyncio.wait_for`` only cancels the wait: on its own it leaves a hung
    child running, with its pipes open, after the caller has moved on. Here
    a timeout, a cancellation of the caller or any other error kills the
    process and reaps it before the exception propagates.
    """
    try:
        communicate = proc.communicate() if input is None else proc.communicate(input)
        return await asyncio.wait_for(communicate, timeout=timeout)
    except BaseException:
        await kill_and_reap(proc)
        raise


async def kill_and_reap(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL ``proc`` (if it still runs) and wait for it, so no zombie is left."""
    try:
        proc.kill()
    except ProcessLookupError:
        pass  # it has exited already
    await proc.wait()
