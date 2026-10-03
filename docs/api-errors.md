# API error answers

Every error answer of the backend (4xx and 5xx), and of the web UI server's
own `/api` and `/auth/*` checks, has the same JSON body:

```json
{
  "detail": "Profile 'docs' not found",
  "code": "profile_not_found",
  "details": { "...": "..." }
}
```

| Field     | Type   | Meaning |
|-----------|--------|---------|
| `detail`  | string | What went wrong, written for people. Always a string: a client that only shows `detail` keeps working. The text may change between releases. |
| `code`    | string | Stable, machine-readable, `snake_case`. Branch on this, not on `detail` or the status alone. |
| `details` | object | Optional; present only for the codes that carry extra data (see below). |

The schema is `ErrorResponse` in the OpenAPI document (every operation lists
it for `4XX` and `5XX`). The backend builds these answers in
`backend/api/errors.py`: routes raise `api_error(status, code, message,
**details)`, and exception handlers put FastAPI's own errors (unknown route,
wrong method, request validation) and unexpected exceptions into the same
shape. `backend/tests/test_api_errors.py` fails when the backend raises a
code that is not listed here.

## Changes from builds before the first release

Before 0.10.0, the first published release, `detail` was sometimes an
object or a list. Clients written against such a build that read those
need to change:

| Before | Now |
|--------|-----|
| request validation (422): `detail` was FastAPI's list of `{loc, msg, type, input}` | `code: "validation_failed"`, `detail` is `"field: message; ..."`, the list is `details.errors` (`loc`, `msg`, `type`; the submitted `input` is no longer echoed) |
| `{"detail": {"code": "invalid_params", "errors": [...]}}` | `code: "invalid_params"`, `details.errors`; `detail` is the errors joined with `; ` |
| `{"detail": {"code": "invalid_import", "errors": [...]}}` | `code: "invalid_import"`, `details.errors` |
| `{"detail": {"code": "name_clash", "names": [...]}}` | `code: "name_clash"`, `details.names` |
| `{"detail": {"message": "Invalid file paths", "invalid_paths": [...]}}` | `code: "invalid_paths"`, `details.invalid_paths` |
| 410: `{"detail": {"code": "GONE", "message", "replacement"}}` | `code: "route_removed"`, `details.replacement` |
| web UI server: `{"detail": {"code": "login_required", "message"}}` (and `login_throttled`, `invalid_password`, `login_misconfigured`) | `code` at the top level, `retry_after` in `details` |

## `details` keys

| Key | With codes | Content |
|-----|------------|---------|
| `errors` | `validation_failed` | list of `{loc, msg, type}` (FastAPI/pydantic issues) |
| `errors` | `invalid_params`, `invalid_import` | list of strings, one per problem |
| `names` | `name_clash` | remote names that exist already |
| `invalid_paths` | `invalid_paths` | paths that are not in the cached diff |
| `replacement` | `route_removed` | the route to use instead, e.g. `POST /profiles/{slug}/sync/start` |
| `retry_after` | `auth_throttled`, `login_throttled`, `invalid_password` | seconds until the next attempt can succeed (also in the `Retry-After` header for 429) |

## Codes of the backend

### Generic (by status)

Errors raised without a specific code (FastAPI's own 404 for an unknown
route, 405, a plain `HTTPException`) get the code of their status.

| Code | Status |
|------|--------|
| `bad_request` | 400 |
| `unauthorized` | 401 |
| `forbidden` | 403 |
| `not_found` | 404 (unknown route) |
| `method_not_allowed` | 405 |
| `conflict` | 409 |
| `gone` | 410 |
| `body_too_large` | 413, the request body is over the limit (1 MiB) |
| `unsupported_media_type` | 415 |
| `invalid_request` | 422; also `POST /config/test-sync` without both folders |
| `too_many_requests` | 429 |
| `internal_error` | 500: an unexpected failure; the OmniSync log has the details |
| `upstream_error` | 502 |
| `service_unavailable` | 503: a backend service is not running (starting, shutting down, rclone missing) |
| `validation_failed` | 422: the request body, query or path did not validate (`details.errors`) |

### Access

