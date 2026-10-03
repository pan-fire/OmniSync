import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nProvider } from '@/i18n';
import { SyncControls } from '@/components/sync/sync-controls';
import { previewFor } from '@/components/sync/sync-confirm-dialog';
import type { ProfileStatus } from '@/types';

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));

vi.mock('@/components/sync/profile-diff-tabs', () => ({
  ProfileDiffTabs: () => null,
}));

function profile (slug: string, name: string, enabled = true): ProfileStatus {
  return {
    id:                    1,
    slug,
    name,
    local_dir:             `/data/${slug}`,
    remote_dir:            `gdrive:${slug}`,
    debounce_seconds:      5,
    pull_interval_minutes: 5,
    rclone_filter:         [],
    rclone_args:           [],
    backup_dir:            null,
    max_retries:           3,
    enabled,
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
}

const PROFILES = [profile('docs', 'Docs'), profile('photos', 'Photos'), profile('old', 'Old', false)];

const counts = (deletes: number, replaces: number, exceeds = false) => ({
  deletes, replaces, creates: 0, exceeds_max_delete: exceeds,
});

// POST /profiles/{slug}/sync/preview answers.
const PREVIEWS: Record<string, unknown> = {
  docs: {
    push: counts(3, 1, true), pull: counts(1, 1), excluded: 2, max_delete: 2, error: null, sync_mode: 'mirror', two_way: null,
  },
  photos: {
    push: counts(0, 0), pull: counts(0, 0), excluded: 0, max_delete: null, error: null, sync_mode: 'mirror', two_way: null,
  },
};

type Call = { url: string; method: string; body?: string };
let calls: Call[];
const originalFetch = globalThis.fetch;

function jsonResponse (body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

beforeEach(() => {
  calls = [];
  globalThis.fetch = vi.fn((input: string | URL | Request, init?: globalThis.RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined });
    if (url === '/api/profiles') return jsonResponse(PROFILES);
    if (url === '/api/sync/status/aggregate') {
      return jsonResponse({ overall_state: 'idle', total_pending_changes: 0, paused_profiles: [], profiles_summary: [] });
    }
    const preview = url.match(/^\/api\/profiles\/([^/]+)\/sync\/preview$/);
    if (preview) return jsonResponse(PREVIEWS[preview[1]]);
    const start = url.match(/^\/api\/profiles\/([^/]+)\/sync\/start$/);
    if (start) return jsonResponse({ job_id: 1, state: 'pushing' });
    // The started jobs keep running (sync-controls-jobs.test.tsx covers the end).
    const job = url.match(/^\/api\/jobs\/(\d+)$/);
    if (job) return jsonResponse({ id: Number(job[1]), status: 'running' });
    return jsonResponse({});
  }) as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function renderControls (ui: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider>{ui}</I18nProvider>
    </QueryClientProvider>
  );
}

const startCalls = () => calls.filter((c) => c.url.includes('/sync/start'));
// Anything that changes server state; the preview only counts.
const mutatingCalls = () => calls.filter((c) => c.method !== 'GET' && !c.url.endsWith('/sync/preview'));

describe('Push/Pull confirmation', () => {
  it('clicking Push then Cancel sends no sync request', async () => {
    const user = userEvent.setup();
    renderControls(<SyncControls />);

    const push = await screen.findByRole('button', { name: 'Push' });
    await waitFor(() => expect(push).toBeEnabled());
    await user.click(push);

    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(startCalls()).toEqual([]);
    expect(mutatingCalls()).toEqual([]);
    // The dialog counts with the side-effect-free preview, never /sync/check.
    expect(calls.some((c) => c.url.endsWith('/sync/check'))).toBe(false);
  });

  it('lists each enabled profile with the files the push would delete', async () => {
    const user = userEvent.setup();
    renderControls(<SyncControls />);

    const push = await screen.findByRole('button', { name: 'Push' });
    await waitFor(() => expect(push).toBeEnabled());
    await user.click(push);

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Push 2 profiles?')).toBeInTheDocument();
    const list = within(dialog).getByTestId('sync-confirm-profiles');
    expect(within(list).getByText('Docs')).toBeInTheDocument();
    expect(within(list).getByText('Photos')).toBeInTheDocument();
    expect(within(list).queryByText('Old')).not.toBeInTheDocument();
    // A push deletes the remote-only files.
    expect(await within(list).findByText('3 files would be deleted from the remote')).toBeInTheDocument();
    expect(within(list).getByText('0 files would be deleted from the remote')).toBeInTheDocument();
    expect(within(list).getByText('1 changed file would be replaced')).toBeInTheDocument();
    expect(within(list).getByText(/2 differing files are left out/)).toBeInTheDocument();
    // Each profile's real delete limit, not a hard-coded 50.
    expect(within(list).getByText('Delete limit: 2 files')).toBeInTheDocument();
    expect(within(list).getByText('No delete limit for this profile')).toBeInTheDocument();
    expect(within(list).getByText(/would delete more than 2 files/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/50 files/)).toBeNull();
    expect(within(dialog).getAllByText(/\.omnisync-trash/).length).toBeGreaterThan(0);
    expect(startCalls()).toEqual([]);
  });

  it('confirming starts each enabled profile through its own endpoint', async () => {
    const user = userEvent.setup();
    renderControls(<SyncControls />);

    const pull = await screen.findByRole('button', { name: 'Pull' });
    await waitFor(() => expect(pull).toBeEnabled());
    await user.click(pull);

    const dialog = await screen.findByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: 'Pull now' });
    await waitFor(() => expect(confirm).toBeEnabled());
    await user.click(confirm);

    await waitFor(() => expect(startCalls()).toHaveLength(2));
    expect(startCalls().map((c) => c.url).sort()).toEqual([
      '/api/profiles/docs/sync/start',
      '/api/profiles/photos/sync/start',
    ]);
    // Confirmed: sent with force, so a paused profile runs too.
    for (const call of startCalls()) {
      expect(JSON.parse(call.body ?? '{}')).toEqual({ direction: 'pull', force: true });
    }
    expect(calls.some((c) => c.url === '/api/sync/start')).toBe(false);
  });

  it('a confirmed push of a paused profile is sent with force and starts', async () => {
    const { toast } = await import('sonner');
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
    const paused = { ...profile('docs', 'Docs'), intervals_paused: true, pending_changes: 4 };
    const base = globalThis.fetch;
    globalThis.fetch = vi.fn((input: string | URL | Request, init?: globalThis.RequestInit) => {
      const url = String(input);
      if (url === '/api/profiles') {
        calls.push({ url, method: 'GET' });
        return jsonResponse([paused]);
      }
      if (url.endsWith('/sync/start')) {
        calls.push({ url, method: 'POST', body: String(init?.body) });
        // The backend refuses a paused profile unless force is set.
        const body = JSON.parse(String(init?.body));
        return body.force
          ? jsonResponse({ job_id: 7, state: 'pushing' })
          : Promise.resolve({ ok: false, status: 409, json: () => Promise.resolve({ detail: 'paused' }) });
      }
      return base(input, init);
    }) as unknown as typeof fetch;

    const user = userEvent.setup();
    renderControls(<SyncControls />);
    const push = await screen.findByRole('button', { name: 'Push' });
    await waitFor(() => expect(push).toBeEnabled());
    await user.click(push);
    const dialog = await screen.findByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: 'Push now' });
    await waitFor(() => expect(confirm).toBeEnabled());
    await user.click(confirm);

    await waitFor(() => expect(startCalls()).toHaveLength(1));
    expect(JSON.parse(startCalls()[0].body ?? '{}')).toEqual({ direction: 'push', force: true });
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(toast.error).not.toHaveBeenCalled();
  });
});

