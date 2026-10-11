# Configuration Guide for OmniSync

Most settings belong to a **sync profile** and are edited in the web UI
(**Profiles**) or the terminal UI. Server-wide settings are the log level
and the job history retention (**Config** page), notification channels (**Notifications** page) and a few
environment variables.

## Profile settings

### Name
Any name with at least one letter or digit. OmniSync derives the profile's
short id (slug) from it, e.g. "My Documents" becomes `my-documents`; the TUI
and the API use the slug.

### Local folder
The absolute path of the folder on your machine, e.g. `/home/you/Sync/Documents`.

- With docker compose it must lie inside `OMNISYNC_SYNC_DIR`, which is
  mounted into the container under the same path. The folder picker only
  shows that folder.
- It must exist. OmniSync never creates a missing local folder, because a
  missing folder usually means a drive or network share is not mounted;
  the profile pauses instead and tells you.
- Folders may not overlap: two enabled profiles cannot use the same local
  or remote folder, or one inside the other, and a backup target cannot
  overlap any profile's folders or another backup target.

### Remote folder
The rclone remote name, a colon and a path: `gdrive:Sync/Documents`,
`onedrive:Work`, `dropbox:` (the whole account).

### Sync mode
- **Two-way (recommended)**, the default for new profiles: runs
  `rclone bisync`. Changes on either side are carried to the other: new and
  edited files on both sides are kept, including edits made while OmniSync
  was not running, and a deletion on either side is carried over (the
  deleted file is kept in that side's `.omnisync-trash`). A file changed on
  both sides keeps both versions: the newer one keeps the name, the other
  is saved next to it as `name.local-conflict1.ext` or
  `name.remote-conflict1.ext` on both sides, and a conflict is listed on the
  **Conflicts** page.
- **Mirror: one-way push/pull only**: local changes are pushed (the cloud
  folder is made to match the local one) and the cloud is pulled on the
  interval (the local folder is made to match the cloud). The side that
  syncs last wins, so a local edit that could not be pushed yet (for
  example while offline) is overwritten by the next pull, and a file that
  exists only in the cloud is
  deleted by the next push. Profiles that existed before two-way sync was
  added keep this mode until you change it.

Switching a profile to two-way makes its next sync a **resync**: both
folders become the union of the two, nothing is deleted, and where a file
differs the newer version wins while the older one goes to the trash. The
profile page of a mirror profile offers the same switch as **Switch to
two-way**. Switching to mirror forgets the two-way state; from then on the
side that syncs last wins. **Push** and **Pull** work in both modes.

rclone bisync names its record of the last sync after both folder paths,
and a file name cannot exceed 255 bytes. For folders whose paths together
are too long (roughly 220 characters), OmniSync gives bisync two short
names instead, `omnisync_bisync_<profile id>_local:root` and
`..._remote:root`: rclone `combine` remotes over the real folders, defined
only in the environment of the rclone process. You see the real paths in
the UI and in messages. A profile keeps the form it was first synced with;
if its folders change, the next sync is a resync anyway. Remote names
starting with `omnisync_bisync_` are reserved for this and refused when
adding or importing remotes or as a profile's remote folder.

### Debounce delay (seconds)
How long OmniSync waits after the last local change before it syncs (a
two-way sync, or a push in mirror mode). Default 5, allowed 1 to 3600. A
longer delay avoids a sync for every save while you are still working on a
file.

### Pull interval (minutes)
How often the cloud side is synced: a two-way sync, or a pull in mirror
mode. Default 5, allowed 1 to 10080 (a week). Shorter intervals pick up
changes from other devices sooner but make more API calls, which some
providers rate-limit.

### Max retries
How often a failed sync is attempted in total, with a growing pause
between attempts. Default 3, allowed 1 to 10. Authentication errors and a
sync stopped by the delete limit are not retried; a provider's rate limit
gets a longer pause (30 s, then 60 s, ...).

### rclone filter rules
[rclone filter rules](https://rclone.org/filtering/), one per entry, applied
to syncs and checks. `-` excludes, `+` includes:

```text
- *.tmp
- node_modules/**
- .cache/
```

OmniSync's own `.omnisync-trash` folder is always excluded.

**Choose folders** (next to the rules) shows the local folder as a tree
with checkboxes, so you can pick the folders to sync instead of writing
rules: uncheck a folder to leave it out, check a folder inside it to bring
that one back, or uncheck the whole folder and check only the ones you
want. A folder with some subfolders checked and some not is shown as
mixed. The dialog previews the rules it writes and puts them into the
rules field, where you can still edit them; other rules (such as
`- *.tmp`) are kept. For example:

```text
+ /Photos/2026/**
- /Photos/**
```

syncs everything except `Photos`, apart from `Photos/2026`, and

```text
+ /Documents/**
- **
```

syncs only `Documents`. A two-way profile always keeps its sync marker
`.omnisync-check` visible, whatever the rules say.

For a two-way profile, changing the filter rules (or a filtering flag such
as `--exclude` or `--max-size`, or marking a file manual) makes the next
sync a resync. OmniSync handles this by itself: it first runs a normal
two-way sync with the old filters, so pending changes and deletions are
carried over, and then a resync with the new ones.

### rclone flags
Extra flags for rclone, e.g. `--bwlimit 1M` or `--transfers 8`. For safety
only a fixed list of transfer, filtering and limit flags is accepted (such
as `--bwlimit`, `--transfers`, `--checkers`, `--tpslimit`, `--max-delete`,
`--max-transfer`, `--fast-list`, `--checksum`, `--exclude`, `--include`,
`--track-renames`, `--drive-skip-gdocs`); flags that read or write files or
run commands (`--config`, `--log-file`, `--password-command`, ...) are
rejected. A flag that takes a value gets it as `--flag value` or
`--flag=value`; a switch such as `--fast-list` takes none (or `=true` /
`=false`), and nothing else may stand between the flags. At most 64
arguments and 500 filter rules are accepted. A two-way profile also
rejects flags that could hide the sync marker `.omnisync-check` from
rclone, which would make every two-way sync fail: `--include`,
`--exclude`, `--filter`, `--exclude-if-present`, `--max-age`,
`--min-age`, `--min-size`, `--max-size` below 1Ki and `--max-depth`
below 1. Use the profile's filters instead, e.g. `+ /Docs/**` then
`- **`.

`--max-delete N` replaces OmniSync's default limit of 50 deleted files per
sync for this profile (`-1` means no limit). A push or pull stops once it
reaches the limit; its retries share the limit with the attempts before. A two-way sync applies it to each side and checks it
with a dry run before anything changes: a sync that would delete more files
on one side does not start and pauses the profile.

### Bandwidth limit
Limits how fast this profile's syncs transfer (rclone's `--bwlimit`).
Either a rate, such as `10M` (10 MiB/s), `512k` or `off`, with
`upload:download` as in `10M:1M`; or a timetable of `HH:MM,rate` entries
separated by spaces, which changes the limit during the day:

```text
08:00,512k 19:00,10M 23:00,off
```

A day can be put in front of a time (`Mon-08:00,1M Sat-00:00,off`). The
value is checked when you save. Set the limit either here or as
`--bwlimit` in the rclone flags, not in both: the form refuses that.

### Sync window
Allows automatic syncs only at certain times, for example on weekdays from
22:00 to 06:00. Choose the days and the start and end time (the server's
time zone, `TZ` in Docker); an end before the start runs past midnight
and belongs to the day the window starts. Outside the window, changes are
still noticed, but the sync they would start and the interval sync wait
until the window opens, and then run. Syncs you start yourself run at any
time; the start notes that the window is closed. In the TUI the window is
written as `22:00-06:00` (every day) or `Mon-Fri 22:00-06:00`.

### Enabled
A disabled profile keeps its settings and history but has no running sync
engine: nothing is watched or synced, and disabling stops a sync that is
running. The switch on the profile's card asks before it enables or
disables the profile.

## Backup targets

Each profile can have backup targets (profile page, **Backups** tab; the
create form can add the first one). A backup copies the profile's **local
folder**:

- **Target type**: *Local directory*, *Same remote* or *Custom remote*. For
  the two remote types the path is the full rclone path including the
  remote name, e.g. `gdrive:Backups/documents`; type it, or pick a folder
  with the folder icon, which browses the profile's remote (*Same remote*)
  or the chosen remote (*Custom remote*). The remote must be configured; a
  path on an unknown remote is refused when you save. A local directory
  must be an absolute path, and the folder must already exist (see below).
- **Mode**:
  - *Mirror* keeps an up-to-date copy in `<target>/current/`; files a run
    replaces or deletes move to `<target>/versions/<timestamp>/`, and
    `<target>/manifests/<timestamp>.json` lists every file the run left in
    `current/`. Each run is a snapshot you can restore, the latest one
    included; the restore rebuilds the folder as it was right after that
    run (see the [Dashboard Guide](dashboard-guide.md#backups)).
  - *Archive* writes a full `backup-<timestamp>.tar.gz` per run. On a local
    target it is written as `.omnisync-partial-backup-<timestamp>.tar.gz`
    and renamed only when complete, so a full disk never leaves a truncated
    archive that would be listed as a snapshot; the next run removes such
    leftovers.

  Neither mode backs up the `.omnisync-trash` folder or rclone's unfinished
  transfer files (`<name>.<8 hex digits>.partial`).
- **Frequency** (hours, 1 to 8760, default 24): counted from the start of
  the target's last backup run (a new target: from when it was created), not
  a time of day. Restarting the server or editing the target keeps that
  schedule. A run that fell due while the server was down starts about two
  minutes after it starts again, further overdue targets one minute apart.
- **Retention** (days, 1 to 365; the form suggests 30): after each
  successful backup, snapshots older than this are deleted. A mirror's
  `current/` copy is always kept.
- **Always keep** (snapshots, 1 to 1000, default 3): the newest snapshots
  retention never deletes, however old. For a mirror only completed backups
  count, so the newest completed one is never deleted, even when later runs
  failed.
- **Verify after each backup** (on for new targets; targets created before
  this setting existed keep running without it until you turn it on):
  after each run the backup is compared with the folder.
  - *Mirror*: `rclone check --one-way` between the local folder and
    `current/` (`rclone cryptcheck` for an encrypted target): every file of
    the folder must be in the backup with the same size and checksum. No
    file content is downloaded: rclone lists `current/` once and hashes
    the local files (reading the local folder once per run); for an
    encrypted target it also reads the 32-byte header of each backed-up
    file. Files only in the backup are not reported. A file changed while
    the backup ran shows up as a difference; the next backup picks it up.
  - *Archive*: the archive is read back in full (gzip checks every byte;
    on a local target the copy on the target itself is read), and the size
    stored at the target is compared. An archive on a remote is not
    downloaded again; when encrypted, its first 64 KiB are downloaded and
    decrypted, which also proves the passphrase.

  The result (*Verified*, or *Verification failed* with the reason) is
  stored with the run and shown on the target card. A failed verification
  does not mark the backup itself as failed, but sends a
  **Backup verification failed** notification.
- **Encryption** (optional, only when creating a target or for an empty
  target folder): with a passphrase (at least 8 characters), everything
  written to the target, file and folder names included, is encrypted
  with rclone's crypt backend. Mirror and archive backups, snapshots,
  retention, browsing and restores work the same. The passphrase is stored
  in OmniSync's database in rclone's obscured form (like the passwords in
  `rclone.conf`: protected by the data directory's permissions, not by a
  second key), is never sent back by the API, and is hidden in the log.
  rclone receives it through the environment of its own processes (a
  crypt remote named `omnisync_backup_crypt_<target id>`, defined only
  there), never on its command line, so other local users cannot read it
  in the process list. Remote names starting with `omnisync_backup_crypt_`
  are reserved for this and refused when adding or importing remotes.
  **Without the passphrase the backups cannot be restored, by OmniSync or
  anyone else; keep it somewhere safe.** It cannot be set, changed or
  removed while the target folder holds anything, because the existing
  backups would become unreadable: choose a new, empty folder (in the same
  edit) instead. To read such a backup without OmniSync, configure an
  rclone `crypt` remote with `remote = <target path>`, your passphrase as
  `password`, no `password2`, `filename_encryption = standard` and
  `directory_name_encryption = true` (rclone's defaults). Encrypted names
  are longer than the originals; very long file names (about 140
  characters and more) may not fit on some file systems.

An enabled target with no completed backup for more than twice its
frequency (a new target: since it was created) is marked **Overdue**, and a
notification is sent once; it is sent again only after the target completed
a backup and fell behind again (or after a server restart).

Snapshots are named by the time of the run, and a restore relies on that
order. A backup refuses to run (a *failed* run with a notification) when the
server clock is behind the newest snapshot at the target, e.g. after the
clock was reset; correct the clock and run it again.

Before each run OmniSync checks that the target is reachable, without
creating anything: a local target folder must exist and be writable (a
missing one is usually an unmounted drive, and OmniSync never creates it,
so a backup cannot land on the local disk underneath), and a remote target
path is listed itself. Only a remote target that has never completed a
backup may lack its folder; the first run creates it. An unreachable target
is skipped: the run is recorded as *skipped*, the target is marked
**Unreachable**, and a notification is sent.

A backup waits for a running sync of the profile to finish (up to 5
minutes). Like a sync, it refuses to run, records a *failed* run and sends a
notification when the local folder is missing or not mounted, when it is
empty while the backup holds files (for an archive target: while archives
exist), or, for a mirror, when the backup has the sync marker
`.omnisync-check` but the local folder does not. Otherwise an unmounted
folder would push the whole backup into `versions/`, and retention would
later delete it. The very first backup of an empty folder runs. How to run a backup now
and restore a snapshot is described in the
[Dashboard Guide](dashboard-guide.md#backups).

## Server settings

### Log level
On the **Config** page: `DEBUG`, `INFO` (default), `WARNING`, `ERROR` or
`CRITICAL`. It takes effect immediately. The **Logs** page shows the
server's log file.

### Job history
On the **Config** page, **Keep job history (days)**: sync and backup jobs
(with their file lists and errors) older than this are removed at startup
and once a day. Default 90; `0` keeps everything. The newest 100 jobs of
each profile and backup target, jobs with an unresolved conflict, and each
backup target's newest completed backup are always kept. Notification
history older than this is removed as well. Stored as `history_days` in
`config.toml`.

### Notifications
On the **Notifications** page you choose the channels and send a test. See
the [Dashboard Guide](dashboard-guide.md#notifications). On a headless
server or NAS (no desktop session, no browser left open) use **Webhook**,
**ntfy** or **Email**: Web Push only reaches browsers that subscribed, and
Host Native needs a desktop session.

The settings are stored in `config.toml` under
`[notifications.channels.<name>]`; the page and the TUI write them for you.
Credentials (webhook header values, the ntfy token and password, the SMTP
password) are never sent back to the browser, and `config.toml` is written
readable by its owner only (0600) once it holds one.

```toml
[notifications.channels.webhook]
enabled = true
min_severity = "warning"
url = "https://ha.example.com/api/webhook/omnisync"
allow_http = false            # true: plain http, only to a LAN/loopback address
[notifications.channels.webhook.headers]
Authorization = "Bearer <token>"

[notifications.channels.ntfy]
enabled = true
min_severity = "error"
server = "https://ntfy.sh"   # or your own server
topic = "omnisync-<something-hard-to-guess>"
token = "tk_..."             # or username + password

[notifications.channels.email]
enabled = true
host = "smtp.example.com"
port = 587
security = "starttls"        # starttls (587), tls (465) or none (25)
username = "nas@example.com"
password = "..."
from_addr = "nas@example.com"
to = ["you@example.com"]
```

The **webhook** receives a POST with this JSON body:

```json
{
  "source": "omnisync", "version": "0.12.0",
  "event_type": "sync_failed", "severity": "error",
  "title": "Sync push failed — Documents", "body": "...",
  "profile_slug": "documents", "profile_name": "Documents",
  "timestamp": "2026-10-02T12:00:00+00:00"
}
```

Any 2xx answer counts as delivered; redirects are not followed. **ntfy**
gets the title and body as UTF-8 through ntfy's JSON API, with the
severity as priority (debug 2, info 3, warning 4, error 5) and a matching
emoji tag. **Email** sends a plain-text mail with the subject
`[OmniSync] <title>`; TLS certificates are verified.

Addresses: since you configure them yourself, addresses on your network
(Home Assistant, n8n, a self-hosted ntfy, a mail relay) are allowed. Plain
http needs **Allow http** and works only for loopback or private addresses
(including Tailscale's 100.64.0.0/10); link-local and cloud metadata
addresses (169.254.0.0/16, fe80::/10, fd00:ec2::254) and schemes other than
http(s) are refused. Without encryption, an SMTP password is only sent to a
local address. Each channel gets 20 seconds per notification, and the
channels are sent to in parallel.

### Environment variables
Where OmniSync keeps its data, the API token, the delete limit
(`OMNISYNC_MAX_DELETE`, default 50; per side for two-way syncs), how long
the trash is kept (`OMNISYNC_TRASH_DAYS`, default 30), where the two-way
sync state of each profile lives (`OMNISYNC_BISYNC_DIR`, default
`/data/omnisync/bisync`), check timeouts and network access are set through
environment variables in `.env`. The [README](../../README.md#environment-variables) lists them all.

## OmniSync's own data

The database, `rclone.conf` (your remotes with their OAuth tokens), the API
token, the Web Push keys and `config.toml` live in the `omnisync-data`
volume (`/data/omnisync`). Before each database migration the backend keeps
a copy as `omnisync.db.pre-<revision>.bak`, and every change OmniSync makes
to `rclone.conf` keeps the previous file as `rclone.conf.bak`. How to back
the volume up, restore it and uninstall OmniSync is in the
[README](../../README.md#backing-up-omnisyncs-own-data).

---

[Next: Dashboard Guide](dashboard-guide.md) | [Back: Getting Started](getting-started.md)
