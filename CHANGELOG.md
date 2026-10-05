# Changelog

All notable changes to OmniSync are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0.0, a
minor version may change the API or the database schema (migrations run on
start).

Add each change under `[Unreleased]` in the pull request that makes it;
`scripts/release/prepare.sh` turns that section into the next version's
release notes.

## [Unreleased]

### Changed

- **A differing file with a modification time on one side only is a
  conflict.** When rclone gave a readable modification time for only one
  copy of a file that differs, the diff counted it as changed on that side
  ("modified local" or "modified remote"), although nothing showed that the
  other copy was unchanged, so a push or pull could overwrite a change. It
  is now listed as a conflict, for you to review, like a file with no
  readable time on either side.
- **Every sync job and conflict record belongs to a profile.** A database
  migration (0012) makes the profile required on sync jobs and conflict
  records. OmniSync has always set it since 0.12.0, and deleting a profile
  deletes its history, so a row without a profile could be neither shown
  under a profile nor acted on. Any such leftover rows are removed (with
  their file changes and errors; the count is logged), and the copy of the
  database taken before migrating still has them.

### Fixed

- **A failed desktop notification shows its real error.** The notifiers
  read the tool's error output as strict UTF-8, so on a Windows host in a
  language other than English (PowerShell writes in the console code page)
  the error became a text-decoding error instead of the reason. Undecodable
  bytes are now replaced, on every platform.
- **Linux desktop notifications about files starting with `-` arrive.**
  `notify-send` read a title or body starting with `-` (a file named
  `-draft.txt`) as an option and refused it, so the notification was lost.
  The text now follows `--`.
- **The sync test probes the folder the profile syncs.** For remotes tested
  through rclone (SFTP, SMB, local and the like), the leading `/` of the
  remote path was dropped, so `server:/srv/data` was tested as
  `server:srv/data` under the remote's home folder, where the test could
  create folders that were never meant to exist. The rclone test now uses
  the path exactly as the sync does. The Google Drive, Dropbox and OneDrive
  tests also ignore a trailing or doubled `/` now.
- **A sync test that leaves its test file behind fails.** When rclone could
  not delete the `.omnisync-test-…` file from the remote, the test still
  passed and the file stayed there; the Google Drive, Dropbox and OneDrive
  tests already failed in that case. Both now fail at the cleanup step
  ("The test file could not be removed from the remote."), and the log
  names the file.
- **The trash list says when rclone is not available.** GET
  /profiles/{slug}/trash answered 502 `rclone_failed` (and logged a crash)
  while the backend's rclone service was not running, for example during
  start-up; it now answers 503 `service_unavailable`, like restoring and
  deleting from the trash.
- **One trash restore that cannot keep the file in its place no longer
  stops the others.** If the trash already held a version of a file for
  every second of the hour after a restore, restoring it raised an
  internal error that ended the whole batch with a 500. That file is now
  reported as failed (nothing moved, with a message that says why) and the
  other selected files are still restored.
- **A hung helper process no longer stays behind.** When `rclone` did not
  answer within its time limit in the network check (GET
  /health/network) or while storing a password, OmniSync gave up waiting
  but left the process running; each check could add one. Desktop
  notifiers that hang were killed but not reaped. All of them are now
  killed and reaped when their time is up or the request is cancelled.

### Security

- **Windows toasts no longer build PowerShell code from file names.** The
  title and body used to be pasted into the PowerShell command as quoted
  text, with only `'` escaped. PowerShell also ends a quoted string at the
  typographic quotes `‘ ’ ‚ ‛`, so a file named `Bob’s report.docx` broke
  the toast, and a crafted file name could run PowerShell commands on the
  Windows host. The command is now fixed text, and the title and body
  reach it as environment variables, so they are only ever data.

## [0.13.0] - 2026-10-04

### Changed

- **The Logs page pages into the rotated log files.** GET /logs goes on
  past the start of `omnisync.log` into `omnisync.log.1`, `.2`, ... in
  order, reading each from the end only as far as the page needs; the level
  and category filters apply across all of them. The web Logs page and the
  terminal UI's Logs view now reach every entry still on disk, and
  `osync logs --skip N` pages back too.
- **The audit trail names the browser for web UI actions.** The web UI's
  proxy now sends the browser's address in an `X-OmniSync-Client` header,
  signed with the API token (HMAC over the address and the time, valid for
  60 seconds). The backend records it as `client` (with the web UI
  container as `via`) only when the request has the right token and the
  signature checks out; authentication and throttling still use the TCP
  peer. See [The audit trail](docs/gem/operations.md#the-audit-trail).
- **The wizard and the remote edit check what the import checks.**
  Creating a remote (POST /wizard/create) or editing one (PUT
  /remotes/{name}) now refuses, like the rclone.conf import, file settings
  such as SFTP's `key_file` that point into OmniSync's data directory or
  contain `$`, and crypt, alias or union style remotes that point at a
  `local` remote or wrap a local path, directly or through a chain
  (422 `invalid_params`). The checks are shared with the import
  (`services/rclone_import.py`).
- **Leftover `.partial` files are cleaned up.** When a push, pull or
  two-way sync succeeds after a run that failed, was stopped or was
  killed, rclone's leftover in-progress files of that run
  (`<name>.<8 hex>.partial`, the pattern every sync already skips) are
  deleted from both of the profile's folders, if they are older than the
  run's start and outside the trash. Nothing else is touched (a file named
  `notes.partial` stays). The log records how many were removed, with the
  job id.
- **The web UI image runs on Node.js 24 LTS** (24.21.0), up from Node 22,
  and takes Debian's security updates at build time like the backend image.
  CI builds and tests the web UI on Node 24 too.
- **CI tests the installer end to end.** A new job builds both images from
  the pull request and runs `scripts/install.sh` against them in a
  temporary folder on non-default ports: install, `status`, a second run
  (up to date) and `uninstall --purge --yes`, checking the health checks,
  the API token and the web UI's path to the API. The installer's
  test-only `OMNISYNC_INSTALL_TEST_RELEASE_DIR` (a local stand-in for the
  release, see CONTRIBUTING.md) makes this possible without downloads.

### Removed

