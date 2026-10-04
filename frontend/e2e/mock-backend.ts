import type { Page, Route } from '@playwright/test';
import type {
  AggregateStatus, BackupTarget, Conflict, DiffResponse, ErrorResponse, FileDiff, GlobalConfig, Health,
  LogEntry, OAuthRedirect, ProfileStatus, Provider, Remote, RemoteDependencies, RemoteHealthResponse,
  RemoteStorageInfo, Snapshot, SnapshotFileEntry, SnapshotFilesResponse, SyncJob, TrashList,
} from '@/types';
import packageJson from '../package.json';

// The backend, mocked in the browser: every /api request the pages make is
// answered here (Playwright route interception), so the suite needs no
// backend. The fixtures are typed with the app's API types, so a schema
// change that breaks them fails `pnpm typecheck`.
//
// The same data feeds the smoke tests and the README screenshots
// (screenshots.spec.ts), so it is meant to look like a real, lived-in
// install. Everything in it is made up: the user "alice", her folders and
// her remotes.

/** The moment the data describes; the screenshots freeze the clock here. */
export const NOW = '2026-09-30T12:10:00Z';

/** NOW minus `minutes`, as an ISO timestamp. */
function ago (minutes: number): string {
  return new Date(Date.parse(NOW) - minutes * 60_000).toISOString().replace('.000Z', 'Z');
}

const GiB = 1024 ** 3;
const MiB = 1024 ** 2;

const PROFILE_DEFAULTS = {
  debounce_seconds:        5,
  pull_interval_minutes:   5,
  rclone_filter:           [],
  rclone_args:             [],
  backup_dir:              null,
  max_retries:             3,
  enabled:                 true,
  created_at:              '2026-06-14T09:30:00Z',
  updated_at:              '2026-09-02T18:05:00Z',
  current_job_id:          null,
  errors:                  0,
  pending_changes:         0,
  intervals_paused:        false,
  paused_at:               null,
  last_error:              null,
  max_delete:              50,
  resync_required:         false,
  mirror_notice_dismissed: true,
  bwlimit:                 null,
  sync_window:             null,
} satisfies Partial<ProfileStatus>;

/** A two-way profile in the middle of a sync, with live progress. */
export const PROFILE: ProfileStatus = {
  ...PROFILE_DEFAULTS,
  id:              1,
  slug:            'documents',
  name:            'Documents',
  local_dir:       '/home/alice/Documents',
  remote_dir:      'gdrive:Documents',
  sync_mode:       'two_way',
  state:           'syncing',
  last_sync:       ago(14),
  current_job_id:  42,
  files_processed: 1284,
  progress:        {
    bytes:         412 * MiB,
    total_bytes:   1.1 * GiB,
    speed:         18.6 * MiB,
    eta_seconds:   38,
    files_done:    37,
    files_total:   96,
    checks:        1284,
    total_checks:  1284,
    current_files: [
      { name: 'Taxes/2025/receipts.pdf', size: 24 * MiB, bytes: 17 * MiB, percentage: 71 },
      { name: 'Work/quarterly-report.docx', size: 3.2 * MiB, bytes: 1.1 * MiB, percentage: 34 },
      { name: 'Recipes/sourdough.md', size: 14_200, bytes: 2_300, percentage: 16 },
    ],
  },
};

const PHOTOS: ProfileStatus = {
  ...PROFILE_DEFAULTS,
  id:                    2,
  slug:                  'photos',
  name:                  'Photos',
  local_dir:             '/home/alice/Pictures',
  remote_dir:            's3:alice-photos/Pictures',
  sync_mode:             'mirror',
  state:                 'idle',
  last_sync:             ago(52),
  files_processed:       18_412,
  pull_interval_minutes: 60,
  bwlimit:               '8M',
};

const PROJECTS: ProfileStatus = {
  ...PROFILE_DEFAULTS,
  id:              3,
  slug:            'projects',
  name:            'Projects',
  local_dir:       '/home/alice/Projects',
  remote_dir:      'nas:backup/Projects',
  sync_mode:       'two_way',
  state:           'idle',
  last_sync:       ago(6),
  files_processed: 5_731,
  rclone_filter:   ['- node_modules/**', '- .venv/**'],
};

const MUSIC: ProfileStatus = {
  ...PROFILE_DEFAULTS,
  id:              4,
  slug:            'music',
  name:            'Music',
  local_dir:       '/home/alice/Music',
  remote_dir:      'gdrive:Music',
  sync_mode:       'mirror',
  state:           'idle',
  last_sync:       ago(3 * 60 + 20),
  files_processed: 2_960,
  sync_window:     { days: [0, 1, 2, 3, 4, 5, 6], start: '01:00', end: '06:00' },
};

