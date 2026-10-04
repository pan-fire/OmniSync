"""scripts/install.sh, the one-line installer, against stand-ins for docker,
curl and the GitHub release downloads.

Every run gets a PATH of its own: a few stub commands (docker, curl, ...)
and links to the real system tools the script may use, so a test also
fails when the script starts to depend on a tool nobody listed. The docker
stub keeps its containers and volumes in a JSON file and logs each call;
the curl stub serves files from a fake github.com / api.github.com tree.
Runs start in a new session, so there is no terminal to prompt on.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "install.sh"
COMPOSE = REPO_ROOT / "deploy" / "compose.yml"
RELEASES = "github.com/pan-fire/OmniSync/releases/download"
LATEST = "api.github.com/repos/pan-fire/OmniSync/releases/latest"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or sys.platform != "linux", reason="needs bash on Linux"
)

# The system tools the installer may call (besides docker and curl).
SYSTEM_TOOLS = (
    "bash", "sh", "env", "cat", "cp", "mv", "rm", "mkdir", "rmdir", "chmod", "chown",
    "grep", "sed", "awk", "cut", "tr", "head", "tail", "od", "mktemp", "date", "uname",
    "id", "sha256sum", "sleep", "wc", "find", "basename", "readlink", "getent", "ss",
    "openssl",
)

DOCKER_STUB = r'''
import json, os, sys

state_path = os.environ["DOCKER_STUB_STATE"]
with open(state_path) as f:
    state = json.load(f)
args = sys.argv[1:]
with open(os.environ["DOCKER_STUB_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")

def save():
    with open(state_path, "w") as f:
        json.dump(state, f)

def env_file(directory):
    values = {}
    path = os.path.join(directory, ".env")
    if os.path.exists(path):
        for line in open(path):
            if "=" in line and not line.startswith("#"):
                key, value = line.rstrip("\n").split("=", 1)
                values[key] = value
    return values

def health(c):
    return c["health"] if c["state"] == "running" else c["state"]

daemon = state.get("daemon", "ok")
if args[:1] == ["info"]:
    if daemon == "denied":
        sys.exit("permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock")
    if daemon == "down":
        sys.exit("Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?")
    print("29.8.0")
    sys.exit(0)

if args[:1] == ["compose"]:
    if not state.get("compose", True):
        sys.exit("docker: unknown command: docker compose")
    rest = args[1:]
    if rest[:1] == ["version"]:
        print("2.40.0")
        sys.exit(0)
    directory = None
    while rest and rest[0].startswith("-"):
        if rest[0] == "--project-directory":
            directory = rest[1]
        rest = rest[2:]
    env = env_file(directory)
    project = env.get("COMPOSE_PROJECT_NAME") or os.path.basename(directory)
    sub, opts = rest[0], rest[1:]
    containers = state.setdefault("containers", [])
    mine = [c for c in containers if c["project"] == project]
    if sub == "up":
        version = env["OMNISYNC_VERSION"]
        if version in state.get("fail_up", []):
            sys.exit("Error response from daemon: failed to start")
        state["containers"] = [c for c in containers if c["project"] != project]
        volume = project + "_omnisync-data"
        for service, image in (("backend", "backend"), ("frontend", "web")):
            state["containers"].append({
                "id": project + "-" + service, "project": project, "workdir": directory,
                "service": service, "image": "ghcr.io/pan-fire/omnisync-" + image + ":" + version,
                "state": "running",
                "health": "unhealthy" if version in state.get("unhealthy_versions", []) else "healthy",
                "mounts": volume if service == "backend" else "",
            })
        volumes = state.setdefault("volumes", [])
        if not any(v["name"] == volume for v in volumes):
            volumes.append({"name": volume, "project": project})
    elif sub == "down":
        state["containers"] = [c for c in containers if c["project"] != project]
        if "--volumes" in opts or "-v" in opts:
            state["volumes"] = [v for v in state.get("volumes", []) if v["name"] != project + "_omnisync-data"]
    elif sub in ("stop", "start"):
        for c in mine:
            if c["service"] in opts:
                c["state"] = "exited" if sub == "stop" else "running"
    elif sub == "ps":
        for c in mine:
            if c["service"] in opts:
                print(c["id"])
    save()
    sys.exit(0)

if args[:1] == ["ps"]:
    for c in state.get("containers", []):
        print("|".join((c["project"], c["workdir"], c["image"], c["state"], c["mounts"])))
    sys.exit(0)

if args[:1] == ["inspect"]:
    for c in state.get("containers", []):
        if c["id"] == args[-1]:
            print(health(c))
            sys.exit(0)
    sys.exit("Error: No such object: " + args[-1])

if args[:2] == ["volume", "ls"]:
    for v in state.get("volumes", []):
        print(v["name"] + "|" + v.get("project", ""))
    sys.exit(0)

if args[:2] == ["volume", "inspect"]:
    sys.exit(0 if any(v["name"] == args[2] for v in state.get("volumes", [])) else 1)

if args[:1] == ["pull"]:
    sys.exit(0)

if args[:1] == ["run"]:
    if any(a.endswith("hash-password.mjs") for a in args):
        password = sys.stdin.read().rstrip("\n")
        if len(password) < 12:
            sys.exit("Use at least 12 characters")
        print("scrypt:15:8:3:c2FsdHNhbHRzYWx0c2FsdA:a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2U")
        sys.exit(0)
    if state.get("backup_fails"):
        sys.exit("tar: write error")
    backup_dir = next(a.split(":")[0] for a in args if a.endswith(":/backup"))
    name = args[args.index("-c") + 3]
    with open(os.path.join(backup_dir, name), "wb") as f:
        f.write(b"fake tarball")
    sys.exit(0)

sys.exit("docker stub: unhandled " + " ".join(args))
'''

CURL_STUB = r'''
import os, shutil, sys

args = sys.argv[1:]
out = args[args.index("-o") + 1] if "-o" in args else None
url = next(a for a in reversed(args) if a.startswith("http"))
with open(os.environ["CURL_STUB_LOG"], "a") as f:
    f.write(url + "\n")
path = os.path.join(os.environ["FAKE_WEB"], url.split("://", 1)[1])
if not os.path.isfile(path):
    sys.stderr.write("curl: (22) The requested URL returned error: 404\n")
    sys.exit(22)
if out:
    shutil.copyfile(path, out)
else:
    sys.stdout.write(open(path).read())
'''


class Env:
    """One sandbox: a HOME, a fake web, the docker stub's state and a PATH."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.home = tmp / "home"
        self.home.mkdir()
        self.web = tmp / "web"
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.sysbin = tmp / "sysbin"
        self.sysbin.mkdir()
        for tool in SYSTEM_TOOLS:
            found = shutil.which(tool)
            if found:
                (self.sysbin / tool).symlink_to(found)
        self.state_file = tmp / "docker-state.json"
        self.docker_log = tmp / "docker.log"
        self.curl_log = tmp / "curl.log"
        self.docker_log.touch()
        self.curl_log.touch()
        self.set_state({})
        self.stub("docker", DOCKER_STUB)
        self.stub("curl", CURL_STUB)
        self.dir = self.home / "omnisync"
        self.sync = self.home / "OmniSync"

    def stub(self, name: str, python_source: str) -> None:
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n{python_source}")
        path.chmod(0o755)

    def shell_stub(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(f"#!{shutil.which('bash')}\n{body}\n")
        path.chmod(0o755)

    def set_state(self, state: dict) -> None:
        self.state_file.write_text(json.dumps(state))

    def update_state(self, **changes: object) -> None:
        self.set_state({**self.state(), **changes})

    def state(self) -> dict:
        return json.loads(self.state_file.read_text())

    def docker_calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.docker_log.read_text().splitlines()]

    def publish(self, version: str, *, compose: str | None = None, in_sums: bool = True,
                tamper: bool = False, latest: bool = True, osync: bytes | None = None) -> None:
        folder = self.web / RELEASES / f"v{version}"
        folder.mkdir(parents=True, exist_ok=True)
        text = compose if compose is not None else COMPOSE.read_text()
        sums = []
        if in_sums:
            (folder / "compose.yml").write_text(text + ("# tampered\n" if tamper else ""))
            sums.append(f"{hashlib.sha256(text.encode()).hexdigest()}  compose.yml")
        if osync is not None:
            (folder / "osync-linux-amd64").write_bytes(osync)
            sums.append(f"{hashlib.sha256(osync).hexdigest()}  osync-linux-amd64")
        (folder / "SHA256SUMS").write_text("\n".join(sums) + "\n")
        if latest:
            api = self.web / LATEST
            api.parent.mkdir(parents=True, exist_ok=True)
            api.write_text(json.dumps({"tag_name": f"v{version}", "name": f"v{version}"}, indent=2))

    def run(self, *args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{self.bin}:{self.sysbin}",
            "HOME": str(self.home),
            "TMPDIR": str(self.tmp),
            "TZ": "Europe/Berlin",
            "DOCKER_STUB_STATE": str(self.state_file),
            "DOCKER_STUB_LOG": str(self.docker_log),
            "CURL_STUB_LOG": str(self.curl_log),
            "FAKE_WEB": str(self.web),
            **(extra_env or {}),
        }
        return subprocess.run(
            [shutil.which("bash") or "bash", str(SCRIPT), *args],
            env=env, cwd=self.home, capture_output=True, text=True, timeout=60,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )

    def env_values(self) -> dict[str, str]:
        values = {}
        for line in (self.dir / ".env").read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                values[key] = value
        return values


