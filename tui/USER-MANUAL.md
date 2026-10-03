# OmniSync TUI: User Manual

## Quick Start

```bash
osync                                  # connect to http://127.0.0.1:8000
osync --url http://192.168.1.100:8000  # another backend
OMNISYNC_API_KEY=YOUR_TOKEN osync      # the backend requires an API token
osync --ascii                          # ASCII borders and symbols
osync --theme light                    # light theme
```

`osync` is the alias `make install` creates; the binary is also available as `osync-tui`.

### Flags

| Flag | Meaning |
|------|---------|
| `--url URL` | Backend base URL (env `OMNISYNC_URL`, default `http://127.0.0.1:8000`) |
| `--api-key TOKEN` | API token, sent as `Authorization: Bearer TOKEN` on every request (env `OMNISYNC_API_KEY`) |
| `--ascii` | ASCII borders and symbols (env `OMNISYNC_ASCII_MODE`) |
| `--theme dark\|light` | Colour theme (env `OMNISYNC_THEME`) |
| `--no-mouse` | Leave the mouse to the terminal: no clickable tabs (env `OMNISYNC_NO_MOUSE`) |
| `--json` | JSON output for every CLI subcommand |
| `--version`, `--help` | Version and help |

Settings can also live in `tui.toml` in the `osync` folder of the user config directory (`~/.config/osync/tui.toml` on Linux). Keys: `url`, `api_key`, `theme`, `ascii_mode`, `log_file`, `no_mouse`. Flags override environment variables, which override the file. The working directory is never searched.

If the token is missing or wrong, the backend answers 401 and the TUI shows: "the backend refused the request (401 Unauthorized): set --api-key or OMNISYNC_API_KEY to the backend's API token". `GET /health` works without a token, so the TUI connects and then shows this error in each view.

## Sync Modes

Every profile has a mode, shown in the Profiles list (Mode column) and on the profile's Overview.

