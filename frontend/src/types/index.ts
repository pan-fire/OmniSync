import type { components } from './api.gen';

// The API's types. Enums and a few models are the ones generated from the
// backend's OpenAPI schema (api.gen.ts: scripts/gen-openapi.py, then
// `pnpm gen:types`); the hand-written interfaces below are checked against
// the generated ones by src/__tests__/api-types.contract.test.ts. Types for
// UI-only shapes (query parameters, form state) are hand-written only.

/** A component schema of the backend's OpenAPI document. */
export type Schema<K extends keyof components['schemas']> = components['schemas'][K];

// Union types (mirroring backend Pydantic enums)

/**
 * What POST /profiles/{slug}/sync/start runs. push / pull are one-way
 * mirrors (explicit overrides in both modes); two_way is a two-way sync and
 * only for two_way profiles.
 */
export type SyncDirection = Schema<'SyncDirection'>;
/**
 * How a profile syncs automatically. two_way carries changes both ways and
 * keeps both versions of a file changed on both sides; mirror pushes local
 * changes and pulls the remote on the interval (the side that syncs last wins).
 */
export type SyncMode = Schema<'SyncMode'>;
/** syncing: a two-way sync (or resync) is running. */
export type SyncState = Schema<'SyncState'>;
export type JobStatus = Schema<'JobStatus'>;
export type FileChangeAction = Schema<'FileChangeAction'>;
/** The folder a recorded file change happened in. */
export type FileSide = Schema<'FileSide'>;
/**
 * keep_local / keep_remote copy that side's file over the other (the replaced
 * version goes to .omnisync-trash), keep_both keeps both versions on both
 * sides, dismiss only closes the record.
 */
export type ConflictResolution = Schema<'ConflictResolution'>;

// Interfaces (mirroring backend Pydantic response models)

/** A file a running sync is transferring right now. */
export interface ProgressFile {
  name:       string;
  size:       number | null;
  bytes:      number;
  percentage: number | null;
}

/**
 * Live progress of a running sync: rclone's stats, about once a second.
 * Totals grow while rclone is still listing. `speed` is bytes per second;
 * `eta_seconds` is null while rclone cannot estimate it.
 */
export interface SyncProgress {
  bytes:         number;
  total_bytes:   number;
  speed:         number;
  eta_seconds:   number | null;
  files_done:    number;
  files_total:   number;
  checks:        number;
  total_checks:  number;
  /** At most 5 of the files in flight. */
  current_files: ProgressFile[];
}

/**
 * When automatic syncs may run (server time). days: 0 = Monday ... 6 =
 * Sunday, the days a window starts on; end before start spans midnight.
 */
export interface SyncWindow {
  days:  number[];
  start: string;
  end:   string;
}

/** Fields the status of a profile carries about pauses, progress and its sync window. */
export interface SyncExtras {
  /** Paused by the user (Pause / Pause all); intervals_paused is then true too. */
  user_paused?:         boolean;
  progress?:            SyncProgress | null;
  /** The sync window is closed now: automatic syncs wait until next_window_start. */
  outside_sync_window?: boolean;
  next_window_start?:   string | null;
  /** An automatic sync waits for the window to open. */
  waiting_for_window?:  boolean;
}

export interface SyncStatus extends SyncExtras {
  state:            SyncState;
  last_sync:        string | null;
  current_job_id:   number | null;
  files_processed:  number;
  errors:           number;
  pending_changes:  number;
  intervals_paused: boolean;
  paused_at:        string | null;
  /** Why the last sync failed or was refused (e.g. folder not mounted, missing .omnisync-check). */
  last_error?:      string | null;
  /** A two-way profile whose sync state was lost: paused until the user confirms a resync. */
  resync_required:  boolean;
}

/**
 * A sync start or resync. The server answers once rclone starts changing
 * files (HTTP 202); follow the job with GET /jobs/{job_id}. A run refused by
 * its own safety checks is answered at once with state 'error' and the
 * reason in `error`.
 */
export interface SyncStartResponse {
  job_id: number;
  state:  SyncState;
  error?: string | null;
  /** E.g. that the sync started outside the profile's sync window. */
  note?:  string | null;
}

