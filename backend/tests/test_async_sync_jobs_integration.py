"""Sync starts run in the background: 202 with the job, clients follow the job.

POST /profiles/{slug}/sync/start and /sync/resync answer once rclone starts
changing files. Refusals decided before that (pause gate, pending resync,
sync marker, empty side, the two-way delete probe) are still answered in the
request itself; everything later is recorded on the job and in last_error.

Real rclone and a real migrated database, with the env fixture of
test_two_way_sync_integration.py (a "remote" of rclone type `local`).
"""

from __future__ import annotations

import asyncio
import os
import signal

from sqlalchemy import select

from backend.db.models import SyncError
from backend.services import sync_engine as sync_engine_module
from backend.services.rclone import SENTINEL_FILE
from backend.tests.sync_jobs import finished, settle, wait_for_job
from backend.tests import test_two_way_sync_integration as two_way
from backend.tests.test_two_way_sync_integration import files_under, synced, wait_for_transfer, write

pytestmark = two_way.pytestmark
env = two_way.env  # the fixture


async def job_errors(env, job_id: int) -> list[str]:
    async with env.factory() as session:
        rows = (await session.execute(select(SyncError).where(SyncError.job_id == job_id))).scalars().all()
        return [r.message for r in rows]


def slow_engine(env, **changes):
    """A two-way engine whose transfers take a while (40 MB at 8 MiB/s)."""
    (env.local / "big.bin").write_bytes(os.urandom(40_000_000))
    return env.engine(rclone_args=["--bwlimit", "8M"], **changes)


async def test_sync_now_answers_202_and_the_job_finishes(env):
    engine = await synced(env, {"a.txt": "A"})
    write(env.local, "b.txt", "B")

    resp = await env.client.post("/profiles/docs/sync/start", json={"direction": "two_way"})

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["state"] == "syncing" and body["error"] is None
    job = await finished(env.client, engine, resp)
    assert (job["id"], job["direction"], job["status"]) == (body["job_id"], "two_way", "completed")
    assert files_under(env.remote)["b.txt"] == "B"
    status = (await env.client.get("/profiles/docs/sync/status")).json()
    assert status["state"] == "idle" and status["current_job_id"] is None
    assert not engine.sync_lock.locked()


async def test_mirror_push_answers_202_and_the_job_finishes(env):
    engine = env.engine(sync_mode="mirror")
    for side in (env.local, env.remote):
        write(side, SENTINEL_FILE, "marker")
    write(env.local, "mine.txt", "local")

    resp = await env.client.post("/profiles/docs/sync/start", json={"direction": "push"})

    assert resp.status_code == 202, resp.text
    assert resp.json()["state"] == "pushing"
    assert (await finished(env.client, engine, resp))["status"] == "completed"
    assert files_under(env.remote)["mine.txt"] == "local"