export const PROFILES: ProfileStatus[] = [PROFILE, PHOTOS, PROJECTS, MUSIC];

const AGGREGATE: AggregateStatus = {
  overall_state:         'syncing',
  total_pending_changes: PROFILES.reduce((sum, p) => sum + p.pending_changes, 0),
  paused_profiles:       [],
  profiles_summary:      PROFILES.map((p) => ({
    slug:             p.slug,
    name:             p.name,
    state:            p.state,
    last_sync:        p.last_sync,
    pending_changes:  p.pending_changes,
    intervals_paused: p.intervals_paused,
    last_error:       p.last_error,
    resync_required:  p.resync_required,
    progress:         p.progress ?? null,
  })),
};

const HEALTH: Health = {
  status:            'ok',
  rclone_installed:  true,
  remote_accessible: true,
  uptime_seconds:    6 * 86_400 + 4 * 3600,
  database_ok:       true,
  // The version the UI was built with, so the sidebar shows no mismatch.
  version:           packageJson.version,
};

const REMOTES: Remote[] = [
  {
    name: 'gdrive', type: 'drive', last_verified: ago(30), provider_id: 'drive', editable: true, reconnectable: true, auth_error: false,
  },
  {
    name: 's3', type: 's3', last_verified: ago(55), provider_id: 's3', editable: true, reconnectable: false, auth_error: false,
  },
  {
    name: 'nas', type: 'sftp', last_verified: ago(8), provider_id: 'sftp', editable: true, reconnectable: false, auth_error: false,
  },
  {
    name: 'dropbox', type: 'dropbox', last_verified: ago(2 * 1440), provider_id: 'dropbox', editable: true, reconnectable: true, auth_error: false,
  },
];

const STORAGE: Record<string, RemoteStorageInfo> = {
  gdrive:  { total_bytes: 100 * GiB, used_bytes: 63.4 * GiB, free_bytes: 36.6 * GiB, trashed_bytes: 1.2 * GiB, supported: true },
  s3:      { total_bytes: null, used_bytes: 212 * GiB, free_bytes: null, trashed_bytes: null, supported: true },
  nas:     { total_bytes: 4000 * GiB, used_bytes: 1710 * GiB, free_bytes: 2290 * GiB, trashed_bytes: null, supported: true },
  dropbox: { total_bytes: 2000 * GiB, used_bytes: 1630 * GiB, free_bytes: 370 * GiB, trashed_bytes: null, supported: true },
};

const DEPENDENCIES: Record<string, RemoteDependencies> = {
  gdrive:  { profiles: [{ slug: 'documents', name: 'Documents' }, { slug: 'music', name: 'Music' }], backup_targets: [] },
  s3:      { profiles: [{ slug: 'photos', name: 'Photos' }], backup_targets: [] },
  nas:     { profiles: [{ slug: 'projects', name: 'Projects' }], backup_targets: [{ profile_slug: 'documents', target_name: 'NAS archive', target_id: 1 }] },
  dropbox: { profiles: [], backup_targets: [] },
};

const REMOTE_HEALTH: RemoteHealthResponse = {
  remotes: REMOTES.map((r) => ({
    remote:     r.name,
    accessible: true,
    profiles:   PROFILES.filter((p) => p.remote_dir.startsWith(`${r.name}:`)).map((p) => p.slug),
  })),
};

function job (id: number, profile: ProfileStatus, minutesAgo: number, seconds: number, fields: Partial<SyncJob>): SyncJob {
  return {
    id,
    direction:     'two_way',
    started_at:    ago(minutesAgo),
    finished_at:   new Date(Date.parse(ago(minutesAgo)) + seconds * 1000).toISOString().replace('.000Z', 'Z'),
    status:        'completed',
    files_changed: 0,
    conflicts:     0,
    errors:        0,
    profile_slug:  profile.slug,
    profile_name:  profile.name,
    ...fields,
  };
}

