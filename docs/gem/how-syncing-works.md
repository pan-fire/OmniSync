# How Syncing Works

The precise rules behind OmniSync's sync modes, automatic syncs, pausing
and safety checks. For a gentler introduction see
[Key Features](features.md); for what to do when a profile pauses, see
[Differences and Conflicts](conflict-resolution.md).

## Sync modes and when they run

Each enabled profile gets its own sync engine, which runs in one of two
**sync modes** (set per profile in the web UI or the TUI):

- **Two-way** (recommended; the default for new profiles) uses
  [`rclone bisync`](https://rclone.org/bisync/), which remembers what both
  folders looked like after the last sync and carries every change since
  then to the other side: an edit made offline is kept, a collaborator's new
  file is kept, and a deletion on either side is carried over (the deleted
  file goes to that side's trash). A file changed on **both** sides keeps
  both versions: the newer one keeps the name, the other is saved next to it
  as `name.local-conflict1.ext` or `name.remote-conflict1.ext` on both sides,
  and the conflict is listed on the Conflicts page (resolve it there by
  keeping only one version, or keep both).
- **Mirror** (what profiles created before two-way sync existed keep, until
  you switch them): local changes are pushed and the remote is pulled on the
  interval, each as a one-way mirror (`rclone sync`). The side that syncs
  last wins: a local edit that could not be pushed yet (for example while
  offline) is overwritten by the next pull, and a file only on the remote is
  deleted by the next push.

Every mirror profile explains this on the Dashboard and on its page, in the
web UI and the TUI, with a one-click switch to two-way (**Switch all mirror
profiles to two-way** on the Dashboard switches them all after one
confirmation that lists them). The first sync after a switch is a resync
(below), which deletes nothing. To keep a profile a mirror, hide its note;
that is stored with the profile (`mirror_notice_dismissed`), so every client
honours it. The mode can also be changed in the profile form, in both
directions.

When the engine syncs automatically:

- **On local changes:** the engine watches the local folder (polling every 3
  seconds, as inotify does not cross Docker bind mounts) and syncs after the
  profile's debounce delay once changes stop (default 5 s): a two-way sync,
  or a push in mirror mode.
- **On the interval** (default 5 minutes): a two-way sync, or a pull in
  mirror mode. A two-way profile also syncs right after it starts, which
  brings in what changed on either side while OmniSync was not running.
- **Sync now** runs a two-way sync at once (two-way profiles).

The first two-way sync of a profile (a new profile, or one just switched to
two-way) is a **resync**: `rclone bisync --resync --resync-mode newer` makes
both folders the union of the two. Nothing is deleted; where a file exists on
both sides with different content, the newer version wins and the older one
goes to the trash. A resync also runs, automatically and safely, after the
profile's filters change: first a normal two-way sync with the old filters
(so pending changes and deletions are carried over), then the resync with the
new ones. Any other state in which rclone says a resync is needed (for
example its record of the last sync is lost) is **never** resynced
automatically: the profile pauses, shows why, and waits for you to confirm
**Resync** in the web UI or the TUI (`POST /profiles/{slug}/sync/resync`).
The two-way state lives in `OMNISYNC_BISYNC_DIR/<profile id>` (default
`/data/omnisync/bisync`); switching a profile to mirror forgets it.

In both modes:

- **Push** makes the remote folder match the local one (remote-only files are
  removed) and **Pull** the local folder match the remote one. They stay
  available as explicit, confirmed one-way overrides in both modes.
- **Check / diff** compares both sides without changing anything and lists
  each file as local-only, remote-only, different, or changed on both sides
  (a conflict). A check reads every file's hash and may take up to 15 minutes
  (`OMNISYNC_CHECK_TIMEOUT`).
- **Per-file actions** on a diff: push, pull, skip, mark for manual handling
  (excluded from every bulk sync until you undo it), or keep both (the remote
  version is kept next to the local one, e.g. `report.conflict-20260927T143022.pdf`,
  on both sides; nothing is deleted). Each file is re-checked before it is
  copied, because the diff may be hours old.
- **Pausing:** automatic syncing pauses when the local folder is missing,
  when a restore changes only one side (from before the restore starts; this
  pause is stored with the profile, so it also holds after a restart or a
  profile edit), and for a two-way profile when a sync hits the delete limit
  or needs a resync. A mirror profile also pauses
  when a check or diff finds pending differences (the startup check
  included) and when the startup check cannot run; a two-way profile does
  not, since a two-way sync carries differences both ways. While paused,
  per-file actions, backups and restores still work. A push, pull or Sync
  now you confirm in the web UI or the TUI still runs (it is sent with
  `force=true`; every safety check below still applies), while the CLI's
  `osync push/pull` and API calls without `force` are refused. A watcher
  sync or scheduled sync that was already waiting (for example behind a
  restore) checks the pause again and does not run, and an automatic sync
  is not retried once the profile is paused. Intervals resume when you
  resume them with nothing pending, or after a bulk sync that leaves no
  conflict behind (a two-way profile that needs a resync resumes only after
  the resync). Local changes made to a mirror profile while it is paused are
  not pushed then; resuming compares both sides first and is refused while
  differences remain, so the next pull cannot overwrite them (push, pull or
  sync them per file, then resume). Manually flagged files are always left
  out of bulk syncs (a two-way sync excludes them through its filters, so
  flagging a file makes the next two-way sync a resync); a push or pull also
  leaves out the conflicts of the last diff.
- **Startup check** (mirror profiles): when an engine starts it compares both sides first
  (bounded by `OMNISYNC_STARTUP_CHECK_TIMEOUT`, default 120 s) before any
  scheduled pull runs, and holds local changes until then. It fails closed:
  if the comparison errors or times out, automatic syncing stays paused and
  the profile shows why, since "could not compare" must never count as "in
  sync".
- **Retries:** a failed sync is retried up to the profile's max retries with
  exponential backoff; a provider rate limit waits longer
  (`OMNISYNC_RATE_LIMIT_DELAY`, default 30 s, doubling). Authentication errors
  are not retried, and the delete limit counts what earlier attempts of the
  same sync deleted.
- **Stop:** stopping a profile's sync sends rclone SIGTERM (SIGINT for a
  two-way sync, which then shuts down gracefully and continues where it left
  off next time; SIGKILL after a grace period) and records the job as failed
  with "Stopped by user.". The watcher, the schedule and the pause state are
  left as they were, so the profile keeps syncing afterwards; a client
  waiting for that sync gets a normal answer.
- **Live progress:** while a push, pull or two-way sync runs, rclone reports
  its stats every second (`--stats 1s`, logged as JSON at NOTICE level) and
  the profile's status carries a `progress` object: bytes done and total,
  speed, time left, files done and total, and up to five files in flight.
  The web UI and the TUI show it as a progress bar on the Dashboard and the
  profile page. Totals grow while rclone is still listing, so the
  percentage can go down early on. Only the latest stats are kept.
- **Pause / Pause all:** pauses automatic syncing (watcher and interval) of
  one profile or of every enabled profile, with the reason "Paused by user"
  (`POST /profiles/{slug}/sync/pause`, `POST /profiles/pause-all`). The
  pause is stored with the profile, so it holds across restarts; syncs you
  start still run, and a successful sync does not lift it. **Resume all**
  (`POST /profiles/resume-all`) lifts only these pauses: a pause OmniSync set
  itself (differences to review, a one-sided restore, a two-way profile that
  needs a resync) stays, is listed in the answer, and needs that profile's
  own **Resume** after you have looked at it. A profile's own Resume lifts
  both.
- **Bandwidth limit:** a profile's *Bandwidth limit* is passed to rclone as
  `--bwlimit` for its push, pull and two-way syncs: a rate such as `10M` or
  `512k` (`10M:1M` for upload:download) or a timetable such as
  `08:00,512k 19:00,10M 23:00,off` (`Mon-08:00,1M` for a day). It is checked
  against rclone's syntax when saved, and refused while `--bwlimit` is also
  among the profile's rclone flags (set it in one place).
- **Sync window:** a profile can allow automatic syncs only at certain
  times, e.g. 22:00 to 06:00 on weekdays (server time, the container's
  `TZ`; an end before the start runs past midnight and belongs to the day it
  starts). Outside the window the watcher push and the interval run wait and
  run once the window opens; syncs you start run at any time (the answer
  notes that the window is closed).
- **Choose folders:** the profile form's *Choose folders* dialog shows the
  local folder as a tree with checkboxes and writes the choice as rclone
  filter rules, deepest first (`+ /Big/keep/**`, `- /Big/**`; or
  `+ /Docs/**`, `- **` for only some folders). The rules stay editable by
  hand; other rules such as `- *.tmp` are kept. Two-way profiles keep the
  sync marker visible on their own (the engine puts `+ /.omnisync-check`
  before the profile's rules).
- **Trash:** every file a sync replaces or deletes is moved to
  `.omnisync-trash/<time>/<path>` on the side that changes. The profile's
  **Trash** tab (web UI and TUI; `GET /profiles/{slug}/trash?side=local|remote`)
  lists each side's trash with time, path and size. **Restore**
  (`POST .../trash/restore`) moves files back to where they were; a file
  that is in the way is moved into the trash first, and one that is newer
  than the trashed version is only replaced after you confirm
  (`overwrite`). The next sync carries restored files to the other side.
  **Delete** (`POST .../trash/delete`) removes trash entries for good.
  Entries are addressed by their path inside the trash and cannot reach
  outside it (no `..`, no absolute paths, no symlinked folders on the local
  side); both actions wait for no sync of the profile to run (409
  otherwise). Trash folders older than `OMNISYNC_TRASH_DAYS` are still
  pruned automatically.

Every sync is recorded as a job with the files it created, changed or
deleted and the side (local or remote) each change happened on (the first
10,000 are stored, `OMNISYNC_MAX_RECORDED_CHANGES`; all are counted).

**Backups** are separate from syncing: a profile can have backup targets (a
local folder, one of your remotes, or another rclone path) that are copied on
a schedule, as a mirror or as dated archive snapshots with a retention period,
and restored to the local side, the remote side or both. A restore rebuilds
the folder exactly as it was right after the chosen backup (the latest one
included) and keeps everything it replaces or removes in
`.omnisync-trash/pre-restore/`. A backup refuses to run on a missing, empty or
unmarked local folder while the backup holds data, and a target that cannot be
reached (a local target folder is never created) is skipped with a
notification. A target can be encrypted with a passphrase (rclone crypt: file
contents and names; a lost passphrase means lost backups) and verified after
each run (`rclone check`/`cryptcheck` against the folder for a mirror, a
read-back for an archive; a failure is notified). Before a full restore the
dialog shows how many files it would add, replace and remove; a snapshot
browser restores single files and folders, to their place or into another
folder.

## Sync safety

OmniSync checks before every sync, in both modes, and keeps what it replaces:

- **Refuses** to sync when the source folder is empty but the destination is not
  (for example an unmounted drive; for a two-way sync: when either side is
  empty and the other is not, since the sync would delete everything there),
  when the local folder is missing (it is never created automatically; the
  profile pauses instead), or when the marker file `.omnisync-check` exists on
  only one side. The marker is written to both sides after a mirror profile's
  first successful sync, and before a two-way profile's first resync (rclone
  bisync checks it on both sides before every run, `--check-access`); if a
  check fails, the reason shows in the profile's status. To confirm a folder
  after a refusal, copy `.omnisync-check` over from the other side. A
  two-way profile's filter rules never hide the marker (an include-style
  filter such as `+ /Docs/**`, `- **` works), and rclone flags that could
  hide it (`--include`, `--exclude`, `--filter`, `--exclude-if-present`,
  `--max-age`, `--min-age`, `--min-size`, `--max-size` below 1Ki,
  `--max-depth` below 1) are refused for two-way profiles; use filter rules
  instead.
