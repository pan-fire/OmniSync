"""Health endpoints for OmniSync.

GET /health is the liveness/readiness probe (public, used by the container
health check): it only looks at local state, so it is cheap and never calls a
cloud provider. It answers 200 with status "ok" when the database responds and
the rclone binary is on PATH, and 503 with status "degraded" otherwise, so
``urlopen`` in the compose health check fails for a broken backend. Both
answers carry the release version (backend/version.py).

GET /health/remotes checks the remotes that running profiles use (provider
API calls, may refresh OAuth tokens) and GET /health/network diagnoses
outbound connectivity. Both need the API token.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.schemas import HealthResponse, RemoteHealth, RemoteHealthResponse
from backend.db.database import get_session
from backend.services import remote_auth
from backend.services.sync_engine_manager import SyncEngineManager
from backend.version import get_version

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level reference to the engine manager, set by main.py at startup
_manager: SyncEngineManager | None = None
_started_at: datetime | None = None


def set_manager(manager: SyncEngineManager | None) -> None:
    """Set the engine manager reference for health checks."""
    global _manager, _started_at
    _manager = manager
    if manager is not None:
        _started_at = datetime.now(timezone.utc)
    else:
        _started_at = None


def rclone_on_path() -> bool:
    """Whether the rclone binary is available; independent of any profile."""
    return shutil.which("rclone") is not None


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "Degraded: database or rclone unavailable"}},
)
async def health_check(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    """Report whether the backend can work: database and rclone binary."""
    db_ok = False
    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        logger.warning("Database health check failed", exc_info=True)

    rclone_installed = rclone_on_path()
    if not rclone_installed:
        logger.warning("Health check: rclone is not on PATH")

    uptime_seconds = 0.0
    if _started_at is not None:
        uptime_seconds = (datetime.now(timezone.utc) - _started_at).total_seconds()

    healthy = db_ok and rclone_installed
    body = HealthResponse(
        status="ok" if healthy else "degraded",
        rclone_installed=rclone_installed,
        uptime_seconds=uptime_seconds,
        database_ok=db_ok,
        version=get_version(),
    )
    return JSONResponse(body.model_dump(), status_code=200 if healthy else 503)


@router.get("/health/remotes", response_model=RemoteHealthResponse)
async def remotes_health() -> RemoteHealthResponse:
    """Check every remote that a running profile syncs with.

    This calls the providers (and may refresh OAuth tokens), so it is not part
    of the liveness probe.
    """
    if _manager is None:
        return RemoteHealthResponse()

    users: dict[str, list[str]] = {}
    rclone = None
    for slug, engine in _manager.engines.items():
        remote_dir = getattr(engine._profile, "remote_dir", "") or ""
        if ":" not in remote_dir:
            continue
        users.setdefault(remote_dir.split(":", 1)[0], []).append(slug)
        rclone = rclone or engine._rclone
    if rclone is None:
        return RemoteHealthResponse()

    async def check(name: str) -> bool:
        try:
            return bool(await rclone.check_remote(name))
        except Exception as exc:
            logger.warning("Remote health check for '%s' failed: %s", name, exc)
            return False

    names = sorted(users)
    results = await asyncio.gather(*(check(n) for n in names))
    return RemoteHealthResponse(remotes=[
        RemoteHealth(remote=n, accessible=ok, profiles=users[n], auth_error=not ok and remote_auth.auth_failed(n))
        for n, ok in zip(names, results, strict=True)
    ])


def _failure(check: str, exc: BaseException) -> dict[str, object]:
    """Diagnostic result without exception text; the text goes to the log."""
    logger.warning("Network check %s failed: %s", check, exc)
    return {"ok": False, "error": type(exc).__name__}


@router.get("/health/network")
async def network_check() -> dict:
    """Diagnose outbound network connectivity from inside the container.

    Tests Python httpx and rclone binary against well-known HTTPS endpoints.
    Useful for debugging Docker networking issues.
    """
    import httpx

    results: dict[str, object] = {}

    # 1. Python httpx → Google
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://www.googleapis.com/drive/v3/about?fields=kind")
            results["httpx_google"] = {"status": resp.status_code, "ok": resp.status_code in (200, 401, 403)}
    except Exception as e:
        results["httpx_google"] = _failure("httpx_google", e)

    # 2. Python httpx → Cloudflare (simple connectivity check)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://1.1.1.1/cdn-cgi/trace")
            results["httpx_cloudflare"] = {"status": resp.status_code, "ok": resp.status_code == 200}
    except Exception as e:
        results["httpx_cloudflare"] = _failure("httpx_cloudflare", e)

    # 3. Rclone binary → version (local, no network)
    try:
        proc = await asyncio.create_subprocess_exec(
            "rclone", "version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        version_line = stdout.decode().splitlines()[0] if stdout else "unknown"
        results["rclone_version"] = {"ok": True, "version": version_line}
    except Exception as e:
        results["rclone_version"] = _failure("rclone_version", e)

    # 4. Rclone binary → outbound HTTPS via "rclone lsf :http: --http-url"
    #    Uses rclone's built-in :http: backend to fetch a public URL,
    #    which tests real outbound HTTPS from the Go binary.
    try:
        proc = await asyncio.create_subprocess_exec(
            "rclone", "lsf", ":http:", "--http-url", "https://example.com", "--max-depth", "1",
            "--contimeout", "5s", "--timeout", "5s",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        rc = proc.returncode
        if rc != 0:
            logger.warning("Network check rclone_network: exit %s: %s", rc, stderr.decode()[:300])
        results["rclone_network"] = {"ok": rc == 0, "return_code": rc}
    except asyncio.TimeoutError:
        results["rclone_network"] = {"ok": False, "error": "timed out after 10s"}
    except Exception as e:
        results["rclone_network"] = _failure("rclone_network", e)

    # 5. DNS resolution check
    import socket
    try:
        addr = socket.getaddrinfo("www.googleapis.com", 443)[0][4][0]
        results["dns_google"] = {"ok": True, "resolved_to": addr}
    except Exception as e:
        results["dns_google"] = _failure("dns_google", e)

    return results