const JOBS: SyncJob[] = [
  { ...job(42, PROFILE, 1, 0, { files_changed: 37 }), status: 'running', finished_at: null },
  job(41, PROJECTS, 6, 4, { files_changed: 12 }),
  job(40, PROFILE, 14, 9, { files_changed: 5, conflicts: 1 }),
  job(39, PHOTOS, 52, 141, { direction: 'push', files_changed: 248 }),
  job(38, PROJECTS, 66, 3, { files_changed: 2 }),
  job(37, MUSIC, 200, 37, { direction: 'pull', files_changed: 18 }),
  job(36, PHOTOS, 300, 12, { direction: 'pull', status: 'failed', errors: 1 }),
  job(35, PROFILE, 420, 6, { files_changed: 9 }),
];

const CONFLICTS: Conflict[] = [{
  id:              3,
  job_id:          40,
  file_path:       'Work/budget-2026.xlsx',
  local_modified:  ago(19),
  remote_modified: ago(17),
  resolved:        false,
  resolution:      null,
  profile_slug:    PROFILE.slug,
  profile_name:    PROFILE.name,
  local_kept_as:   'Work/budget-2026.conflict-local.xlsx',
  remote_kept_as:  'Work/budget-2026.conflict-remote.xlsx',
}];

function diffEntry (path: string, category: FileDiff['category'], local: number | null, remote: number | null, fields: Partial<FileDiff> = {}): FileDiff {
  return {
    path,
    category,
    local_size:      local,
    remote_size:     remote,
    local_mod_time:  local === null ? null : ago(25),
    remote_mod_time: remote === null ? null : ago(40),
    is_conflict:     false,
    manual_flag:     false,
    ...fields,
  };
}

const DIFF_FILES: FileDiff[] = [
  diffEntry('Work/budget-2026.xlsx', 'modified_both', 48_300, 51_020, { is_conflict: true, local_mod_time: ago(19), remote_mod_time: ago(17) }),
  diffEntry('Letters/landlord-2026-09.odt', 'local_only', 22_480, null, { local_mod_time: ago(48) }),
  diffEntry('Travel/lisbon-itinerary.pdf', 'remote_only', null, 1.4 * MiB, { remote_mod_time: ago(2 * 1440 + 130) }),
  diffEntry('Work/meeting-notes.md', 'modified_local', 6_120, 5_870, { local_mod_time: ago(33), remote_mod_time: ago(1440 + 15) }),
  diffEntry('Health/insurance-card.pdf', 'modified_remote', 310_000, 318_400, { local_mod_time: ago(3 * 1440), remote_mod_time: ago(95) }),
  diffEntry('Recipes/sourdough.md', 'modified_local', 14_200, 13_950, { local_mod_time: ago(71), remote_mod_time: ago(5 * 1440) }),
];

const DIFF: DiffResponse = {
  files:   DIFF_FILES,
  summary: {
    local_only:      1,
    remote_only:     1,
    modified_local:  2,
    modified_remote: 1,
    modified_both:   1,
    manual:          0,
    total:           DIFF_FILES.length,
  },
  pagination: { offset: 0, limit: 100, total: DIFF_FILES.length, has_more: false },
  error:      null,
};

const BACKUP_TARGETS: BackupTarget[] = [{
  id:                  1,
  profile_id:          PROFILE.id,
  name:                'NAS archive',
  target_path:         'nas:backup/snapshots/Documents',
  target_type:         'remote',
  remote_name:         'nas',
  retention_days:      90,
  keep_last:           10,
  frequency_hours:     24,
  backup_mode:         'archive',
  enabled:             true,
  encrypted:           true,
  verify_after_backup: true,
  overdue:             false,
  last_liveness_ok:    true,
  last_liveness_error: null,
  last_backup_at:      ago(9 * 60),
  last_backup_status:  'completed',
  last_verify_status:  'verified',
  last_verify_message: null,
  next_scheduled_at:   ago(-15 * 60),
  created_at:          '2026-06-14T09:45:00Z',
  updated_at:          '2026-09-02T18:05:00Z',
}];

const SNAPSHOTS: Snapshot[] = [0, 1, 2, 3].map((day) => ({
  snapshot_id: `2026-09-${String(30 - day).padStart(2, '0')}T030000Z`,
  created_at:  `2026-09-${String(30 - day).padStart(2, '0')}T03:00:00Z`,
  size_bytes:  Math.round((4.8 - day * 0.05) * GiB),
  status:      'completed',
  kind:        'full',
  latest:      day === 0,
}));

function dir (path: string, files: number): SnapshotFileEntry {
  return { path, name: path.split('/').pop()!, is_dir: true, size: null, mod_time: null, file_count: files };
}

function file (path: string, size: number, minutesAgo: number): SnapshotFileEntry {
  return { path, name: path.split('/').pop()!, is_dir: false, size, mod_time: ago(minutesAgo), file_count: null };
}

