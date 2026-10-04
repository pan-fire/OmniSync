# Key Features of OmniSync

## 1. Sync profiles
A profile pairs one local folder with one cloud folder. Each has its own
sync mode, debounce delay, pull interval, retry count, rclone filter rules
and rclone flags, and can be switched off without losing its settings.
Profiles whose folders overlap are refused, so two profiles never fight
over the same files.

## 2. Sync modes
- **Two-way** (recommended; the default for new profiles), built on
  `rclone bisync`: changes on either side are carried to the other. New and
  edited files on both sides are kept, deletions are carried over in both
  directions, and a file changed on both sides keeps both versions: the
  newer one keeps the name, the other is saved next to it as
  `name.local-conflict1.ext` or `name.remote-conflict1.ext` on both sides
  and listed on the **Conflicts** page.
- **Mirror** (one-way push/pull only): local changes are pushed, the cloud
  is pulled on the interval, and the side that syncs last wins. Profiles
  created before two-way sync existed keep this mode until you switch them;
  the profile page shows a **Switch to two-way** hint for them.
- The first two-way sync of a profile is a **resync**: both folders become
  the union of the two, nothing is deleted, and where a file differs the
  newer version wins (the older one goes to the trash).
- **Push** and **Pull** stay available in both modes as one-way overrides
  behind a confirmation.

## 3. Automatic syncing
- Local changes are noticed within a few seconds and synced once you stop
  editing: a two-way sync, or a push in mirror mode.
- On the pull interval the profile runs a two-way sync, or a pull in mirror
  mode. A two-way profile also syncs right after it starts, which picks up
  what changed on either side while OmniSync was not running.
- **Sync now** starts a two-way sync at once; **Resync** starts a resync
  after a confirmation.
- Failed syncs are retried with growing pauses; provider rate limits get
  longer pauses, login problems are reported instead of retried.
- A running sync can be stopped; the profile carries on afterwards.
- A running sync shows its progress: a progress bar with sizes, files,
  speed and time left, and the files being transferred.
- **Pause** one profile or **Pause all**: automatic syncing waits until
  you resume, also after a restart; syncs you start still run. **Resume
  all** never lifts a pause OmniSync set to protect your files.
- A **bandwidth limit** per profile (a rate, or a timetable such as
  "512k by day, unlimited at night") and a **sync window** that limits
  automatic syncs to certain hours and days.
- **Choose folders**: pick the folders to sync in a tree; OmniSync writes
  the filter rules for you.

## 4. Look before you sync
A check compares both folders without changing anything and sorts every
difference: only local, only in the cloud, changed locally, changed in the
cloud, or changed on both sides (a conflict). You can decide per file:
push, pull, skip, mark it to handle yourself, or keep both versions of a
conflict. For a mirror profile, a check that finds something pauses
automatic syncing until you have decided; a two-way profile keeps syncing,
since a two-way sync carries the differences both ways. The confirmation
before a sync shows what it would do, for a two-way profile per side
(files deleted, replaced and newly copied), the number of conflicts, and
whether the run is a resync.
See [Differences and Conflicts](conflict-resolution.md).

## 5. Safety rails
These apply in both modes.
- **Sync marker**: both folders hold `.omnisync-check` (written after a
  mirror profile's first sync, or before a two-way profile's first resync);
  if it is missing on one side (an unmounted drive, an emptied folder),
  OmniSync refuses to sync.
- **Empty-side refusal**: a sync from an empty folder into a full one is
  refused; a two-way sync is refused when either side is empty and the
  other is not.
- **Missing folder**: a missing local folder is never created; the profile
  pauses instead.
- **Delete limit**: 50 files per sync (adjustable per profile with
  `--max-delete`, or server-wide). A push or pull stops once it reaches the
  limit. For a two-way sync the limit applies to each side and is checked
  with a dry run before anything changes: a sync that would delete more
  does not start at all and pauses the profile. The confirmation shows the
  real limit and how many files the sync would delete.
