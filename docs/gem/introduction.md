# Welcome to OmniSync

**OmniSync** keeps folders on your machine in sync with cloud storage such as
Google Drive, OneDrive or Dropbox. It runs as a small self-hosted server
(two Docker containers) and uses [rclone](https://rclone.org) for every
transfer, so any of rclone's providers works.

You work with it through a web UI in your browser or the `osync` terminal UI.
Both talk to the same server, which does the syncing in the background.

## What it does

- **Sync profiles.** Each profile pairs one local folder with one remote
  folder and has its own timing, filters and rclone flags. You can have as
  many as you like.
- **Automatic syncing.** A change in the local folder is synced a few
  seconds after you stop editing, and the cloud side is synced on a
  schedule. How the two sides are combined depends on the profile's sync
  mode (see below).
- **Look before you sync.** A check lists every file that differs and on
  which side. You can decide per file: push it, pull it, skip it, handle it
  yourself, or keep both versions.
- **Safety rails.** OmniSync refuses to sync an empty or unmounted folder
  over a full one, stops a sync that would delete many files, and keeps
  every file it overwrites or deletes in a trash folder for 30 days.
- **Backups.** Each profile can be backed up on a schedule to a local folder
  or another cloud remote, and restored from there.
- **History and notifications.** Every sync is recorded with the files it
  changed; failures and other events can reach you as browser push
  notifications or desktop notifications.

## Sync modes

Each profile runs in one of two modes, chosen in the profile form:

- **Two-way** (recommended; the default for new profiles) uses
  [rclone bisync](https://rclone.org/bisync/). It remembers what both
  folders looked like after the last sync and carries every change since
  then to the other side: new and edited files on either side are kept
  (also edits made while OmniSync was not running), and a deletion on
  either side is carried over, with the deleted file kept in that side's
  trash. A file changed on both sides keeps both versions and is listed on
  the **Conflicts** page.
- **Mirror** is the older behaviour: local changes are pushed (the cloud
  folder is made to match the local one) and the cloud is pulled on the
  interval (the local folder is made to match the cloud). The side that
  syncs last wins. Every profile that existed before two-way sync was added
  keeps this mode until you switch it.

In both modes, **Push** and **Pull** remain available as one-way overrides
that you confirm first.

## What it is not

OmniSync does not merge file contents. When a file changed on both sides,
a two-way sync keeps both versions next to each other and you choose which
one to keep; in mirror mode such files show up as conflicts in a check and
are left out of automatic syncs until you decide.

It is also not a public web service: by default it only answers on this
machine (`127.0.0.1`) and every request needs your API token.

---

[Next: Key Features](features.md)
