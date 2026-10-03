import type { Page, Route } from '@playwright/test';
import type {
  AggregateStatus, ErrorResponse, GlobalConfig, Health, ProfileStatus, Remote, RemoteHealthResponse, SyncJob,
} from '@/types';

// The backend, mocked in the browser: every /api request the pages make is
// answered here (Playwright route interception), so the suite needs no
// backend. The fixtures are typed with the app's API types, so a schema
// change that breaks them fails `pnpm typecheck`.

export const PROFILE: ProfileStatus = {
  id:                    1,
  slug:                  'docs',
  name:                  'Docs',
  local_dir:             '/data/docs',
  remote_dir:            'gdrive:docs',
  debounce_seconds:      5,
  pull_interval_minutes: 5,
  rclone_filter:         [],
  rclone_args:           [],
  backup_dir:            null,
  max_retries:           3,
  enabled:               true,
  created_at:            '2026-01-01T00:00:00Z',
  updated_at:            '2026-01-01T00:00:00Z',
  sync_mode:             'two_way',
  state:                 'idle',
  last_sync:             '2026-09-30T12:00:00Z',
  current_job_id:        null,
  files_processed:       12,
  errors:                0,
  pending_changes:       0,
  intervals_paused:      false,
  paused_at:             null,
  last_error:            null,
  max_delete:            10,
  resync_required:       false,
};

const AGGREGATE: AggregateStatus = {
  overall_state:         'idle',
  total_pending_changes: 0,
  paused_profiles:       [],
  profiles_summary:      [{
    slug:             PROFILE.slug,
    name:             PROFILE.name,
    state:            PROFILE.state,
    last_sync:        PROFILE.last_sync,
    pending_changes:  0,
    intervals_paused: false,
    last_error:       null,
    resync_required:  false,
  }],
};

const HEALTH: Health = {
  status:            'healthy',
  rclone_installed:  true,
  remote_accessible: null,
  uptime_seconds:    3600,
  database_ok:       true,
  version:           '0.9.0',
};

const REMOTES: Remote[] = [{
  name:          'gdrive',
  type:          'drive',
  last_verified: '2026-09-30T12:00:00Z',
  provider_id:   'drive',
  editable:      true,
  reconnectable: true,
  auth_error:    false,
}];

const REMOTE_HEALTH: RemoteHealthResponse = {
  remotes: [{ remote: 'gdrive', accessible: true, profiles: [PROFILE.slug] }],
};

const JOBS: SyncJob[] = [{
  id:            7,
  direction:     'two_way',
  started_at:    '2026-09-30T12:00:00Z',
  finished_at:   '2026-09-30T12:00:05Z',
  status:        'completed',
  files_changed: 3,
  conflicts:     0,
  errors:        0,
  profile_slug:  PROFILE.slug,
  profile_name:  PROFILE.name,
}];

const CONFIG: GlobalConfig = { log_level: 'INFO', history_days: 90 };

/** `<METHOD> <path without /api>` -> JSON body. */
const ROUTES: Record<string, unknown> = {
  'GET /health':                                  HEALTH,
  'GET /health/remotes':                          REMOTE_HEALTH,
  'GET /sync/status/aggregate':                   AGGREGATE,
  'GET /profiles':                                [PROFILE],
  [`GET /profiles/${PROFILE.slug}`]:              PROFILE,
  [`GET /profiles/${PROFILE.slug}/backups`]:      [],
  [`GET /profiles/${PROFILE.slug}/manual-flags`]: { flags: [] },
  'GET /remotes':                                 REMOTES,
  'GET /jobs':                                    JOBS,
  'GET /conflicts':                               [],
  'GET /config':                                  CONFIG,
};

/**
 * Answer /api from ROUTES; anything else gets the API's 404 error envelope
 * and is recorded in the returned list, so a test can see what it missed.
 */
export async function mockBackend (page: Page): Promise<string[]> {
  const unmocked: string[] = [];
  await page.route('**/api/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const key = `${request.method()} ${url.pathname.replace(/^\/api/, '')}`;
    if (key in ROUTES) {
      await route.fulfill({ json: ROUTES[key] });
      return;
    }
    unmocked.push(`${key}${url.search}`);
    const body: ErrorResponse = { code: 'not_found', detail: `No mock for ${key}` };
    await route.fulfill({ status: 404, json: body });
  });
  return unmocked;
}
