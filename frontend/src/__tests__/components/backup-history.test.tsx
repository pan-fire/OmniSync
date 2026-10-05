import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'sonner';
import { I18nProvider, type Locale } from '@/i18n';
import { BackupHistory } from '@/components/profiles/backup-history';
import { formatBytes } from '@/lib/format';
import type { BackupJob, Snapshot } from '@/types';

// The restore dialog's Radix checkbox measures itself; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe () {}
  unobserve () {}
  disconnect () {}
} as unknown as typeof ResizeObserver;

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

const SNAP: Snapshot = {
  snapshot_id: '2026-09-01T10-00-00',
  created_at:  '2026-09-01T10:00:00Z',
  size_bytes:  1024,
  status:      'completed',
  latest:      true,
};

const BASE = '/api/profiles/docs/backups/3';

function job (over: Partial<BackupJob>): BackupJob {
  return {
    id:            1,
    target_id:     3,
    started_at:    '2026-09-01T10:00:00Z',
    finished_at:   '2026-09-01T10:01:00Z',
    status:        'completed',
    direction:     'backup',
    size_bytes:    null,
    snapshot_id:   null,
    error_message: null,
    ...over,
  };
}

const originalFetch = globalThis.fetch;
let fetchMock: ReturnType<typeof vi.fn>;
let snapshotsBody: unknown;
let snapshotsStatus: number;

function respond (data: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(data) });
}

const posts = (suffix: string) => fetchMock.mock.calls.filter(
  ([url, init]) => init?.method === 'POST' && (url as string).endsWith(suffix)
);

beforeEach(() => {
  vi.clearAllMocks();
  snapshotsBody = [SNAP];
  snapshotsStatus = 200;
  fetchMock = vi.fn((url: string, init?: { method?: string }) => {
    if (url === `${BASE}/snapshots`) return respond(snapshotsBody, snapshotsStatus);
    if (url.startsWith(`${BASE}/snapshots/`)) {
      return respond({ snapshot_id: SNAP.snapshot_id, path: '', search: null, entries: [], total: 0, offset: 0, limit: 100, snapshot_files: 0 });
    }
    if (url.endsWith('/restore/preview')) return respond({ snapshot_id: SNAP.snapshot_id, restore_scope: 'local_only', sides: [] });
    if (init?.method === 'POST' && url.endsWith('/restore')) {
      return respond(job({ id: 9, direction: 'restore', snapshot_id: SNAP.snapshot_id }), 202);
    }
    return respond([]);
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function renderHistory (jobs?: BackupJob[], locale: Locale = 'en') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  function Wrapper ({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}><I18nProvider initialLocale={locale}>{children}</I18nProvider></QueryClientProvider>;
  }
  return render(<BackupHistory profileSlug="docs" targetId={3} jobs={jobs} />, { wrapper: Wrapper });
}

describe('BackupHistory states', () => {
  it('shows loading, then says there is no history yet', async () => {
    snapshotsBody = [];
    renderHistory([]);
    expect(screen.getByRole('status')).toHaveTextContent('Loading...');
    expect(await screen.findByText('No backup history yet')).toBeInTheDocument();
  });

  it('explains snapshots that could not be loaded', async () => {
    snapshotsBody = { detail: 'Target unreachable' };
    snapshotsStatus = 503;
    renderHistory();
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the snapshots: Target unreachable');
    expect(screen.queryByText('No backup history yet')).not.toBeInTheDocument();
  });

  it('says there is no history in Persian', async () => {
    snapshotsBody = [];
    renderHistory(undefined, 'fa');
    expect(await screen.findByText('هنوز تاریخچه پشتیبان‌گیری وجود ندارد')).toBeInTheDocument();
  });
});

describe('BackupHistory recent jobs', () => {
  it('shows each job status, size, error and verification', async () => {
    snapshotsBody = [];
    renderHistory([
      job({ id: 1, status: 'completed', size_bytes: 2048, verify_status: 'verified', verify_message: 'all 3 files match' }),
      job({ id: 2, status: 'failed', error_message: 'Remote full', error_code: 'quota', verify_status: 'failed', verify_message: '1 file differs' }),
      job({ id: 3, status: 'running' }),
      job({ id: 4, status: 'skipped', error_message: 'Not reachable' }),
    ]);

    expect(await screen.findByText('Recent Jobs')).toBeInTheDocument();
    expect(screen.getByText('completed')).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
    expect(screen.getByText('running')).toBeInTheDocument();
    expect(screen.getByText('skipped')).toBeInTheDocument();
    expect(screen.getByText(formatBytes(2048, 'en'))).toBeInTheDocument();
    // The error code is in the tooltip; a job without one shows the bare text.
    expect(screen.getByText('Remote full')).toHaveAttribute('title', 'Remote full (quota)');
    expect(screen.getByText('Not reachable')).toHaveAttribute('title', 'Not reachable');
    expect(screen.getByText('Verified').closest('[title]')).toHaveAttribute('title', 'all 3 files match');
    expect(screen.getByText('Verification failed').closest('[title]')).toHaveAttribute('title', '1 file differs');
    expect(screen.queryByText('No backup history yet')).not.toBeInTheDocument();
  });
});

describe('BackupHistory snapshot actions', () => {
  it('marks the latest snapshot and opens the browser, then closes it', async () => {
    const user = userEvent.setup();
    renderHistory();
    expect(await screen.findByText('Latest backup')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Browse' }));
    const dialog = await screen.findByRole('dialog', { name: 'Browse snapshot' });
    expect(await within(dialog).findByText('This folder is empty.')).toBeInTheDocument();

    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  // Restoring overwrites files, so it only happens after the user confirms.
  it('restores a snapshot only after the overwrite is confirmed', async () => {
    const user = userEvent.setup();
    renderHistory();

    await user.click(await screen.findByRole('button', { name: 'Restore' }));
    const dialog = await screen.findByRole('dialog', { name: 'Restore from Snapshot' });
    const confirm = within(dialog).getByRole('button', { name: 'Restore' });
    expect(confirm).toBeDisabled();
    expect(posts('/restore')).toHaveLength(0);

    await user.click(within(dialog).getByRole('checkbox', { name: 'I understand that the restore overwrites these files' }));
    expect(confirm).toBeEnabled();
    await user.click(confirm);

    await waitFor(() => expect(posts('/restore')).toHaveLength(1));
    expect(JSON.parse(posts('/restore')[0][1].body)).toEqual({ snapshot_id: SNAP.snapshot_id, restore_scope: 'local_only' });
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Restore completed'));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('cancelling the restore dialog restores nothing', async () => {
    const user = userEvent.setup();
    renderHistory();

    await user.click(await screen.findByRole('button', { name: 'Restore' }));
    const dialog = await screen.findByRole('dialog', { name: 'Restore from Snapshot' });
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(posts('/restore')).toHaveLength(0);
  });
});
