# FAQ & Troubleshooting for OmniSync

## Frequently Asked Questions

### Which cloud providers work?
The setup wizard covers Google Drive, OneDrive, Dropbox, Amazon S3,
Backblaze B2, SFTP, FTP, WebDAV, SMB and crypt (an encrypted folder on
another remote). OmniSync uses rclone underneath, so a remote of any other
rclone type can be used as well: import it with **Remotes → Import
rclone.conf**, or add it to OmniSync's rclone config
(`/data/omnisync/rclone.conf` in the data volume).

### I already use rclone. Can I take my remotes over?
Yes: **Remotes → Import rclone.conf**, then choose your rclone.conf (usually
`~/.config/rclone/rclone.conf`, or the output of `rclone config file`) or
paste its contents. OmniSync lists the remotes in it; remotes whose name is
taken can be imported under another name, and a crypt remote that wraps a
renamed remote follows the rename. Not imported: `local` remotes (profiles
name local folders directly), remotes that wrap a local path, and settings
that would run a program on the server (SFTP `ssh`, WebDAV
`bearer_token_command`). An encrypted rclone.conf must be decrypted first
(`rclone config encryption remove` on a copy). The file holds your tokens
and passwords: it is sent only to your OmniSync server, never logged and
never shown again. In the TUI, press `I` on the Remotes view.

