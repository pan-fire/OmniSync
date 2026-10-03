"""scripts/release: the version bump (prepare.sh), the release notes
(changelog-notes.sh) and the GitHub release (publish_github_release.py)."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PREPARE = REPO_ROOT / "scripts" / "release" / "prepare.sh"
NOTES = REPO_ROOT / "scripts" / "release" / "changelog-notes.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

CHANGELOG = """# Changelog

Intro text.

## [Unreleased]

### Added

- A new thing.

### Fixed

- A bug.

## [0.9.0] - 2026-09-27

### Added

- The first release.

[Unreleased]: https://github.com/pan-fire/OmniSync/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/pan-fire/OmniSync/releases/tag/v0.9.0
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "VERSION").write_text("0.9.0\n")
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG)
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text(
        '{\n  "name": "frontend",\n  "version": "0.9.0",\n  "private": true\n}\n'
    )
    return tmp_path


def prepare(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "OMNISYNC_RELEASE_ROOT": str(repo)}
    return subprocess.run(["bash", str(PREPARE), *args], env=env, capture_output=True, text=True)


def notes(version: str, changelog: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(NOTES), version, str(changelog)], capture_output=True, text=True)


def snapshot(repo: Path) -> dict[str, str]:
    return {p: (repo / p).read_text() for p in ("VERSION", "CHANGELOG.md", "frontend/package.json")}


def test_prepare_bumps_every_version_and_moves_unreleased(repo):
    result = prepare(repo, "0.10.0", "--date", "2026-10-01")
    assert result.returncode == 0, result.stderr

    assert (repo / "VERSION").read_text() == "0.10.0\n"
    package = (repo / "frontend" / "package.json").read_text()
    assert json.loads(package)["version"] == "0.10.0"
    assert '"private": true' in package  # the rest of the file is untouched

    changelog = (repo / "CHANGELOG.md").read_text()
    assert "## [Unreleased]\n\n## [0.10.0] - 2026-10-01\n\n### Added\n\n- A new thing." in changelog
    assert changelog.index("## [0.10.0]") < changelog.index("## [0.9.0]")
    assert "[Unreleased]: https://github.com/pan-fire/OmniSync/compare/v0.10.0...HEAD\n" in changelog
    assert "[0.10.0]: https://github.com/pan-fire/OmniSync/compare/v0.9.0...v0.10.0\n" in changelog

    # The new section is exactly what the Unreleased section held.
    released = notes("0.10.0", repo / "CHANGELOG.md")
    assert released.returncode == 0, released.stderr
    assert released.stdout == "### Added\n\n- A new thing.\n\n### Fixed\n\n- A bug.\n"


FIRST_CHANGELOG = """# Changelog

## [Unreleased]

### Added

- Everything so far.

[Unreleased]: https://github.com/pan-fire/OmniSync/commits/HEAD
"""


def test_prepare_makes_the_first_release_without_a_previous_tag(repo):
    # Before the first release there is no tag to compare with: VERSION is
    # the next release's development version and the changelog has only
    # Unreleased, linked to the commit history.
    (repo / "VERSION").write_text("0.10.0-dev\n")
    (repo / "CHANGELOG.md").write_text(FIRST_CHANGELOG)
    result = prepare(repo, "0.10.0", "--date", "2026-10-02")
    assert result.returncode == 0, result.stderr

    assert (repo / "VERSION").read_text() == "0.10.0\n"
    changelog = (repo / "CHANGELOG.md").read_text()
    assert changelog.endswith(
        "## [Unreleased]\n\n## [0.10.0] - 2026-10-02\n\n### Added\n\n- Everything so far.\n\n"
        "[Unreleased]: https://github.com/pan-fire/OmniSync/compare/v0.10.0...HEAD\n"
        "[0.10.0]: https://github.com/pan-fire/OmniSync/releases/tag/v0.10.0\n"
    )
    assert "compare/v0.10.0-dev" not in changelog  # the dev version was never tagged
    assert notes("0.10.0", repo / "CHANGELOG.md").stdout == "### Added\n\n- Everything so far.\n"

    # The release after it compares with the first one.
    text = (repo / "CHANGELOG.md").read_text().replace("## [Unreleased]\n", "## [Unreleased]\n\n- More.\n", 1)
    (repo / "CHANGELOG.md").write_text(text)
    assert prepare(repo, "0.11.0", "--date", "2026-11-01").returncode == 0
    changelog = (repo / "CHANGELOG.md").read_text()
    assert "[Unreleased]: https://github.com/pan-fire/OmniSync/compare/v0.11.0...HEAD\n" in changelog
    assert "[0.11.0]: https://github.com/pan-fire/OmniSync/compare/v0.10.0...v0.11.0\n" in changelog
    assert "[0.10.0]: https://github.com/pan-fire/OmniSync/releases/tag/v0.10.0\n" in changelog