- **Compatibility with builds before 0.12.0.** No installation predates
  0.12.0, so the paths that only served older installs, data or clients
  are gone; a 0.12.0 install behaves as before.
  - Database: migrations 0001 to 0010 are squashed into one baseline that
    creates the 0.12.0 schema. It keeps the ID `0010_backups`, so 0.12.0
    databases need no migration for it; the adoption of pre-Alembic
    databases is gone. Migration 0011 drops `sync_profiles.backup_dir`.
  - Config: a single-profile `config.toml` is no longer turned into a
    "Default" profile, and a profile's `backup_dir` no longer into a
    "Legacy backup" target. `backup_dir` leaves the profile API.
  - Backups: mirror version folders without a manifest are no longer
    listed or restorable as "Before this backup" snapshots, and the
    `current` pseudo snapshot is gone. `SnapshotResponse.kind` and
    `RestorePreviewResponse.exact` are removed (osync and the TUI show a
    Latest column instead of Kind). Pre-rename `.gsync-*` markers in
    backup targets are no longer cleaned up.
  - API: the single-engine `/sync/*` routes that answered 410
    (`route_removed`) now get the generic 404; `GET /sync/status/aggregate`
    stays. `GET /health` no longer sends the always-null
    `remote_accessible`, and always sends `version`.
  - Installer: releases must attach `compose.yml`; the copy for 0.11.0 is
    gone and the oldest installable release is 0.12.0.
  - Web UI: the sidebar state and the language are no longer migrated
    from `localStorage`.

### Fixed

- **A long backup snapshot list scrolls inside its card.** With five or more
  snapshots the list grew past its card instead of scrolling; capped scroll
  areas (the snapshot and backup job lists, the notification history, the
  help dialog) now scroll at any length, and every row stays reachable with
  the keyboard.
- **Counts read correctly in every language.** "1 conflicts" and the like
  are gone: the differences summary, the selection count, the file action
  toasts, the sync progress and every other string with a count use the
  plural form for that number, in English, German and Persian.
- **The profile page fits a phone.** At 390 px wide the profile name wraps
  instead of being cut off (in Persian it lost its start), the badges move
  below it, and the tab list starts at its first tab and scrolls to the
  active one.
- **The status cards are named for what they show.** The summary card was
  titled "Dashboard" on the dashboard and on every profile; it is now "Sync
  status" on the dashboard and "Status" on a profile.

## [0.12.0] - 2026-10-04

### Added

- **One-line installer.** `curl -fsSL
  https://raw.githubusercontent.com/pan-fire/OmniSync/main/scripts/install.sh
  | bash` checks Docker and the ports, downloads the release's compose file
  and checks it against `SHA256SUMS` (and their signature with cosign),
  writes a `.env` with a generated API token (mode 0600), optionally a web
  UI password hash, starts OmniSync and waits for its health checks. Run
  again it updates, after a backup of the data volume, and rolls back if
  the new version is not healthy; `status` and `uninstall` (`--purge`) too,
  `--osync` for the terminal client, `--dry-run` to see the steps. It also
  explains how to move an install built from the source over without
  losing data. Releases now attach `compose.yml` (from `deploy/compose.yml`,
  which adds `OMNISYNC_API_PORT` and `OMNISYNC_WEB_PORT`) and `install.sh`,
  both listed in the signed `SHA256SUMS`. See `docs/gem/operations.md`.