async def test_the_delete_probe_still_refuses_in_the_request(env, monkeypatch):
    monkeypatch.setattr("backend.services.sync_engine.safety.DEFAULT_MAX_DELETE", 2)
    await synced(env, {f"doc{i}.txt": "precious" for i in range(6)})
    for i in range(5):
        (env.local / f"doc{i}.txt").unlink()

    resp = await env.client.post("/profiles/docs/sync/start", json={"direction": "two_way"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "error"
    assert "5 file(s) in the remote folder" in body["error"] and "limit of 2" in body["error"]
    job = (await env.client.get(f"/jobs/{body['job_id']}")).json()
    assert job["status"] == "failed"
    assert all(f"doc{i}.txt" in files_under(env.remote) for i in range(6))


async def test_marker_mismatch_still_refuses_in_the_request(env):
    engine = await synced(env, {"a.txt": "A"})
    (env.remote / SENTINEL_FILE).unlink()

    resp = await env.client.post("/profiles/docs/sync/start", json={"direction": "two_way"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "error" and SENTINEL_FILE in resp.json()["error"]
    assert engine._state.last_error == resp.json()["error"]


async def test_a_second_start_while_one_runs_is_refused(env):
    await synced(env, {"a.txt": "A"})
    slow = slow_engine(env)
    resp = await env.client.post("/profiles/docs/sync/start", json={"direction": "two_way"})
    assert resp.status_code == 202, resp.text
    try:
        again = await env.client.post("/profiles/docs/sync/start", json={"direction": "push", "force": True})
        assert again.status_code == 409
        assert "already running" in again.json()["detail"]
        resync = await env.client.post("/profiles/docs/sync/resync", json={"confirm": True})
        assert resync.status_code == 409
    finally:
        await env.client.post("/profiles/docs/sync/stop")
        await settle(slow)
    jobs = await env.jobs()
    assert [j.id for j in jobs][-1] == resp.json()["job_id"]  # no job for the refused starts


async def test_stop_ends_a_background_two_way_run_with_sigint(env, monkeypatch):
    await synced(env, {"a.txt": "A"})
    slow = slow_engine(env)
    signals: list[int] = []
    original = asyncio.subprocess.Process.send_signal

    def record(self, sig):
        signals.append(sig)
        return original(self, sig)

    monkeypatch.setattr(asyncio.subprocess.Process, "send_signal", record)

    resp = await env.client.post("/profiles/docs/sync/start", json={"direction": "two_way"})
    assert resp.status_code == 202, resp.text
    await wait_for_transfer(env)
    stop = await env.client.post("/profiles/docs/sync/stop")

    assert stop.status_code == 200 and stop.json()["message"] == "Sync stopped"
    assert signals and signals[0] == signal.SIGINT  # bisync shuts down gracefully
    job = await wait_for_job(env.client, resp.json()["job_id"])
    assert job["status"] == "failed"
    assert sync_engine_module.STOPPED_BY_USER in await job_errors(env, job["id"])
    await settle(slow)
    assert slow._state.state.value == "idle" and not slow.sync_lock.locked()
    # The profile keeps working: the next Sync now recovers without a resync.
    again = await env.client.post("/profiles/docs/sync/start", json={"direction": "two_way"})
    assert again.status_code == 202, again.text
    assert (await finished(env.client, slow, again))["status"] == "completed"


async def test_shutdown_stops_and_records_a_background_run(env):
    await synced(env, {"a.txt": "A"})
    slow = slow_engine(env)
    resp = await env.client.post("/profiles/docs/sync/start", json={"direction": "two_way"})
    assert resp.status_code == 202, resp.text
    await wait_for_transfer(env)

    await slow.stop()  # what the manager does for every engine at shutdown

    assert slow._launch is None and not slow.sync_lock.locked()
    job = (await env.client.get(f"/jobs/{resp.json()['job_id']}")).json()
    assert job["status"] == "failed" and job["finished_at"] is not None
    assert sync_engine_module.ENGINE_STOPPED in await job_errors(env, job["id"])


async def test_a_crash_during_the_run_is_recorded_on_the_job(env, monkeypatch, caplog):
    engine = await synced(env, {"a.txt": "A"})
    write(env.local, "b.txt", "B")

    async def broken(*args, **kwargs):
        await asyncio.sleep(0.2)
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(engine._rclone, "bisync", broken)
    resp = await env.client.post("/profiles/docs/sync/start", json={"direction": "two_way"})

    assert resp.status_code == 202, resp.text
    job = await finished(env.client, engine, resp)
    assert job["status"] == "failed"
    assert await job_errors(env, job["id"]) == [sync_engine_module.SYNC_CRASHED]
    status = (await env.client.get("/profiles/docs/sync/status")).json()
    assert status["state"] == "error" and status["last_error"] == sync_engine_module.SYNC_CRASHED
    assert "secret" not in str(status)
    assert "secret internal detail" in caplog.text  # logged, with its traceback
    assert not engine.sync_lock.locked()


async def test_a_crash_before_any_change_is_answered_in_the_request(env, monkeypatch):
    engine = await synced(env, {"a.txt": "A"})

    async def broken(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(engine, "_preflight_two_way", broken)
    resp = await env.client.post("/profiles/docs/sync/start", json={"direction": "two_way"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "error"
    assert resp.json()["error"] == sync_engine_module.SYNC_CRASHED
    job = (await env.client.get(f"/jobs/{resp.json()['job_id']}")).json()
    assert job["status"] == "failed"


async def test_a_crash_before_the_job_exists_is_a_500(env, monkeypatch):
    engine = await synced(env, {"a.txt": "A"})

    async def broken(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(engine, "get_manual_flags", broken)
    jobs_before = len(await env.jobs())
    resp = await env.client.post("/profiles/docs/sync/start", json={"direction": "two_way"})

    assert resp.status_code == 500
    assert "secret" not in resp.text and "log" in resp.json()["detail"]
    assert len(await env.jobs()) == jobs_before
    assert engine._state.last_error == sync_engine_module.SYNC_CRASHED
    assert not engine.sync_lock.locked()