const SNAPSHOT_ENTRIES: SnapshotFileEntry[] = [
  dir('Health', 14),
  dir('Letters', 63),
  dir('Recipes', 41),
  dir('Taxes', 212),
  dir('Travel', 27),
  dir('Work', 318),
  file('address-book.vcf', 88_400, 2 * 1440),
  file('reading-list.txt', 4_210, 6 * 1440),
];

function snapshotFiles (url: URL): SnapshotFilesResponse {
  const snapshotId = decodeURIComponent(url.pathname.split('/snapshots/')[1].split('/')[0]);
  return {
    snapshot_id:    snapshotId,
    path:           url.searchParams.get('path') ?? '',
    search:         null,
    entries:        SNAPSHOT_ENTRIES,
    total:          SNAPSHOT_ENTRIES.length,
    offset:         0,
    limit:          100,
    snapshot_files: 1_284,
  };
}

const EMPTY_TRASH = (side: 'local' | 'remote'): TrashList => ({ side, entries: [], total_files: 0, total_bytes: 0, truncated: false });

function log (minutesAgo: number, level: string, logger: string, message: string, requestId: string | null = null): LogEntry {
  return { timestamp: ago(minutesAgo), level, logger, message, request_id: requestId };
}

const LOGS: LogEntry[] = [
  log(1, 'INFO', 'backend.services.sync_engine', 'documents: two-way sync started (job 42)'),
  log(1, 'INFO', 'backend.audit', 'sync.start profile=documents direction=two_way force=false outcome=ok client=127.0.0.1', 'a41f9c02'),
  log(6, 'INFO', 'backend.services.sync_engine', 'projects: two-way sync finished, 12 files changed'),
  log(14, 'WARNING', 'backend.services.sync_engine', 'documents: Work/budget-2026.xlsx changed on both sides, kept both versions'),
  log(52, 'INFO', 'backend.services.sync_engine', 'photos: push finished, 248 files changed'),
  log(300, 'ERROR', 'backend.services.sync_engine', 'photos: pull failed: the remote did not answer in time (attempt 3 of 3)'),
  log(540, 'INFO', 'backend.services.backup_service', 'documents: backup "NAS archive" finished and verified (4.8 GiB)'),
  log(1440, 'INFO', 'backend.audit', 'profile.update profile=projects outcome=ok client=127.0.0.1', '7be2d915'),
];

const PROVIDERS: Provider[] = [
  {
    id:           'drive',
    display_name: 'Google Drive',
    icon:         'gdrive',
    auth_type:    'oauth',
    default_name: 'gdrive',
    setup_guide:  'OmniSync ships no OAuth app of its own: create one in the Google Cloud console.',
    fields:       [
      { name: 'client_id', label: 'Client ID', field_type: 'text', required: true, help_text: 'The client ID of your Google OAuth app (Web application).' },
      { name: 'client_secret', label: 'Client Secret', field_type: 'password', required: true, help_text: 'The client secret of the same Google OAuth app.' },
    ],
  },
  {
    id:           'dropbox',
    display_name: 'Dropbox',
    icon:         'dropbox',
    auth_type:    'oauth',
    default_name: 'dropbox',
    setup_guide:  '',
    fields:       [{ name: 'client_id', label: 'App Key', field_type: 'text', required: true, help_text: 'The App key of your Dropbox app.' }],
  },
  {
    id:           'onedrive',
    display_name: 'OneDrive',
    icon:         'onedrive',
    auth_type:    'oauth',
    default_name: 'onedrive',
    setup_guide:  '',
    fields:       [{ name: 'client_id', label: 'Client ID', field_type: 'text', required: true, help_text: 'The application (client) ID of your Azure app.' }],
  },
  {
    id: 's3', display_name: 'Amazon S3', icon: 's3', auth_type: 'key', default_name: 's3', setup_guide: '', fields: [],
  },
  {
    id: 'b2', display_name: 'Backblaze B2', icon: 'b2', auth_type: 'key', default_name: 'b2', setup_guide: '', fields: [],
  },
  {
    id: 'sftp', display_name: 'SFTP', icon: 'sftp', auth_type: 'key', default_name: 'sftp', setup_guide: '', fields: [],
  },
  {
    id: 'webdav', display_name: 'WebDAV', icon: 'webdav', auth_type: 'key', default_name: 'webdav', setup_guide: '', fields: [],
  },
  {
    id: 'smb', display_name: 'SMB / Windows share', icon: 'smb', auth_type: 'key', default_name: 'smb', setup_guide: '', fields: [],
  },
  {
    id: 'crypt', display_name: 'Encrypted (crypt)', icon: 'crypt', auth_type: 'key', default_name: 'crypt', setup_guide: '', fields: [],
  },
];