export interface SyncStopResponse {
  state:   SyncState;
  message: string;
}

/**
 * What a recorded job did; per-file actions are 'selective', a two-way sync
 * is 'two_way' and a resync (the union of both sides) is 'resync'.
 */
export type JobDirection = Schema<'JobDirection'>;

/** Why a sync left local names alone or treated them specially (see the backend's SyncWarningCode). */
export type SyncWarningCode = Schema<'SyncWarningCode'>;

/**
 * One kind of name problem: how many local names have it, and up to 20 of
 * their paths (escaped for display; a folder ends with "/").
 */
export interface SyncWarning {
  code:  SyncWarningCode;
  count: number;
  paths: string[];
}

export interface SyncJob {
  id:            number;
  direction:     JobDirection;
  started_at:    string;
  finished_at:   string | null;
  status:        JobStatus;
  files_changed: number;
  conflicts:     number;
  errors:        number;
  /** The job's profile as it is named now; null for jobs from before profiles. */
  profile_slug?: string | null;
  profile_name?: string | null;
  /** What the job left alone or treated specially; a completed job with warnings did not sync everything. */
  warnings?:     SyncWarning[];
}

export interface FileChange {
  id:         number;
  job_id:     number;
  file_path:  string;
  action:     FileChangeAction;
  size_bytes: number | null;
  /** The folder that changed; null for changes recorded before it was known. */
  side:       FileSide | null;
}

export interface Conflict {
  id:              number;
  /** null for a conflict a diff found (no sync job involved). */
  job_id:          number | null;
  file_path:       string;
  local_modified:  string | null;
  remote_modified: string | null;
  resolved:        boolean;
  resolution:      ConflictResolution | null;
  profile_slug:    string | null;
  profile_name:    string | null;
  /**
   * Set only for a conflict a two-way sync found: both versions already
   * exist on both sides under these names (one of them normally equals
   * file_path). keep_local / keep_remote then keep only that version under
   * file_path; keep_both and dismiss close the record and leave both files.
   */
  local_kept_as:   string | null;
  remote_kept_as:  string | null;
}

export interface Remote {
  name:           string;
  type:           string;
  last_verified:  string | null;
  /** The wizard provider of this type; null for types the wizard does not offer. */
  provider_id?:   string | null;
  /** Its settings can be edited in place (PUT /remotes/{name}). */
  editable?:      boolean;
  /** It signs in with OAuth and can be reconnected (new token only). */
  reconnectable?: boolean;
  /** The last test, health check or sync was refused: the sign-in needs renewing. */
  auth_error?:    boolean;
}

export interface LogEntry {
  timestamp:   string;
  level:       string;
  message:     string;
  /** The logger that wrote it; backend.audit for the audit trail of user actions. */
  logger?:     string | null;
  /** The API request it was written in (the X-Request-ID an error answer carries). */
  request_id?: string | null;
  /** The traceback or further lines that belong to the entry. */
  exc?:        string | null;
}

/** GET /logs categories: the audit trail, or ERROR and CRITICAL entries. */
export type LogCategory = 'audit' | 'errors';

/**
 * GET /health: local checks only. Served with HTTP 503 and status "degraded"
 * when the database or rclone is missing.
 */
export interface Health {
  status:           string;
  rclone_installed: boolean;
  uptime_seconds:   number;
  database_ok:      boolean;
  /** The backend's release version. */
  version:          string;
}

/** One remote a running profile syncs with (GET /health/remotes). */
export interface RemoteHealth {
  remote:      string;
  accessible:  boolean;
  profiles:    string[];
  /** Not accessible because the provider refused the credentials. */
  auth_error?: boolean;
}

export interface RemoteHealthResponse {
  remotes: RemoteHealth[];
}

// Errors

/** The body of every error answer (docs/api-errors.md). */
export type ErrorResponse = Schema<'ErrorResponse'>;

/** A failed API request: the status and the parsed error envelope. */
export class ApiError extends Error {
  constructor (
    public status: number,
    public detail: string,
    public code?: string,
    public details?: Record<string, unknown>,
    public requestId?: string
  ) {
    // A server error is the owner's to look up in the log: the message (and
    // so every error toast) names the request's id.
    super(status >= 500 && requestId ? `${detail} (ID ${requestId})` : detail);
    this.name = 'ApiError';
  }
}

