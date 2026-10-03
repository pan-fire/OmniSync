import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nProvider } from '@/i18n';
import { SyncControls } from '@/components/sync/sync-controls';
import { ResyncAction } from '@/components/sync/resync-action';
import type { ProfileStatus, SyncJob } from '@/types';

// A sync start answers once the run is under way (202 with the job id); the
// UI then polls GET /jobs/{id} until the job ends and shows the outcome.

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));

vi.mock('@/components/sync/profile-diff-tabs', () => ({
  ProfileDiffTabs: () => null,
}));

const DOCS: ProfileStatus = {
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
  state:                 'idle',
  last_sync:             null,
  current_job_id:        null,
  files_processed:       0,
  errors:                0,
  pending_changes:       0,
  intervals_paused:      false,
  paused_at:             null,
  last_error:            null,
  max_delete:            50,
  resync_required:       false,
  sync_mode:             'mirror',
};

const counts = { deletes: 0, replaces: 0, creates: 0, exceeds_max_delete: false };
const PREVIEW = {
  push: counts, pull: counts, excluded: 0, max_delete: 50, error: null, sync_mode: 'mirror', two_way: null,
};

function job (id: number, status: SyncJob['status'], filesChanged = 0): SyncJob {
  return {
    id,
    direction:     'push',
    started_at:    '2026-01-01T00:00:00Z',
    finished_at:   status === 'running' ? null : '2026-01-01T00:01:00Z',
    status,
    files_changed: filesChanged,
    conflicts:     0,
    errors:        status === 'failed' ? 1 : 0,
  };
}

interface Server {
  /** What POST .../sync/start (or /sync/resync) answers. */
  start:     { job_id: number; state: string; error?: string | null };
  /** GET /jobs/{id} answers, in order; the last one repeats. */
  jobs:      SyncJob[];
  lastError: string | null;
}

let server: Server;
let calls: string[];
const originalFetch = globalThis.fetch;

function jsonResponse (body: unknown, status = 200) {
  return Promise.resolve({ ok: true, status, json: () => Promise.resolve(body) });
}

beforeEach(async () => {
  const { toast } = await import('sonner');
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
  calls = [];
  let jobPolls = 0;
  globalThis.fetch = vi.fn((input: string | URL | Request, init?: globalThis.RequestInit) => {
    const url = String(input);
    calls.push(`${init?.method ?? 'GET'} ${url}`);
    if (url === '/api/profiles') return jsonResponse([DOCS]);
    if (url === '/api/profiles/docs') return jsonResponse({ ...DOCS, last_error: server.lastError });
    if (url === '/api/sync/status/aggregate') {
      return jsonResponse({ overall_state: 'idle', total_pending_changes: 0, paused_profiles: [], profiles_summary: [] });
    }
    if (url.endsWith('/sync/preview')) return jsonResponse(PREVIEW);
    if (url.endsWith('/sync/start') || url.endsWith('/sync/resync')) {
      return jsonResponse(server.start, server.start.state === 'error' ? 200 : 202);
    }
    if (url.match(/^\/api\/jobs\/\d+$/)) {
      const answer = server.jobs[Math.min(jobPolls, server.jobs.length - 1)];
      jobPolls += 1;
      return jsonResponse(answer);
    }
    return jsonResponse({});
  }) as unknown as typeof fetch;
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
  globalThis.fetch = originalFetch;
});

function renderUi (ui: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider>{ui}</I18nProvider>
    </QueryClientProvider>
  );
}

const jobPolls = (id: number) => calls.filter((c) => c === `GET /api/jobs/${id}`);

async function confirmPush () {
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  renderUi(<SyncControls />);
  const push = await screen.findByRole('button', { name: 'Push' });
  await waitFor(() => expect(push).toBeEnabled());
  await user.click(push);
  const dialog = await screen.findByRole('dialog');
  const confirm = within(dialog).getByRole('button', { name: 'Push now' });
  await waitFor(() => expect(confirm).toBeEnabled());
  await user.click(confirm);
}

describe('Push → job polling → outcome', () => {
  it('follows the started job until it finishes, then reports what changed', async () => {
    const { toast } = await import('sonner');
    server = { start: { job_id: 7, state: 'pushing' }, jobs: [job(7, 'running'), job(7, 'running'), job(7, 'completed', 3)], lastError: null };

    await confirmPush();

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Sync started for 1 profile'));
    expect(jobPolls(7).length).toBeGreaterThanOrEqual(1);
    expect(toast.success).not.toHaveBeenCalledWith(expect.stringContaining('finished'));

    await vi.advanceTimersByTimeAsync(2000);
    await vi.advanceTimersByTimeAsync(2000);

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Docs: sync finished, 3 files changed'));
    expect(jobPolls(7)).toHaveLength(3);
    expect(toast.error).not.toHaveBeenCalled();
    // Polling stops once the job has ended.
    await vi.advanceTimersByTimeAsync(6000);
    expect(jobPolls(7)).toHaveLength(3);
  });

  it('shows the reason when the job fails or is stopped', async () => {
    const { toast } = await import('sonner');
    server = { start: { job_id: 8, state: 'pushing' }, jobs: [job(8, 'running'), job(8, 'failed')], lastError: 'Stopped by user.' };

    await confirmPush();
    await waitFor(() => expect(jobPolls(8)).toHaveLength(1));
    await vi.advanceTimersByTimeAsync(2000);

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Docs: Stopped by user.'));
    expect(toast.success).not.toHaveBeenCalledWith(expect.stringContaining('finished'));
  });

  it('shows a refusal the server answered at once, without polling', async () => {
    const { toast } = await import('sonner');
    const reason = 'The sync marker .omnisync is missing from the remote folder.';
    server = { start: { job_id: 9, state: 'error', error: reason }, jobs: [job(9, 'failed')], lastError: reason };

    await confirmPush();

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(`Docs: ${reason}`));
    await vi.advanceTimersByTimeAsync(4000);
    expect(jobPolls(9)).toEqual([]);
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('says so when the job cannot be followed any more', async () => {
    const { toast } = await import('sonner');
    server = { start: { job_id: 10, state: 'pushing' }, jobs: [], lastError: null };
    const base = globalThis.fetch;
    globalThis.fetch = vi.fn((input: string | URL | Request, init?: globalThis.RequestInit) => {
      if (String(input) === '/api/jobs/10') {
        calls.push('GET /api/jobs/10');
        return Promise.resolve({ ok: false, status: 502, json: () => Promise.resolve({}) });
      }
      return base(input, init);
    }) as unknown as typeof fetch;

    await confirmPush();
    await waitFor(() => expect(jobPolls(10)).toHaveLength(1));
    for (let i = 0; i < 4; i += 1) await vi.advanceTimersByTimeAsync(2000);

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Docs: lost track of the sync. Check the profile's status."));
    expect(jobPolls(10)).toHaveLength(5);
  });
});

describe('Resync → job polling → outcome', () => {
  it('follows the resync job until it finishes', async () => {
    const { toast } = await import('sonner');
    server = { start: { job_id: 11, state: 'syncing' }, jobs: [job(11, 'running'), job(11, 'completed', 2)], lastError: null };
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderUi(<ResyncAction slug="docs" name="Docs" />);

    await user.click(screen.getByRole('button', { name: 'Resync' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Resync now' }));

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Resync started'));
    await waitFor(() => expect(jobPolls(11)).toHaveLength(1));
    await vi.advanceTimersByTimeAsync(2000);
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Docs: sync finished, 2 files changed'));
    expect(calls).toContain('POST /api/profiles/docs/sync/resync');
  });
});
