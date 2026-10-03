"""Follow a sync the API started in the background, the way the clients do."""

from __future__ import annotations

import asyncio

from httpx import AsyncClient, Response

from backend.services.sync_engine import SyncEngine


async def wait_for_job(client: AsyncClient, job_id: int, timeout: float = 60) -> dict:
    """Poll GET /jobs/{job_id} until the job is no longer running; return it."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        resp = await client.get(f"/jobs/{job_id}")
        assert resp.status_code == 200, resp.text
        job = resp.json()
        if job["status"] != "running":
            return job
        assert loop.time() < deadline, f"job {job_id} still running after {timeout}s"
        await asyncio.sleep(0.05)


async def settle(engine: SyncEngine, timeout: float = 60) -> None:
    """Wait until the engine's background sync (if any) has fully ended."""
    launch = engine._launch
    if launch is not None and launch.task is not None:
        await asyncio.wait_for(asyncio.shield(launch.task), timeout)


async def finished(client: AsyncClient, engine: SyncEngine, resp: Response) -> dict:
    """The job of a sync start (202 or 200), once it has ended."""
    assert resp.status_code in (200, 202), resp.text
    job = await wait_for_job(client, resp.json()["job_id"])
    await settle(engine)
    return job
