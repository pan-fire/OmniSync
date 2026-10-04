# Operations

Running OmniSync day to day: the containers' settings, the logs, backing
up OmniSync's own data, and running it without Docker. Installing and the
environment variables are in the [README](../../README.md#install-a-release);
how syncs behave is in [How Syncing Works](how-syncing-works.md).

## Install, update and uninstall with the installer

[`scripts/install.sh`](../../scripts/install.sh) sets up the released
images with docker compose, and later updates, checks and removes them.
Run it piped from GitHub or as a downloaded file:

```bash
curl -fsSL https://raw.githubusercontent.com/pan-fire/OmniSync/main/scripts/install.sh | bash -s -- [command] [options]
bash install.sh [command] [options]
```

| Command | What it does |
| --- | --- |
| *(none)* | Installs; on an existing install, updates it when a newer release exists (after asking), else starts it if it is stopped |
| `install` | Installs; leaves an existing install alone |
| `update` | Updates to the latest release, or to `--version X.Y.Z` |
| `status` | Installed and latest version, both containers' health, URLs, folder, data volume, sync folder |
| `uninstall` | Stops and removes the containers; keeps the data volume and `.env`. `--purge` also deletes the volume, `compose.yml` and `.env` |

**What it writes.** Everything lives in one folder, `~/omnisync` (as root
`/opt/omnisync`; `--dir` for another), mode 0700:

- `compose.yml`, the release's [`deploy/compose.yml`](../../deploy/compose.yml);
- `.env` (mode 0600): `COMPOSE_PROJECT_NAME` (default `omnisync`, so the
  data volume is `omnisync_omnisync-data`), `OMNISYNC_VERSION`, a generated
  `OMNISYNC_API_TOKEN` (32 random bytes, hex), `PUID`/`PGID` of the user who
  ran it (through `sudo` too; never 0), `TZ` from the system,
  `OMNISYNC_SYNC_DIR`, `OMNISYNC_BIND_ADDRESS` (default `127.0.0.1`) and
  `OMNISYNC_API_PORT`/`OMNISYNC_WEB_PORT`; with a web UI password also
  `OMNISYNC_UI_PASSWORD_HASH` (made by the web image's
  `scripts/hash-password.mjs`; the password itself is stored nowhere) and
  a generated `OMNISYNC_UI_SESSION_SECRET`;
- `backups/`: the data volume's backups taken before updates.

The settings flags (`--sync-dir`, `--bind-address`, `--api-port`,
`--web-port`, `--project-name`, `--ui-password-prompt`) apply to a fresh
install; afterwards edit `.env` and run `docker compose up -d` in the
folder. The installer never prints the token or the password.

**What it downloads, and the checks.** Only from GitHub: the API's
"latest release" answer and the release's assets
(`github.com/pan-fire/OmniSync/releases/download/v<version>/...`), plus the
images from GHCR. It checks `compose.yml` against the release's
`SHA256SUMS` and refuses a mismatch; with [cosign](https://docs.sigstore.dev/)
installed it first verifies the Sigstore signature of `SHA256SUMS` (the
same check as the README's "Verify a release"), which is what ties the
file to the release workflow. Releases before 0.12.0 attach no
`compose.yml`; for them (0.11.0 is the oldest the installer accepts) it
uses the copy it carries, which is the same file.

**Updating.** `update` (or running the installer again) shows the installed
and the latest version and the release notes link, asks, and then:

1. downloads and checks the new `compose.yml` and pulls the new images;
2. stops the backend and backs up the data volume into
   `backups/omnisync-data-<old version>-<time>.tar.gz` (mode 0600; it holds
   your credentials), unless `--no-backup`;
3. puts the new `compose.yml` in place, changes only the
   `OMNISYNC_VERSION` line of `.env`, and starts the new version;
4. waits for both health checks (`--health-timeout`, default 180 s). If
   they fail, it puts back the previous `compose.yml` and version and starts
   the old version again. A database the new version already migrated may
   keep the old one from starting: then restore the backup as in
   [Restore](#backing-up-omnisyncs-own-data) below.

Downgrades are refused. Read the release's upgrade notes before you
confirm.

**Without a terminal.** Prompts are read from the terminal even when the
script is piped into bash. Without one (cron, provisioning), `--yes` takes
the defaults and confirms updates; `uninstall --purge` then needs
`--yes` too, and in a terminal it always asks you to type the volume name.
`--dry-run` prints every step without changing anything.

**Preflight.** It refuses to run, saying what to do, without Linux or
macOS (WSL counts as Linux), on other architectures than amd64 and arm64,
without a reachable Docker daemon, the compose v2 plugin, curl or wget, or
a SHA256 tool, and when the ports are taken. When your user may not use
Docker, it shows the `usermod -aG docker` fix; it never runs `sudo` itself.

**An install built from the source.** When it finds OmniSync containers
built from a clone, it explains how to switch without losing data: stop
them with `docker compose down` (never `-v`), then run the installer with
`--project-name <their project>` (e.g. `omnisync` for a clone in
`OmniSync/`), which reuses that project's `omnisync-data` volume, and the
same `--sync-dir` as before, so the profiles' folders keep their paths.
The API token is new: give it to `osync`.

**osync.** `--osync` also installs the terminal client for your system to
`~/.local/bin/osync` (as root `/usr/local/bin`), checked against
`SHA256SUMS`, and prints how to hand it the token.

## The containers

The release images are multi-platform: `linux/amd64` (x86-64 PCs and
servers) and `linux/arm64` (a Raspberry Pi 4/5 with a 64-bit OS, most
current NAS models) under the same tag; Docker pulls the right one by
itself. 32-bit ARM (`armv7`) is not supported.

**User.** The backend's entrypoint starts as root only to give the data
volume to `PUID`/`PGID` and then runs OmniSync as that user (with `gosu`);
files that syncs create in your folders belong to that user. Set both to
your own ids (`id -u`, `id -g`). `PUID=0` runs OmniSync as root and makes the
entrypoint print a warning at every start; do not use it. On start the
entrypoint changes the owner only of entries in `/data/omnisync` that belong
to someone else (after a `PUID` change, a restore, or a `docker exec` as
root), so a normal start rewrites nothing. A container started with
`user: "1000:1000"` (or `docker run -u`) skips all of this and runs as that
user; the volume must then already belong to it. The web UI always runs as
the unprivileged `node` user (1000:1000).

**Time zone.** Both images default to UTC. Set `TZ` in `.env`
(e.g. `TZ=Europe/Berlin`); compose passes it to both containers, and the
backend's log timestamps and schedules follow it.

**Health checks.** Both images have a built-in `HEALTHCHECK`, the same one
`docker-compose.yml` sets, so `docker ps` shows `healthy` also without
compose. The backend's checks `GET /health` (no token needed), which answers
503 when the database or the rclone binary is missing; it never calls a
cloud provider. The web UI's fetches `/healthz`, which checks the Next.js server
only (it answers without a login and without asking the backend). `docker inspect --format '{{json .State.Health}}' <container>` shows
the last results.

**Resource limits.** None are set by default. `docker-compose.yml` has
commented `deploy.resources.limits` examples for both services; copy them
into a `docker-compose.override.yml` to use them. rclone's memory grows with
the number of files a sync or check lists, and a two-way sync holds both
sides' listings, so size the backend's memory limit for your largest
profile: an out-of-memory kill in the middle of a two-way sync costs a
resync.

**Desktop notifications.** The backend image includes `dbus` and
`libnotify-bin` (`notify-send`, a few MB) for native Linux desktop
notifications through the host's session bus. They are used only with the
opt-in `docker-compose.dbus.yml`, which mounts the bus socket and disables
AppArmor for the container; webhook, ntfy, email and Web Push notifications
need neither.

**Stopping.** On `docker compose stop` the backend stops running syncs
cleanly; a two-way sync gets up to 45 s, hence the backend's
`stop_grace_period: 90s` (Docker's default of 10 s would kill it, and the
next sync would need a resync). Jobs that were still running when OmniSync
was killed or crashed are marked failed at the next start, with the error
"Interrupted: OmniSync stopped while it ran."

## Logs

### Where they go

**Backend.** Every line goes to two places: the container's standard error
(`docker compose logs backend`) and the log file `/data/omnisync/omnisync.log`
(`OMNISYNC_LOG_PATH`), which the web UI's **Logs** page, the terminal UI's
Logs view and `osync logs` read. Both get the same lines:

- OmniSync's own messages (syncs, backups, notifications, the remote wizard);
- the audit trail of user actions (below);
- the web server's start, stop and errors (uvicorn);
- warnings and errors of the libraries OmniSync uses (scheduler, database,
  HTTP client, file watcher, migrations);
- every unhandled exception, with its traceback: in a request, in a
  background task or in a thread. On the Logs page the traceback is under
  the entry's **Details**.

The access log (one line per HTTP request, including the container health
check every 30 s) goes to `docker compose logs backend` only;
`OMNISYNC_LOG_ACCESS=1` writes it to the file too.

**Web UI server.** Its own events go to `docker compose logs frontend`
(below); they are not in the backend's log file.

**The file** is readable by its owner only (mode 0600, the `PUID` user),
like the other files in the data folder that can hold sensitive data. It
is rotated at `OMNISYNC_LOG_MAX_BYTES` (default 5 MB) into
`omnisync.log.1`, `.2`, ... keeping `OMNISYNC_LOG_BACKUPS` (default 3) of
them. The Logs page, the terminal UI's Logs view and `osync logs --skip N`
page back through the current file and then the rotated ones, newest
first; the level and category filters apply across all of them.

### Levels

The **Log level** setting (web UI: Config; terminal UI: Config, key 8; or
`PUT /config` with `log_level`) sets OmniSync's own level: `DEBUG`, `INFO` (the default),
`WARNING`, `ERROR` or `CRITICAL`, applied at once. Libraries always log
warnings and errors; at `DEBUG` the scheduler, file watcher and migrations
add their INFO lines. SQL statements and the URLs of outgoing HTTP
requests are never logged, at any level. The audit trail is recorded
whatever the level.

### Text and JSON format

The default is one line per entry, with the request id (below) when the
line was written during an API request, and the traceback on the lines
that follow:

```text
2026-10-04 11:50:13,660 - INFO - backend.main - OmniSync 0.11.0 backend started (0 profile engine(s))
2026-10-04 11:50:13,980 - WARNING - backend.audit - [req:my-req-0001] profile.delete profile=nope status=404 code=profile_not_found outcome=refused client=127.0.0.1
2026-10-04 11:50:13,973 - INFO - uvicorn.access - [req:4da3e65b6242748f] 127.0.0.1:38436 - "GET /profiles?token=*** HTTP/1.1" 401
```

Timestamps follow `TZ`. With `OMNISYNC_LOG_FORMAT=json` every entry is one
JSON object on one line, with the time in UTC:

```json
{"ts": "2026-10-04T09:50:30.397Z", "level": "INFO", "logger": "backend.audit", "msg": "settings.update fields=[log_level] log_level=DEBUG outcome=ok client=127.0.0.1", "request_id": "2045eb91b62789e2", "fields": {"action": "settings.update", "fields": ["log_level"], "log_level": "DEBUG", "outcome": "ok", "client": "127.0.0.1"}}
```

| Key | Content |
| --- | --- |
| `ts` | ISO 8601 time in UTC |
| `level` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |
| `logger` | Where the line comes from: `backend.*` (OmniSync), `backend.audit` (the audit trail), `uvicorn.error`, `uvicorn.access`, a library's name |
| `msg` | The message |
| `exc` | The traceback, when there is one |
| `request_id` | The id of the API request it was written in, when there is one |
| `fields` | Structured values (the audit trail's action, targets, outcome and client) |

The Logs page reads both formats, also a file that switched from one to
the other.

### Request ids

Every API answer has an `X-Request-ID` header, and every error answer has
the same id as `request_id` in its body ([API errors](../api-errors.md)).
Lines the backend writes while handling the request carry it
(`[req:<id>]` in the text format). When the web UI shows a server error, its
message ends in `(ID <id>)`; the terminal UI says `(request <id>)`. Search
the Logs page (or `grep <id> omnisync.log`) for it to find what happened.
A client may send its own `X-Request-ID` (8 to 64 letters, digits, `.`, `_`
and `-`); anything else is replaced by a new id.

### The audit trail

User actions are recorded by the `backend.audit` logger, in the same file:
which action, on which profile, remote, backup target or conflict, how it
ended (`ok`, `refused` with the error code, or `failed`), the request id
and the client address. The Logs page (category **User actions**), the
terminal UI's Logs view (`c`) and `osync logs --category audit` show only
these lines. Recorded are:

- syncs: start (with direction and `force`), resync, stop, selective sync,
  pause and resume, pause-all and resume-all;
- profiles: create, update (the names of the changed fields), delete,
  enable, disable;
- remotes: create, edit (the names of the changed settings), reconnect,
  import, delete;
- backups: target create, update and delete, run, restore, single-file
  restore;
- trash restore and delete (how many items, not which), conflict
  resolution;
- notification settings (which channels) and Web Push subscriptions;
- settings, including log level changes;
- rejected API tokens (`auth.token_rejected`, at most one line per client
  address and minute, with the number of attempts).

The client address is the backend's TCP peer: for actions taken in the web
UI that is the web UI container, whose own log has the browser's address
of logins (below).

### The web UI server's log

The Next.js server writes its own events to its standard output
(`docker compose logs frontend`), one JSON object per line in the same
shape as the backend's JSON format, with the event in `fields.event`:

| Event | When |
| --- | --- |
| `proxy.refused` | An `/api` or `/auth` request was refused: `reason` `host` (Host not allowed), `origin` (cross-site) or `content_type` (a body that is not JSON); with method, path (no query string), Host, Origin and client address |
| `auth.login`, `auth.logout` | A login or logout, with the client address |
| `auth.login_failed`, `auth.login_throttled` | A wrong password, a login refused while the address waits (`retry_after`) |
| `auth.login_error`, `auth.config` | The password check failed; a login setting is wrong or weak |

Each event type is limited to 20 lines a minute; the next line after a
pause says how many were `dropped`. These events stay in the frontend
container's log: forwarding them into the backend's log file would need
a new write endpoint on the backend, which the UI server would call with
the full API token, for little gain; a log shipper collects both
containers' logs anyway (below).

### Shipping logs to Loki, Grafana or Elastic

Set `OMNISYNC_LOG_FORMAT=json` and collect the containers' output, which
has every backend line (and the web UI server's events) as JSON:

- **Grafana Loki** with Grafana Alloy or Promtail: discover the containers
  through the Docker socket, then parse with a `json` stage and promote
  `level` and `logger` to labels (keep `request_id` a field: one label value
  per request would explode Loki's index). In Grafana, a query such as
  `{container="omnisync-backend-1"} | json | logger="backend.audit"` (the
  container name depends on your compose project) shows the audit trail.
- **Elastic** with Filebeat: a `container` input on
  `/var/lib/docker/containers/*/*.log` with the `ndjson` parser (or
  `decode_json_fields` on `message`); `ts` is the timestamp.
- Without Docker's log driver: read `omnisync.log` itself (mode 0600, so
  the shipper runs as the `PUID` user or root) with the same JSON parsing.
  It is rotated by renaming, which both shippers follow.

### What is never logged

Secrets are masked in every line before it is written, on both outputs and
in both formats, also in tracebacks, structured fields and the access log:

- the API token, also where it appears without a name;
- `Authorization` headers and `Bearer` tokens;
- OAuth access and refresh tokens and token JSON;
- values of fields named like a secret (`password`, `pass`, `passphrase`,
  `secret`, `key`, `token`, `client_secret`, `access_token`, ...) in
  `name=value`, JSON or config form, including rclone connection strings;
- credentials in URLs (`https://user:password@host`);
- backup passphrases (plain and obscured), notification passwords, the ntfy
  token and webhook header values, once OmniSync has loaded them.

They show as `***`. The audit trail records names and ids only (never
request bodies, remote settings, paths of restored or trashed files, or
passwords), and its values are cleaned of line breaks so they cannot forge
log lines. The web UI server never logs passwords, session ids, cookies or
query strings. The backend does not know the web UI's session secret or
password; they never reach it.

The log still holds file and folder paths, profile and remote names and
client addresses: treat it, and anything you ship it to, as private.

## Backing up OmniSync's own data

Your synced files live in your folders and in the cloud. Everything
OmniSync itself knows lives in the `omnisync-data` volume, mounted at
`/data/omnisync`:

| Path | What |
| --- | --- |
| `omnisync.db` (with `-wal`/`-shm` while running) | Profiles, backup targets, job history, conflicts, notification history, push subscriptions |
| `rclone.conf` | Your remotes, **including OAuth tokens and keys** |
| `rclone.conf.bak` | The previous `rclone.conf`, kept on every change OmniSync makes |
| `api-token` | The generated API token (when `OMNISYNC_API_TOKEN` is not set) |
| `vapid/` | Web Push signing keys; new keys mean every browser must subscribe again |
| `config.toml` | Log level, history retention, notification channels, **including webhook headers, the ntfy token and the SMTP password** (written mode 0600 once it holds one) |
| `bisync/` | Two-way sync state; lost state only costs a resync |
| `omnisync.db.pre-<revision>.bak` | Copies taken before database migrations (below) |
| `omnisync.log*` | The log file and its rotated copies (see [Logs](#logs)) |

The volume holds credentials: keep any copy of it as private as the
accounts it can reach (e.g. `chmod 600` the archive, or encrypt it).

**Back up** while the backend is stopped, so the database is consistent.
Compose prefixes volume names with the project (the folder name), so find
the real name first:

```bash
docker volume ls | grep omnisync-data        # e.g. omnisync_omnisync-data
docker compose stop backend
docker run --rm -v omnisync_omnisync-data:/data:ro -v "$PWD":/backup alpine \
  tar czf /backup/omnisync-data-$(date +%F).tar.gz -C /data .
docker compose start backend
```

To copy only the database without stopping, use SQLite's backup API (a
plain file copy of a running database can miss what is still in the WAL):

```bash
docker compose exec backend python -c "import sqlite3; \
  sqlite3.connect('/data/omnisync/omnisync.db').backup(sqlite3.connect('/data/omnisync/omnisync-copy.db'))"
docker compose cp backend:/data/omnisync/omnisync-copy.db .
```

(`sqlite3 omnisync.db ".backup omnisync-copy.db"` does the same where the
`sqlite3` command is installed.)

**Restore** into a stopped backend, with the same or a newer OmniSync version
than the one that made the backup (newer versions migrate the database on
start; older ones cannot read a newer schema):

```bash
docker compose stop backend
docker run --rm -v omnisync_omnisync-data:/data -v "$PWD":/backup alpine \
  sh -c 'find /data -mindepth 1 -delete && tar xzf /backup/omnisync-data-2026-10-02.tar.gz -C /data'
docker compose start backend
```

The backend fixes the files' owner (`PUID`/`PGID`) on start. On a new
machine, set the same `OMNISYNC_API_TOKEN` in `.env` (or restore
`api-token`) and mount the sync folders at the same paths.

**Automatic database copies.** Before applying database migrations (after
an upgrade), the backend copies the database to
`omnisync.db.pre-<revision>.bak`, named after the revision it migrates to;
the three newest copies are kept. To go back to the previous release after a
failed upgrade: stop the backend, copy that file over `omnisync.db`, delete
`omnisync.db-wal` and `omnisync.db-shm`, and start the previous release.
Likewise `rclone.conf.bak` brings back the remotes as they were before
OmniSync's last change to them.

**Job history** (sync and backup jobs with their file lists and errors) is
kept for 90 days by default: **Config → Keep job history (days)** in the web
UI, `e` on the TUI's Config view, or `history_days` in `config.toml` (`0`
keeps everything). The newest 100 jobs of each profile and backup target,
jobs with an unresolved conflict and each target's newest completed backup
are kept regardless. Notification history older than this goes too (it is
also capped at the newest 100 entries). The cleanup runs at startup and once
a day, and compacts the database when much of it has become free space.

## Running without Docker

Docker compose is the supported way to run OmniSync, but the backend and the
web UI are an ordinary Python app and an ordinary Node.js server. This is a
sketch for a Linux host with systemd; adapt the paths to taste.

You need Python 3.12, Node.js 22 with pnpm 10 (only to build the web UI),
and [rclone](https://rclone.org/install/) on the `PATH` (the images use
rclone 1.75.1). For native desktop notifications also `notify-send`
(`libnotify-bin`).

**1. A user, the code and the data folder.** OmniSync runs as its own user,
which needs write access to the data folder and to every folder it syncs.

```bash
sudo useradd --system --home-dir /var/lib/omnisync --create-home --shell /usr/sbin/nologin omnisync
sudo git clone https://github.com/pan-fire/OmniSync.git /opt/omnisync   # or a release tag
sudo python3.12 -m venv /opt/omnisync/.venv
sudo /opt/omnisync/.venv/bin/pip install -r /opt/omnisync/backend/requirements.lock
sudo install -d -o omnisync -g omnisync -m 0700 /data/omnisync
```

The data folder here is `/data/omnisync`, as in the image; every file in it
follows an `OMNISYNC_*` variable (below), so it can live elsewhere. Keep
them all in that one folder, owned by `omnisync` and readable by nobody else
(it holds your cloud credentials).

**2. The backend** (`/etc/systemd/system/omnisync-backend.service`). Run
directly, it listens on `127.0.0.1` and accepts only the host names
`localhost`, `127.0.0.1` and `::1` unless `OMNISYNC_ALLOWED_HOSTS` says
otherwise; the [README](../../README.md#environment-variables) lists every
variable.

```ini
[Unit]
Description=OmniSync backend
After=network-online.target
Wants=network-online.target

[Service]
User=omnisync
Group=omnisync
WorkingDirectory=/opt/omnisync
# Token, and anything else you would put in .env: one VAR=value per line,
# mode 0600, e.g. OMNISYNC_API_TOKEN=<openssl rand -hex 32>
EnvironmentFile=/etc/omnisync/omnisync.env
Environment=OMNISYNC_DB_PATH=/data/omnisync/omnisync.db
Environment=OMNISYNC_CONFIG_PATH=/data/omnisync/config.toml
Environment=OMNISYNC_LOG_PATH=/data/omnisync/omnisync.log
Environment=OMNISYNC_RCLONE_CONFIG=/data/omnisync/rclone.conf
Environment=OMNISYNC_API_TOKEN_FILE=/data/omnisync/api-token
Environment=OMNISYNC_VAPID_DIR=/data/omnisync/vapid
Environment=OMNISYNC_BISYNC_DIR=/data/omnisync/bisync
# The folders profiles may use (':'-separated).
Environment=OMNISYNC_BROWSE_ROOTS=/srv/sync
Environment=OMNISYNC_HOST_OS=linux
ExecStart=/opt/omnisync/.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
# A two-way sync gets 45 s to stop cleanly (see "Stopping" above).
TimeoutStopSec=90
Restart=on-failure
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
# The data folder, the home (rclone's cache) and every synced folder.
ReadWritePaths=/data/omnisync /var/lib/omnisync /srv/sync

[Install]
WantedBy=multi-user.target
```

`ProtectSystem=strict` makes the whole file system read-only for the
service except `ReadWritePaths`, so add every folder a profile or a local
backup target uses there (and to `OMNISYNC_BROWSE_ROOTS`). Folders under
`/home` need `ProtectHome=false` instead of `read-only`. `UMask=0077` makes
the files OmniSync pulls readable only by `omnisync`; use `0022` (or put
`omnisync` in a shared group and use `0007`) if other users need them.

**3. The web UI.** Build the standalone server once (and after each
update), as the image does:

```bash
cd /opt/omnisync/frontend
sudo pnpm install --frozen-lockfile
sudo pnpm build
sudo cp -r .next/static .next/standalone/.next/
sudo cp -r public .next/standalone/
```

`/etc/systemd/system/omnisync-web.service`:

```ini
[Unit]
Description=OmniSync web UI
After=omnisync-backend.service
Wants=omnisync-backend.service

[Service]
User=omnisync
Group=omnisync
WorkingDirectory=/opt/omnisync/frontend/.next/standalone
# The same OMNISYNC_API_TOKEN as the backend; the server adds it to /api
# requests, the browser never sees it.
EnvironmentFile=/etc/omnisync/omnisync.env
Environment=NODE_ENV=production
Environment=NEXT_TELEMETRY_DISABLED=1
Environment=HOSTNAME=127.0.0.1
Environment=PORT=3000
Environment=BACKEND_URL=http://127.0.0.1:8000
ExecStart=/usr/bin/node server.js
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

Use the path of your `node` binary (`command -v node`). `HOSTNAME=127.0.0.1`
keeps the UI on loopback; to reach it from other machines, follow
[Exposing the web UI / HTTPS](../../README.md#exposing-the-web-ui--https).

**4. Start and check.**

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now omnisync-backend omnisync-web
curl -s http://127.0.0.1:8000/health      # {"status":"ok", ...}
journalctl -u omnisync-backend -f
```

Then open <http://127.0.0.1:3000>. Database migrations run when the backend
starts. To update: stop both services, `git -C /opt/omnisync pull` (or check
out the new tag), reinstall `backend/requirements.lock` into the venv,
rebuild the web UI as in step 3, and start them again. Back up
`/data/omnisync` as described above, with the services stopped instead of
the container.

---

[Back: How Syncing Works](how-syncing-works.md) | [User Guide](index.md)