- **One log for the whole backend.** Besides OmniSync's own lines, the log
  file and `docker compose logs backend` now get uvicorn's start, stop and
  errors, warnings and errors of the libraries OmniSync uses, and every
  unhandled exception with its traceback (in a request, a background task
  or a thread). The Logs page shows a traceback under the entry's
  **Details**; `osync logs` prints it indented under the entry. See
  [Logs](docs/gem/operations.md#logs).
- **Audit trail.** User actions (syncs and pauses, profile, remote and
  backup target changes, backup runs and restores, trash and conflict
  actions, notification settings, log level changes, rejected API tokens)
  are recorded in the log by `backend.audit`, with the outcome, the request
  id and the client address, names and ids only. The Logs page, the
  terminal UI's Logs view (`c`) and `osync logs --category audit|errors`
  filter by category; `GET /logs` takes `category=audit|errors` and returns
  each entry's `logger`, `request_id` and `exc`.
- **Request ids.** Every API answer carries an `X-Request-ID` header and
  every error answer a `request_id`; the log lines written for the request
  carry the same id. Server error messages in the web UI and the terminal UI
  end in the id, so a problem can be found in the log.
- **JSON log lines** with `OMNISYNC_LOG_FORMAT=json` (`ts`, `level`,
  `logger`, `msg`, `exc`, `request_id`, `fields`), for Loki, Elastic and
  other log shippers. `OMNISYNC_LOG_MAX_BYTES` and `OMNISYNC_LOG_BACKUPS`
  set the rotation (still 5 MB and 3 files by default);
  `OMNISYNC_LOG_ACCESS=1` also writes the access log to the file.
- **Web UI server events** (refused requests, logins, failed and
  throttled logins, logouts) are logged as JSON lines on its standard
  output (`docker compose logs frontend`), without passwords, session ids
  or query strings, and limited to 20 lines a minute per event type.

### Changed

- **Secrets are masked in every log line**, on both outputs and in
  tracebacks: the API token, Bearer tokens, OAuth tokens, secret-named
  `name=value` and JSON pairs, credentials in URLs, backup passphrases,
  notification passwords and tokens. Before, only rclone command lines and
  errors were masked.
- The log file is created owner-only (mode 0600); an existing file and its
  rotated copies are tightened on the next start.
- At log level DEBUG, SQL statements and outgoing HTTP request URLs stay
  out of the log.
- A rejected API token is logged as the audit event `auth.token_rejected`
  (still at most once per client address and minute) instead of a
  `backend.security` warning.

## [0.11.0] - 2026-10-03

### Breaking changes and upgrade notes

- **OAuth providers need your own app; existing remotes keep working.**
  OmniSync no longer ships rclone's built-in OAuth client IDs or secrets.
  New Google Drive, Dropbox and OneDrive remotes sign in with an OAuth app
  you register once with the provider: the wizard (web and terminal UI)
  asks for its client ID (Google Drive: and its client secret) and shows
  the redirect URI to register, `<ui>/api/wizard/oauth/callback`.
  `POST /wizard/authorize` refuses a sign-in without them
  (`oauth_client_id_required`, `oauth_client_secret_required`). Remotes
  created with rclone's built-in apps keep working: rclone refreshes their
  tokens, and OmniSync leaves the refresh to rclone. *Action:* register
  your apps as `docs/gem/remotes.md` describes before you add a remote or
  **Reconnect** one that has no client ID. rclone is retiring its shared
  Google Drive app during 2026: move Google Drive remotes to your own app.
- **`AuthorizeResponse.flow` is gone.** The callback is the only sign-in
  flow. *Action:* API clients that read `flow` should ignore it; the
  `OAuthFlow` schema no longer exists.

### Added

- `GET /wizard/oauth/redirect-uri`: the redirect URI to register with your
  own OAuth app, as the server uses it (honours
  `OMNISYNC_OAUTH_REDIRECT_URI`). The wizards show it with a short
  how-to and a link to the provider's section of the guide.
- Step-by-step app registration for Google Drive, Dropbox and OneDrive in
  `docs/gem/remotes.md`.

### Changed

- Release images are multi-platform: `linux/amd64` and `linux/arm64`
  (Raspberry Pi 4/5 with a 64-bit OS, ARM NAS) under the same tags, so
  Docker pulls the right one by itself. Each platform is built natively,
  and the signature, SBOM and provenance cover both.
- CI and releases run on GitHub-hosted runners, so pull requests from
  forks get CI results.
- The images are labelled with the repository
  (`org.opencontainers.image.source`, also on the multi-platform index),
  so GHCR links each package to `github.com/pan-fire/OmniSync`.
- The `osync` TUI and CLI are built with Go 1.27.1 (toolchain), and the
  web UI's dependencies are updated, among them lucide-react 1.49.
  Building `osync` from the source still needs Go 1.25 or newer, which
  fetches the 1.27.1 toolchain by itself.

### Removed

- rclone's built-in OAuth client IDs (Google Drive, Dropbox, OneDrive) and
  the Google client secret, with their `.gitleaks.toml` allow-list entry.
- The paste-back sign-in: `POST /wizard/sessions/{id}/redirect`, its
  `PasteRedirectRequest` schema, and the paste step of the web and
  terminal UI wizards. The error codes `invalid_redirect` and
  `authorization_used` are no longer returned.
- The OneDrive built-in-app error (`onedrive_builtin_app_unsupported`).

### Fixed

- A token refresh no longer sends an empty `client_secret` for apps
  without one (Dropbox PKCE apps, Azure public clients).
- Archive backups, archive restores and manifest writes remove their
  temporary folder (`omnisync-backup-*`, `omnisync-restore-*`,
  `omnisync-manifest-*`) also when the job is cancelled; a cancel at the
  wrong moment used to leave it in the temp directory.

### Security

- `POST /wizard/create` stores the OAuth app (client ID and secret) that
  the sign-in was started with, taken from the wizard session, instead of
  the values in `params`. A different client ID or secret in `params` is
  refused with `oauth_client_mismatch` (422): the token was issued to the
  session's app, and no other app could refresh it. The web UI and `osync`
  send the same values and are not affected.
- A failed token exchange logs the provider's `error` value only when it
  looks like an error code (e.g. `invalid_grant`); anything else a
  provider puts there is dropped. Microsoft Graph error codes longer than
  64 characters are dropped instead of cut.
- A failed sign-in shows (in the callback page and `GET
  /wizard/sessions/{id}`) a fixed message looked up by its error code,
  never the exception's text.
- `GET /browse/local` and the log line of a remote update are written so
  CodeQL can see their existing guards: the folder is confined to the
  browse roots with a realpath and prefix check, and only option names
  (never values) are logged. Behaviour is unchanged.
- The web UI's client-address filter for log lines uses a plain character
  class (CodeQL `js/overly-large-range`); it accepts the same addresses.
- Web UI development dependencies: vitest and @vitest/coverage-v8
  4.0.18 → 4.1.11 (critical GHSA-5xrq-8626-4rwp and GHSA-82fw-gwwq-j7x9),
  vite 7.3.1 → 7.3.6 (now a direct dev dependency, so the peer resolves to
  the fixed release), esbuild 0.28.2, and the lockfile refreshed for
  hono, @hono/node-server, undici, fast-uri, ip-address (via
  express-rate-limit 8.7.0), js-yaml, minimatch, brace-expansion, picomatch,
  postcss, rollup, nanoid, flatted, qs, path-to-regexp, body-parser, ajv
  and @humanfs/node. `pnpm audit` (all dependencies) reports only braces
  3.0.3 (GHSA-vfj7-8cjw-p6xm), which has no fixed release; it is reached
  only through ESLint's file globbing with patterns from this repository.
  All of these are development dependencies (tests, linting, the shadcn
  CLI); the image ships only the Next.js standalone server.

## [0.10.0] - 2026-10-02

Released before this repository was public; there is no public tag for it.

### Breaking changes and upgrade notes

This is OmniSync's first published release. If you ran a build from the
source before it, these changes can break your setup, your scripts or a
client of your own; each one says what to do.

- **`osync` exit statuses.** Scriptable commands exit 0 (done), 1 (error:
  backend unreachable or unhealthy, a failed sync, backup, restore or
  test, an API error), 2 (wrong arguments or flags; nothing was sent), 3
  (refused: HTTP 409, a busy profile, an unconfirmed restore or resync, a
  mirror profile for `sync`/`resync`) or 4 (not found: HTTP 404, which the
  sync endpoints also answer for a disabled profile). Refusals and missing
  profiles used to exit 1. *Action:* scripts that test for exit status 1
  in these cases must test for 3 or 4; scripts that only test for
  "non-zero" need no change.
- **The web UI refuses unknown host names.** Its server answers `/api`
  requests with 403 (`request_refused`) unless the `Host` header is
  `localhost`, `127.0.0.1` or `[::1]`, so a UI opened by LAN IP, host name
  or through a reverse proxy loads but cannot do anything. *Action:* list
  those names in `OMNISYNC_UI_ALLOWED_HOSTS` (comma-separated, optionally
  with `:port`), after reading "Exposing the web UI / HTTPS" in the README.
- **The web UI's health check is `/healthz`.** With the optional login on,
  `/` redirects to `/login`, so a check of `/` fails. *Action:* point your
  own health checks and monitors at `/healthz`; the shipped compose file
  and the image's built-in check already use it.
- **API errors have one shape:** `{"detail": "<message>", "code":
  "<code>", "details": {...}}`. `detail` is always a string (it used to be
  an object or a list for some errors); request validation (422) puts the
  issues in `details.errors`. *Action:* clients of your own that read
  fields out of `detail` must read `code` and `details` instead
  ([docs/api-errors.md](docs/api-errors.md)). Clients that only show
  `detail` need no change.
- **Long operations answer at once and run in the background.** Starting a
  sync or a resync (`POST /profiles/{slug}/sync/start`, `.../sync/resync`)
  answers 202 with the job as soon as the run is under way, instead of
  waiting for its end; a run its own safety checks refused before it
  changed anything is still answered at once (200, with the reason).
  Backups and restores follow the same pattern: a 202 answer means
  started, not finished. `POST /profiles/{slug}/backups/{id}/run`,
  `.../restore` and `.../restore-files` answer 202 with the job in status
  `running`; follow it with the new
  `GET /profiles/{slug}/backups/{id}/jobs/{job_id}`. Per-file actions
  (`POST /profiles/{slug}/sync/selective`) answer 202 with `status:
  "running"` and are followed with the new
  `GET /profiles/{slug}/sync/selective/{job_id}`, which carries the
  per-file errors once they ended. While another sync, backup or restore of
  the profile runs or waits, these starts are refused at once with 409
  `sync_busy` instead of waiting for it. A failed backup or restore job
  has a stable `error_code` and a fixed `error_message` for it (OmniSync's
  own explanation for a refusal); rclone's output is only in the OmniSync
  log ([docs/api-errors.md](docs/api-errors.md)). The web UI's proxy now
  gives up on a request after 20 minutes instead of 24 hours. *Action:*
  clients of your own must follow the job (e.g. `GET /jobs/{id}` or the
  routes above) until it ends instead of treating the answer as the
  result, retry a `sync_busy` start later, and branch on `error_code`
  rather than parse `error_message`; a reverse proxy in front of the UI
  needs a read timeout of 20 minutes, not 24 hours. The web UI, the TUI and
  `osync` already follow the jobs.
- **Local backup target folders are no longer created.** A missing local
  target folder fails the backup ("does not exist or is not mounted"), so
  a backup never lands on the local disk under an unmounted drive.
  *Action:* make sure every local backup target's folder exists (mount the
  drive, or create the folder). A remote target that never completed a
  backup may still lack its folder; its first backup creates it.