- **Trash**: every file a sync overwrites or deletes is moved to
  `.omnisync-trash/<timestamp>/` on the side it was removed from and kept
  for 30 days (adjustable; `OMNISYNC_TRASH_DAYS=0` keeps it forever). The
  profile's **Trash** tab lists it per side; restore puts files back where
  they were (asking before replacing a newer file, whose current version
  then goes to the trash too) and delete removes them for good.
- **Resync only with your consent**: if a two-way profile's record of the
  last sync is lost or inconsistent, it pauses with the reason ("Resync
  needed") and waits until you confirm a resync. After a filter change
  OmniSync resyncs by itself, safely: it first syncs with the old filters,
  then resyncs with the new ones.
- **One at a time**: a profile runs one sync, per-file action, backup or
  restore at a time.
- **Fail closed**: if OmniSync cannot compare the two sides when a mirror
  profile starts, it pauses instead of assuming they are in sync.

## 6. Backups and restore
Each profile can back up its local folder on a schedule to a local folder or
a cloud remote, either as a mirror with dated versions or as dated archives,
with a retention period. A snapshot, the latest backup included, can be
restored to the local folder, the cloud folder, or both, exactly as the
folder was right after that backup; what the restore replaces or removes is
kept as a safety copy first, and the restore dialog first shows how many
files would be added, replaced and removed. Single files and folders can be
picked in a snapshot browser and restored to their place or into another
folder. A target can be encrypted with a passphrase, and each backup can be
verified against the folder afterwards. A backup of an empty or unmounted
folder is refused, and an unreachable target is skipped with a notification.

## 7. Remotes
A setup wizard adds Google Drive, OneDrive and Dropbox (sign-in in the
browser) or Amazon S3, Backblaze B2, SFTP, FTP, WebDAV (Nextcloud, ownCloud,
SharePoint, others) and SMB/Windows shares (keys or login), tests the
connection, and shows storage use and which profiles use each remote. A
**crypt** remote encrypts file contents and names before they reach another
remote (pick the remote and folder that hold the encrypted files).
OmniSync keeps its own rclone configuration and never touches yours.

- **Edit** changes a remote's settings in place (rotate an S3 key, fix an
  SFTP password, point a crypt remote elsewhere), so the profiles that use it
  keep working. Stored secrets are never shown again: an empty secret field
  keeps the stored one.
- **Reconnect** renews the sign-in of a Google Drive, Dropbox or OneDrive
  remote whose token expired or was revoked; only the token is replaced.
  When a test, health check or sync is refused by the provider, the remote
  card is marked and offers Reconnect (or *Update credentials* for remotes
  with keys or a password).
- **Import rclone.conf** adds remotes from an existing rclone configuration
  (file or pasted text): it shows which remotes the file holds, which names
  are already taken (import those under another name) and which cannot be
  imported, then adds the ones you choose.
- **The same safety checks everywhere.** Whether a remote is imported,
  created in the wizard or edited, OmniSync refuses settings that would
  reach this machine's own files past the checks local folders get: a
  crypt, alias or union style remote that points at a `local` remote or
  wraps a local path (directly or through a chain of remotes), and file
  settings such as SFTP's key file that point into OmniSync's data folder
  or contain `$`.

## 8. History, logs and notifications
- **Jobs**: every sync (push, pull, two-way sync or resync) with its status
  and the files it created, changed or deleted, and on which side; each
  profile's page has its own sync history.
- **Logs**: the server log, filtered by level.
- **Notifications** for failed syncs, paused profiles, backups and more,
  with a minimum severity per channel: by webhook (Home Assistant, n8n, ...),
  ntfy or email, which work on a headless server or NAS; by web push to the
  browsers you subscribed; or as desktop notifications on the host (needs a
  desktop session).

## 9. Two interfaces, locked down by default
- The **web UI** (English, German, Persian; light and dark theme).
- The **terminal UI** `osync`, with subcommands for scripts
  (`osync status`, `osync push <profile>`, ...).
- Both reach the server only from the same machine by default, and every
  request needs the API token.

---

[Next: Getting Started](getting-started.md) | [Back: Introduction](introduction.md)