// --- Wizard types ---

export type AuthType = Schema<'AuthType'>;
/**
 * 'password': a secret, never shown again once stored. 'select': one of
 * `options`. 'remote_path': "<existing remote>:<folder>" (a crypt remote's target).
 */
export type FieldType = Schema<'FieldType'>;
export type WizardSessionStatus = Schema<'WizardSessionStatus'>;

export interface ProviderField {
  name:       string;
  label:      string;
  field_type: FieldType;
  required:   boolean;
  help_text:  string;
  /** For 'select' fields: the allowed values. */
  options?:   string[];
  /** The value a new remote starts with. */
  default?:   string;
}

export interface Provider {
  id:           string;
  display_name: string;
  icon:         string;
  auth_type:    AuthType;
  fields:       ProviderField[];
  default_name: string;
  setup_guide:  string;
}

export interface AuthorizeResponse {
  session_id:    string;
  auth_url:      string;
  /** Where the provider sends the browser back to (OmniSync's callback). */
  redirect_uri?: string;
}

/** GET /wizard/oauth/redirect-uri: the redirect URI to register with your own OAuth app. */
export type OAuthRedirect = Schema<'OAuthRedirectResponse'>;

/** GET /wizard/sessions/{id}. The OAuth token stays on the server. */
export interface WizardSession {
  session_id:  string;
  status:      WizardSessionStatus;
  auth_url:    string | null;
  error:       string | null;
  /** Set with failures the wizard explains (WIZARD_ERROR_KEYS in config-step.tsx). */
  error_code?: string | null;
}

/**
 * POST /wizard/create. For OAuth providers pass the completed wizard
 * session_id; the server reads the token from that session.
 */
export interface CreateRemoteRequest {
  name:        string;
  provider_id: string;
  params?:     Record<string, string>;
  session_id?: string;
}

export interface TestRemoteResult {
  success:     boolean;
  error:       string | null;
  /** The provider refused the credentials. */
  auth_error?: boolean;
}

/** One setting of an existing remote. A secret's value is never sent: `is_set` says whether one is stored. */
export interface RemoteConfigField {
  name:   string;
  value:  string;
  is_set: boolean;
  secret: boolean;
}

/** GET /remotes/{name}/config: the settings for the edit form, secrets masked, no tokens. */
export interface RemoteConfig {
  name:        string;
  type:        string;
  provider_id: string;
  fields:      RemoteConfigField[];
  /** Settings the wizard has no field for (names only); an update keeps them. */
  other_keys:  string[];
}

/**
 * PUT /remotes/{name}. Left-out fields stay; a non-secret set to "" is
 * removed; a secret set to "" is kept, another value replaces it; `clear`
 * removes secrets.
 */
export interface UpdateRemoteRequest {
  params: Record<string, string>;
  clear?: string[];
}

/** One remote of an uploaded rclone.conf (POST /remotes/import/preview). Values are never returned. */
export interface ImportCandidate {
  name:     string;
  type:     string;
  /** A remote of this name exists already. */
  exists:   boolean;
  /** Reasons it cannot be imported. */
  problems: string[];
  keys:     string[];
}

export interface ImportPreview {
  remotes: ImportCandidate[];
  /** The file as a whole could not be read. */
  errors:  string[];
}

export interface ImportSelection {
  source: string;
  /** The name to add it under; defaults to source. */
  name?:  string;
}

export interface TestSyncStep {
  step:   string;
  ok:     boolean;
  error?: string;
}

export interface TestSyncResult {
  success: boolean;
  steps:   TestSyncStep[];
  error:   string | null;
}

// --- Browse types ---

export interface DirEntry {
  name: string;
  path: string;
}

export interface BrowseResponse {
  current: string;
  parent:  string | null;
  entries: DirEntry[];
}

// --- Sync check types ---

export interface SyncCheckResult {
  has_changes: boolean;
  local_only:  string[];
  remote_only: string[];
  differ:      string[];
  error:       string | null;
}

