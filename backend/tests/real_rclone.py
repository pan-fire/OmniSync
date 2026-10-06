"""Profiles whose remote is a real local folder, synced by the real rclone binary.

Shared by the scale and hostile-filename tests: the "remote" is an rclone
remote of type `local` in a temp rclone.conf, the database an in-memory
SQLite one, and every sync below is executed by rclone itself.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from backend.db.models import Base, SyncError, SyncJob
from backend.models.profile_config import ProfileConfig
from backend.services.rclone import PARTIAL_NAME, SENTINEL_FILE, TRASH_DIR, RcloneService
from backend.services.sync_engine import SyncEngine

if shutil.which("rclone") is None and os.environ.get("OMNISYNC_REQUIRE_RCLONE") == "1":
    raise RuntimeError("OMNISYNC_REQUIRE_RCLONE=1 but rclone is not on PATH")
needs_rclone = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")


def digest(path: Path) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def tree(root: Path, trash: bool = False) -> dict[str, str]:
    """Relative path -> content digest of every file under root.

    The sync marker is left out, and the trash unless asked for. Paths are
    the raw (byte-preserving) names os.walk returns, so an NFC and an NFD
    spelling of a name stay two entries.
    """
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        if not trash and rel_dir == ".":
            dirnames[:] = [d for d in dirnames if d != TRASH_DIR]
        for name in filenames:
            rel = os.path.normpath(os.path.join(rel_dir, name))
            if rel != SENTINEL_FILE:
                out[rel] = digest(Path(dirpath, name))
    return out


def empty_dirs(root: Path) -> set[str]:
    """Relative paths of the directories under root (trash excluded) that hold nothing."""
    out = set()
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        if rel.split(os.sep)[0] == TRASH_DIR:
            continue
        if not dirnames and not filenames and rel != ".":
            out.add(rel)
    return out


def partials(*roots: Path) -> list[str]:
    """rclone's in-progress files (<name>.<8 hex>.partial) left anywhere under roots."""
    return [str(p) for root in roots for p in root.rglob("*.partial") if PARTIAL_NAME.fullmatch(p.name)]


def write(root: Path, rel: str, data: bytes | str, mtime: float | None = None) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@dataclass
class Env:
    """A profile on tmp/local and the folder tmp/remote, with its database."""

    local: Path
    remote: Path
    conf: Path
    factory: async_sessionmaker
    engines: list[SyncEngine] = field(default_factory=list)

    def engine(self, **changes) -> SyncEngine:
        """An engine for the profile (changes: ProfileConfig fields)."""
        config = ProfileConfig(
            profile_id=1, slug="docs", name="Docs",
            local_dir=str(self.local), remote_dir=f"testremote:{self.remote}", max_retries=1,
        )
        engine = SyncEngine(replace(config, **changes), RcloneService(rclone_config_path=str(self.conf)), self.factory)
        self.engines.append(engine)
        return engine

    async def job(self, job_id: int | None) -> SyncJob:
        assert job_id is not None
        async with self.factory() as session:
            job = await session.get(SyncJob, job_id)
        assert job is not None
        return job

    async def errors(self, job_id: int) -> list[str]:
        async with self.factory() as session:
            rows = (await session.execute(select(SyncError).where(SyncError.job_id == job_id))).scalars().all()
            return [r.message for r in rows]

    async def completed(self, job_id: int | None) -> SyncJob:
        """The job, which must have completed."""
        job = await self.job(job_id)
        assert job.status == "completed", (job.direction, await self.errors(job.id))
        return job


async def make_env(base: Path, monkeypatch) -> tuple[Env, AsyncEngine]:
    """An Env under base (bisync's workdir too); returns it and the database engine to dispose."""
    local, remote = base / "local", base / "remote"
    local.mkdir(parents=True)
    remote.mkdir()
    conf = base / "rclone.conf"
    conf.write_text("[testremote]\ntype = local\n")
    monkeypatch.setattr("backend.services.sync_engine.two_way.BISYNC_DIR", str(base / "bisync"))
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return Env(local, remote, conf, async_sessionmaker(db, expire_on_commit=False)), db