const OAUTH_REDIRECT: OAuthRedirect = { redirect_uri: 'http://localhost:3000/api/wizard/oauth/callback' };

const CONFIG: GlobalConfig = { log_level: 'INFO', history_days: 90 };

type Handler = unknown | ((url: URL) => unknown);

/** `<METHOD> <path without /api>` -> JSON body, or a function of the URL that returns it. */
const ROUTES: Record<string, Handler> = {
  'GET /health':                HEALTH,
  'GET /health/remotes':        REMOTE_HEALTH,
  'GET /sync/status/aggregate': AGGREGATE,
  'GET /profiles':              PROFILES,
  'GET /remotes':               REMOTES,
  'GET /jobs':                  (url: URL) => {
    const profile = url.searchParams.get('profile');
    return profile ? JOBS.filter((j) => j.profile_slug === profile) : JOBS;
  },
  'GET /conflicts': (url: URL) => {
    const profile = url.searchParams.get('profile');
    return profile ? CONFLICTS.filter((c) => c.profile_slug === profile) : CONFLICTS;
  },
  'GET /logs':                      LOGS,
  'GET /config':                    CONFIG,
  'GET /wizard/providers':          PROVIDERS,
  'GET /wizard/oauth/redirect-uri': OAUTH_REDIRECT,
};

for (const profile of PROFILES) {
  const base = `/profiles/${profile.slug}`;
  const isDocuments = profile.slug === PROFILE.slug;
  ROUTES[`GET ${base}`] = profile;
  ROUTES[`GET ${base}/backups`] = isDocuments ? BACKUP_TARGETS : [];
  ROUTES[`GET ${base}/manual-flags`] = { flags: [] };
  ROUTES[`GET ${base}/trash`] = (url: URL) => EMPTY_TRASH(url.searchParams.get('side') === 'remote' ? 'remote' : 'local');
  ROUTES[`POST ${base}/diff`] = isDocuments
    ? DIFF
    : { ...DIFF, files: [], summary: { ...DIFF.summary, local_only: 0, remote_only: 0, modified_local: 0, modified_remote: 0, modified_both: 0, total: 0 }, pagination: null };
}
ROUTES[`GET /profiles/${PROFILE.slug}/backups/1/snapshots`] = SNAPSHOTS;
for (const remote of REMOTES) {
  ROUTES[`GET /remotes/${remote.name}/about`] = STORAGE[remote.name];
  ROUTES[`GET /remotes/${remote.name}/dependencies`] = DEPENDENCIES[remote.name];
}

/** Handlers for paths with a variable part, tried after ROUTES. */
const PATTERNS: Array<[RegExp, Handler]> = [
  [new RegExp(`^GET /profiles/${PROFILE.slug}/backups/1/snapshots/[^/]+/files$`), snapshotFiles],
  [/^GET \/jobs\/(\d+)$/, (url: URL) => JOBS.find((j) => url.pathname.endsWith(`/jobs/${j.id}`)) ?? JOBS[0]],
  [/^GET \/jobs\/\d+\/files$/, []],
];

function resolve (key: string, url: URL): { found: boolean; body?: unknown } {
  let handler: Handler | undefined = ROUTES[key];
  if (!(key in ROUTES)) {
    handler = PATTERNS.find(([pattern]) => pattern.test(key))?.[1];
    if (handler === undefined) return { found: false };
  }
  return { found: true, body: typeof handler === 'function' ? (handler as (url: URL) => unknown)(url) : handler };
}

/**
 * Answer /api from ROUTES and PATTERNS; anything else gets the API's 404
 * error envelope and is recorded in the returned list, so a test can see
 * what it missed.
 */
export async function mockBackend (page: Page): Promise<string[]> {
  const unmocked: string[] = [];
  await page.route('**/api/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const key = `${request.method()} ${url.pathname.replace(/^\/api/, '')}`;
    const { found, body } = resolve(key, url);
    if (found) {
      await route.fulfill({ json: body });
      return;
    }
    unmocked.push(`${key}${url.search}`);
    const error: ErrorResponse = { code: 'not_found', detail: `No mock for ${key}` };
    await route.fulfill({ status: 404, json: error });
  });
  return unmocked;
}