/** What one bulk sync direction would do to its destination. */
export interface SyncPreviewCounts {
  /** Files only on the destination: deleted (moved to .omnisync-trash). */
  deletes:            number;
  /** Files on both sides that differ: overwritten (old version to the trash). */
  replaces:           number;
  /** Files only on the source: copied. */
  creates:            number;
  /** The sync would stop at the delete limit. */
  exceeds_max_delete: boolean;
}

/**
 * What the next two-way sync of a two_way profile would do. `local` and
 * `remote` count the changes to that folder; a side with
 * exceeds_max_delete would stop the sync before anything changes.
 */
export interface TwoWayPreview {
  local:           SyncPreviewCounts;
  remote:          SyncPreviewCounts;
  /** Files changed on both sides (both versions will be kept). */
  conflicts:       number;
  /** The next run is a resync: the union of both sides, nothing deleted. */
  resync:          boolean;
  /** Automatic syncing is paused until the user confirms a resync. */
  resync_required: boolean;
  /** The dry run failed; no two-way preview. */
  error:           string | null;
}

/** POST /profiles/{slug}/sync/preview: counts only, nothing is changed. */
export interface SyncPreview {
  push:       SyncPreviewCounts;
  pull:       SyncPreviewCounts;
  /** Differing files bulk syncs leave out (manual flags, unresolved conflicts). */
  excluded:   number;
  /** The profile's effective delete limit; null means no limit. */
  max_delete: number | null;
  error:      string | null;
  sync_mode:  SyncMode;
  /** Set for two_way profiles: the next two-way sync. */
  two_way:    TwoWayPreview | null;
  /** Local names a sync cannot carry as they are. */
  warnings?:  SyncWarning[];
}

// --- Granular sync types ---

export type ChangeCategory = Schema<'ChangeCategory'>;

export type FileAction = Schema<'FileAction'>;

export interface FileDiff {
  path:            string;
  category:        ChangeCategory;
  local_size:      number | null;
  remote_size:     number | null;
  local_mod_time:  string | null;
  remote_mod_time: string | null;
  is_conflict:     boolean;
  manual_flag:     boolean;
}

export interface DiffSummary {
  local_only:      number;
  remote_only:     number;
  modified_local:  number;
  modified_remote: number;
  modified_both:   number;
  manual:          number;
  total:           number;
}

export interface DiffPagination {
  offset:   number;
  limit:    number;
  total:    number;
  has_more: boolean;
}

export interface DiffResponse {
  files:      FileDiff[];
  summary:    DiffSummary;
  pagination: DiffPagination | null;
  error:      string | null;
  /** Local names a sync cannot carry as they are (the same on every page). */
  warnings?:  SyncWarning[];
}

export interface SelectiveSyncItem {
  path:   string;
  action: FileAction;
}

export interface FileError {
  path:  string;
  error: string;
}

/** Per-file actions: 'running' right after the start, then their result. */
export interface SelectiveSyncResponse {
  job_id:    number;
  status:    JobStatus;
  total:     number;
  succeeded: number;
  failed:    number;
  errors:    FileError[];
}

export interface ManualFlagsResponse {
  flags: string[];
}

// --- Aggregate status types ---

export interface PausedProfileSummary {
  slug:            string;
  name:            string;
  pending_changes: number;
  paused_at:       string | null;
  /** Paused by the user (Pause / Pause all). */
  user_paused?:    boolean;
}

export interface ProfileSummary {
  slug:             string;
  name:             string;
  state:            SyncState;
  last_sync:        string | null;
  pending_changes:  number;
  intervals_paused: boolean;
  /** Why the last sync failed or was refused. */
  last_error:       string | null;
  /** Paused until the user confirms a resync (two-way profiles). */
  resync_required:  boolean;
  user_paused?:     boolean;
  progress?:        SyncProgress | null;
}

export interface AggregateStatus {
  overall_state:         SyncState;
  total_pending_changes: number;
  paused_profiles:       PausedProfileSummary[];
  profiles_summary:      ProfileSummary[];
}

// --- Notification types ---

export type NotificationSeverity = 'debug' | 'info' | 'warning' | 'error';

/** Webhook settings as the API returns them: header values are secrets, only `value_set` is shown. */
export interface WebhookSettings {
  url:        string;
  allow_http: boolean;
  headers:    { name: string; value_set: boolean }[];
}