- **Two-way profiles refuse rclone flags that can hide the sync marker:**
  `--include`, `--exclude`, `--filter`, `--exclude-if-present`,
  `--max-age`, `--min-age`, `--min-size`, `--max-size` below 1Ki and
  `--max-depth` below 1 (422, `invalid_rclone_args`). Existing profiles are
  not changed and keep syncing with their flags, but saving any edit of
  such a profile (even a rename), or switching a mirror profile with these
  flags to two-way, is refused until the flags are removed. *Action:* move
  path filters into the profile's filter rules (e.g. `+ /Docs/**`, then
  `- **`) and remove the flags.
- **Reserved remote names.** Names starting with `omnisync_backup_crypt_`
  (any case, `-` or `_`) belong to encrypted backup targets, names
  starting with `omnisync_bisync_` to two-way syncs of long paths; creating or
  importing such a remote is refused. A remote you already have with such
  a name is not removed, but OmniSync's own remote of the same name
  takes its place in the rclone runs that use it. *Action:* recreate such
  a remote under another name and point its profiles at it.
- **The host-native (desktop) notification channel is off by default.** A
  `config.toml` that enables it explicitly keeps it on. *Action:* to keep
  desktop notifications, turn the channel on in the notification settings
  (web UI or TUI); in Docker it also needs `docker-compose.dbus.yml`.
- **Conflict lists are paged.** `GET /conflicts` and the per-profile
  conflict list return at most 1000 conflicts per request (`skip`/`limit`,
  `limit` at most 1000) and the total in `X-Total-Count`. *Action:*
  clients of your own that may see more than 1000 conflicts page through
  them.
- **Request size limits.** Request bodies over 1 MB are refused with 413
  (`body_too_large`), and request lists and strings have size limits
  (422). *Action:* none for the web UI, the TUI and `osync`; scripts that
  send larger bodies must split them.
- **`OMNISYNC_BROWSE_ROOTS` confines new folders.** When it is set (the
  shipped compose file sets it to `OMNISYNC_SYNC_DIR`), a new or changed
  profile folder, local backup target path or restore target folder must
  lie inside those roots (422, `path_not_allowed`). Existing profiles and
  targets keep working and are listed in the log at startup. *Action:*
  before moving a folder outside the roots, add its root to
  `OMNISYNC_BROWSE_ROOTS` (and mount it into the container).
- **`docker-compose.yml` changed.** The backend has
  `stop_grace_period: 90s` (Docker's default of 10 s can kill a running
  two-way sync, which then needs a resync), the web UI's health check uses
  `/healthz`, `TZ` and the `OMNISYNC_UI_*` login variables are passed
  through, and the `test-sandbox` service moved to
  `docker-compose.dev.yml` (`docker compose -f docker-compose.yml -f
  docker-compose.dev.yml run --rm test-sandbox`). *Action:* if you keep a
  compose file of your own, copy these changes into it.
- **Image names.** The repository's `docker-compose.yml` builds both
  images from the source (compose names them after the project, e.g.
  `omnisync-backend` and `omnisync-frontend`). The released images are
  `ghcr.io/pan-fire/omnisync-backend` and `ghcr.io/pan-fire/omnisync-web`
  (the web UI's image is `omnisync-web`, while its compose service is
  still `frontend`). *Action:* to switch to released images, use the
  compose file in the README's "Install a release" and keep the compose
  project name you used before (e.g. `COMPOSE_PROJECT_NAME=omnisync` in
  `.env` for a clone in a folder named `OmniSync`); otherwise compose
  creates a new, empty `omnisync-data` volume and your profiles, remotes
  and history seem gone.

### Added

- The web UI's API types are generated from the backend's OpenAPI schema
  and checked against the hand-written ones; CI fails when the generated
  files are out of date.
- Live progress of running syncs (bytes, total, speed, time left, files,
  the files in flight) on the profile status, shown as a progress bar in
  the web UI and the TUI.
- Pause all / Resume all and Pause per profile: stored with the profile
  and kept across restarts; syncs you start still run. Resume all never
  lifts a pause OmniSync set to protect files (differences, a restore, a
  needed resync).
