# Differences and Conflicts

How OmniSync handles files that differ depends on the profile's sync mode.

- A **two-way** profile carries changes from either side to the other by
  itself. A file changed on both sides keeps both versions and becomes an
  entry on the **Conflicts** page (see
  [Conflicts from a two-way sync](#conflicts-from-a-two-way-sync)).
- In a **mirror** profile, a push makes the cloud folder a copy of the
  local one and a pull does the reverse. That is safe as long as only one
  side changed. When both sides have changes, OmniSync stops syncing that
  profile automatically and lets you decide.

The **Differences** tab below works the same in both modes.

## When OmniSync pauses a profile

A profile's automatic syncing **pauses** when:

- the local folder is missing or not mounted;
- a backup is restored to only one side (from before the restore starts;
  this pause stays after a server restart or a profile edit, until you
  resume or sync);
- (two-way) a sync would delete more files on one side than the delete
  limit; the sync does not start at all, and the reason is shown as the
  profile's last error;
- (two-way) the profile needs a resync that you have to confirm: its
  record of the last sync was lost or is inconsistent, for example after an
  interrupted run. The profile shows **Resync needed** with the reason;
  after you confirm **Resync**, automatic syncing resumes;
- (mirror) a check or diff finds differences, including the check that
  runs every time the profile's engine starts (server start, profile
  enabled or edited);
- (mirror) that startup check cannot compare the two sides (network,
  login, rate limit or timeout). "Could not compare" never counts as "in
  sync".

A two-way profile does not pause because a check finds differences, since
its next sync carries them both ways.

The dashboard shows a paused banner and the profile's **Differences** tab
gets an amber dot. While paused, a **Push**, **Pull** or **Sync now** you
confirm in the web UI or the TUI still runs (every safety check still
applies); the per-file actions below, backups and restores work as well.
An automatic sync that was already waiting when the profile paused does
not run.

## The Differences tab

On a profile's page, **Differences** lists every file that is not the same
on both sides:

| Category | Meaning |
| --- | --- |
| local only | exists only in the local folder |
| remote only | exists only in the cloud folder |
| modified local | differs; only the local copy changed since the last sync |
| modified remote | differs; only the cloud copy changed since the last sync |
| conflict | differs and changed on both sides since the last sync (or OmniSync cannot tell which side changed) |
| manual | you marked it to handle yourself |

You can filter by category, search, sort, and group the list by folder.

### Per-file actions

Use a row's **Action** menu, or tick several files and use the toolbar:

- **Push**: copy the local version to the cloud.
- **Pull**: copy the cloud version to the local folder.
- **Skip**: leave the file for now. It disappears from the list and no
  longer keeps the profile paused, but the next full sync handles it like
  any other file. To keep a file out of syncs, mark it manual instead.
- **Mark manual**: exclude the file from every sync until you **Unmark
  manual** it (the list of manual files is at the bottom of the tab). For a
  two-way profile, marking or unmarking a file makes the next sync a
  resync, which OmniSync runs by itself after a normal sync.

Before copying, OmniSync checks each file again, since the list may be
hours old; a file that has changed since is reported and not copied. A file
that gets replaced is kept in `.omnisync-trash/<timestamp>/` on the side it
was replaced on. Per-file actions only copy, they never delete: pushing a
"remote only" file reports that there is nothing to copy. Removing a file
that exists on one side only takes a full push or pull.

### Conflicts

Pushing or pulling a file marked as a conflict opens **Resolve conflict**:

- **Keep local version (push)** or **Keep remote version (pull)**: the other
  version goes to the trash folder of its side.
- **Keep both versions**: the cloud version is saved next to the file as
  `name.conflict-YYYYMMDDTHHMMSS.ext`, your local version replaces the cloud
  one, and both files end up on both sides. Nothing is deleted.
- **Mark manual**: decide later.

When several conflicts are selected you can apply the same choice to all of
them, or **Leave the conflicts for now**. Full pushes and pulls always
leave the conflicts of the last diff and manual files out; a two-way sync
leaves manual files out.

## Conflicts from a two-way sync

When a two-way sync finds a file that changed on both sides since the last
sync, it does not choose for you. Both versions are kept on both sides: the
newer one keeps the file's name, the other is saved next to it with
`local-conflict1` or `remote-conflict1` before the extension, e.g.
`report.local-conflict1.pdf` next to `report.pdf`. The conflict is listed on
the **Conflicts** page as "Both versions were kept: local version as X,
remote version as Y", with these choices (each asks first):

- **Keep only local**: the local version is kept under the original name on
  both sides; the other version is moved to `.omnisync-trash` on each side.
- **Keep only remote**: the same with the remote version.
- **Keep both**: closes the entry; both files stay on both sides as they
  are. You can also rename or delete one of them yourself; the next two-way
  sync carries that over.

## Resuming

When the list is empty (or only manual files are left), click **Resume
Intervals** on the profile or on the dashboard's paused banner. A full push
or pull that leaves no conflict behind resumes the profile too. A two-way
profile that needs a resync resumes only after the resync; one paused by
the delete limit resumes with **Resume Intervals** once you have checked
the folder (and raised the limit, if the deletions were intended).

A mirror profile does not push local changes made while it is paused. If
there were any, **Resume Intervals** first compares both sides and is
refused while differences remain, so the next pull cannot overwrite your
edits: push, pull or handle them per file, then resume.

## The Conflicts page

The **Conflicts** page in the sidebar lists every unresolved conflict across
all profiles; an entry is recorded when a profile's differences are loaded
and when a two-way sync finds a file changed on both sides. For entries
from the differences it offers the same choices as the Differences tab
(**Keep local**, **Keep remote**, **Keep both**) plus **Dismiss**, which
closes the entry without changing a file; for entries from a two-way sync
it offers the choices described above. Every choice asks for confirmation
first. If the file changed again since the differences were loaded, the
action is refused and nothing is overwritten.

---

[Next: FAQ & Troubleshooting](faq.md) | [Back: Dashboard Guide](dashboard-guide.md)