export interface NtfySettings {
  server:       string;
  topic:        string;
  allow_http:   boolean;
  username:     string;
  token_set:    boolean;
  password_set: boolean;
}

export type SmtpSecurity = 'starttls' | 'tls' | 'none';

export interface EmailSettings {
  host:         string;
  port:         number;
  security:     SmtpSecurity;
  username:     string;
  password_set: boolean;
  from_addr:    string;
  to:           string[];
}

export interface ChannelConfig {
  enabled:      boolean;
  min_severity: NotificationSeverity;
  /** Set for the channel of that name only. */
  webhook?:     WebhookSettings;
  ntfy?:        NtfySettings;
  email?:       EmailSettings;
}

export interface NotificationConfig {
  channels: Record<string, ChannelConfig>;
}

/** A header to store; an empty value keeps the stored value of the same name. */
export interface WebhookHeaderUpdate {
  name:  string;
  value: string;
}

/** Settings updates: a secret left out or empty keeps the stored one; `clear` removes it. */
export interface WebhookSettingsUpdate {
  url?:        string;
  allow_http?: boolean;
  /** Replaces the stored list. */
  headers?:    WebhookHeaderUpdate[];
}

export interface NtfySettingsUpdate {
  server?:     string;
  topic?:      string;
  allow_http?: boolean;
  username?:   string;
  token?:      string;
  password?:   string;
  clear?:      ('token' | 'password')[];
}

export interface EmailSettingsUpdate {
  host?:      string;
  port?:      number;
  security?:  SmtpSecurity;
  username?:  string;
  password?:  string;
  from_addr?: string;
  to?:        string[];
  clear?:     'password'[];
}

export interface ChannelConfigUpdate {
  enabled?:      boolean;
  min_severity?: NotificationSeverity;
  webhook?:      WebhookSettingsUpdate;
  ntfy?:         NtfySettingsUpdate;
  email?:        EmailSettingsUpdate;
}

/** PUT /notifications/config: only the channels and fields given change. */
export interface NotificationConfigUpdate {
  channels: Record<string, ChannelConfigUpdate>;
}

/** GET /notifications/channels/status: availability and what is missing; enabled and severity live in the config. */
export interface ChannelStatusInfo {
  available:             boolean;
  detection_method?:     string | null;
  host_os?:              string | null;
  /** Codes, e.g. "notify-send", "dbus_socket", "push_subscription". */
  missing_dependencies?: string[];
  permission_status?:    string | null;
  /** Web Push: browsers subscribed. */
  subscriptions?:        number | null;
}

export interface ChannelStatus {
  channels:          Record<string, ChannelStatusInfo>;
  /** The notification settings in config.toml have errors (defaults are used). */
  config_error?:     boolean;
  /** Configured channel names no channel handles. */
  unknown_channels?: string[];
}

export interface NotificationLogEntry {
  id:                 number;
  event_type:         string;
  severity:           NotificationSeverity;
  title:              string;
  body:               string;
  timestamp:          string;
  channels_delivered: string[];
}

export interface NotificationHistory {
  items: NotificationLogEntry[];
  total: number;
}

export interface TestNotificationResponse {
  success:            boolean;
  channels_delivered: string[];
  /** Channel -> "unavailable" | "timeout" | "failed". */
  errors:             Record<string, string>;
}

// --- Profile types ---

export interface Profile {
  id:                    number;
  slug:                  string;
  name:                  string;
  local_dir:             string;
  remote_dir:            string;
  debounce_seconds:      number;
  pull_interval_minutes: number;
  rclone_filter:         string[];
  rclone_args:           string[];
  max_retries:           number;
  enabled:               boolean;
  created_at:            string;
  updated_at:            string;
  sync_mode:             SyncMode;

  /** The user hid the mirror-mode note of this profile (web UI and TUI). */
  mirror_notice_dismissed?: boolean;
  /** rclone --bwlimit for this profile's syncs (a rate or a timetable). */
  bwlimit?:                 string | null;
  /** Automatic syncs run only inside this window; null: any time. */
  sync_window?:             SyncWindow | null;
}

