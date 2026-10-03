"""The release version: one VERSION file, reported publicly by GET /health."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend import version
from backend.api.routes import health

REPO_ROOT = Path(__file__).resolve().parents[2]
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


@pytest.fixture(autouse=True)
def fresh_version_cache():
    version.get_version.cache_clear()
    yield
    version.get_version.cache_clear()


def test_version_file_is_semver():
    assert SEMVER.match(version.VERSION_FILE.read_text().strip())


def test_get_version_reads_the_repository_version_file():
    assert version.VERSION_FILE == REPO_ROOT / "VERSION"
    assert version.get_version() == (REPO_ROOT / "VERSION").read_text().strip()


def test_missing_version_file_reports_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(version, "VERSION_FILE", tmp_path / "VERSION")
    assert version.get_version() == version.UNKNOWN_VERSION


def test_blank_version_file_reports_unknown(monkeypatch, tmp_path):
    (tmp_path / "VERSION").write_text("\n")
    monkeypatch.setattr(version, "VERSION_FILE", tmp_path / "VERSION")
    assert version.get_version() == version.UNKNOWN_VERSION


def test_web_ui_package_version_matches():
    # scripts/release/prepare.sh keeps these in step; the release workflow
    # refuses a tag when they differ.
    package = json.loads((REPO_ROOT / "frontend" / "package.json").read_text())
    assert package["version"] == version.get_version()


async def test_health_reports_the_version_without_a_token(test_client, monkeypatch):
    monkeypatch.setattr(health.shutil, "which", lambda name: f"/usr/bin/{name}")
    from backend.main import app

    # A client without the token (test_client only sets up the database):
    # /health is public, and so is the version.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.json()["version"] == version.get_version()


async def test_degraded_health_still_reports_the_version(test_client, monkeypatch):
    monkeypatch.setattr(health.shutil, "which", lambda name: None)
    response = await test_client.get("/health")
    assert response.status_code == 503
    assert response.json()["version"] == version.get_version()
