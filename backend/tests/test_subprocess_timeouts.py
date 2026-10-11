"""Child processes that outlive their time limit are killed and reaped.

asyncio.wait_for only stops waiting: on its own it leaves a hung child
running (rclone with no route out, a notifier stuck on D-Bus), one per
request. These tests start a real process that sleeps far longer than the
limit and check that it is gone - not running, and not a zombie either -
once the caller has its answer.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
from collections.abc import Iterator

import httpx
import pytest

from backend.api.routes import health
from backend.services.notification_channels.platforms.linux import LinuxNotifier
from backend.services.notification_events import NotificationSeverity
from backend.services.rclone import RcloneService
from backend.services.subprocesses import communicate_or_kill

SLEEPER = [sys.executable, "-c", "import time; time.sleep(120)"]
REAL_EXEC = asyncio.create_subprocess_exec
REAL_WAIT_FOR = asyncio.wait_for


def gone(pid: int) -> bool:
    """True when ``pid`` is neither running nor a zombie (kill 0 still reaches a zombie)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


@pytest.fixture
def sleepers(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[asyncio.subprocess.Process]]:
    """Every create_subprocess_exec starts the sleeper instead; limits are cut to 0.2 s."""
    started: list[asyncio.subprocess.Process] = []

    async def spawn(*_argv, **kwargs) -> asyncio.subprocess.Process:
        kwargs.pop("env", None)
        proc = await REAL_EXEC(*SLEEPER, **kwargs)
        started.append(proc)
        return proc

    async def quick_wait_for(aw, timeout):
        return await REAL_WAIT_FOR(aw, timeout=min(timeout, 0.2) if timeout is not None else None)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(asyncio, "wait_for", quick_wait_for)
    yield started
    for proc in started:  # never leave one behind, even when a test fails
        if proc.returncode is None:
            proc.kill()


def assert_all_gone(started: list[asyncio.subprocess.Process], count: int) -> None:
    assert len(started) == count
    for proc in started:
        assert proc.returncode is not None, "the child is still running"
        assert gone(proc.pid), "the child was not reaped"


async def test_timeout_kills_and_reaps(sleepers) -> None:
    proc = await asyncio.create_subprocess_exec("x", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    with pytest.raises(asyncio.TimeoutError):
        await communicate_or_kill(proc, timeout=5)
    assert_all_gone(sleepers, 1)


async def test_cancelled_caller_kills_and_reaps(sleepers) -> None:
    """A request cancelled while it waits (client gone, shutdown) takes its child with it."""
    proc = await asyncio.create_subprocess_exec("x", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    started = asyncio.Event()

    async def caller() -> None:
        started.set()
        await communicate_or_kill(proc, timeout=None)  # type: ignore[arg-type]

    task = asyncio.create_task(caller())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_all_gone(sleepers, 1)


async def test_process_that_already_ended_is_left_alone() -> None:
    """A finished process is reaped once; killing it again is no error."""
    proc = await REAL_EXEC(sys.executable, "-c", "print('hi')", stdout=asyncio.subprocess.PIPE)
    out, _ = await communicate_or_kill(proc, timeout=30)
    assert out.strip() == b"hi" and proc.returncode == 0 and gone(proc.pid)


async def test_health_network_reaps_a_hung_rclone(sleepers, monkeypatch: pytest.MonkeyPatch) -> None:
    """/health/network: both rclone checks time out, and neither rclone is left running."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("192.0.2.10", port))])

    class Offline:
        def __init__(self, **_kwargs) -> None: ...
        async def __aenter__(self) -> Offline:
            return self
        async def __aexit__(self, *_exc) -> None: ...
        async def get(self, _url: str):
            raise OSError("offline")

    monkeypatch.setattr(httpx, "AsyncClient", Offline)
    body = await health.network_check()
    assert body["rclone_network"] == {"ok": False, "error": "timed out after 10s"}
    assert body["rclone_version"] == {"ok": False, "error": "TimeoutError"}
    assert_all_gone(sleepers, 2)


async def test_obscure_reaps_a_hung_rclone(sleepers, tmp_path) -> None:
    service = RcloneService(rclone_config_path=str(tmp_path / "rclone.conf"))
    with pytest.raises(asyncio.TimeoutError):
        await service.obscure("hunter2")
    assert_all_gone(sleepers, 1)


async def test_notifier_reaps_a_hung_notify_send(sleepers) -> None:
    with pytest.raises(RuntimeError, match="timed out"):
        await LinuxNotifier().send("Title", "Body", NotificationSeverity.INFO)
    assert_all_gone(sleepers, 1)