- **Keeps** every file a sync or a per-file action overwrites or deletes in
  `.omnisync-trash/<timestamp>/` inside the destination folder. The trash is
  never synced, and neither are rclone's leftover `<name>.<8 hex>.partial`
  files from an interrupted transfer. When a push, pull or two-way sync
  succeeds after a run that did not (it failed, was stopped or killed),
  those leftovers are deleted from both folders: only files with exactly
  that name pattern, last changed before the run started, outside the
  trash (your own `notes.partial` stays). The log line of the job says how
  many went. After a successful sync, timestamped trash folders older than
  `OMNISYNC_TRASH_DAYS` (default 30; `0` keeps them forever) are deleted on the
  side that was synced to, at most once an hour; anything else in the trash (pre-restore safety
  copies, your own files) is left alone.
- **Stops** a sync once it reaches the delete limit: 50 files by default
  (`OMNISYNC_MAX_DELETE`), or `--max-delete` in a profile's rclone arguments
  (`-1` means no limit). The limit is per sync: retries share it. The confirmation dialogs show the profile's real
  limit. What a push or pull deleted before the stop is in the trash. A
  two-way sync applies the limit to each side, and before it changes
  anything: a dry run counts the deletions, and a sync that would delete
  more files on one side does not start at all and pauses the profile
  (`rclone bisync --max-delete` itself counts a percentage of all files,
  which cannot express an absolute limit, so OmniSync checks the count
  itself).