- Per-profile bandwidth limit (`bwlimit`, rclone `--bwlimit` including
  timetables) and sync window (automatic syncs only on chosen days and
  hours, server time; outside it they wait and run when it opens).
- "Choose folders" in the web profile form: pick folders in a tree and it
  writes the rclone filter rules (still editable).
- Trash browser (web and TUI): list each side's `.omnisync-trash`, restore
  files to their original place (a newer file is replaced only after
  confirmation) and delete entries for good.
- Backups: optional encryption per backup target with a passphrase
  (rclone crypt on the fly: file contents and names). Mirror and archive
  backups, snapshots, retention, browsing and restores work unchanged. The
  passphrase is stored obscured, never returned by the API and hidden in
  the log; it cannot be changed while the target holds backups. A lost
  passphrase means lost backups.
- Backups: verification after each backup (on by default for new
  targets): `rclone check --one-way` (`cryptcheck` when encrypted) for
  mirrors, a read-back plus size check (and a decryption test when
  encrypted) for archives. The result shows on the target and the job; a
  failure sends a "Backup verification failed" notification.
- Backups: a snapshot browser (web UI and TUI) to browse or search a
  snapshot and restore chosen files and folders to their original place
  or into another local folder; replaced files are kept in
  `.omnisync-trash/pre-restore/`.
- Backups: before a full restore the dialog (and the TUI confirmation)
  shows how many files would be added, replaced and removed on each side.
- `OMNISYNC_CONFIG_PATH` moves `config.toml` like the other data files
  (default `/data/omnisync/config.toml`), and counts as part of the data
  directory that profile folders may not point at.
- The images build for `linux/arm64` (Raspberry Pi 4/5 with a 64-bit OS,
  ARM NAS) as well as `linux/amd64`. Releases publish `linux/amd64` images
  for now; on ARM, build from the source.
- Signed releases: images and the osync `SHA256SUMS` are signed with
  cosign (keyless), and images carry an SBOM and SLSA provenance. See
  "Verify a release" in the README.
- Both images have a built-in health check, so `docker run` and other
  runtimes report `healthy` without compose.
- `TZ` setting (default `UTC`) for both containers.
- CONTRIBUTING.md, SECURITY.md (private reporting via GitHub security
  advisories), issue and pull request templates.
- User guide pages "How Syncing Works" and "Operations", including
  running OmniSync without Docker as systemd services.
- Web UI: optional built-in login. Set `OMNISYNC_UI_PASSWORD_HASH` (make
  it with `docker compose exec frontend node scripts/hash-password.mjs`)
  and every page and `/api` request needs a session: one password,
  HttpOnly/SameSite=Strict session cookies (Secure on HTTPS) that last
  `OMNISYNC_UI_SESSION_DAYS` (default 7) and are extended by use, a logout
  button, and throttled, logged failed logins. Off by default. See
  "Exposing the web UI / HTTPS" in the README.
- Web UI: `/healthz` for container health checks.
- Edit a remote in place (web UI Edit, TUI `e`): `GET /remotes/{name}/config`
  (secrets masked, no tokens) and `PUT /remotes/{name}`, validated against
  the provider's fields; an empty secret keeps the stored one.
- Reconnect an OAuth remote (Drive, Dropbox, OneDrive) whose sign-in
  expired: only its token is replaced. Remotes whose last test, health
  check or sync was refused are marked (`auth_error`) and offer the fix
  (web UI and TUI `a`).
- WebDAV, SMB and crypt (an encrypted folder on another remote) in the
  setup wizard; SFTP key file and passphrase fields.
- Import remotes from an existing rclone.conf (web UI upload or paste, TUI
  `I`), with clash renaming; local remotes, local-path wrappers and
  command-running options are refused.
- `osync` covers the rest of the API for scripts and cron:
  `profile show|enable|disable|stop|check|diff|resume|manual-flags`,
  `conflicts` and `conflicts resolve <id> --keep local|remote|both|dismiss`,
  `remotes`, `remotes test`, `remotes about`, `backups <slug>` and
  `backups run|snapshots|restore` (`--wait`; restore asks on a terminal
  and needs `--yes` otherwise), `logs [--level] [--limit] [--follow]` and
  `notifications test [--channel]`, all with `--json`. Shell completion
  (`osync completion bash|zsh|fish|powershell`) also completes profile
  slugs, remote names and flag values.
- Notification channels for headless servers and NAS: **Webhook** (JSON
  POST, optional secret headers), **ntfy** (ntfy.sh or self-hosted, token
  or user/password, severity mapped to priority) and **Email** (SMTP with
  STARTTLS/TLS). Configurable in the web UI (Notifications → Configure)
  and the TUI (`c`; `t` tests one channel). Secrets are never returned by
  the API and `config.toml` is written 0600 once it holds one.
- A test fails when a German or Persian string equals the English one
  (unless allow-listed), uses different `{{placeholders}}` than English,
  or has a Latin word starting with punctuation not marked left-to-right.
- Overdue backup targets: an "Overdue" badge (web UI and TUI) and a
  one-time "Backup overdue" notification when no backup has completed for
  more than twice the target's frequency.
- "Always keep" (`keep_last`) setting for backup targets in the API, web UI
  and TUI. Database migration 0007 adds `backup_targets.keep_last`.
- Job history retention: sync and backup jobs older than `history_days`
  (default 90, 0 keeps all; Config page, TUI Config view or config.toml)
  are removed at startup and daily. The newest 100 jobs per profile and
  backup target are always kept, and the database is compacted when it
  has become largely free space.
- The database runs in WAL mode with a 30 s busy timeout, and is copied to
  `omnisync.db.pre-<revision>.bak` (3 newest kept) before migrations run.
- README: exposing the web UI safely (HTTPS reverse proxy with
  authentication), backing up, restoring and uninstalling OmniSync's own
  data.
- Releases: pushing a `v*` tag runs the CI suite, publishes the backend and
  web UI images to GHCR (`ghcr.io/pan-fire/omnisync-backend`,
  `ghcr.io/pan-fire/omnisync-web`) and `osync` binaries for Linux, macOS and
  Windows (amd64 and arm64) with `SHA256SUMS`, and creates a GitHub release
  with these notes.
