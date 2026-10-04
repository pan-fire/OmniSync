# osync-tui

A terminal user interface (TUI) for managing OmniSync cloud-sync operations. Compiled to a single static binary with zero runtime dependencies. `make install` also installs it as `osync`.

## Features

- Full-screen interactive TUI with dashboard, profile management (with per-profile differences, backups and sync history), sync control, job history, log viewer, conflict resolution, remote setup wizard, notification configuration, and global config editing
- Two sync modes per profile: **two-way** (recommended, the default for new profiles; changes on either side are carried to the other and a file changed on both sides keeps both versions) and **mirror** (one-way push/pull only; the side that syncs last wins)
- Two-way profiles: "Sync now" (`n`), a confirmed "Resync" (`R`; the union of both folders, nothing deleted) and a prominent warning when a profile needs a resync; mirror profiles can be switched to two-way (`w`)
- Pushes, pulls and paused two-way syncs ask first and show how many files they would delete on each side
- Non-interactive CLI subcommands for scripting, all with `--json` and documented exit statuses: `status`, `health`, `profiles`, `jobs`, `push`, `pull`, `sync`, `resync`, `profile`, `conflicts`, `remotes`, `backups`, `logs`, `notifications`, plus shell completion
- Adaptive polling (2s while a sync runs, 30s when idle), continuous health checks, and exponential backoff while the backend is unreachable
- Dark and light themes, ASCII mode, `NO_COLOR` support (the selected row is marked with `>` and reverse video, not only colour)
- Cross-platform: Linux, macOS, Windows (amd64/arm64)

## Installation

Download the binary for your platform from the releases page, or build from source:

```bash
cd tui/
make build          # ./osync-tui
make install        # ~/.local/bin/osync-tui and the alias ~/.local/bin/osync
```

## Configuration

Settings are read from (highest to lowest precedence):

1. Flags: `--url`, `--api-key`, `--ascii`, `--theme`, `--no-mouse`
2. Environment variables: `OMNISYNC_URL`, `OMNISYNC_API_KEY`, `OMNISYNC_ASCII_MODE`, `OMNISYNC_THEME`, `OMNISYNC_LOG_FILE`, `OMNISYNC_NO_MOUSE`
3. The config file `tui.toml` in the `osync` folder of the user config directory:
   `~/.config/osync/tui.toml` on Linux (or `$XDG_CONFIG_HOME/osync/tui.toml`),
   `~/Library/Application Support/osync/tui.toml` on macOS, `%AppData%\osync\tui.toml` on Windows.

The working directory is never searched for a config file.

### API key

The backend requires `Authorization: Bearer <token>` on every route except `GET /health`. Pass the token with `--api-key`, `OMNISYNC_API_KEY` or `api_key` in `tui.toml`; the TUI sends it on every request. If it is missing or wrong, the backend answers 401 and the TUI and CLI say so and name `--api-key` and `OMNISYNC_API_KEY`. Prefer the environment variable or the config file: flags are visible to other users in the process list.

### Example `tui.toml`

```toml
url = "http://192.168.1.100:8000"
api_key = "your-token"
theme = "dark"        # or "light"; Ctrl+T switches and saves it here
ascii_mode = false
log_file = ""         # optional path for the TUI's debug log (API calls, errors, crash stack traces)
no_mouse = false      # true: no clickable tabs, the terminal keeps the mouse
```

Without `log_file` the TUI logs nothing (it never writes to the terminal while it runs). If osync crashes, it restores the terminal, prints the panic and a "fatal error" message with the bug-report link, and writes the stack trace to the log file when one is set.

## Usage

### Interactive TUI

```bash
osync                                   # launch the TUI (default backend: http://127.0.0.1:8000)
osync --url http://myserver:8000        # connect to another backend
osync --ascii --theme light             # ASCII borders and symbols, light theme
```

### CLI subcommands

```bash
osync status                  # aggregate sync status
osync status --json           # the same as JSON
osync health                  # backend health; exit status 1 if unreachable or not "ok"
osync health --json           # JSON output, same exit status
osync profiles                # list profiles with their mode (two-way or mirror; error state shows the reason)
osync jobs --limit 10         # recent sync jobs (--profile SLUG to filter)
osync push SLUG               # push a profile and wait until it has finished
osync pull SLUG               # pull a profile and wait until it has finished
osync sync SLUG               # two-way sync of a two-way profile ("Sync now"), and wait
osync resync SLUG --yes       # resync a two-way profile (asks in a terminal without --yes)

osync profile show SLUG       # settings and sync state of one profile
osync profile enable SLUG     # enable (disable) a profile
osync profile stop SLUG       # stop the running sync (automatic syncing continues)
osync profile check SLUG      # compare both folders now (the startup check)
osync profile diff SLUG       # per-file differences with a summary
osync profile resume SLUG     # resume paused sync intervals
osync profile manual-flags SLUG [--clear PATH]   # list (or clear) manual-handling flags

osync conflicts [--profile SLUG]                       # unresolved conflicts
osync conflicts resolve ID --keep local|remote|both|dismiss

osync remotes                 # configured rclone remotes
osync remotes test NAME       # connection test (exit 1 when it fails)
osync remotes about NAME      # storage usage

osync backups SLUG                                    # backup targets of a profile
osync backups run SLUG TARGET_ID [--wait]             # back up now
osync backups snapshots SLUG TARGET_ID                # snapshots of a target
osync backups restore SLUG TARGET_ID SNAPSHOT --scope local_only|remote_only|both [--yes] [--wait]

osync logs [--level ERROR] [--category audit] [--limit 50] [--follow]    # backend log, oldest first; -f keeps polling
osync notifications test [--channel NAME]             # send a test notification

osync completion bash|zsh|fish|powershell             # shell completion script
```