def test_prepare_links_the_tag_page_after_a_release_never_tagged_here(repo):
    # A version released before the repository was public has a section but
    # no tag (and no link reference): there is nothing to compare with.
    (repo / "VERSION").write_text("0.11.0-dev\n")
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- New.\n\n"
        "## [0.10.0] - 2026-10-02\n\nReleased before this repository was public.\n\n- Old.\n\n"
        "[Unreleased]: https://github.com/pan-fire/OmniSync/commits/main\n"
    )
    result = prepare(repo, "0.11.0", "--date", "2026-10-05")
    assert result.returncode == 0, result.stderr
    changelog = (repo / "CHANGELOG.md").read_text()
    assert changelog.endswith(
        "[Unreleased]: https://github.com/pan-fire/OmniSync/compare/v0.11.0...HEAD\n"
        "[0.11.0]: https://github.com/pan-fire/OmniSync/releases/tag/v0.11.0\n"
    )
    assert "compare/v0.10.0" not in changelog


def test_prepare_only_prints_the_git_commands(repo):
    result = prepare(repo, "v0.10.0", "--date", "2026-10-01")  # a leading v is accepted
    assert result.returncode == 0, result.stderr
    assert "git tag -a v0.10.0" in result.stdout
    assert "git push origin v0.10.0" in result.stdout
    assert not (repo / ".git").exists()


@pytest.mark.parametrize(
    ("version", "message"),
    [
        ("0.9.0", "not newer"),
        ("0.8.5", "not newer"),
        ("0.9.0-rc.1", "not newer"),  # a pre-release comes before its release
        ("1.0", "not a semantic version"),
        ("1.0.0.0", "not a semantic version"),
        ("01.0.0", "not a semantic version"),
    ],
)
def test_prepare_refuses_bad_versions_and_changes_nothing(repo, version, message):
    before = snapshot(repo)
    result = prepare(repo, version)
    assert result.returncode != 0
    assert message in result.stderr
    assert snapshot(repo) == before


def test_prepare_accepts_a_pre_release(repo):
    assert prepare(repo, "1.0.0-rc.1").returncode == 0
    assert (repo / "VERSION").read_text() == "1.0.0-rc.1\n"


def test_prepare_refuses_an_empty_unreleased_section(repo):
    assert prepare(repo, "0.10.0").returncode == 0
    before = snapshot(repo)
    result = prepare(repo, "0.11.0")
    assert result.returncode != 0
    assert "no entries" in result.stderr
    assert snapshot(repo) == before


def test_prepare_refuses_a_version_already_in_the_changelog(repo):
    (repo / "CHANGELOG.md").write_text(CHANGELOG.replace("## [0.9.0]", "## [1.0.0] - x\n\n- y\n\n## [0.9.0]"))
    result = prepare(repo, "1.0.0")
    assert result.returncode != 0
    assert "already has a section" in result.stderr


def test_prepare_refuses_uncommitted_changes(repo):
    if shutil.which("git") is None:
        pytest.skip("needs git")
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    (repo / "stray.txt").write_text("x")
    result = prepare(repo, "0.10.0")
    assert result.returncode != 0
    assert "uncommitted" in result.stderr
    assert prepare(repo, "0.10.0", "--allow-dirty").returncode == 0


def test_notes_for_the_oldest_section_leave_out_the_link_references(repo):
    result = notes("0.9.0", repo / "CHANGELOG.md")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "### Added\n\n- The first release.\n"


def test_notes_fail_for_a_missing_or_empty_section(repo):
    assert notes("2.0.0", repo / "CHANGELOG.md").returncode != 0
    empty = repo / "EMPTY.md"
    empty.write_text("## [Unreleased]\n\n## [1.0.0] - 2026-10-01\n\n## [0.9.0] - x\n\n- y\n")
    result = notes("1.0.0", empty)
    assert result.returncode != 0
    assert "empty" in result.stderr


def test_the_repository_changelog_has_notes_for_the_current_version():
    # The release workflow refuses a tag without them. Between releases
    # VERSION may hold the next release's development version (e.g.
    # 0.10.0-dev), which is never tagged: then the Unreleased section must
    # hold the notes prepare.sh turns into that release.
    version = (REPO_ROOT / "VERSION").read_text().strip()
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text()
    if f"## [{version}]" not in changelog and version.endswith("-dev"):
        version = "Unreleased"
    result = notes(version, REPO_ROOT / "CHANGELOG.md")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


# --- publish_github_release.py against a fake GitHub API ---