export interface ProfileStatus extends Profile, SyncExtras {
  state:            SyncState;
  last_sync:        string | null;
  current_job_id:   number | null;
  files_processed:  number;
  errors:           number;
  pending_changes:  number;
  intervals_paused: boolean;
  paused_at:        string | null;
  /** Why the last sync failed or was refused. */
  last_error:       string | null;
  /**
   * Files one sync may delete before it stops (the profile's --max-delete or
   * the server default); null means no limit. A two-way sync applies it to
   * each side and checks before changing anything.
   */
  max_delete:       number | null;
  /** A two-way profile whose sync state was lost: paused until the user confirms a resync (last_error says why). */
  resync_required:  boolean;
}

export interface ProfileCreateRequest {
  name:                   string;
  local_dir:              string;
  remote_dir:             string;
  debounce_seconds?:      number;
  pull_interval_minutes?: number;
  rclone_filter?:         string[];
  rclone_args?:           string[];
  max_retries?:           number;
  /** Defaults to two_way on the server; the first two-way run is a resync. */
  sync_mode?:             SyncMode;
  bwlimit?:               string | null;
  sync_window?:           SyncWindow | null;
}

export interface ProfileUpdateRequest {
  name?:                  string;
  local_dir?:             string;
  remote_dir?:            string;
  debounce_seconds?:      number;
  pull_interval_minutes?: number;
  rclone_filter?:         string[];
  rclone_args?:           string[];
  max_retries?:           number;
  /** Switching to two_way makes the next sync a resync; switching to mirror forgets the two-way state. */
  sync_mode?:             SyncMode;

  /** Hides (true) or shows again (false) the mirror-mode note. */
  mirror_notice_dismissed?: boolean;
  /** Omitted: unchanged; null or '' clears the limit. */
  bwlimit?:                 string | null;
  /** Omitted: unchanged; null clears the window. */
  sync_window?:             SyncWindow | null;
}

/** POST /profiles/pause-all and /profiles/resume-all, by slug. */
export interface PauseAllResponse {
  changed:      string[];
  unchanged:    string[];
  /** resume-all: still paused for another reason (slug -> why); needs that profile's own resume. */
  still_paused: Record<string, string>;
}

export type TrashSide = Schema<'TrashSide'>;

/** A file in a profile's .omnisync-trash; id is folder/path. */
export interface TrashEntry {
  id:         string;
  folder:     string;
  path:       string;
  size:       number | null;
  modified:   string | null;
  trashed_at: string | null;
}

export interface TrashList {
  side:        TrashSide;
  entries:     TrashEntry[];
  total_files: number;
  total_bytes: number;
  truncated:   boolean;
}

export interface TrashItemError {
  id:      string;
  /** not_found / target_newer / target_is_folder / invalid / failed */
  code:    string;
  message: string;
}

export interface TrashActionResponse {
  done:   string[];
  failed: TrashItemError[];
}

// --- Global config types ---

export interface GlobalConfig {
  log_level:    string;
  /** Days of job history kept (0: all); the newest jobs are kept regardless. */
  history_days: number;
}

// --- Backup types ---

export type BackupTargetType = Schema<'BackupTargetType'>;
export type BackupMode = Schema<'BackupMode'>;
export type BackupJobStatus = Schema<'BackupJobStatus'>;
export type RestoreScope = Schema<'RestoreScope'>;

export interface BackupTarget {
  id:                  number;
  profile_id:          number;
  name:                string;
  target_path:         string;
  target_type:         BackupTargetType;
  remote_name:         string | null;
  retention_days:      number;
  /** Retention never deletes the newest keep_last snapshots, however old. */
  keep_last:           number;
  frequency_hours:     number;
  backup_mode:         BackupMode;
  enabled:             boolean;
  /** Encrypted with a passphrase; the passphrase itself is never returned. */
  encrypted:           boolean;
  /** Compares the backup with the folder after each run. */
  verify_after_backup: boolean;
  /** Enabled, and no backup completed for more than twice its frequency. */
  overdue:             boolean;
  last_liveness_ok:    boolean | null;
  last_liveness_error: string | null;
  last_backup_at:      string | null;
  last_backup_status:  string | null;
  /** Verification of the last backup: 'verified', 'failed' or null (not verified). */
  last_verify_status:  VerifyStatus | null;
  last_verify_message: string | null;
  next_scheduled_at:   string | null;
  created_at:          string;
  updated_at:          string;
}

