import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nProvider } from '@/i18n';
import { ProfileForm } from '@/components/profiles/profile-form';
import { TestSyncButton } from '@/components/profiles/test-sync-button';
import type { Profile } from '@/types';

globalThis.ResizeObserver ??= class {
  observe () {}
  unobserve () {}
  disconnect () {}
} as unknown as typeof ResizeObserver;

const EXISTING: Profile = {
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
};

const OK = { success: true, steps: [{ step: 'local_write', ok: true }], error: null };
const FAILED = {
  success: false,
  steps:   [{ step: 'local_write', ok: true }, { step: 'remote_upload', ok: false }],
  error:   'Could not upload the test file to the remote. See the server log for details.',
};

let fetchMock: ReturnType<typeof vi.fn>;
const originalFetch = globalThis.fetch;

function respond (body: unknown) {
  return { ok: true, status: 200, json: () => Promise.resolve(body) };
}

beforeEach(() => {
  fetchMock = vi.fn().mockResolvedValue(respond([]));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function wrapper ({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nProvider>{children}</I18nProvider>
    </QueryClientProvider>
  );
}

const testCalls = () => fetchMock.mock.calls.filter(([url]) => String(url).includes('test-sync'));

describe('TestSyncButton', () => {
  it('tests the given folders and shows success', async () => {
    fetchMock.mockImplementation(async (url: string) => respond(url.includes('test-sync') ? OK : []));
    const user = userEvent.setup();
    render(<TestSyncButton localDir="/data/docs" remoteDir="gdrive:docs" />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Test Sync' }));
    expect(await screen.findByText(/Sync test passed/)).toBeInTheDocument();
    const [url, init] = testCalls()[0];
    expect(url).toBe('/api/config/test-sync');
    expect(JSON.parse(init.body)).toEqual({ local_dir: '/data/docs', remote_dir: 'gdrive:docs' });
  });

  it('tests a saved profile through its own endpoint and shows the failed step', async () => {
    fetchMock.mockImplementation(async (url: string) => respond(url.includes('test-sync') ? FAILED : []));
    const user = userEvent.setup();
    render(<TestSyncButton slug="my docs" localDir="/data/docs" remoteDir="gdrive:docs" />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Test Sync' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Sync test failed');
    expect(alert).toHaveTextContent('Could not upload the test file');
    expect(alert).toHaveTextContent('Upload to the remote folder');
    expect(alert).toHaveTextContent('failed');
    expect(testCalls()[0][0]).toBe('/api/profiles/my%20docs/config/test-sync');
    expect(testCalls()[0][1].method).toBe('POST');
  });

  it('shows the request error and is disabled without both folders', async () => {
    fetchMock.mockImplementation(async () => ({ ok: false, status: 503, json: () => Promise.resolve({ detail: 'rclone service not available' }) }));
    const user = userEvent.setup();
    const { rerender } = render(<TestSyncButton localDir="" remoteDir="gdrive:docs" />, { wrapper });
    expect(screen.getByRole('button', { name: 'Test Sync' })).toBeDisabled();

    rerender(<TestSyncButton localDir="/data" remoteDir="gdrive:docs" />);
    await user.click(screen.getByRole('button', { name: 'Test Sync' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('rclone service not available');
  });
});

describe('ProfileForm test sync and remote setup', () => {
  it('tests unchanged folders through the profile and edited ones as typed', async () => {
    fetchMock.mockImplementation(async (url: string) => respond(url.includes('test-sync') ? OK : []));
    const user = userEvent.setup();
    render(<ProfileForm profile={EXISTING} onSubmit={vi.fn()} onCancel={vi.fn()} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Test Sync' }));
    await screen.findByText(/Sync test passed/);
    expect(testCalls()[0][0]).toBe('/api/profiles/docs/config/test-sync');

    const remote = screen.getByLabelText('Remote directory');
    await user.clear(remote);
    await user.type(remote, 'gdrive:other');
    // The old result belonged to the old folders.
    expect(screen.queryByText(/Sync test passed/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Test Sync' }));
    await waitFor(() => expect(testCalls()).toHaveLength(2));
    expect(testCalls()[1][0]).toBe('/api/config/test-sync');
    expect(JSON.parse(testCalls()[1][1].body)).toEqual({ local_dir: '/data/docs', remote_dir: 'gdrive:other' });
  });

  it('offers the setup wizard when no remote exists, without submitting the form', async () => {
    fetchMock.mockImplementation(async () => respond([]));
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<ProfileForm onSubmit={onSubmit} onCancel={vi.fn()} />, { wrapper });

    await user.click(await screen.findByRole('button', { name: /Setup Wizard/ }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