def _load_publisher():
    spec = importlib.util.spec_from_file_location(
        "publish_github_release", REPO_ROOT / "scripts" / "release" / "publish_github_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeGitHub:
    """Just enough of the releases API: list, create, update, assets."""

    def __init__(self) -> None:
        self.releases: list[dict] = []
        self.calls: list[tuple[str, str]] = []
        self.next_id = 1
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output quiet
                pass

            def _reply(self, status: int, body: object | None = None) -> None:
                raw = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self) -> bytes:
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def _handle(self) -> None:
                url = urlparse(self.path)
                fake.calls.append((self.command, url.path))
                assert self.headers["Authorization"] == "Bearer secret-token"
                parts = url.path.strip("/").split("/")
                if url.path == "/repos/o/r/releases" and self.command == "GET":
                    return self._reply(200, fake.releases)
                if url.path == "/repos/o/r/releases" and self.command == "POST":
                    release = {**json.loads(self._body()), "id": fake.next_id, "assets": [],
                               "upload_url": f"{fake.base}/uploads/{fake.next_id}/assets{{?name,label}}"}
                    fake.next_id += 1
                    fake.releases.append(release)
                    return self._reply(201, release)
                if parts[:4] == ["repos", "o", "r", "releases"] and parts[4] == "assets" and self.command == "DELETE":
                    for release in fake.releases:
                        release["assets"] = [a for a in release["assets"] if a["id"] != int(parts[5])]
                    return self._reply(204)
                if parts[:4] == ["repos", "o", "r", "releases"] and self.command == "PATCH":
                    release = next(r for r in fake.releases if r["id"] == int(parts[4]))
                    release.update(json.loads(self._body()))
                    return self._reply(200, release)
                if parts[0] == "uploads" and self.command == "POST":
                    release = next(r for r in fake.releases if r["id"] == int(parts[1]))
                    name = parse_qs(url.query)["name"][0]
                    asset = {"id": fake.next_id, "name": name, "data": self._body().decode()}
                    fake.next_id += 1
                    release["assets"].append(asset)
                    return self._reply(201, asset)
                self._reply(404, {"message": "Not Found"})

            do_GET = do_POST = do_PATCH = do_DELETE = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def fake_github(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setenv("GH_TOKEN", "secret-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_API_URL", fake.base)
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


def test_publish_creates_a_draft_uploads_then_publishes(fake_github, tmp_path):
    publisher = _load_publisher()
    (tmp_path / "notes.md").write_text("### Added\n\n- x\n")
    (tmp_path / "osync-linux-amd64").write_text("binary")
    (tmp_path / "SHA256SUMS").write_text("sums")

    assert publisher.main(["--tag", "v1.2.0", "--notes", str(tmp_path / "notes.md"),
                           str(tmp_path / "osync-linux-amd64"), str(tmp_path / "SHA256SUMS")]) == 0

    [release] = fake_github.releases
    assert release["tag_name"] == "v1.2.0"
    assert release["name"] == "OmniSync 1.2.0"
    assert release["body"] == "### Added\n\n- x\n"
    assert release["draft"] is False and release["make_latest"] == "true"
    assert [a["name"] for a in release["assets"]] == ["osync-linux-amd64", "SHA256SUMS"]
    # Created as a draft first: the POST carried draft=true, and the publish
    # (draft=false) came after the last upload.
    methods = [c for c in fake_github.calls if c[0] != "GET"]
    assert methods[0] == ("POST", "/repos/o/r/releases")
    assert methods[-1][0] == "PATCH"


def test_publish_rerun_replaces_files_and_keeps_one_release(fake_github, tmp_path):
    publisher = _load_publisher()
    (tmp_path / "notes.md").write_text("first")
    asset = tmp_path / "SHA256SUMS"
    asset.write_text("old")
    args = ["--tag", "v1.3.0-rc.1", "--prerelease", "--notes", str(tmp_path / "notes.md"), str(asset)]
    assert publisher.main(args) == 0
    (tmp_path / "notes.md").write_text("second")
    asset.write_text("new")
    assert publisher.main(args) == 0

    [release] = fake_github.releases
    assert release["body"] == "second"
    assert release["prerelease"] is True and release["make_latest"] == "false"
    assert [(a["name"], a["data"]) for a in release["assets"]] == [("SHA256SUMS", "new")]


def test_publish_refuses_missing_files_before_any_call(fake_github, tmp_path):
    publisher = _load_publisher()
    (tmp_path / "notes.md").write_text("x")
    assert publisher.main(["--tag", "v1.0.0", "--notes", str(tmp_path / "notes.md"),
                           str(tmp_path / "nope")]) == 2
    assert fake_github.calls == []
