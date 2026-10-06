# OmniSync Dashboard Guide

A tour of the web UI at <http://127.0.0.1:3000>. The sidebar leads to
**Dashboard**, **Profiles**, **Jobs**, **Remotes**, **Configuration**,
**Logs**, **Conflicts** and **Notifications**; at its bottom you choose the
**Theme** (system, dark, light) and the **Language** (English, Deutsch,
فارسی). Most pages have a **?** button with help for that page.

The pages refresh themselves every 30 seconds, and every 2 seconds while a
sync runs.

## Dashboard

- **Buttons for all enabled profiles**: **Sync now** (two-way profiles
  only, after a confirmation that shows the preview), **Push**, **Pull**,
  **Check for changes**, **Show Diff** (one tab per profile with pending
  changes; once open, the button reads **Refresh diff** and reloads the
  differences while the table stays visible, and **Hide Diff** closes them)
  and, while a sync runs, **Stop**. **Stop** first asks, naming every
  profile whose sync is running; confirming stops those syncs, and their
  automatic syncing goes on afterwards. **Cancel** or Escape stops nothing.
- **Pause all** pauses automatic syncing (file watcher and interval) of
  every enabled profile until you resume; syncs you start still run.
  **Resume all** lifts those pauses; a profile OmniSync paused itself
  (differences to review, a one-sided restore, a needed resync) stays
  paused and is named in a message, so you resume it on its page after a
  look.
- **Paused banner**: lists each paused profile with its pending count (or
  **Paused by you**), a **Review** link to its differences and **Resume
  Intervals** (available once nothing is pending).
- **Dashboard card**: overall state (Idle, Pushing, Pulling, Syncing, Error), number
  of profiles, the most recent sync and the total of pending changes.
- **System Health**: whether rclone is installed and the database works,
  the server's uptime, and whether each running profile's remote is
  reachable.
- **Sync Profiles**: every profile with its state, pending changes, last
  sync (as "5 minutes ago"; hover for the exact time) and last error, a
  **Resync needed** badge on a two-way profile that waits for a resync, and
  push and pull arrows that sync just that profile after the usual
  confirmation; paused and failing profiles come first. While a profile
  syncs, its row shows a progress bar with the percentage, sizes, files,
  speed and time left.
- **No profiles yet**: a card with **Create your first profile**, which opens
  the create form on the Profiles page.

## Profiles

The list shows each profile as a card with its sync mode (**Two-way** or
**Mirror**), folders, last sync, pending changes and last error, an on/off
switch (a disabled profile does nothing), **Push** / **Pull**, a gear to
open it and a trash icon to delete it (history included; your files are not
touched). A two-way profile's card also has **Sync now**, or **Resync**
when it shows **Resync needed**.

The on/off switch asks before it changes anything. Disabling says that the
profile's automatic syncs stop (no file watcher, no interval sync, no
**Push**, **Pull** or **Sync now** until you enable it again) and, if a sync
is running, that it is stopped now. Enabling says that its automatic syncs
start again, or that they wait for **Resume Intervals** when you paused
the profile. Settings, history and files stay either way; **Cancel** or
Escape leaves the profile as it was.