@pytest.fixture
def sandbox(tmp_path: Path) -> Env:
    return Env(tmp_path)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def install(sb: Env, *extra: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return sb.run("--yes", "--api-port", str(free_port()), "--web-port", str(free_port()), *extra,
                  extra_env=extra_env)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ---------------------------------------------------------------- basics


def test_help_and_unknown_flags(sandbox):
    result = sandbox.run("--help")
    assert result.returncode == 0
    for word in ("install", "update", "status", "uninstall", "--dry-run", "--osync", "--purge", "--yes"):
        assert word in result.stdout

    result = sandbox.run("--frobnicate")
    assert result.returncode == 2
    assert "unknown argument: --frobnicate" in result.stderr
    assert sandbox.run("--api-port", "99999").returncode == 2
    assert sandbox.run("--version", "latest").returncode == 2
    assert sandbox.run("status", "--purge").returncode == 2
    assert sandbox.docker_calls() == []


def test_embedded_compose_is_deploy_compose():
    """Releases before 0.12.0 attach no compose.yml; the script's copy must
    be the repository's."""
    script = SCRIPT.read_text()
    embedded = script.split("cat <<'COMPOSE'\n", 1)[1].split("\nCOMPOSE\n", 1)[0] + "\n"
    assert embedded == COMPOSE.read_text()


def test_readme_compose_matches_deploy_compose():
    readme = (REPO_ROOT / "README.md").read_text()
    blocks = [b.split("```", 1)[0] for b in readme.split("```yaml\n")[1:]]
    image_blocks = [yaml.safe_load(b) for b in blocks if "ghcr.io/pan-fire/omnisync-backend" in b]
    assert image_blocks == [yaml.safe_load(COMPOSE.read_text())]


# ---------------------------------------------------------------- preflight


def test_preflight_docker_missing(sandbox):
    (sandbox.bin / "docker").unlink()
    result = install(sandbox)
    assert result.returncode == 1
    assert "docker is not installed" in result.stderr
    assert "https://docs.docker.com/engine/install/" in result.stderr
    assert not sandbox.dir.exists()


def test_preflight_not_in_docker_group(sandbox):
    sandbox.update_state(daemon="denied")
    result = install(sandbox)
    assert result.returncode == 1
    assert 'sudo usermod -aG docker "$USER"' in result.stderr
    assert "never runs sudo" in result.stderr


def test_preflight_daemon_down_and_no_compose(sandbox):
    sandbox.update_state(daemon="down")
    result = install(sandbox)
    assert result.returncode == 1
    assert "daemon is not reachable" in result.stderr

    sandbox.update_state(daemon="ok", compose=False)
    result = install(sandbox)
    assert result.returncode == 1
    assert "compose v2 plugin is missing" in result.stderr


def test_preflight_port_in_use(sandbox):
    sandbox.publish("0.12.0")
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        result = sandbox.run("--yes", "--api-port", str(port), "--web-port", str(free_port()))
    assert result.returncode == 1
    assert f"port {port} is in use" in result.stderr
    assert not sandbox.dir.exists()


def test_preflight_architecture_and_os(sandbox):
    sandbox.shell_stub("uname", 'case $1 in -m) echo armv7l ;; -r) echo 6.1 ;; *) echo Linux ;; esac')
    result = install(sandbox)
    assert result.returncode == 1
    assert "unsupported architecture armv7l" in result.stderr

    sandbox.shell_stub("uname", 'case $1 in -m) echo x86_64 ;; *) echo FreeBSD ;; esac')
    result = install(sandbox)
    assert result.returncode == 1
    assert "unsupported system FreeBSD" in result.stderr


def test_preflight_lists_every_failure(sandbox):
    sandbox.update_state(compose=False)
    (sandbox.bin / "curl").unlink()
    result = install(sandbox)
    assert result.returncode == 1
    assert "neither curl nor wget" in result.stderr
    assert "compose v2 plugin is missing" in result.stderr
    assert "2 preflight check(s) failed" in result.stderr


# ---------------------------------------------------------------- install


def test_fresh_install(sandbox):
    sandbox.publish("0.12.0")
    api, web = free_port(), free_port()
    result = sandbox.run("--yes", "--api-port", str(api), "--web-port", str(web))
    assert result.returncode == 0, result.stderr

    env = sandbox.env_values()
    token = env["OMNISYNC_API_TOKEN"]
    assert len(token) == 64 and int(token, 16) >= 0
    assert env["OMNISYNC_VERSION"] == "0.12.0"
    assert env["COMPOSE_PROJECT_NAME"] == "omnisync"
    assert env["PUID"] == str(os.getuid()) and env["PGID"] == str(os.getgid())
    assert env["TZ"] == "Europe/Berlin"
    assert env["OMNISYNC_SYNC_DIR"] == str(sandbox.sync)
    assert env["OMNISYNC_BIND_ADDRESS"] == "127.0.0.1"
    assert env["OMNISYNC_API_PORT"] == str(api) and env["OMNISYNC_WEB_PORT"] == str(web)
    assert "OMNISYNC_UI_PASSWORD_HASH" not in env
    assert mode(sandbox.dir / ".env") == 0o600
    assert mode(sandbox.dir) == 0o700
    assert (sandbox.dir / "compose.yml").read_text() == COMPOSE.read_text()
    assert sandbox.sync.is_dir()

    # The token is never shown.
    assert token not in result.stdout + result.stderr
    assert f"http://127.0.0.1:{web}" in result.stdout
    assert "Setup Wizard" in result.stdout

    calls = sandbox.docker_calls()
    compose_subs = [c[c.index("-f") + 2] for c in calls if c[:1] == ["compose"] and "-f" in c]
    assert compose_subs[:2] == ["pull", "up"]
    assert {c["image"] for c in sandbox.state()["containers"]} == {
        "ghcr.io/pan-fire/omnisync-backend:0.12.0", "ghcr.io/pan-fire/omnisync-web:0.12.0",
    }
    # Only the API and the release's assets were fetched.
    urls = sandbox.curl_log.read_text().split()
    assert urls and all(
        u.startswith(("https://api.github.com/repos/pan-fire/OmniSync/", "https://github.com/pan-fire/OmniSync/releases/download/"))
        for u in urls
    )


def test_install_is_idempotent(sandbox):
    sandbox.publish("0.12.0")
    assert install(sandbox).returncode == 0
    before = (sandbox.dir / ".env").read_text()
    result = install(sandbox)
    assert result.returncode == 0, result.stderr
    assert "up to date" in result.stdout
    assert (sandbox.dir / ".env").read_text() == before

    result = sandbox.run("install", "--yes")
    assert result.returncode == 0
    assert "already installed" in result.stdout
    assert (sandbox.dir / ".env").read_text() == before


def test_rerun_starts_a_stopped_install(sandbox):
    sandbox.publish("0.12.0")
    assert install(sandbox).returncode == 0
    sandbox.update_state(containers=[])
    result = sandbox.run("--yes")
    assert result.returncode == 0, result.stderr
    assert "Starting OmniSync" in result.stdout
    assert len(sandbox.state()["containers"]) == 2


def test_install_options(sandbox):
    sandbox.publish("0.12.0", latest=False)
    sandbox.publish("0.13.0")
    target = sandbox.tmp / "srv" / "omni"
    result = install(sandbox, "--dir", str(target), "--version", "0.12.0", "--sync-dir", "~/Data",
                     "--bind-address", "0.0.0.0", "--project-name", "mysync")
    assert result.returncode == 0, result.stderr
    sandbox.dir = target
    env = sandbox.env_values()
    assert env["OMNISYNC_VERSION"] == "0.12.0"
    assert env["OMNISYNC_SYNC_DIR"] == str(sandbox.home / "Data")
    assert env["OMNISYNC_BIND_ADDRESS"] == "0.0.0.0"
    assert env["COMPOSE_PROJECT_NAME"] == "mysync"
    assert "mysync_omnisync-data" in result.stdout


def test_checksum_mismatch_is_refused(sandbox):
    sandbox.publish("0.12.0", tamper=True)
    result = install(sandbox)
    assert result.returncode == 1
    assert "checksum mismatch for compose.yml" in result.stderr
    assert not sandbox.dir.exists()
    assert not any(set(c) & {"up", "pull", "run"} for c in sandbox.docker_calls())


def test_compose_missing_from_a_new_release_is_refused(sandbox):
    sandbox.publish("0.12.0", in_sums=False)
    result = install(sandbox)
    assert result.returncode == 1
    assert "no compose.yml in its SHA256SUMS" in result.stderr
    assert not sandbox.dir.exists()


def test_release_without_compose_asset_uses_the_embedded_copy(sandbox):
    sandbox.publish("0.11.0", in_sums=False)
    result = install(sandbox)
    assert result.returncode == 0, result.stderr
    assert "using the copy built into this installer" in result.stdout
    assert (sandbox.dir / "compose.yml").read_text() == COMPOSE.read_text()


def test_too_old_version_is_refused(sandbox):
    sandbox.publish("0.10.0")
    result = install(sandbox)
    assert result.returncode == 1
    assert "0.11.0 and later" in result.stderr


def test_random_bytes_without_openssl(sandbox):
    sandbox.publish("0.12.0")
    sandbox.shell_stub("openssl", "exit 1")
    assert install(sandbox).returncode == 0
    token = sandbox.env_values()["OMNISYNC_API_TOKEN"]
    assert len(token) == 64 and int(token, 16) >= 0


def test_without_a_terminal_it_needs_yes(sandbox):
    sandbox.publish("0.12.0")
    result = sandbox.run("--api-port", str(free_port()), "--web-port", str(free_port()))
    assert result.returncode == 1
    assert "rerun with --yes" in result.stderr
    assert not sandbox.dir.exists()

    result = install(sandbox, "--ui-password-prompt")
    assert result.returncode == 1
    assert "needs a terminal" in result.stderr


def test_dry_run_changes_nothing(sandbox):
    sandbox.publish("0.12.0")
    result = sandbox.run("--dry-run", "--api-port", str(free_port()), "--web-port", str(free_port()))
    assert result.returncode == 0, result.stderr
    assert "[dry-run]" in result.stdout
    assert "OMNISYNC_API_TOKEN=<generated>" in result.stdout
    assert not sandbox.dir.exists()
    assert not sandbox.sync.exists()
    mutating = {"up", "pull", "down", "stop", "start", "run"}
    assert not any(set(c) & mutating for c in sandbox.docker_calls())
    assert sorted(p.name for p in sandbox.tmp.iterdir() if p.name.startswith("omnisync-install")) == []


def test_source_built_install_is_explained(sandbox):
    sandbox.publish("0.12.0")
    sandbox.update_state(containers=[{
        "id": "src-backend", "project": "omnisync", "workdir": "/home/me/OmniSync", "service": "backend",
        "image": "omnisync-backend", "state": "running", "health": "healthy", "mounts": "omnisync_omnisync-data",
    }], volumes=[{"name": "omnisync_omnisync-data", "project": "omnisync"}])
    result = install(sandbox, "--project-name", "fresh")
    assert result.returncode == 1
    assert "built from source was found in /home/me/OmniSync" in result.stderr
    assert "--project-name omnisync" in result.stderr
    assert "docker compose down" in result.stderr and "never 'down -v'" in result.stderr
    assert not sandbox.dir.exists()

    # Stopped and with the same project, the install takes over its volume.
    sandbox.update_state(containers=[{**sandbox.state()["containers"][0], "state": "exited"}])
    result = install(sandbox)
    assert result.returncode == 0, result.stderr
    assert "reuses its data volume omnisync_omnisync-data" in result.stdout


def test_other_volume_is_mentioned(sandbox):
    sandbox.publish("0.12.0")
    sandbox.update_state(volumes=[{"name": "old_omnisync-data", "project": "old"}])
    result = install(sandbox)
    assert result.returncode == 0, result.stderr
    assert "rerun with --project-name old" in result.stderr


def test_ui_password_is_hashed_by_the_web_image(sandbox):
    """The prompt needs a terminal; run the script under a pseudo-terminal."""
    sandbox.publish("0.12.0")
    script = """
import os, pty, sys, termios, time
pid, fd = pty.fork()
if pid == 0:
    os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
replies = {b"profiles must": b"\\r", b"settings?": b"y\\r",
           b"not shown": b"correct horse battery\\r", b"Repeat it": b"correct horse battery\\r"}
out = pending = b""
while True:
    try:
        chunk = os.read(fd, 1024)
    except OSError:
        break
    if not chunk:
        break
    out += chunk
    pending += chunk
    for prompt, reply in replies.items():
        if prompt in pending:
            pending = b""
            if b"correct horse" in reply:
                # Type the password only once `read -s` has turned echo
                # off; typed earlier, the terminal itself would echo it.
                deadline = time.monotonic() + 10
                while termios.tcgetattr(fd)[3] & termios.ECHO and time.monotonic() < deadline:
                    time.sleep(0.01)
            os.write(fd, reply)
os.waitpid(pid, 0)
sys.stdout.write(out.decode(errors="replace"))
"""
    env = {
        "PATH": f"{sandbox.bin}:{sandbox.sysbin}", "HOME": str(sandbox.home), "TMPDIR": str(sandbox.tmp),
        "TZ": "UTC", "DOCKER_STUB_STATE": str(sandbox.state_file), "DOCKER_STUB_LOG": str(sandbox.docker_log),
        "CURL_STUB_LOG": str(sandbox.curl_log), "FAKE_WEB": str(sandbox.web),
    }
    result = subprocess.run(
        [sys.executable, "-c", script, shutil.which("bash") or "bash", str(SCRIPT), "--ui-password-prompt",
         "--api-port", str(free_port()), "--web-port", str(free_port())],
        env=env, cwd=sandbox.home, capture_output=True, text=True, timeout=60,
    )
    assert "correct horse battery" not in result.stdout
    env_values = sandbox.env_values()
    assert env_values["OMNISYNC_UI_PASSWORD_HASH"].startswith("scrypt:15:8:3:")
    assert len(env_values["OMNISYNC_UI_SESSION_SECRET"]) == 64
    assert "correct horse" not in (sandbox.dir / ".env").read_text()
    run = next(c for c in sandbox.docker_calls() if c[:1] == ["run"])
    assert run[-3:] == ["ghcr.io/pan-fire/omnisync-web:0.12.0", "node", "scripts/hash-password.mjs"]
    assert "--network" in run


# ---------------------------------------------------------------- update


@pytest.fixture
def installed(sandbox: Env) -> Env:
    sandbox.publish("0.12.0")
    result = install(sandbox)
    assert result.returncode == 0, result.stderr
    with (sandbox.dir / ".env").open("a") as f:
        f.write("OMNISYNC_UI_ALLOWED_HOSTS=nas.lan\n")
    sandbox.docker_log.write_text("")
    return sandbox


def test_update_keeps_every_other_setting(installed):
    sb = installed
    before = sb.env_values()
    new_compose = COMPOSE.read_text() + "# 0.13.0\n"
    sb.publish("0.13.0", compose=new_compose)
    result = sb.run("--yes")
    assert result.returncode == 0, result.stderr
    assert "installed 0.12.0, latest 0.13.0" in result.stdout
    assert "https://github.com/pan-fire/OmniSync/releases/tag/v0.13.0" in result.stdout

    after = sb.env_values()
    assert after == {**before, "OMNISYNC_VERSION": "0.13.0"}
    assert mode(sb.dir / ".env") == 0o600
    assert (sb.dir / "compose.yml").read_text() == new_compose
    assert not (sb.dir / "compose.yml.previous").exists()

    backups = list((sb.dir / "backups").iterdir())
    assert len(backups) == 1 and backups[0].name.startswith("omnisync-data-0.12.0-")
    calls = sb.docker_calls()
    backup = next(c for c in calls if c[:1] == ["run"])
    assert "omnisync_omnisync-data:/data:ro" in backup
    assert "ghcr.io/pan-fire/omnisync-backend:0.12.0" in backup
    # The backend is stopped for the backup, before the new version starts.
    order = [c[c.index("-f") + 2] if c[:1] == ["compose"] else c[0]
             for c in calls if c[:1] in (["run"], ["pull"]) or (c[:1] == ["compose"] and "-f" in c)]
    assert order.index("stop") < order.index("run") < order.index("up")
    assert ["pull", "--quiet", "ghcr.io/pan-fire/omnisync-web:0.13.0"] in calls
    assert {c["image"].rsplit(":", 1)[1] for c in sb.state()["containers"]} == {"0.13.0"}


def test_update_rolls_back_when_unhealthy(installed):
    sb = installed
    before_env = (sb.dir / ".env").read_text()
    before_compose = (sb.dir / "compose.yml").read_text()
    sb.publish("0.13.0", compose=COMPOSE.read_text() + "# 0.13.0\n")
    sb.update_state(unhealthy_versions=["0.13.0"])
    result = sb.run("update", "--yes", "--health-timeout", "1")
    assert result.returncode == 1
    assert "rolling back to 0.12.0" in result.stderr
    assert "0.12.0 runs again" in result.stderr
    assert (sb.dir / ".env").read_text() == before_env
    assert (sb.dir / "compose.yml").read_text() == before_compose
    assert {c["image"].rsplit(":", 1)[1] for c in sb.state()["containers"]} == {"0.12.0"}
    assert len(list((sb.dir / "backups").iterdir())) == 1


def test_update_needs_confirmation(installed):
    sb = installed
    before = (sb.dir / ".env").read_text()
    sb.publish("0.13.0")
    result = sb.run("update")
    assert result.returncode == 1
    assert "rerun with --yes" in result.stderr
    assert (sb.dir / ".env").read_text() == before
    assert not any(c[:1] in (["run"], ["pull"]) for c in sb.docker_calls())


def test_update_without_backup_and_failed_backup(installed):
    sb = installed
    sb.publish("0.13.0")
    sb.update_state(backup_fails=True)
    result = sb.run("update", "--yes")
    assert result.returncode == 1
    assert "the backup failed; nothing was updated" in result.stderr
    assert sb.env_values()["OMNISYNC_VERSION"] == "0.12.0"
    assert all(c["state"] == "running" for c in sb.state()["containers"])

    result = sb.run("update", "--yes", "--no-backup")
    assert result.returncode == 0, result.stderr
    assert sb.env_values()["OMNISYNC_VERSION"] == "0.13.0"
    assert not (sb.dir / "backups").exists() or not list((sb.dir / "backups").iterdir())


def test_update_refuses_a_downgrade(installed):
    result = installed.run("update", "--yes", "--version", "0.11.0")
    assert result.returncode == 1
    assert "downgrading is not supported" in result.stderr


def test_update_dry_run_changes_nothing(installed):
    sb = installed
    before = (sb.dir / ".env").read_text()
    sb.publish("0.13.0")
    result = sb.run("update", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "set OMNISYNC_VERSION=0.13.0" in result.stdout
    assert (sb.dir / ".env").read_text() == before
    assert not (sb.dir / "backups").exists()
    assert not any(set(c) & {"up", "pull", "stop", "run"} for c in sb.docker_calls())


def test_existing_install_found_through_its_containers(installed):
    sb = installed
    moved = sb.tmp / "elsewhere"
    sb.dir.rename(moved)
    state = sb.state()
    for c in state["containers"]:
        c["workdir"] = str(moved)
    sb.set_state(state)
    result = sb.run("status")
    assert result.returncode == 0, result.stderr
    assert f"found an existing install in {moved}" in result.stdout


# ---------------------------------------------------------------- status and uninstall


def test_status(installed):
    sb = installed
    sb.publish("0.13.0")
    result = sb.run("status")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    env = sb.env_values()
    assert "installed     0.12.0" in out and "latest        0.13.0" in out
    assert "backend       healthy" in out and "web UI        healthy" in out
    assert f"http://127.0.0.1:{env['OMNISYNC_WEB_PORT']}" in out
    assert "omnisync_omnisync-data" in out and str(sb.sync) in out
    assert "Update available" in out
    assert env["OMNISYNC_API_TOKEN"] not in out


def test_uninstall_keeps_the_data(installed):
    sb = installed
    result = sb.run("uninstall", "--yes")
    assert result.returncode == 0, result.stderr
    assert sb.state()["containers"] == []
    assert [v["name"] for v in sb.state()["volumes"]] == ["omnisync_omnisync-data"]
    assert (sb.dir / ".env").exists() and (sb.dir / "compose.yml").exists()


def test_uninstall_purge(installed):
    sb = installed
    result = sb.run("uninstall", "--purge")
    assert result.returncode == 1
    assert "no terminal to confirm --purge" in result.stderr
    assert sb.state()["volumes"]

    result = sb.run("uninstall", "--purge", "--yes")
    assert result.returncode == 0, result.stderr
    assert sb.state()["containers"] == [] and sb.state()["volumes"] == []
    assert not sb.dir.exists()
    assert sb.sync.is_dir()  # the user's files stay


def test_commands_need_an_install(sandbox):
    for command in ("status", "update", "uninstall"):
        result = sandbox.run(command, "--yes")
        assert result.returncode == 1
        assert "no OmniSync install found" in result.stderr


# ---------------------------------------------------------------- osync


def test_osync_is_installed_verified(sandbox):
    sandbox.publish("0.12.0", osync=b"\x7fELF fake osync")
    result = install(sandbox, "--osync")
    assert result.returncode == 0, result.stderr
    binary = sandbox.home / ".local" / "bin" / "osync"
    assert binary.read_bytes() == b"\x7fELF fake osync"
    assert mode(binary) == 0o755
    assert "OMNISYNC_API_KEY" in result.stdout


def test_osync_checksum_mismatch_is_refused(installed):
    sb = installed
    sb.publish("0.12.0", osync=b"original")
    (sb.web / RELEASES / "v0.12.0" / "osync-linux-amd64").write_bytes(b"evil")
    result = sb.run("--yes", "--osync")
    assert result.returncode == 1
    assert "checksum mismatch for osync-linux-amd64" in result.stderr
    assert not (sb.home / ".local" / "bin" / "osync").exists()


# ---------------------------------------------------------------- CI test release


def make_test_release(sb: Env, version: str = "9999.0.0-ci", tamper: bool = False) -> Path:
    """A folder standing in for a GitHub release (OMNISYNC_INSTALL_TEST_RELEASE_DIR)."""
    folder = sb.tmp / "test-release"
    folder.mkdir(exist_ok=True)
    text = COMPOSE.read_text()
    (folder / "VERSION").write_text(version + "\n")
    (folder / "compose.yml").write_text(text + ("# tampered\n" if tamper else ""))
    (folder / "SHA256SUMS").write_text(f"{hashlib.sha256(text.encode()).hexdigest()}  compose.yml\n")
    return folder


def test_ci_test_release_installs_without_downloading_or_pulling(sandbox):
    folder = make_test_release(sandbox)
    env = {"OMNISYNC_INSTALL_TEST_RELEASE_DIR": str(folder)}
    result = install(sandbox, extra_env=env)
    assert result.returncode == 0, result.stderr
    assert "for CI tests only" in result.stderr
    assert sandbox.env_values()["OMNISYNC_VERSION"] == "9999.0.0-ci"
    assert (sandbox.dir / "compose.yml").read_text() == COMPOSE.read_text()
    assert sandbox.curl_log.read_text() == ""  # nothing fetched from GitHub
    calls = sandbox.docker_calls()
    assert not any("pull" in call for call in calls)
    assert any(call[:1] == ["compose"] and "up" in call for call in calls)

    again = sandbox.run("--yes", extra_env=env)
    assert again.returncode == 0, again.stderr
    assert "up to date" in again.stdout
    assert sandbox.curl_log.read_text() == ""


def test_ci_test_release_is_still_checked(sandbox):
    folder = make_test_release(sandbox, tamper=True)
    result = install(sandbox, extra_env={"OMNISYNC_INSTALL_TEST_RELEASE_DIR": str(folder)})
    assert result.returncode == 1
    assert "checksum mismatch for compose.yml" in result.stderr
    assert not (sandbox.dir / ".env").exists()

    (folder / "SHA256SUMS").unlink()
    result = install(sandbox, extra_env={"OMNISYNC_INSTALL_TEST_RELEASE_DIR": str(folder)})
    assert result.returncode == 1
    assert "has no SHA256SUMS" in result.stderr
    assert sandbox.curl_log.read_text() == ""


def test_without_the_test_release_downloads_as_before(sandbox):
    sandbox.publish("0.12.0")
    result = install(sandbox, extra_env={"OMNISYNC_INSTALL_TEST_RELEASE_DIR": ""})
    assert result.returncode == 0, result.stderr
    assert "for CI tests only" not in result.stderr
    assert "releases/latest" in sandbox.curl_log.read_text()
    assert any(call[:1] == ["compose"] and "pull" in call for call in sandbox.docker_calls())