| Code | Status | Meaning |
|------|--------|---------|
| `token_missing` | 401 | No `Authorization: Bearer <token>` header |
| `token_invalid` | 401 | Wrong API token |
| `auth_throttled` | 429 | Too many wrong tokens from this address (`details.retry_after`) |
| `host_not_allowed` | 400 | The `Host` header is not in `OMNISYNC_ALLOWED_HOSTS` |

### Profiles and syncing

| Code | Status | Meaning |
|------|--------|---------|
| `profile_not_found` | 404 | No profile with this slug |
| `profile_not_running` | 404, 409 | The profile has no running engine (disabled) |
| `profile_conflict` | 409 | Name or folders clash with another profile |
| `invalid_rclone_args` | 422 | rclone flags not allowed for this profile (two-way marker, bandwidth limit) |
| `path_not_allowed` | 403, 422 | Folder outside `OMNISYNC_BROWSE_ROOTS`, or OmniSync's own data folder |
| `confirmation_required` | 400 | Pass `confirm=true` (delete, resync, backup target delete) |
| `sync_busy` | 409 | Another operation of this profile is running |
| `sync_paused` | 409 | Automatic syncing is paused; pass `force=true` |
| `resync_required` | 409 | A two-way profile needs a resync first |
| `not_two_way` | 409 | Two-way only, and the profile syncs in mirror mode |
| `no_cached_diff` | 409 | Run `POST /profiles/{slug}/diff` first |
| `invalid_paths` | 400 | Paths not in the cached diff (`details.invalid_paths`) |
| `intervals_not_resumable` | 409 | Intervals cannot be resumed yet (differences remain) |
| `manual_flag_not_found` | 404 | No manual flag for this path |
| `rclone_failed` | 500, 502 | rclone failed; the OmniSync log has the details |
| `route_removed` | 410 | A retired `/sync/*` route (`details.replacement`) |
| `job_not_found` | 404 | No job with this id |
| `conflict_not_found` | 404 | No conflict with this id |
| `conflict_already_resolved` | 409 | The conflict is resolved already |
| `conflict_resolution_refused` | 409 | The resolution cannot be applied (files changed meanwhile) |

### Folder picker

| Code | Status | Meaning |
|------|--------|---------|
| `directory_not_found` | 404 | No such local folder |
| `permission_denied` | 403 | The folder cannot be read |
| `invalid_remote_path` | 422 | Not `remote:` or `remote:path` |

### Backups

| Code | Status | Meaning |
|------|--------|---------|
| `backup_target_not_found` | 404 | No such backup target of this profile |
| `invalid_backup_target` | 422 | Target type, path and remote do not fit together |
| `path_overlap` | 400, 422 | The folder is, contains or is inside a synced folder or another backup target |
| `remote_not_configured` | 422 | The target's remote is not in rclone.conf |
| `rclone_unavailable` | 503 | rclone could not be asked (remotes, passphrase, location check) |
| `backup_location_not_empty` | 409 | A passphrase change needs an empty location |
| `backup_running` | 409 | A backup of this target is running |
| `invalid_snapshot_id` | 422 | Malformed snapshot id |
| `snapshot_not_found` | 404 | No such snapshot |
| `snapshot_unavailable` | 409 | The snapshot cannot be read as needed (e.g. a missing local folder) |
| `backup_job_not_found` | 404 | No such backup or restore job of this target |

A backup or restore start (`POST .../run`, `.../restore`, `.../restore-files`)
answers 202 with the running job; follow it with
`GET /profiles/{slug}/backups/{target_id}/jobs/{job_id}`. While another sync,
backup or restore of the profile runs or waits, the start is refused with
`sync_busy` (409). Per-file actions (`POST /profiles/{slug}/sync/selective`)
answer the same way and are followed with `GET .../sync/selective/{job_id}`.

#### Error codes of a backup or restore job

A job that failed or was skipped carries `error_code` and `error_message`
(not an error answer: the job itself). The message is fixed for the code,
or OmniSync's own explanation where the table says so; rclone's output is
only in the OmniSync log.