`push` and `pull` wait as long as the sync runs (there is no fixed timeout), print progress, and exit with status 1 if the sync failed, printing the backend's reason. Ctrl+C stops waiting; the sync itself keeps running on the server. A push makes the remote match the local folder (remote-only files are deleted); a pull makes the local folder match the remote (local-only files are deleted). Deleted and replaced files are moved to `.omnisync-trash`, and a sync stops once it reaches the profile's delete limit (50 files unless changed). The CLI commands have no confirmation and send no `force`, so they are refused while a profile's automatic syncs are paused. They work in both sync modes (for a two-way profile they are one-way overrides).

`sync` and `resync` are for two-way profiles and wait the same way. `sync` runs a two-way sync ("Sync now"; `n` in the profile view) without `force`, so it is refused while the profile is paused and when it needs a resync. `resync` makes both folders the union of both sides and deletes nothing (`R` in the profile view). Without `--yes` (`-y`) it explains the resync and asks `[y/N]` when stdin is a terminal; otherwise it refuses, so a script must pass `--yes`. Both refuse a mirror profile before sending anything: switch it to two-way first (`w` in the profile view, or on the Dashboard for all mirror profiles).

With `--json`, `push`, `pull`, `sync` and `resync` print one JSON object on stdout when the sync has finished (`profile`, `action` (`push`, `pull`, `two_way` or `resync`), `job_id`, `state`, `files_processed`, `errors`, `last_error`), also for a failed sync; progress goes to stderr.

`backups run` and `backups restore` start the job on the server, which answers at once. Without `--wait` they print the job ID and exit 0 while the job runs (`--json` prints `{"profile", "target_id", "job_id", "status": "running"}`); with `--wait` they poll the job (`GET /profiles/{slug}/backups/{target_id}/jobs/{job_id}`) every 2 seconds until it has ended and exit 1 when it failed (or a backup was skipped because the target was unreachable). A busy profile or a target already backing up is refused at once (exit 3). `backups restore` asks `[y/N]` on a terminal and refuses without `--yes` otherwise, like `resync`. `logs --follow` polls every 2 seconds until Ctrl+C; with `--json` it prints one JSON object per line.

Every subcommand takes `--json` and prints the backend's answer (or the result object above) as JSON on stdout; messages go to stderr. Exit statuses:

| Status | Meaning |
|--------|---------|
| 0 | Done |
| 1 | Error: backend unreachable or unhealthy, a sync, backup, restore, check, diff, remote test or test notification failed, any other API error, or Ctrl+C while waiting |
| 2 | Wrong arguments or flags; nothing was sent |
| 3 | Refused: the backend answered 409 (already running, paused, already resolved, ...), the profile is busy, a mirror profile for `sync`/`resync`, or a resync/restore was not confirmed |
| 4 | Not found: the backend answered 404 (no such profile, conflict, remote, backup target, manual flag or notification channel; the sync endpoints also answer 404 for a disabled profile) |

`osync completion bash|zsh|fish|powershell` prints a completion script (for example `source <(osync completion bash)`); it completes subcommands, flags, flag values, profile slugs and remote names (asking the backend).

## Keyboard shortcuts

Global keys (not while a form, text field or prompt is open; there every key goes to the form, Esc cancels and Ctrl+C still quits):

| Key | Action |
|-----|--------|
| `1`–`8` | Switch view (Dashboard, Profiles, Jobs, Logs, Conflicts, Remotes, Notifications, Config) |
| `Tab` / `Shift+Tab` | Next / previous view |
| `?` | Help for the current view (any key closes it) |
| `Ctrl+T` | Toggle dark/light theme (saved to `tui.toml`) |
| `q` | Quit (asks first) |
| `Ctrl+C` | Quit immediately |
| Mouse click on a tab | Switch to that view (mouse wheel scrolls; `--no-mouse` turns the mouse off) |

The bottom bar shows the active view's keys; every view lists its own keys in the `?` overlay; [USER-MANUAL.md](USER-MANUAL.md) documents them all.

### Sync modes in the TUI

The profile form (`c`/`e` in Profiles) has a Mode field: **Two-way (recommended)** or **Mirror (one-way push/pull only)**. On a two-way profile's Overview, `n` syncs now (asking first, with a per-side preview, while the profile's automatic syncs are paused) and `R` runs a resync after a confirmation: both folders become the union of both sides and nothing is deleted. A profile whose two-way state was lost shows *Resync required* with the reason until you confirm a resync. On a mirror profile, `w` switches it to two-way after a confirmation; its first two-way sync is a resync. Push (`p`) and pull (`l`) stay available in both modes as confirmed one-way overrides. See [Sync Modes](USER-MANUAL.md#sync-modes) in the manual.

## Development

```bash
make build          # build for current platform
make build-all      # cross-compile all platforms
make test           # go test -race ./...
make lint           # golangci-lint
make release        # build all + checksums
```

The API types in `internal/api` mirror `backend/api/schemas.py`. The contract tests in `test/contract` decode fixtures that `test/fixtures/gen_fixtures.py` generates from the backend's Pydantic models; after changing the backend schemas, regenerate them from the repository root:

```bash
PYTHONPATH=<env with pydantic>:. python3 tui/test/fixtures/gen_fixtures.py
```

## License

Part of the OmniSync project.