- One version for the whole project in the `VERSION` file. `GET /health`
  reports it (`version`), the web UI shows it at the foot of the sidebar
  (and the backend's too when they differ), `osync --version` prints it with
  the commit, and `osync health` shows the backend's.
- `scripts/release/prepare.sh` bumps the version and moves this section into
  a new release.
- Installation from the released images and binaries in the README and the
  user guide.
- OAuth sign-in with rclone's built-in apps (Google Drive, Dropbox,
  OneDrive): after allowing access, paste the address the browser shows into
  the wizard (web UI and TUI). Own OAuth apps keep the automatic callback.
  Both flows use PKCE.
- Mirror profiles explain what mirror mode means and offer a switch to
  two-way sync, one profile or all at once (web UI and TUI); the note can be
  hidden per profile.
- `osync sync` and `osync resync` for two-way profiles, `--json` for push,
  pull, sync and resync, and the mode in `osync profiles`.
- Notifications for conflicts, unreachable remotes, authentication errors,
  crashes and failed startups, and for two-way syncs, resyncs and the
  resync-required pause; a test button per channel; Web Push subscribe and
  unsubscribe from the settings page.
- Web UI: Test Sync in the profile form and page, browsing a remote's
  folders from its card, a conflicts filter by profile, paged logs with a
  server-side level filter, per-profile push and pull on the dashboard, and
  right-to-left layout for Persian.