### My Google Drive / Dropbox / OneDrive remote stopped working ("invalid_grant", "unauthorized")
Its sign-in expired or was revoked (a changed password, a removed app
permission, or a token unused for months). Click **Reconnect** on the
remote's card (the card is marked when a test, health check or sync was
refused) and sign in again; only the token is replaced, so profiles and
backups that use the remote keep working. In the TUI: `a` on the Remotes
view. A Google Drive app in *Testing* mode ends its sign-ins after 7 days;
publish the app to avoid that (see [Cloud Remotes](remotes.md#google-drive)).

### Why do I need my own OAuth app?
OmniSync ships no OAuth client IDs or secrets of its own, rclone's
included: Google Drive, Dropbox and OneDrive sign in with an app you
register once with the provider. [Cloud Remotes](remotes.md) has the steps
for each. A remote that uses rclone's built-in app (for example one from
an imported rclone.conf) keeps working, because rclone refreshes its token; to **Reconnect** it, enter your own app's client ID.

### How do I change a password or key of a remote?
**Edit** on the remote's card (TUI: `e`). Stored passwords and keys are not
shown; leave a field empty to keep it, type a new value to replace it, or
remove it (e.g. to sign in to SFTP with a key file instead of a password).
Do not change a crypt remote's passwords or encryption settings once files
are stored: the files can only be read with the ones they were written with.

### Can I sync several folders?
Yes: create one profile per folder pair. With docker compose every local
folder must lie inside `OMNISYNC_SYNC_DIR`; for folders elsewhere add
mounts in a `docker-compose.override.yml`. Folders of enabled profiles may
not overlap.

### Is it safe for important data?
OmniSync is built to fail safe in both sync modes: it refuses to sync an
empty or unmarked folder over a full one, never creates a missing local
folder, limits deletions to 50 per sync (per side, checked before anything
changes, for a two-way sync), and keeps everything it overwrites or
deletes in `.omnisync-trash` for 30 days. A two-way sync keeps both
versions of a file changed on both sides; a mirror profile pauses when both
sides changed. Push and Pull are still one-way mirrors in both modes, so
read [Differences and Conflicts](conflict-resolution.md) and set up a
backup target for data you cannot lose.

### What is the difference between two-way and mirror?
A **two-way** profile carries changes from either side to the other: new
and edited files on both sides are kept and deletions are carried over in
both directions. A **mirror** profile pushes local changes and pulls the
cloud on the interval, each time making one side an exact copy of the
other, so the side that syncs last wins. New profiles are two-way; profiles
created before two-way sync existed stay mirror until you switch them. See
[Sync mode](configuration.md#sync-mode).

### Should I switch my existing profile to two-way?
In most cases, yes: with mirror, a local edit that could not be pushed yet
(for example while offline) is overwritten by the next pull, and a file
that exists only in the cloud is deleted by the next push. Two-way keeps
changes from both sides. Use **Switch to two-way** on the profile's page,
or **Switch all mirror profiles to two-way** on the Dashboard to switch every
mirror profile after one confirmation (in the TUI: `w` on the profile, or
`w` on the Dashboard for all of them). You can also change the mode in the
profile form. The next sync of each switched profile is a resync, which
deletes nothing. Keep mirror if one side should always be an exact copy of
the other, for example a folder that is only ever changed in one place;
**Hide this note** (TUI: `h`) then stops the mirror-mode explanation for
that profile, in the web UI and the TUI alike.

### How do I sync or resync a two-way profile from a script?
`osync sync <profile>` runs a two-way sync ("Sync now") and waits until it
has finished; `osync resync <profile> --yes` runs a resync. Without `--yes`,
`osync resync` asks when run in a terminal and refuses otherwise. Both exit
non-zero when the sync fails or is refused (for example for a mirror
profile, a paused profile, or `osync sync` on a profile that needs a
resync), and `--json` prints the outcome as JSON. `osync profiles` shows
each profile's mode.

### What is a resync?
The first two-way sync of a profile, and a fresh start after its sync state
was lost. It makes both folders the union of the two: a file that exists on
only one side is copied to the other, nothing is deleted, and where a file
exists on both sides with different content the newer version wins and the
older one is moved to `.omnisync-trash/<timestamp>/`. OmniSync runs a
resync by itself for a new or newly switched profile and after the
profile's filters change (then it first syncs with the old filters). In any
other case it pauses the profile with **Resync needed** and waits for you to
confirm **Resync** (web UI) or `R` (TUI). A file you deleted on one side
before a resync comes back from the other side, because a resync does not
know about earlier deletions.

### What are the `.local-conflict1` and `.remote-conflict1` files?
A two-way sync found the file changed on both sides and kept both versions:
the newer one under the original name, the other one next to it, e.g.
`report.remote-conflict1.pdf`, on both sides. The **Conflicts** page lists
it; choose **Keep only local**, **Keep only remote** or **Keep both**. See
[Conflicts from a two-way sync](conflict-resolution.md#conflicts-from-a-two-way-sync).

### How do I get a deleted or overwritten file back?
Look in `.omnisync-trash/<timestamp>/` inside the folder the file was
deleted from or overwritten in (local folder or cloud folder). The
timestamp is the time of the sync, in UTC. Copy the file back by hand.

### Can I use it from another computer or my phone?
By default both the web UI and the API answer only on this machine. To use
the API from elsewhere, set `OMNISYNC_BIND_ADDRESS` and
`OMNISYNC_ALLOWED_HOSTS` in `.env` (see the README's
[access section](../../README.md#access-and-authentication)); anyone who
reaches it still needs the API token. Put TLS in front of it if the
network is not trusted. The web UI's port is published on `127.0.0.1` in
`docker-compose.yml`; change that mapping deliberately if you need it.

### How do I change the ports?
Edit the `ports:` mappings in `docker-compose.yml` (or an override file),
e.g. `"127.0.0.1:8080:3000"` for the web UI. The redirect URI of your
OAuth apps (Google Drive, Dropbox, OneDrive) is the web UI's address plus
`/api/wizard/oauth/callback`, so update it at the provider as well.

### Does it run on a Raspberry Pi or an ARM NAS?
Yes, on a 64-bit OS: the release images are built for `linux/amd64` and
`linux/arm64` under the same tag, and Docker pulls the right one. 32-bit
ARM (`armv7`) is not supported. The `osync` terminal client is published
for Linux, macOS and Windows on amd64 and arm64.

### Which version am I running, and how do I upgrade?
The web UI shows its version at the foot of the sidebar, and the server's
next to it if the two differ; `osync --version` and `osync health` show the
terminal UI's and the server's. What changed in each version is in the
[changelog](../../CHANGELOG.md). With the released images, set the new
`OMNISYNC_VERSION` in `.env` and run `docker compose pull && docker compose
up -d`; with a source checkout, `git pull` and `docker compose up -d
--build`. Database changes are applied on start. Upgrade the backend and
the web UI together.

## Troubleshooting

### Every page shows errors, or the API answers 401
The web UI's server and the backend must use the same `OMNISYNC_API_TOKEN`.
Set it in `.env` and run `docker compose up -d` again. If you never set
one, the backend generated a token into `/data/omnisync/api-token`; either
copy it into `.env` or set your own. The terminal UI needs the same value
in `OMNISYNC_API_KEY`.

### "Host not allowed"
The backend only answers requests addressed to `localhost`, `127.0.0.1`,
`::1` (and `backend` inside compose). Add the name you use to
`OMNISYNC_ALLOWED_HOSTS`.

### A profile says "paused" and nothing syncs
Open the profile: the last error says why. A mirror profile pauses when it
found differences or could not compare the two sides; the **Differences**
tab lists what to decide, and when nothing is pending you click **Resume
Intervals**. A two-way profile pauses when a sync would have deleted more
files than the delete limit (see below) or when it needs a resync.

### "Resync needed" / "Two-way sync needs a resync"
The two-way profile's record of the last sync was lost or is inconsistent,
for example after a run that was interrupted in a way rclone could not
recover from. OmniSync does not resync on its own in this case. Check both
folders (a file deleted on one side comes back during a resync), then click
**Resync** on the profile and confirm (in the TUI: `R`). Automatic syncing
resumes afterwards.

### `osync push` or the API says "Sync intervals paused due to N unresolved differences"
A push or pull from the CLI, or an API call without `force=true`, is
refused while a profile is paused. The **Push** and **Pull** buttons in the
web UI and the TUI ask for confirmation and then run anyway. Otherwise,
resolve the files in the **Differences** tab (per-file push or pull, skip,
mark manual), then resume.

### "The sync marker .omnisync-check is missing from the ... folder"
One side has the marker and the other does not, which usually means a
drive is not mounted or a folder was replaced or emptied. Check that the
folder is the right one; if it is, copy `.omnisync-check` over from the
other side.

### "Local folder ... does not exist or is not mounted"
OmniSync never creates a missing local folder. Mount the drive or create
the folder, then restart the profile (switch it off and on) or the server.

### "Stopped: this push/pull would delete more than the allowed number of files"
The sync reached the profile's delete limit (50 files unless changed) and
stopped. Check the other side first. If
the deletions are intended, add `--max-delete <n>` to the profile's rclone
arguments (or raise `OMNISYNC_MAX_DELETE` for all profiles) and sync again.
Anything the stopped sync had already replaced or deleted is in the trash.

### "Stopped before changing anything: this two-way sync would delete ..."
A two-way sync counted, in a dry run, more deletions on one side than the
delete limit, so it did not start and the profile paused. Check the folder
named in the message: were the files deleted on purpose, or is a drive
missing? If they were deleted on purpose, raise `--max-delete` for the
profile (or delete them on the other side too) and click **Resume
Intervals**; if not, restore them (for example from `.omnisync-trash`)
first.

### "Refusing to sync: the local folder is empty while the remote folder has N item(s)"
A two-way sync would carry the empty side over and delete everything on
the other side, so it is refused (this applies to either side). Check that
the right drive is mounted. If the folder was emptied on purpose, delete
the files on the other side too, or use a confirmed **Push** or **Pull**.

### "Startup check could not compare local and remote"
The server could not reach the cloud or read the files when the mirror
profile started (network, expired login, rate limit or a timeout after 120 s).
Fix the cause, then run a check again or resume.

### How do I check that a remote is reachable?
The System Health card lists the remotes of running profiles with their
reachability; **Test** on the Remotes page checks a single remote. A remote
the provider refused to sign in to is marked on its card with the fix
(Reconnect or Update credentials).

### Desktop notifications do not appear
Host Native is off by default: switch it on on the Notifications page. In
Docker it needs the D-Bus override:
`docker compose -f docker-compose.yml -f docker-compose.dbus.yml up -d`.
The card lists what is missing when it cannot deliver, and **Test** checks
it. Web Push works without it.

### Where are the logs?
On the **Logs** page, or in `/data/omnisync/omnisync.log` in the
`omnisync-data` volume (`docker compose logs backend` shows the same).

---

[Back to Introduction](introduction.md)
