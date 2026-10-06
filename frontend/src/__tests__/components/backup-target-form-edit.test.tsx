import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import { BackupTargetForm } from '@/components/profiles/backup-target-form';
import type { BackupTarget } from '@/types';

// Radix Switch measures itself; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe () {}
  unobserve () {}
  disconnect () {}
} as unknown as typeof ResizeObserver;

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

const TARGET: BackupTarget = {
  id:                  4,
  profile_id:          1,
  name:                'Nightly',
  target_path:         '/backups/docs',
  target_type:         'local',
  remote_name:         null,
  retention_days:      30,
  keep_last:           3,
  frequency_hours:     24,
  backup_mode:         'archive',
  enabled:             true,
  last_liveness_ok:    null,
  last_liveness_error: null,
  last_backup_at:      null,
  last_backup_status:  null,
  next_scheduled_at:   null,
  overdue:             false,
  encrypted:           false,
  verify_after_backup: true,
  last_verify_status:  null,
  last_verify_message: null,
  created_at:          '2026-01-01T00:00:00Z',
  updated_at:          '2026-01-01T00:00:00Z',
};

const originalFetch = globalThis.fetch;
let fetchMock: ReturnType<typeof vi.fn>;
let remotesOk: boolean;

function respond (data: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(data) });
}

beforeEach(() => {
  vi.clearAllMocks();
  remotesOk = true;
  fetchMock = vi.fn((url: string, init?: { method?: string; body?: string }) => {
    if (url === '/api/remotes') {
      return remotesOk ? respond([{ name: 'gdrive', type: 'drive', last_verified: null }]) : respond({ detail: 'down' }, 500);
    }
    if (url === '/api/profiles/docs' && !init?.method) {
      return respond({ slug: 'docs', name: 'Docs', local_dir: '/data/docs', remote_dir: 'gdrive:Docs', state: 'idle' });
    }
    if (url === '/api/profiles/docs/backups/4' && init?.method === 'PUT') {
      return respond({ ...TARGET, ...JSON.parse(init.body ?? '{}') });
    }
    return respond([]);
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function renderForm (target: BackupTarget | null = TARGET) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}><I18nProvider>{children}</I18nProvider></QueryClientProvider>
  );
  const onOpenChange = vi.fn();
  render(<BackupTargetForm profileSlug="docs" target={target} open onOpenChange={onOpenChange} />, { wrapper });
  return { dialog: screen.getByRole('dialog'), onOpenChange };
}

const puts = () => fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT');

describe('BackupTargetForm editing a target', () => {
  it('opens with the saved values and sends the changed ones', async () => {
    const user = userEvent.setup();
    const { dialog, onOpenChange } = renderForm();
    expect(within(dialog).getByRole('heading', { name: 'Edit Backup Target' })).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Name')).toHaveValue('Nightly');

    await user.click(within(dialog).getByLabelText(/^Mirror/, { selector: 'input[type="radio"]' }));
    fireEvent.change(within(dialog).getByLabelText('Retention (days)'), { target: { value: '60' } });
    fireEvent.change(within(dialog).getByLabelText('Frequency (hours)'), { target: { value: '6' } });
    fireEvent.change(within(dialog).getByLabelText('Always keep (newest snapshots)'), { target: { value: '5' } });
    await user.click(within(dialog).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(JSON.parse(puts()[0][1].body)).toMatchObject({
      name:            'Nightly',
      backup_mode:     'mirror',
      retention_days:  60,
      frequency_hours: 6,
      keep_last:       5,
    });
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Backup target "Nightly" updated'));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('cancel closes without saving', async () => {
    const user = userEvent.setup();
    const { dialog, onOpenChange } = renderForm();
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(puts()).toHaveLength(0);
  });
});

describe('BackupTargetForm remote list', () => {
  it('retries the remote list for a custom remote target', async () => {
    remotesOk = false;
    const user = userEvent.setup();
    const { dialog } = renderForm(null);
    await user.click(within(dialog).getByLabelText('Custom remote'));
    const retry = await within(dialog).findByRole('button', { name: 'Retry' });
    remotesOk = true;
    await user.click(retry);
    expect(await within(dialog).findByRole('button', { name: /gdrive/ })).toBeInTheDocument();
  });
});
