# Operations

Running OmniSync day to day: the containers' settings, backing up
OmniSync's own data, and running it without Docker. Installing and the
environment variables are in the [README](../../README.md#install-a-release);
how syncs behave is in [How Syncing Works](how-syncing-works.md).

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
| `omnisync.log*` | The log file |

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