**Create Profile** opens the form described in the
[Configuration Guide](configuration.md#profile-settings). The folder icons
open a picker for local folders (inside your sync folder) and for folders on
the chosen remote. With no remote yet, **Setup Wizard** under the remote list
creates one without leaving the form. **Test Sync** writes a small test file
into the local folder, uploads it to the remote folder, checks that it
arrived and removes it again, and shows which step failed if one did.

### A profile's page

- The header shows the profile's state and its sync mode.
- **Sync now** (two-way profiles) runs a two-way sync at once; while the
  profile is paused it asks first. The confirmation shows, for the local
  and the remote folder, how many files would be deleted, replaced or newly
  copied, whether more would be deleted than the delete limit (per side),
  how many files changed on both sides (both versions will be kept), and
  whether the run is a resync.
- **Resync** (two-way profiles) asks first and explains what it does: both
  folders become the union of both, nothing is deleted, and where a file
  differs the newer version wins and the older one is moved to
  `.omnisync-trash`. Afterwards automatic two-way syncing resumes.
- **Resync needed**: when a two-way profile's sync state was lost or is
  inconsistent (for example after an interrupted sync), a red alert shows
  the reason and a **Resync** button, and automatic syncing stays paused
  until you confirm it. **Sync now** is unavailable until then.
- **Push** and **Pull** sync the whole folder one way after a confirmation
  that says how many files would be deleted or replaced, and warns when the
  sync would stop at the delete limit. A confirmed push or pull also runs
  while the profile is paused. On a two-way profile they are one-way
  overrides. **Stop** ends a running sync after a confirmation that names
  the profile; the profile keeps syncing afterwards.
- **Switch to two-way** appears on a mirror profile: a short explanation
  and a button that switches the profile after a confirmation. Its next
  sync is then a resync.
- **Pause** pauses this profile's automatic syncing until you resume it
  (a **Paused by you** badge shows it); **Resume Intervals** appears while
  the profile is paused, for a pause of yours or one OmniSync set.
- **Overview**: the current state, a progress bar while a sync runs (with
  the files being transferred), files processed, errors, the last error in
  full, whether automatic syncs wait for the sync window, and the
  configuration (including the bandwidth limit and sync window) with
  **Edit**, **Delete** and **Test Sync**
  (the same round-trip test as in the form, on the saved folders). After a
  rename the page moves to the profile's new address.
- **Differences**: the file-by-file comparison; see
  [Differences and Conflicts](conflict-resolution.md).
- **Backups**: see below.
- **Sync history**: this profile's syncs, 20 per page, like
  [Jobs](#jobs) without the profile column. Click a job for its files.
- **Trash**: what this profile's syncs replaced or deleted, kept in
  `.omnisync-trash`. Switch between **Local folder** and **Remote folder**;
  the list shows each file's original place, when it was moved to the
  trash and its size, and the total. Select files, then **Restore** puts
  them back where they were (the next sync carries them to the other side).
  A file that is in the way is moved into the trash first; if it is newer
  than the trashed version, you are asked before it is replaced.
  **Delete** removes files from the trash for good, after a confirmation.
  Both wait until no sync of the profile runs.

### Backups

**Add Backup Target** creates a target (see the
[Configuration Guide](configuration.md#backup-targets)); it can be
encrypted with a passphrase and verified after each backup. Each target card
shows its type and mode, an **Encrypted** badge, when it last ran and runs
next, the last verification (**Verified**, or **Verification failed** with
the reason), retention and frequency, how many snapshots it always keeps,
an **Unreachable** badge when the last attempt could not reach it, and an
**Overdue** badge when no backup completed for more than twice its
frequency.

- **Run Now** backs up immediately and reports whether the backup completed
  or failed (with the reason) when it has finished.
- **History** lists the snapshots by date, newest first; the newest is
  marked **Latest backup** and is restorable like the others. **Restore** on
  a snapshot opens the restore dialog. Choose **Local Only** (default), **Remote Only** or
  **Both**, confirm that the restore overwrites files, and start it.
  - Before you confirm, the dialog shows what the restore would change on
    each side: how many files would be added, replaced and removed (moved
    to the safety copy), and how many stay unchanged, with example paths as
    tooltips. Files are compared by size and modification time, like the
    restore itself; nothing is changed by the preview.
  - Files the restore replaces or removes are first copied to
    `.omnisync-trash/pre-restore/<timestamp>-<id>/` inside the restored
    folder, and are never deleted automatically.
  - Restoring a snapshot makes the folder hold exactly the files it had
    right after that backup: changed files are replaced and files that are
    not in the snapshot are moved to that safety copy. This holds for
    archive and mirror targets alike. The sync marker and the trash folder
    are left alone, and empty folders are not removed.
  - If the backup is damaged (a file of the snapshot is missing), the
    restore fails before it changes anything.
  - A missing local folder is not created; the restore fails instead.
  - When restoring only one side, automatic syncing of the profile pauses
    before the restore starts, so no sync undoes or spreads it; the pause
    stays after a server restart or a profile edit. Review the differences,
    then sync or resume.
- **Browse** on a snapshot opens the snapshot browser: the snapshot's
  folders (click one to open it, the path above leads back), a search over
  the whole snapshot, and checkboxes to select files and folders (a folder
  means everything in it). **Restore** puts the selection back:
  - to its **original place in the local folder**: only the selected files
    are written, files they replace are kept in
    `.omnisync-trash/pre-restore/<timestamp>-<id>/`, nothing else changes
    and nothing is deleted. Syncing is **not** paused: the restored files
    are an ordinary local change, so a mirror profile pushes them to the
    remote and a two-way sync spreads them like any edit. That is usually
    what bringing back a lost or broken file means; to look at old versions
    without touching the synced folder, restore elsewhere.
  - or into **another local folder** (an absolute path, created if its
    parent exists; the folder picker helps): the files land there with
    their folders. OmniSync's data directory and backup target folders are
    refused, and with `OMNISYNC_BROWSE_ROOTS` set the folder must be inside
    one of those roots.

  Listing an archive reads it from start to end (an archive on a remote is
  downloaded once for that, then the list is kept in memory); a mirror
  snapshot is listed from its file list.
- **Delete** removes the target; the backup files stay where they are.

## Jobs

**Job History** lists every sync, 20 per page: direction (Push, Pull,
Selective for per-file actions, Two-way, or Resync), start time, status,
files changed and errors. Click a job for its details and the **File
Changes** table (each file created, modified or deleted, and for newer jobs
the **Side**, Local or Remote, it changed on). The error message of a failed sync is
shown on the profile ("Last error").

## Remotes

Each remote shows its type, used and total storage where the provider
reports it, and **Used by**: the profiles and backup targets that use it.

- **Test** checks that the remote can be listed and shows the latency.
- **Browse** opens the remote's folders to look around.
- **Setup Wizard** adds a remote; see
  [Getting Started](getting-started.md#2-add-a-cloud-remote).
- The trash icon deletes the remote's configuration (not the files in the
  cloud). A remote that is still in use needs **Force Delete**.

## Configuration

**Global Settings** holds the server's **Log level**, which applies
immediately, and **Keep job history (days)**: older sync and backup jobs
are removed once a day (default 90, `0` keeps everything; the newest 100
jobs of each profile and backup target always stay). Below it is a short list of remotes with the wizard.

## Logs

The server log, newest first, 100 entries per page. **Older** and **Newer**
page through the whole file. The level tabs (**All**, **Debug**, **Info**,
**Warning**, **Error**) filter on the server, so **Error** lists every error
in the file, not only those among the newest entries. **Refresh** loads new
lines.

## Conflicts

Files changed on both sides since the last sync, recorded when a profile's
differences are loaded and when a two-way sync finds such a file. Every
choice asks for confirmation first. With more than one profile, the
**Profile** selector shows only that profile's conflicts.

An entry from the differences offers **Keep local**, **Keep remote**,
**Keep both** or **Dismiss**:

- **Keep local** / **Keep remote** copy that side's version over the other;
  the replaced version goes to the trash.
- **Keep both** keeps the local version under its name and the remote one as
  `<name>.conflict-<time>` on both sides.
- **Dismiss** only closes the entry; no file changes.

If a file changed again since the differences were loaded, the action is
refused and nothing is overwritten.

An entry from a two-way sync says "Both versions were kept: local version
as X, remote version as Y"; both files already exist on both sides. It
offers:

- **Keep only local** / **Keep only remote**: keeps that version under the
  original name on both sides; the other version is moved to
  `.omnisync-trash` on each side.
- **Keep both**: closes the entry; both files stay as they are.

See
[Differences and Conflicts](conflict-resolution.md).

## Notifications

- **Web Push**: notifications in this browser, even when the tab is closed.
  Switching it on asks for the browser's permission and subscribes this
  browser; switching it off unsubscribes it. In another browser, use
  **Enable in this browser** on the card. The card shows how many browsers
  are subscribed.
- **Host Native**: desktop notifications on the machine that runs OmniSync.
  Off by default. In Docker this needs the D-Bus override
  (`docker-compose.dbus.yml`). The card shows the detected host and, when
  the channel cannot deliver, what is missing (for example the D-Bus
  socket or `notify-send`).
- **Webhook**, **ntfy** and **Email**: work without a desktop or a browser,
  so they are the channels for a headless server or NAS. Off until set up:
  **Configure** on the card opens the form (webhook URL and optional
  headers such as `Authorization`; ntfy server, topic and an access token or
  user and password; SMTP server, port, encryption, login, sender and
  recipients). Plain `http://` needs **Allow plain http** and works only for
  addresses on your network. Stored secrets show as "•••• set — leave empty
  to keep": leave the field empty to keep them, or **Remove** them. The
  format and the address rules are in the
  [Configuration Guide](configuration.md#notifications). In the TUI, `c` on
  the channel in the Notifications view opens the same form and `t` tests
  it.
- **Min severity** per channel (Debug, Info, Warning, Error; default
  Warning).
- **Test** on a card sends a test notification through that channel only,
  even when it is switched off. **Send test** below the cards sends one
  through every channel that is on.
- **History** lists the last 100 notifications and the channels they went
  through (entries older than the job history setting are removed).

The page also warns when the notification settings in `config.toml` have
errors (the defaults are used where they are invalid) or name a channel
that does not exist.

Notifications are sent for completed and failed syncs (two-way syncs
included), authentication errors, unreachable remotes, conflicts (new
ones found by a check, and files a two-way sync kept in two versions),
paused profiles, a two-way profile that needs a resync, a sync or backup
that crashed, a profile that could not start, backups and restores,
unreachable and overdue backup targets, server start, and a failing scheduler,
database or rclone.

---

[Next: Differences and Conflicts](conflict-resolution.md) | [Back: Configuration Guide](configuration.md)
