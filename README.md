# OmniSync

[![CI](https://github.com/pan-fire/OmniSync/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/pan-fire/OmniSync/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)

Self-hosted folder sync between your machine and Google Drive, OneDrive,
Dropbox or any other cloud that [rclone](https://rclone.org) supports.

> **Status: beta.** OmniSync is a side project, maintained on a best-effort
> basis. It is built to refuse syncs that look destructive, but it moves and
> deletes files: back up your data before you try two-way sync, and read
> the [changelog](CHANGELOG.md) before you upgrade.

OmniSync runs as a small server on your own machine or NAS. You set up cloud
remotes and **sync profiles** (a local folder paired with a remote folder) in a
web UI or a terminal UI; the server watches each local folder, syncs changes
from either side to the other, and refuses or stops a sync that looks like it
would destroy data.

- **Two-way sync** with rclone bisync: edits, new files and deletions
  travel both ways, and a file changed on both sides keeps both versions
- **Several profiles**, each with its own folders, interval, filters
  ("choose folders" tree), bandwidth limit and sync window
- **Remote setup wizard** for OAuth providers, keys or logins (S3, B2,
  SFTP, FTP, WebDAV, SMB) and crypt, or import an existing rclone.conf
- **Safety rails**: marker file, empty-side refusal, delete limit, and a
  trash folder that keeps whatever a sync replaced or deleted
- **Diff view** with per-file push, pull, skip and "keep both"
- **Scheduled backups** to a folder or another remote, optionally encrypted
  and verified, with full or per-file restores
- **Job history, logs and notifications** (webhook, ntfy, email, Web Push,
  desktop)
- **Web UI** in English, German and Persian, and **`osync`**, a terminal UI
  with scripting subcommands

## Quick install

On Linux, macOS or Windows (WSL 2) with Docker and its compose plugin, on
x86-64 or 64-bit ARM:

```bash
curl -fsSL https://raw.githubusercontent.com/pan-fire/OmniSync/main/scripts/install.sh | bash
```

The installer checks Docker, the ports and your system, downloads the latest
release's compose file and checks it against the release's `SHA256SUMS`
(and their signature, when [cosign](https://docs.sigstore.dev/) is
installed), writes a `.env` with a new API token (mode 0600), pulls the
images and starts OmniSync in `~/omnisync`, then waits for both health
checks and prints the address: <http://127.0.0.1:3000>. It asks for the
folder OmniSync may sync (default `~/OmniSync`) and, if you like, a web UI
password, of which it stores only the hash.

Inspect it first? Download it, read it, then run it:

```bash
curl -fsSLO https://raw.githubusercontent.com/pan-fire/OmniSync/main/scripts/install.sh
less install.sh
bash install.sh --dry-run     # prints what it would do, changes nothing
bash install.sh
```

Run it again to update (it backs up the data volume first and rolls back
if the new version is not healthy); `bash install.sh status` shows the
versions and health, `uninstall` removes the containers and keeps your
data. `--help` lists the options (`--dir`, `--version`, `--sync-dir`,
`--web-port`, `--api-port`, `--osync` for the terminal client, `--yes` for
no questions, ...); [Operations](docs/gem/operations.md#install-update-and-uninstall-with-the-installer)
has the details. To set it up by hand instead, see
[Install a release](#install-a-release); to build from the source, the
[Quick start](#quick-start).

## Why OmniSync?

rclone reaches more than 70 storage providers, but on its own it is a
command line: two-way sync (`rclone bisync`) needs scripts, schedules and
care to run safely. Syncthing syncs between your own devices but not to
cloud storage; Nextcloud brings a whole server stack; Insync and similar
clients are proprietary and cover a few providers. OmniSync puts rclone's
reach behind a web UI and a terminal UI, runs two-way sync for you with
guards against the classic data-loss mistakes (an unmounted folder, a
mass deletion, a lost sync state), and adds scheduled, optionally
encrypted backups. It is self-hosted, keeps your credentials on your own
machine, and is free software under the GPL-3.0.

## Contents

- [Quick install](#quick-install)
- [Why OmniSync?](#why-omnisync)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Install a release](#install-a-release) ([verify it](#verify-a-release))
- [Access and authentication](#access-and-authentication)
- [Exposing the web UI / HTTPS](#exposing-the-web-ui--https)
- [How syncing works](#how-syncing-works)
- [Sync safety](#sync-safety)
- [Environment variables](#environment-variables)
- [Backing up OmniSync's own data](#backing-up-omnisyncs-own-data)
- [Uninstall](#uninstall)
- [Terminal UI](#terminal-ui)
- [Development](#development)
- [License](#license)

The [user guide](docs/gem/index.md) covers setup step by step, every
setting, the web UI, [how syncing works](docs/gem/how-syncing-works.md) in
detail and [operations](docs/gem/operations.md) (container settings, backups,
running without Docker). Contributing: [CONTRIBUTING.md](CONTRIBUTING.md);
reporting a vulnerability: [SECURITY.md](SECURITY.md).

## Architecture

```text
 browser ──> 127.0.0.1:3000  web UI (Next.js server) ──┐  /api/* + API token
                                                       v
 osync (TUI/CLI) ──────────> 127.0.0.1:8000  backend (FastAPI) ──> rclone ──> cloud
                                               │
                                               ├─ /data/omnisync (volume): SQLite database,
                                               │  rclone.conf, config.toml, API token, logs
                                               └─ your sync folder (bind mount)
```

| Part | Where | What it does |
| --- | --- | --- |
| Backend | [`backend/`](backend), root [`Dockerfile`](Dockerfile) | FastAPI app. One sync engine per profile (file watcher, pull scheduler, rclone runs), backups, notifications, the remote wizard. SQLite with Alembic migrations, applied on start. |
| Web UI | [`frontend/`](frontend) | Next.js 16 app. Its server forwards `/api/*` to the backend and adds the API token, so the browser never holds it. |
| Terminal UI | [`tui/`](tui) | `osync`, a Go TUI and CLI client for the same API. |
| rclone | in the backend image | Does every transfer, check and listing, with its own config file (`/data/omnisync/rclone.conf`); your personal rclone config is never touched. |

The backend owns all state. The web UI and the TUI are clients; you can use
either or both.

## Quick start

Requirements: Docker with the compose plugin. This builds the images from
the source; to run released images instead, see
[Install a release](#install-a-release).

1. Clone the repository and create a `.env` file next to `docker-compose.yml`:

   ```bash
   git clone https://github.com/pan-fire/OmniSync.git
   cd OmniSync
   cat > .env <<EOF
   OMNISYNC_API_TOKEN=$(openssl rand -hex 32)
   OMNISYNC_SYNC_DIR=$HOME/Sync
   PUID=$(id -u)
   PGID=$(id -g)
   TZ=$(cat /etc/timezone 2>/dev/null || echo UTC)
   EOF
   mkdir -p ~/Sync
   ```

   - `OMNISYNC_API_TOKEN` is the key every API request needs. The web UI's
     server and the backend both read it from `.env`; give the same value to
     the TUI.
   - `OMNISYNC_SYNC_DIR` is the one host folder the backend can see (an
     absolute path). It is mounted at the same path inside the container, so
     profile folders are written exactly as on the host and must lie inside
     it. Unset, the repository's `./sync` folder is mounted at `/sync`.
   - `PUID`/`PGID` are the user and group the backend writes files as
     (default 1000). Never `0`: that runs OmniSync as root.
   - `TZ` is the time zone of log timestamps and schedules (default `UTC`).

2. Build and start the backend and the web UI:

   ```bash
   docker compose up -d --build
   ```

   Both are published on the host's loopback only: the web UI on
   <http://127.0.0.1:3000>, the API on `127.0.0.1:8000`.

3. Open <http://127.0.0.1:3000>:
   1. **Remotes → Setup Wizard**: pick a provider and name the remote. For
      OAuth providers (Google Drive, OneDrive, Dropbox) enter the client ID
      (for Google also the secret) of your own OAuth app, open the
      authorization link and approve access; the wizard then creates the
      remote by itself.
   2. **Profiles → Create Profile**: a name, a local folder inside
      `OMNISYNC_SYNC_DIR`, and a remote folder such as `gdrive:Sync`.
   3. A new profile with files on either side starts **paused**: its
      startup check finds the differences. Either confirm a **Push** or
      **Pull** (the dialog shows how many files it would delete and replace),
      or open the profile's **Differences** tab, handle the files one by one
      and then **Resume Intervals**. From then on the profile syncs on its
      own. See [How syncing works](#how-syncing-works).

OmniSync ships no OAuth client IDs or secrets: Google Drive, Dropbox and
OneDrive sign in with your own OAuth app, which you register once with the
provider ([step by step](docs/gem/remotes.md)). Register
`<ui>/api/wizard/oauth/callback` as the app's redirect URI (the wizard shows
the exact address). After you allow access the provider returns the browser
to the web UI, whose server tells the backend the browser's address through
`X-Forwarded-*` headers, and the sign-in finishes by itself. Behind another
reverse proxy, or to pin the address, set `OMNISYNC_OAUTH_REDIRECT_URI`.
Remotes that use rclone's built-in apps (made with `rclone config`, or
imported from an rclone.conf) keep working: rclone refreshes their tokens
itself. rclone is retiring its shared Google Drive
app during 2026, though: move such a Drive remote to your own app with
**Reconnect**.

To update, pull the repository and run `docker compose up -d --build` again.
Database migrations run automatically on start. All state lives in the
`omnisync-data` volume; `docker compose down -v` deletes it. See
[Backing up OmniSync's own data](#backing-up-omnisyncs-own-data).

**Opt-in compose overrides** (combine with
`docker compose -f docker-compose.yml -f <file> ...`):

- `docker-compose.dev.yml`: live reload with `./backend` bind-mounted, and a
  `test-sandbox` shell (`docker compose -f docker-compose.yml -f
  docker-compose.dev.yml run --rm test-sandbox`).
- `docker-compose.dbus.yml`: native Linux desktop notifications through the host
  D-Bus session bus. This mounts the bus socket and disables AppArmor for the
  container (the host bus rejects connections from Docker's default AppArmor
  profile); Web Push needs neither.

For more folders than `OMNISYNC_SYNC_DIR`, add mounts in a
`docker-compose.override.yml`.

## Install a release

Each release publishes ready-built images and `osync` binaries, so you need
neither the source nor a build.

The version is in the web UI's sidebar,
`GET /health` (`"version"`) and `osync --version`; the
[changelog](CHANGELOG.md) lists what each release changed.

| What | Where |
| --- | --- |
| Backend image | `ghcr.io/pan-fire/omnisync-backend:<version>` |
| Web UI image | `ghcr.io/pan-fire/omnisync-web:<version>` |
| `osync` binaries and `SHA256SUMS` | the [GitHub release](https://github.com/pan-fire/OmniSync/releases) |

Image tags: the exact version (e.g. `1.2.3`), the minor line (`1.2`, which
moves to its newest patch release) and `latest`. Pre-releases (`1.0.0-rc.1`) get
only their exact tag. Run the backend and the web UI at the same version.

### Images with docker compose

This is what the [installer](#quick-install) sets up; by hand: put this
`compose.yml` in an empty folder, next to a `.env` made as in the
[Quick start](#quick-start) plus `OMNISYNC_VERSION=<version>`, the
release you install (the newest is on the
[releases page](https://github.com/pan-fire/OmniSync/releases)). It is
[`deploy/compose.yml`](deploy/compose.yml), attached to each release from
0.12.0 on as `compose.yml`: the repository's `docker-compose.yml` with the
images in place of the builds, whose comments explain each setting.
`OMNISYNC_API_PORT` and `OMNISYNC_WEB_PORT` change the host ports (default
8000 and 3000).

Switching from images built from the source? Keep the compose project name
you used, or compose creates a new, empty data volume: a clone in a folder
named `OmniSync` used the project `omnisync`, so add
`COMPOSE_PROJECT_NAME=omnisync` to the new `.env`, and stop the old
containers first. `docker volume ls` shows the volume it must reuse (e.g.
`omnisync_omnisync-data`). The installer finds such an install and prints
these steps; run it with `--project-name omnisync` (the old project name)
and `--sync-dir` set to the old `OMNISYNC_SYNC_DIR`.

```yaml
services:
  backend:
    image: ghcr.io/pan-fire/omnisync-backend:${OMNISYNC_VERSION:?set OMNISYNC_VERSION in .env}
    ports:
      - "${OMNISYNC_BIND_ADDRESS:-127.0.0.1}:${OMNISYNC_API_PORT:-8000}:8000"
    environment:
      - PUID=${PUID:-1000}
      - PGID=${PGID:-1000}
      - TZ=${TZ:-UTC}
      - OMNISYNC_LOG_FORMAT=${OMNISYNC_LOG_FORMAT:-}
      - OMNISYNC_LOG_MAX_BYTES=${OMNISYNC_LOG_MAX_BYTES:-}
      - OMNISYNC_LOG_BACKUPS=${OMNISYNC_LOG_BACKUPS:-}
      - OMNISYNC_LOG_ACCESS=${OMNISYNC_LOG_ACCESS:-}
      - OMNISYNC_HOST_OS=linux
      - OMNISYNC_API_TOKEN=${OMNISYNC_API_TOKEN:-}
      - OMNISYNC_ALLOWED_HOSTS=backend,${OMNISYNC_ALLOWED_HOSTS:-}
      - OMNISYNC_BROWSE_ROOTS=${OMNISYNC_SYNC_DIR:-/sync}
    volumes:
      - omnisync-data:/data/omnisync
      - ${OMNISYNC_SYNC_DIR:-./sync}:${OMNISYNC_SYNC_DIR:-/sync}
    security_opt:
      - no-new-privileges:true
    cap_drop: [ALL]
    cap_add: [CHOWN, DAC_OVERRIDE, SETUID, SETGID]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    restart: unless-stopped
    stop_grace_period: 90s

  frontend:
    image: ghcr.io/pan-fire/omnisync-web:${OMNISYNC_VERSION:?set OMNISYNC_VERSION in .env}
    ports:
      - "127.0.0.1:${OMNISYNC_WEB_PORT:-3000}:3000"
    environment:
      - BACKEND_URL=http://backend:8000
      - OMNISYNC_API_TOKEN=${OMNISYNC_API_TOKEN:-}
      - OMNISYNC_UI_ALLOWED_HOSTS=${OMNISYNC_UI_ALLOWED_HOSTS:-}
      - OMNISYNC_UI_PASSWORD_HASH=${OMNISYNC_UI_PASSWORD_HASH:-}
      - OMNISYNC_UI_PASSWORD=${OMNISYNC_UI_PASSWORD:-}
      - OMNISYNC_UI_SESSION_SECRET=${OMNISYNC_UI_SESSION_SECRET:-}
      - OMNISYNC_UI_SESSION_DAYS=${OMNISYNC_UI_SESSION_DAYS:-}
      - TZ=${TZ:-UTC}
    depends_on:
      - backend
    read_only: true
    tmpfs:
      - /tmp
    security_opt:
      - no-new-privileges:true
    cap_drop: [ALL]
    healthcheck:
      test: ["CMD", "node", "-e", "fetch('http://127.0.0.1:3000/healthz').then(r => process.exit(r.ok ? 0 : 1), () => process.exit(1))"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    restart: unless-stopped

volumes:
  omnisync-data:
```

```bash
docker compose up -d        # pulls the images on first start
```

To upgrade, read the [changelog](CHANGELOG.md), set the new
`OMNISYNC_VERSION` in `.env` and run `docker compose pull && docker compose
up -d`. Database migrations run on start; all state stays in the
`omnisync-data` volume. Each image is multi-platform: `linux/amd64` and
`linux/arm64` (e.g. a Raspberry Pi 4 or 5 with a 64-bit OS, or an ARM NAS)
under the same tag, and Docker pulls the one for your machine by itself.
32-bit ARM (`linux/arm/v7`) is not offered. The images carry their own
health checks; container settings such as `TZ` and resource limits are in
[Operations](docs/gem/operations.md#the-containers).

### The `osync` binary

Static binaries for Linux, macOS and Windows on amd64 and arm64
(`osync-<os>-<arch>`, `.exe` on Windows), with a `SHA256SUMS` file:

```bash
# Linux on x86-64; pick your file from the release page for other systems.
curl -fsSLO https://github.com/pan-fire/OmniSync/releases/latest/download/osync-linux-amd64
curl -fsSLO https://github.com/pan-fire/OmniSync/releases/latest/download/SHA256SUMS
sha256sum -c SHA256SUMS --ignore-missing
install -m 0755 osync-linux-amd64 ~/.local/bin/osync
osync --version             # osync version <version> (commit ...)
```

`gh release download --repo pan-fire/OmniSync --pattern 'osync-linux-amd64'
--pattern SHA256SUMS` does the same with the GitHub CLI. macOS may refuse to open a downloaded binary until you clear its quarantine
flag (`xattr -d com.apple.quarantine osync`). Use it as described in
[Terminal UI](#terminal-ui).

### Verify a release

The release workflow signs every image and
the binaries' `SHA256SUMS` with [cosign](https://docs.sigstore.dev/)
keyless signing: there is no key to trust, the signature's certificate
names the workflow and tag that built it, and it is recorded in Sigstore's
public transparency log. A signature that verifies proves the file came
from this repository's release workflow for that tag. An image's signature
covers its multi-platform index, so one check covers both platforms.

```bash
VERSION=0.11.0   # the release you are installing
ID="https://github.com/pan-fire/OmniSync/.github/workflows/release.yml@refs/tags/v$VERSION"
ISSUER=https://token.actions.githubusercontent.com

# Images (once per image)
cosign verify --certificate-identity "$ID" --certificate-oidc-issuer "$ISSUER" \
  ghcr.io/pan-fire/omnisync-backend:$VERSION
cosign verify --certificate-identity "$ID" --certificate-oidc-issuer "$ISSUER" \
  ghcr.io/pan-fire/omnisync-web:$VERSION

# osync binaries: download SHA256SUMS.sigstore.json next to SHA256SUMS, then
cosign verify-blob --bundle SHA256SUMS.sigstore.json \
  --certificate-identity "$ID" --certificate-oidc-issuer "$ISSUER" SHA256SUMS
sha256sum -c SHA256SUMS --ignore-missing
```

Each image also carries an SBOM (the packages inside it) and SLSA
provenance (how and from which commit it was built) as attestations:

```bash
docker buildx imagetools inspect ghcr.io/pan-fire/omnisync-backend:$VERSION --format '{{ json .SBOM }}'
docker buildx imagetools inspect ghcr.io/pan-fire/omnisync-backend:$VERSION --format '{{ json .Provenance }}'
```

## Access and authentication

The API is reachable from this machine only, and every request needs a token:

- **Network:** with docker compose the backend publishes port 8000 and the
  web UI port 3000 on the host's loopback only (`127.0.0.1`, no host
  networking); inside their containers they listen on `0.0.0.0`, reachable
  solely through those mappings and the compose network. Run directly, the
  backend binds `127.0.0.1`. To serve the API to other machines set
  `OMNISYNC_BIND_ADDRESS` (e.g. `0.0.0.0`) in `.env` and add the host name
  clients use to `OMNISYNC_ALLOWED_HOSTS`. The OAuth redirect returns through
  the web UI (`<ui>/api/wizard/oauth/callback`, normally
  `http://127.0.0.1:3000/api/wizard/oauth/callback`), so the backend's port
  never needs to be reachable from the browser. To use the web UI from other
  machines, see [Exposing the web UI / HTTPS](#exposing-the-web-ui--https).
- **Token:** set `OMNISYNC_API_TOKEN` (in `.env` for docker compose). If it is
  unset, the backend generates one on first start and writes it to
  `/data/omnisync/api-token` (mode 0600); the log says where. The web UI then
  gets 401 answers until you put that value into `.env`. Only `GET /health`
  and the OAuth callback (which accepts only the state of an open wizard
  session) work without a token. Use at least 32 random characters
  (`openssl rand -hex 32`); a shorter `OMNISYNC_API_TOKEN` still works but
  logs a warning at every start.
- **Failed logins:** a request with a wrong token is logged with the client
  address (the audit event `auth.token_rejected`, at most one line per
  address and minute). After 10 wrong tokens
  within a minute that address gets `429 Too Many Requests` for every request
  without the right token (except `/health`) for a minute; requests with the
  right token always pass, so the web UI and other clients keep working.
  The address is the connection's own: `X-Forwarded-For` is ignored (the
  image runs uvicorn with `--no-proxy-headers`; add the flag if you run
  uvicorn yourself).
- **Request size:** request bodies over 1 MB are refused with 413; lists in
  requests are bounded too (e.g. 64 rclone arguments, 500 filter rules).
- **Web UI:** its server reads `OMNISYNC_API_TOKEN` and `BACKEND_URL` at run
  time and adds the token to every `/api` request, replacing any
  `Authorization` header the browser sends; the browser never sees it. In
  compose it reaches the backend as `http://backend:8000`, so the backend
  always accepts the host name `backend`. Before adding the token it refuses
  (403) `/api` requests for host names other than `localhost`, `127.0.0.1`,
  `[::1]` and those in `OMNISYNC_UI_ALLOWED_HOSTS` (DNS rebinding), and
  state-changing requests that do not come from the UI's own origin or carry
  a body that is not JSON (cross-site forms and scripts). Its pages are sent
  with a Content-Security-Policy and `X-Frame-Options: DENY`. It has an
  optional password login, off by default; see
  [Exposing the web UI / HTTPS](#exposing-the-web-ui--https).
- **Terminal UI:** `OMNISYNC_API_KEY=<token> osync`, `api_key` in its config
  file, or `osync --api-key <token>` (flags are visible in the process list).
  It writes its config file owner-only (0600) and warns at startup when that
  file holds `api_key` but others can read it, or when the key would go over
  plain `http://` to a host other than loopback.
- **Host names:** requests for host names other than `localhost`,
  `127.0.0.1` and `::1` (and `backend` in compose) are rejected, which stops
  DNS-rebinding attacks from web pages; add names (e.g. a NAS host name) with
  `OMNISYNC_ALLOWED_HOSTS`.
- **Folders:** the folder picker only lists your home directory and `/sync`
  (in docker: `OMNISYNC_SYNC_DIR`); change that with `OMNISYNC_BROWSE_ROOTS`
  (`:`-separated). Your home directory is not mounted into the container.
  When `OMNISYNC_BROWSE_ROOTS` is set (docker compose sets it to the sync
  folder), new profile folders, local backup targets and sync tests must be
  inside those folders too (422 otherwise). Profiles and targets saved
  earlier keep working; the backend logs a warning for each at startup.
- **Data directory:** a profile folder, local backup target, sync test or
  restore may never be OmniSync's data directory (the folders of
  `OMNISYNC_DB_PATH`, `OMNISYNC_CONFIG_PATH`, `OMNISYNC_RCLONE_CONFIG`,
  `OMNISYNC_API_TOKEN_FILE`, `OMNISYNC_LOG_PATH`, `OMNISYNC_VAPID_DIR` and
  `OMNISYNC_BISYNC_DIR`; by
  default `/data/omnisync`), a folder inside it or one containing it, so the
  remote credentials and the API token cannot be synced to a remote. Symlinks
  are followed. Keep these files in a folder of their own.
- **Remote names** in API paths and queries must be letters, digits, `_` and
  `-` (not starting with `-`); rclone always gets remote paths after `--`, so
  no name or path is read as an rclone option.
- OAuth tokens stay on the server: the wizard finishes by session id and never
  returns a token. Errors carry a generic message; the details (rclone output,
  paths) go to the server log.
- **Remote settings** are written only for the provider's own fields (an
  edit is validated like a new remote), and secrets are never returned:
  `GET /remotes/{name}/config` says only whether one is set. A reconnect
  replaces only the token. An imported rclone.conf is parsed in memory and
  never logged; `local` remotes, wrappers around local paths and options that
  run programs (`ssh`, `bearer_token_command`) are refused.
- API docs (`/docs`, `/openapi.json`) are off unless `OMNISYNC_API_DOCS=1`.

**Health:** `GET /health` checks only local state (database and the rclone
binary) and never calls a cloud provider. It answers 200 `"ok"`, or 503
`"degraded"` with the same body when either is missing, so the container health
check (built into the image and set in compose) fails for a broken backend. `remote_accessible` is always `null` there;
`GET /health/remotes` (token required) checks the remotes that profiles use, and
`GET /health/network` diagnoses outbound DNS/HTTPS.

## Exposing the web UI / HTTPS

The web UI's server adds the API token to every request, so anyone who can
open the UI has full control: they can push or pull (and so delete files on
either side), restore backups, add or remove remotes and read the logs.
**By default the UI has no login**, which is why compose publishes port 3000
on `127.0.0.1` only. Before the UI is reachable from anywhere else, turn on
its built-in login or put an authenticating reverse proxy in front of it,
and use HTTPS beyond your own LAN.

### Built-in login

One password for the whole UI (there are no user accounts). Make a hash of
it and put that in `.env`:

```bash
docker compose up -d                                       # once, so the container runs
docker compose exec frontend node scripts/hash-password.mjs   # asks twice, prints the hash
echo 'OMNISYNC_UI_PASSWORD_HASH=scrypt:15:8:3:...' >> .env    # the line it printed
docker compose up -d                                       # restart the UI with it
```

The script reads the password from the terminal (or standard input) and
prints only the hash, so the password never ends up in the shell history,
the process list or a log. It needs 12 characters or more; a few random
words work well. Without Docker: `node frontend/scripts/hash-password.mjs`
(Node 20+, no dependencies). The hash contains no `$`, so `.env` needs no
quoting.

With the login on, every page redirects to `/login` and every `/api`
request without a session answers `401`. Open without a session: the login
page, `/healthz` (the container health check), static files and the OAuth
callback `/api/wizard/oauth/callback` (the provider's redirect does not
carry the session cookie; the backend accepts only the one-time state of a
wizard session started from a logged-in UI). The sidebar gets a **Log out**
button; when a session expires the UI returns to the login page and, after
logging in, to the page you were on.

- **Sessions** are cookies signed with HMAC-SHA256: `HttpOnly`,
  `SameSite=Strict`, and `Secure` (named `__Host-omnisync-session`) when
  the UI is reached over HTTPS, directly or through a proxy that sets
  `X-Forwarded-Proto: https` for a host in `OMNISYNC_UI_ALLOWED_HOSTS`.
  They last `OMNISYNC_UI_SESSION_DAYS` (default 7) and are extended while
  you use the UI. They hold no secrets, only an id and times.
- **Signing key:** `OMNISYNC_UI_SESSION_SECRET` (32+ characters, e.g.
  `openssl rand -hex 32`) when set, else derived from the password hash.
  Either way sessions survive restarts of the (read-only) container, and a
  new password ends them all. This makes the hash as sensitive as the API
  token next to it in `.env`: whoever knows it can sign sessions.
- **Logout** ends the session in that browser and on the server. The list
  of logged-out sessions is kept in memory; after a restart of the UI a
  copy of an old cookie would work again until it expires. To end every
  session at once, change `OMNISYNC_UI_SESSION_SECRET` or the password.
- **Wrong passwords** get a generic answer and are logged with the client
  address (never the password). After 3 failures in a row an address must
  wait 1 s, then 2 s, 4 s, ... up to 15 minutes; the login page shows how
  long. Without a reverse proxy the client address comes from
  `X-Forwarded-For`, which a client can forge, so failures from all
  addresses together are also limited (a burst of 10, then one every 6 s).
  Such an attack can slow down your own login by a few seconds; existing
  sessions keep working. A long password is what really protects you.
- **CSRF:** the cookie is `SameSite=Strict`, and the login, like every
  state-changing request, must come from the UI's own origin as JSON.
- **`OMNISYNC_UI_PASSWORD`** (the plain password) also works, for a quick
  test. It logs a warning at start, and without `OMNISYNC_UI_SESSION_SECRET`
  every restart signs everyone out. If both are set, the hash wins. A hash
  the UI cannot read refuses every login (and logs why) instead of letting
  anyone in.

Then publish the port where you need it (e.g. `"3000:3000"` for the LAN in
a `docker-compose.override.yml`) and add the names or addresses you open it
under to `OMNISYNC_UI_ALLOWED_HOSTS` (e.g. `192.168.1.10,nas.lan`). Plain
`http://` on a LAN sends the password and the cookie unencrypted to anyone
who can watch the network; for anything beyond a trusted home network, use
HTTPS as below. The backend's port 8000 stays on loopback either way.

### HTTPS and reverse proxies

To serve the UI over HTTPS (needed outside a trusted LAN, and for Web Push
notifications on any address but `localhost`), put a reverse proxy on the
same host in front of `127.0.0.1:3000` that terminates TLS, and keep the
backend's port 8000 on loopback (the proxy only needs the UI). The proxy can
ask for its own login instead of, or in addition to, the built-in one. Then:

- Set `OMNISYNC_UI_ALLOWED_HOSTS` to the public name (in `.env` for compose,
  e.g. `OMNISYNC_UI_ALLOWED_HOSTS=sync.example.com`; add `:port` to allow
  only that port). The UI refuses `/api` requests for any other `Host`; it
  checks the `Host` header, not `X-Forwarded-Host`.
- The proxy must pass the browser's `Host` header on unchanged (Caddy does;
  in nginx use `$http_host`, which keeps a non-default port) and set
  `X-Forwarded-Proto`, so state-changing requests match their `Origin` and the
  OAuth redirect becomes `https://sync.example.com/api/wizard/oauth/callback`
  (register that at the provider for your own OAuth app).
- Allow requests of up to 20 minutes: syncs, backups and restores answer
  once they are under way, but a check or diff of a large folder answers
  when it is done (the backend stops it after `OMNISYNC_CHECK_TIMEOUT`, 15
  minutes by default; the UI's own limit is 20 minutes).
- With the built-in login, let the proxy append the client address to
  `X-Forwarded-For` (Caddy does; nginx: `$proxy_add_x_forwarded_for`), so
  failed logins are throttled per client rather than all at once.

Caddy (gets a certificate automatically). The `basic_auth` block is the
proxy's own login: leave it out when you use the built-in one, or create
the hash with `caddy hash-password`:

```caddyfile
sync.example.com {
	basic_auth {
		alice $2a$14$...hash...
	}
	reverse_proxy 127.0.0.1:3000
}
```

nginx (likewise, drop the two `auth_basic` lines with the built-in login, or
create the password file with `htpasswd -c -B /etc/nginx/omnisync.htpasswd alice`):

```nginx
server {
    listen 443 ssl;
    server_name sync.example.com;
    ssl_certificate     /etc/letsencrypt/live/sync.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sync.example.com/privkey.pem;

    auth_basic           "OmniSync";
    auth_basic_user_file /etc/nginx/omnisync.htpasswd;

    location / {
        proxy_pass         http://127.0.0.1:3000;
        proxy_set_header   Host              $http_host;
        proxy_set_header   X-Forwarded-Host  $http_host;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_read_timeout 20m;
    }
}
```

Other authentication in front of the UI works as well: an SSO proxy such as
Authelia or oauth2-proxy, or a VPN such as WireGuard or Tailscale (publish
port 3000 on the VPN address only and add the name or address you open to
`OMNISYNC_UI_ALLOWED_HOSTS`). What matters is that nothing reaches port 3000
without a login: the built-in one, the proxy's, or both.

## How syncing works

Each enabled profile has its own sync engine, in one of two **sync modes**:

- **Two-way** (recommended; the default for new profiles) uses
  [`rclone bisync`](https://rclone.org/bisync/): edits, new files and
  deletions on either side reach the other, and a file changed on both sides
  keeps both versions (`name.local-conflict1.ext` /
  `name.remote-conflict1.ext`, listed on the Conflicts page). Its first sync
  is a **resync** that makes both folders the union of the two and deletes
  nothing.
- **Mirror** (what profiles created before two-way sync keep until you switch
  them): local changes are pushed and the remote is pulled on the interval,
  each as a one-way mirror, so the side that syncs last wins.

A profile syncs after local changes settle (debounce, default 5 s), on its
interval (default 5 minutes) and on **Sync now**; **Push** and **Pull** stay
available as confirmed one-way overrides. It **pauses** instead of guessing
when something looks wrong (missing folder, delete limit, a needed resync,
pending differences in a mirror profile), and every sync is recorded as a
job with the files it changed. **Backups** to a local folder or another
remote are separate from syncing and can be restored.

The full rules (watcher, intervals, resyncs, pausing, startup check, retries,
stopping, backups) are in
[How Syncing Works](docs/gem/how-syncing-works.md).

## Sync safety

OmniSync checks before every sync, in both modes, and keeps what it replaces:

- **Refuses** to sync when one side is empty and the other is not, when the
  local folder is missing (it is never created; the profile pauses), or when
  the marker file `.omnisync-check` exists on one side only.
- **Keeps** every file a sync overwrites or deletes in
  `.omnisync-trash/<timestamp>/` in the destination folder for
  `OMNISYNC_TRASH_DAYS` (default 30).
- **Stops** at the delete limit, 50 files per sync by default
  (`OMNISYNC_MAX_DELETE`, or `--max-delete` per profile); a two-way sync
  checks the count with a dry run before it changes anything.
- **Fails closed**: a startup check that cannot compare both sides pauses
  automatic syncing, and only one sync, per-file action, backup or restore
  runs per profile at a time.

Every rule and its exceptions: [How Syncing Works, Sync
safety](docs/gem/how-syncing-works.md#sync-safety).

## Environment variables

Backend (set in `docker-compose.yml` / `.env`, or the environment when run
directly):

| Variable | Default | Purpose |
| --- | --- | --- |
| `OMNISYNC_API_TOKEN` | generated | API token clients send as `Authorization: Bearer <token>`; under 32 characters logs a warning |
| `OMNISYNC_API_TOKEN_FILE` | `/data/omnisync/api-token` | Where a generated token is stored |
| `OMNISYNC_ALLOWED_HOSTS` | (none) | Extra accepted `Host` names, comma-separated (compose adds `backend`) |
| `OMNISYNC_BROWSE_ROOTS` | home and `/sync` | Folders the folder picker may list, `:`-separated (compose: the sync folder). When set, new profile folders and local backup targets must be inside them |
| `OMNISYNC_API_DOCS` | off | `1` serves `/docs` and `/openapi.json` without a token (development only) |
| `OMNISYNC_OAUTH_REDIRECT_URI` | derived | OAuth callback URL for your own OAuth apps. Unset: built from the web UI's `X-Forwarded-*` headers, else from the request address. Set: every sign-in uses it |
| `OMNISYNC_DB_PATH` | `/data/omnisync/omnisync.db` | SQLite database |
| `OMNISYNC_CONFIG_PATH` | `/data/omnisync/config.toml` | Global settings and notification channels (may hold channel credentials) |
| `OMNISYNC_LOG_PATH` | `/data/omnisync/omnisync.log` | Log file (owner-only, mode 0600) shown on the Logs page; see [Logs](docs/gem/operations.md#logs) |
| `OMNISYNC_LOG_FORMAT` | `text` | `json` writes one JSON object per line (`ts`, `level`, `logger`, `msg`, `exc`, `request_id`, `fields`) for Loki, Elastic and the like |
| `OMNISYNC_LOG_MAX_BYTES` | `5242880` (5 MB) | Size at which the log file is rotated |
| `OMNISYNC_LOG_BACKUPS` | `3` | Rotated log files kept (`omnisync.log.1` ...) |
| `OMNISYNC_LOG_ACCESS` | off | `1` also writes uvicorn's access log (one line per request) to the log file; it always goes to `docker logs` |
| `OMNISYNC_RCLONE_CONFIG` | `/data/omnisync/rclone.conf` | OmniSync's own rclone config |
| `OMNISYNC_VAPID_DIR` | `/data/omnisync/vapid` | Web Push keys |
| `OMNISYNC_MAX_DELETE` | `50` | Files one sync may delete before it stops (two-way: per side, checked before the sync) |
| `OMNISYNC_BISYNC_DIR` | `/data/omnisync/bisync` | Two-way sync state (rclone bisync listings), one folder per profile |
| `OMNISYNC_TRASH_DAYS` | `30` | Days timestamped trash folders are kept; `0` never prunes |
| `OMNISYNC_CHECK_TIMEOUT` | `900` | Seconds a check/diff may take |
| `OMNISYNC_STARTUP_CHECK_TIMEOUT` | `120` | Seconds the startup check may take |
| `OMNISYNC_RATE_LIMIT_DELAY` | `30` | First retry delay in seconds after a rate limit |
| `OMNISYNC_MAX_RECORDED_CHANGES` | `10000` | File changes stored per job |
| `OMNISYNC_PUSH_HOSTS` | (none) | Extra Web Push service host names, comma-separated |
| `OMNISYNC_HOST_OS` | detected | Host OS for desktop notifications: `linux`, `macos`, `windows`, `android` |
| `DBUS_SESSION_BUS_ADDRESS` | (none) | Session bus for Linux desktop notifications (see `docker-compose.dbus.yml`) |

Docker compose only:

| Variable | Default | Purpose |
| --- | --- | --- |
| `OMNISYNC_SYNC_DIR` | `./sync` at `/sync` | Host folder mounted into the backend at the same path |
| `OMNISYNC_BIND_ADDRESS` | `127.0.0.1` | Host address the API port is published on |
| `PUID` / `PGID` | `1000` | User and group the backend runs and writes files as; `0` (root) logs a warning at every start |
| `TZ` | `UTC` | Time zone of both containers (log timestamps, schedules), e.g. `Europe/Berlin` |

Web UI server (all read per request; see
[Exposing the web UI / HTTPS](#exposing-the-web-ui--https) for the login):

| Variable | Default | Purpose |
| --- | --- | --- |
| `BACKEND_URL` | `http://127.0.0.1:8000` | Where `/api` requests go (compose sets `http://backend:8000`) |
| `OMNISYNC_API_TOKEN` | (none) | The backend's API token, added to every `/api` request |
| `OMNISYNC_UI_ALLOWED_HOSTS` | (none) | Extra `Host` names the UI serves `/api` for besides `localhost`, `127.0.0.1` and `[::1]`, comma-separated, each optionally with `:port` (no wildcards) |
| `OMNISYNC_UI_PASSWORD_HASH` | (none: no login) | Turns the login on: a hash from `node scripts/hash-password.mjs` |
| `OMNISYNC_UI_PASSWORD` | (none) | The login password in plain text, if no hash is set (logs a warning; prefer the hash) |
| `OMNISYNC_UI_SESSION_SECRET` | derived from the hash | Key that signs session cookies, at least 32 characters; changing it ends every session |
| `OMNISYNC_UI_SESSION_DAYS` | `7` | How long a session lasts without use; each use extends it |

Profile settings (sync mode, folders, debounce delay, pull interval, rclone
filters and flags, max retries), the log level and the job history retention
are set in the web UI or the TUI, not through the environment.

## Backing up OmniSync's own data

Everything OmniSync itself knows (profiles, job history, `rclone.conf` with
your remotes' **OAuth tokens and keys**, the API token, Web Push keys,
`config.toml` with notification credentials, two-way sync state) lives in
the `omnisync-data` volume at `/data/omnisync`. Keep any copy of it as
private as the accounts it can reach.

Back it up while the backend is stopped:

```bash
docker volume ls | grep omnisync-data        # e.g. omnisync_omnisync-data
docker compose stop backend
docker run --rm -v omnisync_omnisync-data:/data:ro -v "$PWD":/backup alpine \
  tar czf /backup/omnisync-data-$(date +%F).tar.gz -C /data .
docker compose start backend
```

What each file is, copying only the database while running, restoring,
the automatic copies taken before migrations, job history retention and
clean shutdown are in [Operations](docs/gem/operations.md#backing-up-omnisyncs-own-data).

## Uninstall

```bash
docker compose down                # stop and remove the containers; data is kept
docker compose down -v --rmi all   # also delete the omnisync-data volume and the images
rm ~/.local/bin/osync              # the TUI, if installed; its settings: ~/.config/osync
```

Installed with the installer: `bash install.sh uninstall` stops and removes
the containers and keeps the data volume and `.env`; `uninstall --purge`
also deletes the volume, `compose.yml` and `.env` (it asks you to type the
volume's name).

`down -v` deletes the database and `rclone.conf` for good; back them up
first if you may come back. Your files are not touched: the synced folders
stay, locally and in the cloud, each with OmniSync's marker file
`.omnisync-check` and trash folder `.omnisync-trash/` (which may still hold
replaced or deleted files), and backups stay at their targets. Delete
those by hand if you no longer want them. OAuth sign-ins stay valid until
revoked: remove OmniSync's (rclone's) access in your Google, Microsoft or
Dropbox account settings.

## Terminal UI

`osync` is a full-screen terminal client with the same views as the web UI,
plus scripting subcommands, all with `--json`: `status`, `health`,
`profiles`, `jobs`, `push`, `pull`, for two-way profiles `sync` and
`resync`, `profile show|enable|disable|stop|check|diff|resume|manual-flags`,
`conflicts` (`resolve ID --keep local|remote|both|dismiss`), `remotes`
(`test`, `about`), `backups` (`run`, `snapshots`, `restore`, with `--wait`),
`logs` (`--level`, `--category`, `--follow`), `notifications test` and `completion
bash|zsh|fish`. `osync profiles` shows each profile's mode; `osync sync
<profile>` runs a two-way sync and `osync resync <profile> --yes` a resync
(without `--yes` it asks in a terminal and refuses otherwise; so does
`backups restore`). The exit status is 0 on success, 1 on an error or a
failed job, 2 for wrong arguments, 3 when the request was refused (HTTP 409
or not confirmed) and 4 when the object does not exist (HTTP 404). Download it from a release (see
[The `osync` binary](#the-osync-binary)), or build and install it with Go 1.25:

```bash
make -C tui install     # ~/.local/bin/osync-tui and the alias ~/.local/bin/osync
OMNISYNC_API_KEY=<token> osync
```

`osync --version` prints the version and the commit it was built from;
`osync health` also shows the backend's version.

| Flag | Environment | Purpose |
| --- | --- | --- |
| `--url` | `OMNISYNC_URL` | Backend URL (default `http://127.0.0.1:8000`) |
| `--api-key` | `OMNISYNC_API_KEY` | The backend's API token |
| `--theme` | `OMNISYNC_THEME` | `dark` or `light` |
| `--ascii` | `OMNISYNC_ASCII_MODE` | ASCII borders and symbols |
|  | `OMNISYNC_LOG_FILE` | Log file for the TUI itself |

Settings can also live in `~/.config/osync/tui.toml`. See
[`tui/README.md`](tui/README.md) and [`tui/USER-MANUAL.md`](tui/USER-MANUAL.md)
for the keys, views and commands.

## Development

```text
backend/     FastAPI app; tests in backend/tests, migrations in backend/migrations
frontend/    Next.js web UI
tui/         Go terminal UI
docs/        user guide (docs/gem) and the API error codes
scripts/     container entrypoint, OpenAPI generator, release scripts (scripts/release)
```

[CONTRIBUTING.md](CONTRIBUTING.md) has a code map, the development setup of
the backend, the web UI and the TUI, the checks CI runs and how to run them
locally, the changelog and commit conventions, and how a release is made;
[AGENTS.md](AGENTS.md) is the short version for coding agents. Report security problems privately as described in
[SECURITY.md](SECURITY.md), not in an issue.

## License

OmniSync is free software: you can redistribute it and/or modify it under
the terms of the [GNU General Public License v3.0](LICENSE) (GPL-3.0-only).
It comes with no warranty; see the license for details.