export type VerifyStatus = 'verified' | 'failed';

export interface BackupTargetCreateRequest {
  name:                   string;
  target_path:            string;
  target_type:            BackupTargetType;
  remote_name?:           string | null;
  retention_days?:        number;
  keep_last?:             number;
  frequency_hours?:       number;
  backup_mode?:           BackupMode;
  enabled?:               boolean;
  /** Encrypts the target (at least 8 characters). Lost passphrase = lost backups. */
  encryption_passphrase?: string | null;
  verify_after_backup?:   boolean;
}

export interface BackupTargetUpdateRequest {
  name?:                  string;
  target_path?:           string;
  target_type?:           BackupTargetType;
  remote_name?:           string | null;
  retention_days?:        number;
  keep_last?:             number;
  frequency_hours?:       number;
  backup_mode?:           BackupMode;
  enabled?:               boolean;
  /**
   * Set, change ('' or null: remove) the passphrase. The backend refuses it
   * (409) while the target's location holds backups.
   */
  encryption_passphrase?: string | null;
  verify_after_backup?:   boolean;
}

export interface BackupJob {
  id:              number;
  target_id:       number;
  started_at:      string;
  finished_at:     string | null;
  status:          BackupJobStatus;
  direction:       string;
  size_bytes:      number | null;
  snapshot_id:     string | null;
  /** Why it failed or was skipped (docs/api-errors.md); error_message is the matching text. */
  error_code?:     string | null;
  error_message:   string | null;
  verify_status?:  VerifyStatus | null;
  verify_message?: string | null;
}

export interface Snapshot {
  snapshot_id: string;
  created_at:  string;
  size_bytes:  number | null;
  status:      string;
  /** The most recent backup of the target. */
  latest:      boolean;
}

export interface RestoreRequest {
  snapshot_id:   string;
  restore_scope: RestoreScope;
}

/** One file or folder of a snapshot. */
export interface SnapshotFileEntry {
  path:       string;
  name:       string;
  is_dir:     boolean;
  /** A folder: the total size of its files. */
  size:       number | null;
  mod_time:   string | null;
  /** A folder: how many files it holds, at any depth. */
  file_count: number | null;
}

export interface SnapshotFilesResponse {
  snapshot_id:    string;
  path:           string;
  search:         string | null;
  entries:        SnapshotFileEntry[];
  total:          number;
  offset:         number;
  limit:          number;
  snapshot_files: number;
}

export interface SnapshotFilesQuery {
  path?:   string;
  search?: string;
  offset?: number;
  limit?:  number;
}

/** Restore chosen files and folders; no target_dir: to their original place. */
export interface RestoreFilesRequest {
  snapshot_id: string;
  paths:       string[];
  target_dir?: string | null;
}

export interface RestorePreviewSide {
  side:              'local' | 'remote';
  path:              string;
  added:             number;
  replaced:          number;
  removed:           number;
  unchanged:         number;
  added_examples:    string[];
  replaced_examples: string[];
  removed_examples:  string[];
}

export interface RestorePreview {
  snapshot_id:   string;
  restore_scope: RestoreScope;
  sides:         RestorePreviewSide[];
}

// --- Remote extended types ---

export interface RemoteStorageInfo {
  total_bytes:   number | null;
  used_bytes:    number | null;
  free_bytes:    number | null;
  trashed_bytes: number | null;
  supported:     boolean;
}

export interface RemoteTestResult {
  success:     boolean;
  latency_ms:  number | null;
  error:       string | null;
  /** The provider refused the credentials: reconnect or edit them. */
  auth_error?: boolean;
}

export interface RemoteDependencyProfile {
  slug: string;
  name: string;
}

export interface RemoteDependencyBackupTarget {
  profile_slug: string;
  target_name:  string;
  target_id:    number;
}

export interface RemoteDependencies {
  profiles:       RemoteDependencyProfile[];
  backup_targets: RemoteDependencyBackupTarget[];
}