describe('Sync now (two-way) on the dashboard', () => {
  const twoWay = (slug: string, name: string, extra: Partial<ProfileStatus> = {}): ProfileStatus => ({
    ...profile(slug, name), sync_mode: 'two_way', ...extra,
  });

  const twoWayPreview = (extra: Record<string, unknown>) => ({
    push:       counts(0, 0),
    pull:       counts(0, 0),
    excluded:   0,
    max_delete: 5,
    error:      null,
    sync_mode:  'two_way',
    two_way:    {
      local:           { deletes: 0, replaces: 0, creates: 7, exceeds_max_delete: false },
      remote:          { deletes: 0, replaces: 2, creates: 3, exceeds_max_delete: false },
      conflicts:       0,
      resync:          false,
      resync_required: false,
      error:           null,
      ...extra,
    },
  });

  function serve (profiles: ProfileStatus[], previews: Record<string, unknown>) {
    const base = globalThis.fetch;
    globalThis.fetch = vi.fn((input: string | URL | Request, init?: globalThis.RequestInit) => {
      const url = String(input);
      if (url === '/api/profiles') return jsonResponse(profiles);
      const preview = url.match(/^\/api\/profiles\/([^/]+)\/sync\/preview$/);
      if (preview) {
        calls.push({ url, method: 'POST' });
        return jsonResponse(previews[preview[1]]);
      }
      return base(input, init);
    }) as unknown as typeof fetch;
  }

  it('is not offered when no profile syncs both ways', async () => {
    renderControls(<SyncControls />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Push' })).toBeEnabled());
    expect(screen.queryByRole('button', { name: 'Sync now' })).not.toBeInTheDocument();
  });

  it('covers the enabled two-way profiles that need no resync, shows the preview, and sends two_way with force', async () => {
    serve(
      [
        twoWay('docs', 'Docs'),
        twoWay('music', 'Music'),
        twoWay('photos', 'Photos', { resync_required: true }),
        profile('old', 'Old'),
      ],
      {
        docs:  twoWayPreview({ resync: true }),
        music: twoWayPreview({ error: 'bisync dry run failed' }),
      }
    );
    const user = userEvent.setup();
    renderControls(<SyncControls />);

    const syncNow = await screen.findByRole('button', { name: 'Sync now' });
    await waitFor(() => expect(syncNow).toBeEnabled());
    await user.click(syncNow);

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Sync 2 profiles both ways?')).toBeInTheDocument();
    const list = within(dialog).getByTestId('sync-confirm-profiles');
    expect(within(list).queryByText('Photos')).not.toBeInTheDocument();
    expect(within(list).queryByText('Old')).not.toBeInTheDocument();
    expect(await within(list).findByText(/This run is a resync/)).toBeInTheDocument();
    expect(within(list).getByText('7 new files would be copied')).toBeInTheDocument();
    expect(within(list).getByText('2 changed files would be replaced')).toBeInTheDocument();
    expect(within(list).getByText('Delete limit: 5 files per side')).toBeInTheDocument();
    expect(within(list).getByText('Could not count the changes: bisync dry run failed')).toBeInTheDocument();
    expect(within(dialog).getByText(/delete limit applies to each side/)).toBeInTheDocument();
    expect(startCalls()).toEqual([]);

    // Music has no preview, so forcing it needs an explicit acknowledgement.
    const confirm = within(dialog).getByRole('button', { name: 'Sync now' });
    expect(confirm).toBeDisabled();
    await user.click(within(dialog).getByLabelText(/runs without a preview/));
    await user.click(confirm);
    await waitFor(() => expect(startCalls()).toHaveLength(2));
    expect(startCalls().map((c) => c.url).sort()).toEqual([
      '/api/profiles/docs/sync/start',
      '/api/profiles/music/sync/start',
    ]);
    for (const call of startCalls()) {
      expect(JSON.parse(call.body ?? '{}')).toEqual({ direction: 'two_way', force: true });
    }
  });
});

describe('Sync now with a preview that has no two_way part', () => {
  it('counts as a failed preview: the confirm stays disabled until acknowledged', async () => {
    const twoWay: ProfileStatus = { ...profile('docs', 'Docs'), sync_mode: 'two_way' };
    const base = globalThis.fetch;
    globalThis.fetch = vi.fn((input: string | URL | Request, init?: globalThis.RequestInit) => {
      const url = String(input);
      if (url === '/api/profiles') return jsonResponse([twoWay]);
      if (url.endsWith('/sync/preview')) {
        calls.push({ url, method: 'POST' });
        // An answer without the two-way dry run (two_way: null).
        return jsonResponse({
          push: counts(0, 0), pull: counts(0, 0), excluded: 0, max_delete: 5, error: null, sync_mode: 'two_way', two_way: null,
        });
      }
      return base(input, init);
    }) as unknown as typeof fetch;
    const user = userEvent.setup();
    renderControls(<SyncControls />);

    const syncNow = await screen.findByRole('button', { name: 'Sync now' });
    await waitFor(() => expect(syncNow).toBeEnabled());
    await user.click(syncNow);

    const dialog = await screen.findByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: 'Sync now' });
    const ack = await within(dialog).findByLabelText(/runs without a preview/);
    expect(confirm).toBeDisabled();
    await user.click(confirm);
    expect(startCalls()).toEqual([]);

    await user.click(ack);
    await user.click(confirm);
    await waitFor(() => expect(startCalls()).toHaveLength(1));
    expect(JSON.parse(startCalls()[0].body ?? '{}')).toEqual({ direction: 'two_way', force: true });
  });
});

describe('previewFor', () => {
  const preview = {
    push: counts(2, 1), pull: counts(1, 1), excluded: 0, max_delete: 50, error: null, sync_mode: 'mirror' as const, two_way: null,
  };
  it('picks the counts of the chosen direction', () => {
    expect(previewFor('push', preview).deletes).toBe(2);
    expect(previewFor('pull', preview).deletes).toBe(1);
  });
});