- Web UI: a History tab on the profile page lists that profile's sync jobs.
- The folder browser of a backup target's path also browses remotes: the
  profile's remote for a same-remote target, the chosen remote for a custom
  one (backup target form and the profile form's initial backup).
- TUI: a History tab on the profile view (that profile's jobs, paged, with
  job details), and Ctrl+O folder browsing for a backup target's path
  (local folders, or the target's remote); the create form now asks for
  the type and remote before the path.
- TUI: coloured states, a top bar, key hints, folder and remote pickers in
  the profile form, log search, a conflicts filter and a debug log.

### Changed

- **API (breaking for third-party clients that parse error objects):**
  every error answer has one shape, `{"detail": "<message>", "code":
  "<snake_case code>", "details": {...}}`. `detail` is always a readable
  string, so clients that only show it keep working; `code` is stable and
  documented in `docs/api-errors.md`. Request validation (422) has a
  summary in `detail` and the issues in `details.errors` (without the
  submitted input); the retired `/sync/*` routes (410) answer
  `code: "route_removed"` with `details.replacement`. The web UI server's
  own login answers use the same shape. The OpenAPI schema documents it as
  `ErrorResponse`.
- The sync engine, rclone wrapper and backup service are packages of
  smaller modules (no behaviour change).
- Database migration 0009 adds `sync_profiles.bwlimit`, `sync_window` and
  `user_paused`.
- Remote names starting with `omnisync_backup_crypt_` (any case, `-` or
  `_`) are reserved for encrypted backup targets; creating or importing
  such a remote is refused.
- Mirror snapshot manifests also record each file's modification time.
- Database migration 0008 adds backup encryption and verification
  columns.
- The backend container no longer changes the owner of every file in the
  data volume on each start, only of files owned by someone else, and
  warns loudly when `PUID=0` runs it as root. The web UI container runs as
  user 1000.
- The `test-sandbox` service moved from `docker-compose.yml` to
  `docker-compose.dev.yml`.
- Base images and GitHub Actions are pinned by digest and commit;
  Dependabot keeps them current.
- The README is shorter: the detailed sync rules, backup procedures and
  development guide moved to the user guide and CONTRIBUTING.md (old
  links still work).
- docker-compose.yml: the web UI health check uses `/healthz` (with the
  login on, `/` redirects to `/login`), and the `OMNISYNC_UI_*` login
  variables are passed through. Update your own compose file if you
  copied the release example.
- `POST /wizard/create` rejects missing required fields, invalid select
  values and ports with 422.
- `osync` exit statuses are meaningful: 0 ok, 1 error, 2 wrong arguments
  or flags, 3 refused (HTTP 409, busy profile, not confirmed), 4 not
  found (HTTP 404). Commands that used to exit 1 for a refusal or a
  missing profile now exit 3 or 4.
- CI also runs ruff, pyright and actionlint, and runs the backend tests in
  parallel.
- Notification history older than `history_days` is removed by the daily
  history cleanup.
- The TUI changes only the toggled channel when enabling, disabling or
  changing a channel's severity.
- Web UI: below the md breakpoint the sidebar is a menu drawer opened from
  a top bar.
- Web UI: the dashboard shows a two-step "add a remote → create a profile"
  checklist before the first profile, lists disabled profiles, and labels
  the count "Enabled profiles".
- Web UI: the profile delete confirmation names the profile and states
  that no files are deleted.
- Web UI: Persian uses the Vazirmatn font; Geist is now actually applied
  for English and German.
- Two-way profiles refuse rclone flags that could hide the sync marker
  (`--include`, `--exclude`, `--filter`, `--exclude-if-present`,
  `--max-age`, `--min-age`, `--min-size`, `--max-size` below 1Ki,
  `--max-depth` below 1) with a 422; use the profile's filter rules
  instead.
- Database migration 0006 adds `sync_profiles.pause_reason`.
- Starting a sync or resync answers once it is under way (202 with the job);
  the web UI, the TUI and `osync` follow the job to its end. Safety refusals
  are still answered at once.
- Restoring a mirror backup snapshot now restores the files exactly as they
  were right after that backup, and the latest backup can be restored.
  Files not in the snapshot are moved to `.omnisync-trash`, never deleted.
- Local backup target folders must exist; the liveness check no longer
  creates them, and remote targets are checked at their path.
- Notifications are sent to all channels at once with a timeout per
  channel; the host-native channel is off by default.

### Fixed

- Two-way sync failed for folders with long paths ("Lock file exists, but
  contents are unreadable", "file name too long"): rclone bisync names its
  files after both paths, and from about 220 characters together the names
  exceeded the 255-byte limit. Such profiles now run bisync on two short
  remotes OmniSync defines only in the rclone process's environment
  (`omnisync_bisync_<profile id>_local` and `_remote`); profiles whose
  names fit keep their records. A profile already synced on its real paths
  that no longer fit pauses and asks for a resync, with that reason. Remote
  names starting with `omnisync_bisync_` are now reserved like
  `omnisync_backup_crypt_` (also as a profile's remote folder). Mirror
  profiles were not affected.
- Restoring single files into a mirror profile's local folder now pauses
  automatic syncing first, like a one-sided restore: a scheduled pull that
  waited for the restore no longer removes the restored files the remote
  does not have. Review the diff, then push or resume.
- A restore that waited too long for a running sync ended in an unlogged
  error 500 without a job; it now records a failed job (`sync_busy`) and
  notifies, and a restore started in the UI is refused at once while the
  profile is busy.
- The profile's bandwidth limit and its transfer flags (`--transfers`,
  `--checkers`, timeouts, chunk sizes) now also apply to per-file pushes
  and pulls, conflict resolutions, backups and restores. Its filters,
  `--update` and `--max-delete` stay limited to syncs (rclone refuses
  filters next to the file lists of per-file copies).
- Backups and restores still running at shutdown are stopped and recorded
  as stopped by the shutdown, instead of being left "running" until the
  next start; the shutdown of the backup service and the history cleanup
  is bounded, so it fits the container's stop grace period.
- The subsystem health check (schedulers, database, rclone) runs once a
  minute on a scheduler of its own, also with no profile at all; it used to
  run once per profile engine.

- Web UI: a remote's storage bar showed "1.0 undefined" for sizes just
  below 1 PiB (and above it); sizes up to EB are shown, and the property
  test that sometimes failed on this is now deterministic about it.
- A restore refused because the snapshot cannot be restored as asked now
  shows its reason instead of "already running".
- The TUI's remote wizard opens the OAuth sign-in page in the browser on
  Windows too.
- Two-way sync no longer writes its marker and filter files on the event
  loop, so a slow disk cannot stall the API and other profiles.
- Persian (fa) UI: about 180 strings that still showed in English are now
  translated, including the push/pull deletion confirmations, conflict
  resolution explanations, backup and restore warnings, form validation
  and three help pages, with consistent terminology throughout.
- Persian UI: file and flag names such as `.omnisync-trash`,
  `--max-delete` and paths starting with `/` no longer show their
  punctuation on the wrong end in right-to-left text.
- Web UI: long dialogs (edit profile, backup target, remote wizard,
  restore) scroll instead of running off small screens.
- Web UI: paths and `remote:path` values keep their order in Persian;
  spacing uses logical (RTL-aware) utilities.
- Web UI: diff category and notification severity badges meet contrast in
  light mode; disabled profile cards no longer fade text below AA.
- Web UI: the profile page tells "not found" from load errors (with
  retry); the profiles list and remote picker show loading and error
  states.
- Web UI: Pull on local-only and Push on remote-only files are disabled
  with a reason instead of failing.
- Web UI: skip link, a single h1 per page, per-page browser titles, focus
  kept when collapsing the sidebar, larger dashboard row buttons, text
  alternatives for the diff badges and paused indicator, and a visible
  reason when Resume is disabled.
- Web UI: missing translations fall back to English; the dialog close
  label is translated; the health card refreshes every minute.
- Backup schedules no longer restart with every server start or target
  edit: the next run follows the target's last backup, and runs missed
  while the server was down start shortly after it comes back (staggered).
- Backups of a profile without a running sync engine now take the
  profile's shared sync lock, so enabling the profile mid-backup cannot
  sync the folder at the same time.
- A local archive backup that fails (e.g. a full disk) no longer leaves a
  truncated `backup-*.tar.gz` that shows up as a snapshot.
- Backups no longer include the `.omnisync-trash` folder or rclone's
  unfinished `*.partial` transfer files.
- Retention never deletes a target's newest snapshots: it keeps at least
  "Always keep" snapshots (default 3), and for mirrors the newest completed
  backup always stays.
- A backup is refused when the system clock is behind the newest snapshot,
  so restores never rebuild from snapshots in the wrong order.
- A watcher push or scheduled pull that was waiting while a backup was
  restored to one side no longer runs afterwards and spreads or undoes the
  restore: automatic syncs check the pause again once they can run, and
  are not retried once a profile is paused.
- The pause after a one-sided restore now starts before the restore
  changes anything and is kept across server restarts and profile edits,
  until you resume or sync.
- Mirror profiles: files edited while syncing was paused are no longer
  overwritten by the next pull after resuming. Resume first compares both
  sides and is refused while differences remain.
- Two-way profiles with include-style filters (e.g. `+ /Docs/**`, `- **`)
  no longer fail every sync and resync with "check file check failed":
  the sync marker is always included.
- Mirror push and pull no longer copy rclone's leftover `.partial` files
  from an interrupted transfer to the other side.
- The delete limit now applies to a whole sync: retries after a failed
  attempt can only delete what is left of the limit.
- Push/pull/two-way confirmation: when the preview failed, confirming
  (which forces the sync) requires ticking an acknowledgement; the button
  is shown as destructive when the preview reports deletions.
- rclone.conf (and the API token and config.toml) are written atomically:
  a full disk or a crash during an OAuth token refresh or while adding a
  remote no longer truncates rclone.conf and loses every remote. The
  previous rclone.conf is kept as `rclone.conf.bak`, and concurrent token
  refreshes and remote changes no longer overwrite each other.
- Sync and backup jobs left "running" by a crash or kill are marked failed
  at the next start ("Interrupted: OmniSync stopped while it ran.").
- Stopping the backend stops all sync engines at once, and
  docker-compose.yml gives the backend a 90 s stop grace period, so a
  running two-way sync can shut down cleanly instead of being killed.
- Browsing a remote from its root returned folder paths with a leading
  slash (`nas:/Backups`), which on SFTP and local-backed remotes points at
  the file system root instead of the folder shown; it now returns
  `nas:Backups`.
- A remote backup target on a remote that is not configured in rclone is
  refused (422) when it is created or its path changes, instead of failing
  at every scheduled run.
- A backup whose source folder is empty, unmounted or missing its sync
  marker is refused instead of moving the whole backup into a version
  folder that retention later deletes.
- `GET /remotes/{name}/about` on a remote without usage information answers
  "unsupported" instead of 503, and a successful remote test records
  `last_verified`.

### Security

- Encrypted backup targets no longer put the (obscured, reversible)
  encryption passphrase on rclone's command line, where any local user
  could read it in the process list while a backup, listing, verification
  or restore ran. The crypt remote reaches only the environment of the
  rclone processes that use it. Existing encrypted backups are unaffected.
- The `osync` TUI and CLI are built with Go 1.25.14 instead of 1.25.0,
  fixing 25 known standard-library vulnerabilities.
- Weekly security scans (pip-audit, pnpm audit, govulncheck, CodeQL, a
  Trivy image scan) and Dependabot updates for all ecosystems.
- Notification URLs are checked against SSRF: link-local and cloud
  metadata addresses and non-http(s) schemes are refused, plain http only
  to LAN or loopback when explicitly allowed, connections are pinned to
  the checked address, and redirects are not followed.
- Web UI: upgraded Next.js 16.1.6 → 16.3.8 (fixes a critical
  image-optimizer RCE and several middleware/proxy bypass advisories),
  React 19.2.8 and transitive dependencies; `pnpm audit --prod` is clean.
  The image optimizer is disabled (`images.unoptimized`).
- Web UI: the `/api` proxy refuses (403) requests for unknown `Host` names
  (DNS rebinding), state-changing requests from other origins (cross-site
  forms and fetches) and non-JSON request bodies before it adds the API
  token. The new `OMNISYNC_UI_ALLOWED_HOSTS` lists extra host names, e.g.
  a reverse proxy's public name; opening the UI by LAN IP or host name
  needs it.
- Web UI: responses carry a Content-Security-Policy (`frame-ancestors
  'none'`), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer` and a Permissions-Policy.
- Remote names in API paths and queries are validated (422) and every
  remote path reaches rclone after `--`, so a name such as
  `--log-file=/tmp/x` can no longer pass options to rclone
  (`/browse/remote`, `/remotes/{name}/test|about`, `/wizard/test`,
  `/config/test-sync`).
- Profile folders, local backup targets, sync tests and restores refuse
  OmniSync's data directory (rclone.conf, API token, database, Web Push
  keys), folders inside it and folders containing it. With
  `OMNISYNC_BROWSE_ROOTS` set, new local folders must be inside those
  roots; existing profiles keep working and are logged at startup.
- `rclone_args`: a switch such as `--fast-list` can no longer be followed
  by a stray value, and a value flag needs its value.
- A configured `OMNISYNC_API_TOKEN` under 32 characters logs a warning.
  Wrong tokens are logged with the client address (once per minute per
  address); 10 within a minute block that address with 429 for a minute.
- Request bodies over 1 MB are refused (413); request lists and strings
  have size limits. Conflict listings take `skip`/`limit` (default 1000)
  and return `X-Total-Count`.
- The TUI writes `tui.toml` owner-only (0600) and warns when it holds
  `api_key` but is readable by others, or when the key goes over plain
  http to a non-loopback host.
- The failed-token throttle can no longer be dodged or aimed at another
  address with `X-Forwarded-For`: the image runs uvicorn with
  `--no-proxy-headers` (add it if you run uvicorn yourself), and a request
  with the right API token is never throttled.
- Notification URLs: link-local and metadata addresses are also refused
  when embedded in IPv6 (NAT64 `64:ff9b::/96`, 6to4, Teredo,
  IPv4-compatible), and plain http to a 6to4 address of a public IPv4
  address no longer counts as local.
- rclone.conf import: options that make rclone read a local file
  (`key_file`, `known_hosts_file`, `service_account_file`, ...) may not
  point into OmniSync's data directory or use environment variables, and
  crypt, alias, union and other wrappers may not point at an existing
  `local` remote (or one wrapping a local path).

### Earlier development

The work before this release, recorded at the time as a version that was
never published.

#### Added

- Two-way sync mode built on `rclone bisync`, now the default for new
  profiles: edits and deletions travel both ways, a file changed on both
  sides keeps both versions, and a resync after a failure always waits for
  the user's confirmation.
- Real conflict records and resolution (keep local, keep remote, keep both,
  dismiss) in the web UI and the TUI, a side-effect-free sync preview for
  confirmation dialogs, and OAuth sign-in through the web UI.
- Sync safety rails: a sync that would wipe the other side is refused
  (missing folder, empty source, `.omnisync-check` marker on one side only),
  replaced files are kept in `.omnisync-trash`, deletions are capped per sync,
  and only one operation runs per profile at a time.
- Stop really stops a running sync; each job records the files it changed;
  trash retention.
- Versioned database migrations with Alembic, applied on start, with
  enforced cascades and per-profile manual flags.
- API token on every route except `GET /health` and the OAuth callback,
  Host-header checks, and the web UI's server adding the token so the browser
  never holds it.
- A production web UI image (Next.js standalone, non-root) started by the
  shipped `docker-compose.yml`.
- CI: backend tests, frontend lint/types/tests/build,
  TUI checks and the image builds.
- GPL-3.0 license.

#### Changed

- The backend is published on the host's loopback only; `network_mode: host`,
  the `$HOME` mount and live reload are gone from the default compose file
  (live reload and D-Bus notifications are opt-in override files).
- `GET /health` checks only local state (database, rclone binary); remote
  reachability moved to `GET /health/remotes`.
- Engines are keyed by the immutable profile id, so renaming or moving a
  profile restarts the right engine.
- The startup check fails closed: an error pauses the profile instead of
  reporting "in sync".
- The TUI and the web UI follow the backend's API contract and confirm
  destructive syncs with a preview of deletes and replacements.
- The README and user guide are rewritten to match the app.
- No wall-clock limit on backup transfers.

#### Fixed

- A fresh clone builds: `frontend/src/lib/utils.ts` was ignored by
  `.gitignore`, and a fresh backend install lacked `greenlet`.
- The web UI's profile page crash, and TUI input and selection bugs.
- A confirmed push or pull was refused because the preview had paused the
  profile.
- Error responses no longer carry rclone stderr or exception text.
- Backup targets are validated on the server, including overlap checks, and
  deleting a remote counts backup targets that use it.
- An invalid legacy configuration no longer crashes startup.

#### Security

- rclone inputs are validated (absolute local paths, `<remote>:<path>`
  remotes, allow-listed flags), and SFTP/FTP passwords are stored obscured.
- The rclone binary in the image is a pinned release verified by SHA-256;
  Python dependencies are installed from an exact lock file.
- The OAuth token stays on the server and is never returned by the API.

[Unreleased]: https://github.com/pan-fire/OmniSync/compare/v0.13.0...HEAD
[0.13.0]: https://github.com/pan-fire/OmniSync/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/pan-fire/OmniSync/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/pan-fire/OmniSync/releases/tag/v0.11.0