- **Two-way** (recommended; the default for new profiles): changes on either side are carried to the other. Files edited while offline are kept, new files on either side are kept, and deletions are carried over in both directions (the deleted file is kept in that side's `.omnisync-trash/<timestamp>` folder). A file changed on *both* sides keeps both versions: the newer one keeps the name, the other is saved next to it (e.g. `report.local-conflict1.txt` or `report.remote-conflict1.txt`) on both sides, and a conflict is listed in the Conflicts view. The first two-way sync is a **resync**: both folders become the union of both sides and nothing is deleted (where a file differs, the newer version wins and the older one goes to the trash).
- **Mirror** (one-way push/pull only; what profiles created before two-way sync existed keep): local changes are pushed (the remote is made to match the local folder) and the remote is pulled on the interval (the local folder is made to match the remote). The side that syncs last wins, and files that exist on only one side can be deleted by the next mirror.

Push and pull stay available in both modes as explicit one-way overrides; they always ask first and show a preview.

**Moving to two-way.** Every mirror profile explains mirror mode and its risk (the next push or pull overwrites or deletes changes that exist only on the other side, such as edits made while syncing was paused or offline) on its Overview, and the Dashboard lists the mirror profiles in a *Mirror mode* panel. Switch one profile with `w` on its Overview, or all mirror profiles at once with `w` on the Dashboard; the first sync after the switch is a resync, which deletes nothing. To keep a profile a mirror, press `h` on its Overview to hide its note. The choice is stored with the profile (`mirror_notice_dismissed`), so the web UI hides it too, and `h` shows it again.

**Delete limit in two-way mode.** The profile's delete limit applies to *each* side and is checked before anything changes (a dry run): a two-way sync that would delete more files than the limit on one side changes nothing and pauses the profile, with the reason in *Last error*.

**Resync required.** If a two-way profile's sync state was lost or is inconsistent (for example, a run was interrupted in a way rclone cannot recover from), automatic syncing stops and the profile shows *Resync required* with the reason. Open the profile and press `R` to confirm a resync.

## CLI Subcommands

```bash
osync status              # aggregate sync status
osync health              # backend health; exit status 1 when unreachable or status is not "ok"
osync profiles            # all profiles with their mode (two-way or mirror); a profile in the error state shows why
osync jobs                # recent jobs (--profile SLUG, --limit N, 1-100, default 20)
osync push PROFILE_SLUG   # push and wait until it has finished
osync pull PROFILE_SLUG   # pull and wait until it has finished
osync sync PROFILE_SLUG   # two-way sync ("Sync now") of a two-way profile, and wait
osync resync PROFILE_SLUG --yes   # resync a two-way profile (asks in a terminal without --yes)
osync profile show|enable|disable|stop|check|diff|resume PROFILE_SLUG
osync profile manual-flags PROFILE_SLUG [--clear PATH]
osync conflicts [--profile PROFILE_SLUG]
osync conflicts resolve ID --keep local|remote|both|dismiss
osync remotes                     # list; remotes test NAME, remotes about NAME
osync backups PROFILE_SLUG        # backup targets; then run, snapshots, restore
osync logs [--level LEVEL] [--limit N] [--follow]
osync notifications test [--channel NAME]
osync completion bash|zsh|fish|powershell
```

`push` makes the remote match the local folder: remote files that are not in the local folder are deleted. `pull` makes the local folder match the remote: local files that are not on the remote are deleted. Deleted and replaced files are moved to `.omnisync-trash` on the side that changes, and a sync stops once it reaches the profile's delete limit (50 files unless changed). The CLI commands have no confirmation and send no `force`, so they are refused while a profile's automatic syncs are paused; use the TUI's confirmed push/pull for that.

`push` and `pull` work for profiles in both modes (for a two-way profile they are one-way overrides).

`sync` and `resync` are for two-way profiles; for a mirror profile they send nothing and say to switch it first (`w`). `sync` is the TUI's `n`: a two-way sync without `force`, so it is refused while the profile's automatic syncs are paused, and it points to `resync` when the profile needs a resync. `resync` is the TUI's `R`: both folders become the union of both sides and nothing is deleted. Without `--yes` (`-y`) it prints what a resync does and asks `Resync now? [y/N]` when stdin is a terminal; any answer but `y`/`yes` cancels. Without a terminal (a script, a pipe, cron) it refuses unless `--yes` is given.

`push`, `pull`, `sync` and `resync` have no fixed timeout: they print progress every 2 seconds until the sync has finished, the backend runs the sync in the background and answers once it is under way (or at once, with the reason, when a safety check refuses it). If the connection drops while waiting, they keep following the profile's status. Ctrl+C stops waiting; the sync keeps running on the server. Exit status: 0 when the sync finished without error; 1 when it failed (with the backend's reason) or you stopped waiting; 3 when it was refused or not confirmed; 4 when the profile does not exist or is disabled (see [Exit statuses](#exit-statuses)). With `--json` they print one JSON object on stdout when the sync has finished, also when it failed: `profile`, `action` (`push`, `pull`, `two_way` or `resync`), `job_id`, `state`, `files_processed`, `errors` and `last_error`; progress goes to stderr.


### Profiles

| Command | What it does |
|---------|--------------|
| `profile show SLUG` | Settings and state: mode, folders, last sync, pending changes, paused intervals, delete limit, last error |
| `profile enable SLUG` / `profile disable SLUG` | Start or stop the profile's automatic syncing |
| `profile stop SLUG` | Stop the sync that is running now; automatic syncing continues |
| `profile check SLUG` | Compare both folders now, like the startup check, and list files only on one side or different (`POST /profiles/{slug}/sync/check`); refreshes the pending count, changes no file |
| `profile diff SLUG` | The per-file differences (as the Differences tab) with a summary per category; files flagged as conflict or manual are marked |
| `profile resume SLUG` | Resume paused sync intervals; exit 3 when the backend refuses |
| `profile manual-flags SLUG` | Files flagged for manual handling; `--clear PATH` clears one flag (exit 4 when the file has none) |

`check` and `diff` exit 1 when the comparison failed (the reason is in the output's `error`).

### Conflicts

`osync conflicts` lists the unresolved conflicts (`--profile SLUG` for one profile), oldest first, with the conflict ID. `osync conflicts resolve ID --keep local|remote|both|dismiss` resolves one, like the Conflicts view: `local` copies the local version over the remote one, `remote` the other way round (the replaced version goes to `.omnisync-trash`), `both` keeps both versions, `dismiss` only closes the record. For a conflict a two-way sync found (both versions already kept), `local`/`remote` keep only that version under the original name and `both`/`dismiss` close the record. Exit 3 when the conflict cannot be resolved now (already resolved, profile not running, a file changed since), 4 for an unknown ID.

### Remotes

`osync remotes` lists the configured remotes. `osync remotes test NAME` tests the connection and exits 1 when it fails (with the reason); `osync remotes about NAME` shows the storage usage (total, used, free, trash), or says the remote does not report it.

### Backups

```bash
osync backups docs                        # targets of profile "docs", with their IDs
osync backups run docs 3 --wait           # back up to target 3 and wait for the result
osync backups snapshots docs 3            # snapshots of target 3
osync backups restore docs 3 2026-09-27T08-30-00 --scope local_only --wait
```

The backend starts a backup or restore and answers at once; the job then runs on the server. Without `--wait` the command prints the job ID and exits 0 (`--json`: `{"profile", "target_id", "job_id", "status": "running"}`); check the job later with `osync backups SLUG` (last backup and its status). With `--wait` it polls the job every 2 seconds until it has ended and exits 1 when it failed or a backup was skipped (target unreachable), with the reason and its code (for example `[target_unreachable]`); Ctrl+C stops waiting and the job keeps running. A refusal comes at once: a target that is already backing up, or a profile that is busy (a sync, backup or restore of it runs), exits 3.

`restore --scope` picks what is restored: `local_only`, `remote_only` or `both`. A full snapshot makes that folder identical to the snapshot (newer files are overwritten, files not in the snapshot are removed); a legacy snapshot only copies its files back. Syncing of the profile is paused while the restore runs. Without `--yes` (`-y`) the command explains this and asks `Restore now? [y/N]` on a terminal; without a terminal (a script, cron) it refuses with exit 3.

### Logs and notifications

`osync logs` prints the last 50 entries of the backend's log, oldest first (`--limit N`, 1-200; `--level DEBUG|INFO|WARNING|ERROR|CRITICAL`). `--follow` (`-f`) keeps asking for new entries every 2 seconds until Ctrl+C; with `--json` it prints one JSON object per line.

`osync notifications test` sends a test notification to every enabled channel; `--channel NAME` (`webpush`, `host_native`) tests that one channel even when it is turned off. It exits 1 when nothing was delivered or a channel failed, 4 for an unknown channel.

### Exit statuses

Every subcommand takes `--json` (the backend's answer, or the result object described above, on stdout; messages on stderr) and exits with:

| Status | Meaning |
|--------|---------|
| 0 | Done |
| 1 | Error: backend unreachable or unhealthy, a sync, backup, restore, check, diff, remote test or test notification failed, any other API error, or Ctrl+C while waiting |
| 2 | Wrong arguments or flags; nothing was sent |
| 3 | Refused: the backend answered 409, the profile is busy, a mirror profile for `sync`/`resync`, or a resync or restore was not confirmed |
| 4 | Not found: the backend answered 404 (profile, conflict, remote, backup target, manual flag, notification channel; the sync endpoints also answer 404 for a disabled profile) |

```bash
osync conflicts resolve 12 --keep remote
case $? in 0) echo resolved ;; 3) echo "not possible now" ;; 4) echo "no such conflict" ;; *) echo failed ;; esac
```

### Shell completion

`osync completion bash|zsh|fish|powershell` prints a completion script, for example `source <(osync completion bash)` in `~/.bashrc`, or `osync completion fish > ~/.config/fish/completions/osync.fish`; `osync completion SHELL --help` explains the setup per shell. Besides subcommands and flags it completes flag values (`--keep`, `--scope`, `--level`, `--channel`), profile slugs and remote names; those come from the backend, so `--url`/`--api-key` (or their environment variables or `tui.toml`) apply.

---

## Global Controls

| Key | Action |
|-----|--------|
| `1`–`8` | Switch to view by number |
| `Tab` / `Shift+Tab` | Next / previous view |
| `?` | Help overlay for the current view; any key closes it |
| `Ctrl+T` | Toggle dark/light theme (saved to `tui.toml`) |
| `q` | Quit (asks first; `y` quits) |
| `Ctrl+C` | Quit immediately |
| Click a tab | Switch to that view (second line of the top bar) |
| Mouse wheel | Scroll, like `Up`/`Down` |

The mouse is on by default (it also works in tmux and over SSH when the terminal passes mouse events). To select text with the mouse, hold `Shift` while dragging (most terminals), or start osync with `--no-mouse`. Clicks are ignored while a form or prompt is open.

While a form, text field, confirmation prompt or wizard step is open, these keys go to it instead: you can type `q`, digits, spaces and any other characters (including umlauts), and `Tab` moves between fields. `Esc` closes the form or prompt; `Ctrl+C` always quits.

### Forms

| Key | Action |
|-----|--------|
| `Tab` / `Down` | Next field |
| `Shift+Tab` / `Up` | Previous field |
| `Left` / `Right` | Move in a text field; change the value of a choice field (`Space` also cycles) |
| `Enter` | Submit |
| `Esc` | Cancel |

Pasting into a text field works as well. Fields marked `*` are required.

### Confirmation prompts

| Key | Action |
|-----|--------|
| `y` | Yes: carry out the action |
| `n` / `Esc` | No: nothing is sent |

Every other key is ignored while a prompt is open. A prompt always acts on the row that was selected when it opened, even if a background refresh reorders the table meanwhile.

### Tables

| Key | Action |
|-----|--------|
| `Up` / `Down`, `k` / `j` | Move the selection (wraps to the previous/next page) |
| `n` / `N` | Next / previous page |

The selected row is marked with `>` and drawn in reverse video, so it stays visible with `NO_COLOR` set. A refresh keeps the selection on the same row.

## Top Bar

```
OmniSync  http://127.0.0.1:8000  [IDLE]  14:05:09
1:Dashboard 2:Profiles 3:Jobs 4:Logs 5:Conflicts 6:Remotes 7:Notifications 8:Config
```

The first line shows the backend URL, the aggregate sync state (`[IDLE]` gray, `[PUSHING]`/`[PULLING]`/`[SYNCING]` blue, `[ERROR]` red, `[UNKNOWN]` while disconnected; read with every health check) and a clock. The second line holds the view tabs; the active one is underlined, and clicking a tab switches to it.

## Bottom Bar

```
● 23ms  p:push all  l:pull all  s:stop  Enter:detail  r:refresh  |  ?:help  q:quit  1-8/Tab:views  Ctrl+T:theme
```

- `● 23ms` (green): connected and healthy, with the latency of the last health check
- `● degraded 23ms` (yellow): reachable, but the backend's `/health` status is not `ok`
- `○ reconnecting` (red): the last health check failed; retries back off 2s, 4s, 8s, 16s, 30s

Health is re-checked every 10 seconds while connected. In ASCII mode the symbols are `*` and `o`.

After the connection indicator come the keys of the active view (they change with its mode: a form shows the form keys, a prompt `y:yes  n/Esc:no`), then the global keys. Flash messages (green = success, red = error) replace the keys for 5 seconds.

---

## 1: Dashboard

At-a-glance overview: aggregate state, backend health, network diagnostics, remote reachability and all profiles. The `remotes:` line comes from `GET /health/remotes` (checked when the dashboard opens and on `r`): one mark per remote a running profile uses, `unknown` when the check failed, `none in use` when no running profile uses a remote. The State column is coloured (idle gray, pushing/pulling/syncing blue, error red, resync required yellow) and always says the state in words. Profiles in the error state are listed below the table with the reason (`last_error`). Two-way profiles that need a resync get a warning line in the Sync Status panel (open the profile and press `R`). The state `syncing` means a two-way sync or resync is running. The profile table has a Mode column (`two-way` or `mirror`). Below it, the *Mirror mode* panel names the mirror profiles whose note is not hidden, explains the risk of mirror mode and offers `w`.

| Key | Action |
|-----|--------|
| `p` | Push all enabled profiles (one confirmation listing them) |
| `l` | Pull all enabled profiles (one confirmation listing them) |
| `s` | Stop all running syncs |
| `w` | Switch all mirror profiles to two-way (one confirmation listing them) |
| `z` | Pause automatic syncing of all enabled profiles (`POST /profiles/pause-all`) |
| `u` | Resume all profiles you paused (`POST /profiles/resume-all`) |
| `Up`/`Down`, `k`/`j` | Move in the profile table |
| `n`/`N` | Next/previous page |
| `Enter` | Open the selected profile's detail view |
| `r` | Refresh, including the network check |

"Push/Pull all" first previews every enabled profile (`POST /profiles/{slug}/sync/preview`, "Counting... n of N done", `Esc` cancels), then lists them in one prompt with the files each would delete and replace, its delete limit, and whether it would stop at that limit ("counts unavailable" when a preview failed). Two-way profiles are marked, since for them this is a one-way override. After `y` it starts `POST /profiles/{slug}/sync/start` with `force=true` for each of them.

"Switch all to two-way" lists every mirror profile (with hidden notes too, and disabled ones marked) and explains that the first two-way sync of each is a resync that deletes nothing. After `y` it sends `PUT /profiles/{slug}` with `{"sync_mode": "two_way"}` for each of them, one after the other, and a flash reports how many were switched and which failed; `n`/`Esc` sends nothing.

While a profile syncs, the Sync Status panel shows its progress: a bar, the percentage, sizes, files, speed and time left (rclone's stats, refreshed with the 2s polling). A profile you paused reads *paused by you*. `z` pauses automatic syncing (watcher and interval) of every enabled profile until you resume; syncs you start still run. `u` lifts only those pauses: a profile OmniSync paused itself (differences to review, a restore, a needed resync) stays paused, the flash names it, and you resume it with `i` on its page after a look.

The network check (DNS, internet, rclone network) is slow, so it runs when the dashboard opens and on `r`, not on every refresh. Polling: every 2s while a sync runs, every 30s otherwise.

---

## 2: Profiles

| Key | Action |
|-----|--------|
| `c` | Create a profile (form: name, local dir, remote dir, filters, mode, pull interval, debounce, max retries, rclone args, bandwidth limit, sync window) |
| `e` | Edit the selected profile (including its mode) |
| `d` | Delete the selected profile (asks first; files are not touched) |
| `t` | Enable/disable the selected profile |
| `Up`/`Down`, `k`/`j` | Move |
| `n`/`N` | Next/previous page |
| `Enter` | Open the profile detail view |
| `r` | Refresh |

Local dir must be an absolute path, remote dir `<remote>:<path>`, and filters a comma-separated list of rclone filter rules (e.g. `- *.tmp, - .cache/**`). The Mode field is a choice (`Left`/`Right` or `Space` change it): **Two-way (recommended)**, the default for new profiles, or **Mirror (one-way push/pull only)**; its help line (shown while the field is focused) explains both (see [Sync Modes](#sync-modes)). The edit form sends the mode only when you changed it. Switching a profile to two-way makes its next sync a resync (the union of both folders, nothing deleted); switching to mirror forgets the two-way state.

**Choosing folders.** In the form, `Ctrl+O` on *Local Dir* opens a folder browser for the machine the backend runs on (`GET /browse/local`; limited to the backend's browse roots, by default your home folder and `/sync`). On *Remote Dir* it first lists your remotes (`GET /remotes`), then the folders on the chosen one (`GET /browse/remote`); a field that already holds `remote:path` opens in that folder.

| Key | Action (folder browser) |
|-----|--------|
| `Up`/`Down`, `k`/`j` | Move |
| `Enter` / `Right` | Open the folder or remote (on `[use this folder]`: choose the folder shown) |
| `Left` / `Backspace` | Up one folder (at a remote's top: back to the list of remotes) |
| `s` | Use the folder shown |
| `~` | Home folder (local browser) |
| `Esc` | Back to the form without changing the field |

**Other fields.** *Pull every (min)* (1-10080), *Debounce (s)* (1-3600) and *Max retries* (1-10) are whole numbers; empty means the backend's default (5, 5 and 3). *rclone args* are extra rclone flags separated by spaces (e.g. `--transfers 8`); the edit form sends them only when you changed them. *Bandwidth limit* is rclone's `--bwlimit` for this profile's syncs: a rate (`10M`, `512k`, `off`, `10M:1M`) or a timetable such as `08:00,512k 19:00,10M 23:00,off`; empty means no limit (not together with `--bwlimit` in the rclone args). *Sync window* limits automatic syncs to certain times (server time): `22:00-06:00` every day, or days first, as in `Mon-Fri 22:00-06:00` or `Sat,Sun 00:00-08:00`; an end before the start runs past midnight. Empty means any time. Outside the window, watcher and interval syncs wait and run when it opens; syncs you start run at any time.

If the backend refuses the profile (for example a relative local dir or an unknown remote), the form stays open with the backend's message and everything you typed; fix the field and press `Enter` again.

The Mode column shows `two-way` or `mirror`. The State column is coloured like the dashboard's and shows `error: <reason>` for a profile whose last sync failed and `resync required` for a two-way profile that needs a resync; the full reason is shown below the table when that profile is selected.

---

## 2a: Profile Detail (sub-view)

Open with `Enter` in the Dashboard or Profiles view; `Esc` goes back to Profiles. The header names the profile and its slug. Five tabs: Overview, Differences, Backups, History and Trash; `Left`/`Right` switch tabs and `o` jumps to Overview.

### Overview

Shows state, the progress of a running sync (bar, sizes, files, speed, time left and the files being transferred), the last error (red when the profile is in the error state), the mode, enabled, local and remote folders, the delete limit, the bandwidth limit and sync window (and whether automatic syncs wait for it), last sync, file/error/pending counts and a banner when intervals are paused (*Paused by you* for your own pause). A two-way profile that needs a resync shows a red *Resync required* block with the reason instead of the plain last error. A mirror profile explains mirror mode, its risk and the switch (`w`) until you hide that note with `h`.

| Key | Action |
|-----|--------|
| `n` | Sync now: run a two-way sync (two-way profiles only; asks first while the profile's automatic syncs are paused) |
| `R` | Resync: make both folders the union of both sides, nothing deleted (two-way profiles only; asks first) |
| `w` | Switch a mirror profile to two-way sync (asks first) |
| `h` | Hide the mirror-mode note of a mirror profile, or show it again (stored with the profile) |
| `p` | Push: make the remote match the local folder (asks first; a one-way override on two-way profiles) |
| `l` | Pull: make the local folder match the remote (asks first; a one-way override on two-way profiles) |
| `s` | Stop the running sync |
| `k` | Show differences (switches to the Differences tab and loads them) |
| `i` | Resume paused intervals (also a pause you set with `z`) |
| `z` | Pause automatic syncing of this profile until you resume it (`POST /profiles/{slug}/sync/pause`) |
| `x` | Run a test sync |
| `r` | Refresh |
| `Esc` | Back to Profiles |

Push and pull first preview both sides (`POST /profiles/{slug}/sync/preview`, which changes nothing; `Esc` cancels while it runs), then ask:

```
Push profile "Documents" (docs)?

Direction: local /home/you/Documents → remote gdrive:Documents

Deletes 2 file(s) on the remote that are not in the local folder.
Replaces 1 changed file(s) on the remote.
Uploads 3 new file(s).

Files it deletes or replaces are kept in .omnisync-trash on the remote side.
Delete limit: 50 files. A sync that would delete more stops at the limit
and deletes no more.

[y]es  [n]o (Esc)
```

Nothing is synced until you press `y`. The confirmed sync is sent with `force=true`, so it also runs while the profile's automatic syncs are paused; every safety check still applies. The sync then runs in the background; the state switches to pushing/pulling and a flash reports when it finished or why it failed. On a two-way profile the prompt adds that a push or pull is a one-way override: changes that exist only on the other side are replaced or deleted.

#### Sync now (two-way profiles)

`n` runs a two-way sync right away (`POST /profiles/{slug}/sync/start` with direction `two_way`, no `force`); the state switches to `syncing`. While the profile's automatic syncs are paused, `n` first previews the two-way sync (nothing is changed; `Esc` cancels while it runs) and asks; the confirmed sync is sent with `force=true`:

```
Sync profile "Documents" (docs) both ways now?

Local:  /home/you/Documents
Remote: gdrive:Documents

Local folder: deletes 1, replaces 2, creates 3 file(s)
Remote:       deletes 60, replaces 4, creates 5 file(s)  ⚠ more deletes than the limit of 25
2 file(s) changed on both sides: both versions will be kept and listed
under Conflicts.

Files it deletes or replaces are kept in .omnisync-trash on the side that
changes.
Delete limit: 25 files on each side, checked before anything changes:
a sync that would delete more on one side changes nothing and pauses
the profile.

⚠ This sync would delete more files than the limit of 25 on one side:
it will change nothing and pause the profile.

Automatic syncs of this profile are paused; this sync runs anyway.
Why they are paused: ...

[y]es  [n]o (Esc)
```

When the next run is a resync, the prompt says so (the union of both folders, nothing deleted); when the dry run failed, it shows the error instead of the counts. `n` does nothing on a mirror profile (use `p`/`l`, or `w` to switch) and on a profile that needs a resync (press `R` first); a flash says why.

#### Resync (two-way profiles)

`R` asks first and explains what a resync does: files that are only in one folder are copied to the other and nothing is deleted; where a file differs, the newer version wins and the older one is moved to `.omnisync-trash` on its side. When the profile needs a resync, the prompt also shows the reason. After `y` the TUI sends `POST /profiles/{slug}/sync/resync` with `{"confirm": true}`; `n`/`Esc` sends nothing. When the resync has finished, automatic two-way syncing resumes.

#### Switch to two-way (mirror profiles)

`w` asks first, explains two-way sync and that its first run is a resync, and after `y` sends `PUT /profiles/{slug}` with `{"sync_mode": "two_way"}`. To switch back, edit the profile (`e` in Profiles) and choose Mirror.

`h` sends `PUT /profiles/{slug}` with `{"mirror_notice_dismissed": true}` (or `false` to show the note again). A hidden note stays hidden in the web UI too; the Mode line and the `w` key remain.

### Differences

Loaded when the tab is first opened (or with `k`/`r`). All differing files are listed, 20 per page.

| Key | Action |
|-----|--------|
| `f` | Cycle filter: All, Local Only, Remote Only, Mod Local, Mod Remote, Mod Both |
| `Space` | Select/deselect the highlighted file |
| `a` | Select all / none |
| `p` | Push the selected files (or the highlighted one; asks first) |
| `l` | Pull the selected files (or the highlighted one; asks first) |
| `s` | Skip the selected files |
| `m` | Flag the selected files for manual handling |
| `n`/`N` | Next/previous page |
| `r` | Reload the differences |

Push, pull, skip and manual run in the background on the server: the flash says the run started (with its job ID) and the tab shows it as running; when it has ended, a flash gives the outcome (for failed files the count and the first file's error) and the differences reload. A profile that is busy (a sync, backup or restore of it runs) refuses the run at once.

### Backups

| Key | Action |
|-----|--------|
| `c` | Create a backup target (name, type, remote name, path, mode, frequency, retention, verification, optional passphrase) |
| `d` | Delete the selected target (asks first; existing backups stay) |
| `t` | Enable/disable the selected target |
| `b` | Run a backup of the selected target now |
| `Enter` | Show the target's snapshots |
| `r` | Refresh |

Backups and restores (full or of chosen files) run in the background on the server. When one starts, a flash names its job ID and the tab lists it under *Running in the background* until it ends; a flash then gives the outcome (finished, or failed/skipped with the reason). The TUI checks the job every 2 seconds and stays usable meanwhile. A busy profile or a target already backing up refuses the start at once.

The *Enc* column marks encrypted targets. *Last* reads `UNVERIFIED` when the verification after the last backup failed; the reason is shown under the table for the selected target. In the create form, *Verify* (default `yes`) compares each backup with the folder, and a *Passphrase* (entered twice, at least 8 characters) encrypts the target. A lost passphrase means lost backups, and it cannot be changed while the target holds backups.

In the snapshot list, `Enter` or `r` opens the restore form (restore to `local_only`, `remote_only` or `both`); after it, a preview shows how many files the restore would add, replace and remove on each side, and `y` starts it. `f` opens the snapshot browser and `Esc` returns to the targets.

In the snapshot browser, `Enter` opens a folder, `Backspace` (or `u`) goes up or leaves a search, `/` searches the whole snapshot, `Space` selects a file or folder (a folder means everything in it) and `c` clears the selection. `R` restores the selection to its original place in the local folder (asks first), `O` into another local folder. Only the selected files are written; files they replace are kept in `.omnisync-trash/pre-restore/`. In a mirror profile, automatic syncing is paused so the next pull does not remove the restored files: review the diff, then push or resume. A two-way profile syncs them like any other change.

**Choosing the target folder.** In the create form, `Ctrl+O` on *Path* opens the same folder browser as the profile form; choose the *Type* first. For a `local` target it lists folders on the machine the backend runs on (`GET /browse/local`). For a `remote` ("same remote") target it opens the folders of the profile's own remote (the part of its Remote Dir before `:`); for a `custom_remote` target, those of the remote in *Remote name*. A path already on that remote opens in that folder. While the remote is not known (a `custom_remote` target with an empty *Remote name*), the browser lists your remotes first (`GET /remotes`); the chosen folder then also fills *Remote name*. Remote folders are written as `remote:path`, without a slash after the colon (`nas:Backups`). In the browser, `Enter`/`Right` opens a folder, `Left` goes up, `s` (or `Enter` on *[use this folder]*) chooses the folder and `Esc` returns to the form unchanged.

### History

This profile's sync jobs (`GET /jobs?profile=<slug>`), newest first, 20 per page; loaded when the tab is first opened and refreshed while it is shown. The columns are those of the Jobs view without Profile: ID, direction, start time, status (coloured), files changed, conflicts and errors. The header says which page is shown and whether older jobs exist.

| Key | Action |
|-----|--------|
| `Up`/`Down`, `k`/`j` | Move |
| `Enter` | Show the job's detail and file changes (as in the Jobs view) |
| `n`/`N` | Older/newer page of jobs (in the job detail: next/previous page of file changes) |
| `r` | Refresh |
| `Esc` | In the job detail: back to the list; otherwise back to Profiles |

### Trash

What this profile's syncs replaced or deleted, kept in `.omnisync-trash` (`GET /profiles/{slug}/trash?side=local|remote`), newest sync first: the original place, when the file was moved to the trash and its size, with the total above. Restore moves the files back to where they were; the next sync carries them to the other side. A file that is in the way is moved into the trash first; if it is newer than the trashed version, a prompt asks before replacing it. Delete removes files for good after a prompt. Both are refused while a sync of the profile runs.

| Key | Action |
|-----|--------|
| `v` | Switch between the local and the remote trash |
| `Space` | Select the file |
| `a` | Select all / none |
| `u` | Restore the selected files (or the highlighted one) |
| `d` | Delete the selected files for good (asks first) |
| `n`/`N` | Next/previous page |
| `r` | Reload |

---

## 3: Jobs

Job history, newest first, 20 per page. One profile's jobs are also on that profile's History tab.

| Key | Action |
|-----|--------|
| `Up`/`Down`, `k`/`j` | Move |
| `Enter` | Job detail with its file changes |
| `f` | Filter: cycles all profiles, then each profile, then all again |
| `n` / `N` | Older / newer page |
| `r` | Refresh |
| `Esc` | Back to the list (from the detail) |

The Status column is coloured: running blue, completed green, failed red. The backend does not report how many jobs exist, so the header says `Page 2 (more: n)` when older jobs exist, `Page 3 (last page)` on the oldest one and `Page 1 (only page)` when everything fits on one page.

The Direction column shows `push`, `pull`, `selective`, `two-way` (a two-way sync) or `resync` (a two-way resync). In the job detail, the file changes list the folder that changed in the Side column (`local` or `remote`; `-` for changes recorded before the side was known).

---

## 4: Logs

Backend log lines, 200 per page, oldest at the top and newest at the bottom. The header shows the page, which lines are in view, the level filter and the search.

| Key | Action |
|-----|--------|
| `Up`/`Down`, `k`/`j` | Scroll one line (`Up` goes to older lines) |
| `PgUp`/`PgDn` | Scroll one screen |
| `Home`/`g`, `End`/`G` | Oldest / newest line of the page |
| `n` / `N` | Older / newer page |
| `/` | Search messages (case-insensitive text; the list filters as you type; `Enter` keeps the search, `Esc` cancels) |
| `Esc` | Clear the search |
| `f` | Cycle level filter: ALL, DEBUG, INFO, WARNING, ERROR |
| `F` | Toggle live updates (LIVE indicator; every 2s) |
| `r` | Refresh |

Live mode shows the newest page and keeps the newest line in view. Scrolling up or going to an older page (`n`) leaves live mode, so new lines do not move what you are reading; `F` turns it back on and jumps to the newest lines. Search and level filter apply to the page shown.

---

## 5: Conflicts

Unresolved conflicts (files changed on both sides since the last sync) with profile, file and both modification times. They are recorded when a profile's differences are loaded and when a two-way sync finds a file changed on both sides.

A conflict a two-way sync found is different: both versions were already kept, on both sides. The view says so ("Both versions were kept: local version as `docs/plan.local-conflict1.odt`, remote version as `docs/plan.odt`") and offers:

| Key | Action (two-way conflict) |
|-----|--------|
| `l` | Keep only local: keep only the local version, under the file's name; the other copy goes to `.omnisync-trash` (asks first) |
| `r` | Keep only remote: keep only the remote version, under the file's name; the other copy goes to `.omnisync-trash` (asks first) |
| `b` | Keep both: close the conflict; both files stay (asks first) |
| `d` | Dismiss: close the conflict without changing any file; both files stay (asks first) |

For other conflicts the keys are:

| Key | Action |
|-----|--------|
| `Up`/`Down`, `k`/`j` | Move |
| `n`/`N` | Next/previous page |
| `f` | Filter: cycles all profiles, then each profile, then all again |
| `Enter` | Resolve the selected conflict |
| `l` | (resolving) Keep local: copy the local file over the remote one (the remote version goes to `.omnisync-trash`; asks first) |
| `r` | (resolving) Keep remote: copy the remote file over the local one (the local version goes to `.omnisync-trash`; asks first) |
| `b` | (resolving) Keep both: the remote version is kept as `<name>.conflict-<time>` on both sides (asks first) |
| `d` | (resolving) Dismiss: close the conflict without changing any file (asks first) |
| `Esc` | (resolving) Cancel |
| `r` / `R` | Refresh (when not resolving) |

---

## 6: Remotes

| Key | Action |
|-----|--------|
| `Up`/`Down`, `k`/`j` | Move |
| `c` | Quick add a remote that signs in with keys or a password |
| `e` | Edit the selected remote's settings (keys, host, password, ...) |
| `a` | Reconnect the selected OAuth remote (Google Drive, Dropbox, OneDrive) |
| `I` | Import remotes from an rclone.conf on this computer |
| `t` | Test the selected remote (shows latency) |
| `i` | Storage info of the selected remote |
| `w` | Open the setup wizard |
| `d` | Delete the selected remote (asks first) |
| `r` | Refresh |

**Quick add** (`c`) asks for a name and a type, then the fields of that type (for example host, user and password for SFTP, or the access keys for S3), and creates the remote with the same call as the wizard (`POST /wizard/create`). Only types that need no browser sign-in are offered; for Google Drive, OneDrive or Dropbox use the wizard (`w`). If the backend refuses, the form stays open with its message and your input. Test the new remote with `t`.

Before deleting, the TUI asks the backend which profiles and backup targets use the remote and lists them in the prompt. Deleting removes the rclone configuration, not the files on the remote.

**Edit** (`e`) changes a remote in place, so the profiles that use it keep working (`GET /remotes/{name}/config`, `PUT /remotes/{name}`). The form shows the stored settings; secrets (passwords, keys, tokens) are never shown: leave a secret empty to keep it, type a new one to replace it, or pick it in *Remove secret* to delete it (e.g. the SFTP password, to sign in with a key file instead). Settings the wizard has no field for are listed and stay as they are. Only remotes of a type the wizard offers can be edited; OAuth remotes are reconnected instead. For a crypt remote, keep the passwords and encryption settings: files already stored can only be read with the ones they were written with.

**Reconnect** (`a`) is for a Google Drive, Dropbox or OneDrive remote whose sign-in expired or was revoked. It opens the wizard at that remote's sign-in (see 6a); afterwards only the remote's token is replaced. When a test, the dashboard's remote check or a sync was refused by the provider, the Type column says `(refused)` and the line below the table names the key that fixes it (`a` to reconnect, `e` to update the credentials).

**Import** (`I`) reads an rclone.conf on the computer the TUI runs on (by default rclone's own, `~/.config/rclone/rclone.conf`; at most 512 KB) and sends its text to the backend, which lists the remotes in it before importing any. Each remote that can be imported gets a field with the name to import it under; an empty field skips it. A remote whose name is taken starts empty, with a free name suggested in its help (e.g. `gdrive-imported`). Remotes that cannot be imported are listed with the reason: `local` remotes, wrappers around a local path, and settings that would run a program on the backend's machine (SFTP `ssh`, WebDAV `bearer_token_command`). An encrypted rclone.conf must be decrypted first (`rclone config encryption remove`). Values are copied as they are; when a remote is imported under a new name, a crypt (or alias, union, ...) remote of the same import that points at it follows the rename.

---

## 6a: Wizard (sub-view)

Open with `w` in Remotes.

1. **Select provider** (`Up`/`Down`, `Enter`; `Esc` back to Remotes).
2. **Fill in the form**: the remote name (pre-filled with the provider's default) and the provider's fields.
   - Key-based providers (S3, B2, SFTP, FTP, WebDAV, SMB, crypt): access keys, host, user, password and so on. Choices (WebDAV's vendor, crypt's file name encryption) are dropdowns (`Left`/`Right`). A crypt remote's *Encrypted folder* is another remote and a folder on it, e.g. `gdrive:Encrypted`. `Enter` creates the remote directly.
   - OAuth providers (Google Drive, Dropbox, OneDrive): the client ID of your own OAuth app, for Google Drive also its client secret (both required). OmniSync does not use rclone's shared app. Create the app once with the provider ([Google Drive](https://github.com/pan-fire/OmniSync/blob/main/docs/gem/remotes.md#google-drive), [Dropbox](https://github.com/pan-fire/OmniSync/blob/main/docs/gem/remotes.md#dropbox), [OneDrive](https://github.com/pan-fire/OmniSync/blob/main/docs/gem/remotes.md#onedrive) in `docs/gem/remotes.md`). The form's intro repeats the provider's short guide, the link to those steps and the exact **redirect URI** to register with the app, as the backend reports it (`GET /wizard/oauth/redirect-uri`; typically `http://127.0.0.1:8000/wizard/oauth/callback` when the TUI talks to the backend directly). If the backend cannot be asked, the form shows `<API address>/wizard/oauth/callback` and marks it as assumed. `Enter` starts the sign-in; a refusal (e.g. a missing client ID or secret) is shown in the form, which keeps what you entered.
3. **Sign in (OAuth only)**: the TUI shows the provider's sign-in URL. Open it in any browser and allow access; `o` opens it in the default browser when possible (`xdg-open` on Linux, `open` on macOS, `rundll32 url.dll,FileProtocolHandler` or `cmd /c start` on Windows). The provider then returns to OmniSync by itself through the registered redirect URI, and the TUI checks the session every 2 seconds. `Esc` cancels. The remote is then created from the wizard session, with your app's client ID (and secret) in its settings so rclone can refresh the token; the token never passes through the TUI.
4. **Done**: the new remote is tested automatically. `Enter` or `Esc` returns to Remotes.

**Reconnect mode** (`a` in Remotes) skips the provider and name: the form shows only the client ID and secret, both optional here. Empty keeps the app stored in the remote. A remote that was created with rclone's shared app (for example one imported from rclone.conf or made before OmniSync required an own app) stores none, so reconnecting it needs your own app's client ID (and, for Google Drive, secret); the backend says so in the form otherwise. Then the same sign-in as above follows. When it completes, the backend replaces only the remote's token (`POST /wizard/reconnect`). `Esc` returns to Remotes.

Existing remotes that were created with rclone's shared apps keep working as long as their token is valid: rclone refreshes it by itself. Only a reconnect needs your own app.

---

## 7: Notifications

Two tabs, Channels and History (`Left`/`Right`).

| Key | Action |
|-----|--------|
| `Left`/`Right` | Switch tab |
| `Up`/`Down`, `k`/`j` | Move |
| `n`/`N` | Next/previous page |
| `e` / `Enter` | Enable/disable the selected channel |
| `s` | Cycle the selected channel's minimum severity |
| `c` | Configure the selected webhook, ntfy or email channel (URL and headers; server, topic and token; SMTP server, encryption, login, sender and recipients). Secrets are never shown: leave a secret field empty to keep the stored one |
| `t` | Send a test through the selected channel only, even when it is off |
| `T` | Send a test notification through every enabled channel |
| `r` | Refresh |

Webhook, ntfy and email work on a headless server; the line under the
table shows the selected channel's settings and what it still lacks.

---

## 8: Config

| Key | Action |
|-----|--------|
| `e` | Edit the global configuration (log level: DEBUG, INFO, WARNING, ERROR; days of job history kept, 0 for all) |
| `t` | Test sync between a local and a remote directory |
| `r` | Refresh |

---

## Connection Error Screen

If the backend is unreachable at startup:

```
  Connection Error

  Cannot reach backend at: http://127.0.0.1:8000
  cannot reach backend at http://127.0.0.1:8000: <reason>

  Retrying automatically every 4s.  r: retry now  e: edit URL  q: quit
```

`e` edits the URL (type it, `Enter` to connect, `Esc` to cancel); `r` retries now. The TUI also retries on its own with backoff.

If the connection drops later, the flash shows "Reconnecting...", the top bar shows `○ reconnecting`, and "Reconnected" appears when it is back.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Connection Error" at startup | Backend not running or wrong URL | Start the backend, or press `e` and fix the URL |
| "401 Unauthorized: set --api-key or OMNISYNC_API_KEY" | Missing or wrong API token | Set `OMNISYNC_API_KEY` or `api_key` in `tui.toml` |
| "API error 404" on a profile action | The profile is disabled (its sync engine is stopped) | Enable it with `t` in Profiles |
| "API error 409: Sync intervals paused ..." (CLI push/pull) | Unresolved differences paused the profile | Use the TUI's confirmed push/pull, or review the Differences tab and `i` to resume |
| "Resync required" on a two-way profile | Its two-way sync state was lost or is inconsistent; automatic syncing stopped | Open the profile, read the reason, press `R` and confirm |
| A two-way sync "changes nothing and pauses the profile" | It would delete more files on one side than the delete limit | Check that side (a missing or empty folder?); if the deletes are intended, raise the limit or use a confirmed push/pull |
| "API error 409" on Sync now or Resync | The profile is a mirror, or (Sync now) it needs a resync | Check the profile's mode on its Overview; press `R` when it shows *Resync required* |
| "API error 409" when resolving a conflict | The file changed since the differences were loaded, or the profile is disabled | Reload the differences, or enable the profile |
| Push/pull ends with "would delete more than the allowed number of files" | The profile's delete limit | Check the other side; raise `--max-delete` for the profile if intended |
| Health shows ✗ for dns/internet | Container network isolation | Expected in Docker without host networking |
| "Unexpected response from ..." | The backend (or a proxy in front of it) sent something that is not the expected JSON | Set `OMNISYNC_LOG_FILE=/tmp/osync.log`; the raw response (first 2 KB) is logged there |
| "osync: fatal error ... please report it" | A bug in osync; the terminal was restored | Report it at https://github.com/pan-fire/OmniSync/issues with the stack trace (printed above the message, and in the log file when `OMNISYNC_LOG_FILE` is set) |
| Cannot select text with the mouse | The TUI uses the mouse for its tabs | Hold `Shift` while selecting, or start with `--no-mouse` |

### Debug log

`OMNISYNC_LOG_FILE=/path/to/osync.log osync` (or `log_file` in `tui.toml`) writes a debug log: every API request with its status and duration, error responses, the raw body of malformed responses and, after a crash, the stack trace. The file is appended to and created with mode 0600; the API token is never logged. Without it nothing is logged, and the TUI never writes log output to the terminal while it runs.