| `error_code` | Meaning | `error_message` |
|--------------|---------|-----------------|
| `path_overlap` | The target's folder overlaps a synced folder or another target | OmniSync's explanation |
| `target_unreachable` | The target could not be reached (skipped) | OmniSync's explanation |
| `backup_refused` | Refused to protect the backup (folder missing or empty, sync marker missing, clock behind) | OmniSync's explanation |
| `restore_refused` | The restore cannot run as asked (missing local folder, damaged snapshot) | OmniSync's explanation |
| `sync_busy` | Waited too long for the running sync of the profile | OmniSync's explanation |
| `interrupted` | OmniSync stopped while it ran (recorded at the next start) | OmniSync's explanation |
| `auth_failed` | Signing in to the remote failed | fixed |
| `backup_failed` | The backup failed (rclone or the disk) | fixed |
| `restore_failed` | The restore failed (rclone or the disk) | fixed |
| `snapshot_not_found` | The snapshot is not at the target | fixed |
| `crashed` | The run failed unexpectedly | fixed |
| `stopped` | Stopped before it finished | fixed |
| `shutdown` | Stopped because OmniSync shut down | fixed |

### Remotes and the setup wizard

| Code | Status | Meaning |
|------|--------|---------|
| `remote_not_found` | 404 | No remote with this name |
| `remote_exists` | 409 | A remote with this name exists already |
| `remote_changed` | 409 | The remote changed meanwhile; reload |
| `remote_in_use` | 409 | Profiles or backup targets use the remote; pass `force=true` |
| `remote_not_editable` | 409 | This remote type is edited with `rclone config` |
| `remote_unreachable` | 503 | The remote did not answer |
| `reconnect_required` | 409 | OAuth remotes are changed by reconnecting |
| `invalid_remote_name` | 422 | Letters, digits, `_` and `-`, not starting with `-`, not reserved |
| `invalid_params` | 422 | Remote settings rejected (`details.errors`) |
| `invalid_rclone_config` | 422 | The pasted rclone.conf cannot be parsed |
| `invalid_import` | 422 | Selected remotes cannot be imported (`details.errors`) |
| `name_clash` | 409 | Imported names exist already (`details.names`) |
| `unknown_provider` | 400 | No such provider |
| `oauth_not_supported` | 400, 422 | The provider does not sign in with OAuth |
| `oauth_client_id_required` | 422 | The sign-in needs the client ID of your own OAuth app (OmniSync ships none) |
| `oauth_client_secret_required` | 422 | The provider (Google) needs your OAuth app's client secret too |
| `provider_mismatch` | 422 | The remote is of another provider |
| `too_many_sessions` | 429 | Too many wizard sessions at once |
| `wizard_session_not_found` | 404 | Unknown or expired wizard session |
| `session_mismatch` | 422 | The session belongs to another provider or remote |
| `oauth_client_mismatch` | 422 | `/wizard/create` got a client ID or secret other than the app the sign-in was started with |
| `wrong_oauth_flow` | 422 | The step does not fit this session (new remote or reconnect) |
| `authorization_pending` | 409 | The authorization is not complete yet |
| `authorization_required` | 422 | The provider needs a completed authorization (`session_id`) |

### Notifications

| Code | Status | Meaning |
|------|--------|---------|
| `invalid_channel_settings` | 400 | Settings of a channel are not valid |
| `unknown_channel` | 400, 404 | No such notification channel |
| `invalid_subscription` | 400 | A Web Push subscription without its keys |
| `subscription_not_found` | 404 | No such Web Push subscription |

## Codes of the web UI server

The Next.js server answers some requests itself (`frontend/src/proxy.ts`,
`frontend/src/lib/auth/gate.ts`) in the same shape:

| Code | Status | Meaning |
|------|--------|---------|
| `login_required` | 401 | The UI login is on and the session is missing or expired |
| `request_refused` | 403 | Foreign Host or Origin, or a non-JSON body on `/api` |
| `invalid_login_request` | 400 | `POST /auth/login` without `{"password": "..."}` |
| `invalid_password` | 401 | Wrong password (`details.retry_after` once locked) |
| `login_throttled` | 429 | Too many attempts (`details.retry_after`) |
| `login_misconfigured` | 503 | `OMNISYNC_UI_PASSWORD_HASH` is not a valid hash |
| `login_failed` | 500 | The password check failed |
| `login_disabled` | 404 | The UI has no login |
| `method_not_allowed` | 405 | Wrong method on `/auth/*` |