- **Fails closed at startup** (see **Startup check** under
  [Sync modes and when they run](#sync-modes-and-when-they-run)): a startup
  check that cannot compare pauses automatic syncing.
- Runs one sync, per-file action, backup or restore per profile at a time.

## What a sync leaves out, and the profile's filters

A push or pull leaves out manually flagged files, unresolved conflicts of
the diff and, for a pull, local symbolic links (see
[File names and links](#file-names-and-links)). These exclusions always
come first: rclone applies its rules by kind rather than in the order given
(every `--include` flag, then every `--exclude` flag, then the `--filter`
rules, then filter files), so OmniSync passes a push or pull one filter
file that starts with its own exclusions and then holds the profile's
filter rules and its `--include`, `--exclude` and `--filter` flags as rules
in rclone's order. What the profile's filters select stays the same. A
rule `!` in a profile's filters clears the rules before it; OmniSync keeps
only the profile's rules after the last `!` (all that rclone would keep of
them), so it never clears OmniSync's exclusions or the trash's. A two-way
sync puts the same exclusions first in its filters file.

## File names and links

Most names sync byte for byte, whatever they contain: accents, right-to-left
text, emoji, spaces, leading dashes, `#`, `%`, `*`, `[`, quotes, control
characters, names up to the 255-byte limit. A few cases have rules of their
own. Where a sync cannot carry a name as it is, OmniSync says so: the sync
confirmation (`POST /profiles/{slug}/sync/preview`) and the diff list a
**warning** first, and the job keeps it (`warnings` on the job, up to 20
paths per kind with a count, shown as **Completed with warnings**). The log
gets one line per kind, and the "completed" notification becomes a warning.
OmniSync checks the local folder at the start of each preview, diff, push,
pull and two-way sync, only among the files the profile's filters let a
sync see. In a warning, bytes that are not UTF-8 are shown as `\xNN`, and
control and bidi characters (such as U+202E, which can make `exe` look
like `jpg`) as `\xNN` or `\uNNNN`.

- **Symbolic links** are never followed and never copied (rclone's default
  for a local folder): neither the link nor what it points to reaches the
  other side, and the link and its target are left alone. If the remote
  folder has a file or folder with the **same name** as a local link:
  - a **pull** or **two-way sync** leaves both alone: the path is left out
    of the run, so the remote item is not copied over the link (which would
    replace the link) and nothing is written through a link to a folder
    into the folder it points to. The warning names the link; the remote
    item arrives once the link is removed or renamed. A per-file pull onto
    a link is refused.
  - a **push** treats the link as no file, like any file missing locally:
    the remote item is deleted into the remote trash
    (`.omnisync-trash/<timestamp>/`), and the warning says so.

  Neither a profile's filter rules nor its `--include`, `--exclude` or
  `--filter` flags can undo this protection (see below).
- **Names equal after Unicode normalisation.** An accented letter can be
  stored as one character (composed, NFC: most systems) or as a letter plus
  an accent (decomposed, NFD: files created on macOS). rclone compares names
  after normalising them, so that a file synced from a Mac matches its
  copy elsewhere. Two local names in one folder that differ only in this
  way (for example both spellings of `café.txt`) are one file to rclone: it
  syncs one of them and leaves the other behind, a folder with all its
  content. OmniSync warns about each such name; nothing is deleted, and
  renaming one of the two lets both sync. (OmniSync does not turn rclone's
  normalisation off: that would make every macOS name look different from
  its copy on other storage.)
- **Names that are not valid UTF-8** (bytes from an old system or a broken
  archive) are carried byte for byte by push, pull and two-way sync, and
  reported with a warning: rclone lists such a name with U+FFFD in place of
  the bad bytes, so a per-file action cannot name the file and fails with
  "The file name is not valid UTF-8", and a two-way sync matches the file
  with its record of the last run only one run late (an edit can then arrive
  as a conflict, with both versions kept). Renaming the file is safest.
- **Empty folders** travel only with a two-way sync (rclone bisync
  `--create-empty-src-dirs`). A push, a pull and the per-file actions copy
  files only: an empty folder on the source side is not created on the
  other side.
- **Case-only renames** (`Report.txt` to `report.txt`) are carried by push,
  pull and two-way sync as a rename: one file under the new name on both
  sides, the old name in the other side's trash. The per-file actions only
  copy, never delete: the old name is copied back from the other side, so
  both names end up on both sides. This holds where both folders tell
  upper and lower case apart; on storage that does not (Windows and macOS
  drives by default, several cloud providers), check the result after such
  a rename.

---

[Next: Operations](operations.md) | [Back: User Guide](index.md)
