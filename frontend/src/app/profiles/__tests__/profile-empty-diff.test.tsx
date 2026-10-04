import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nProvider } from '@/i18n';
import { SidebarControlsProvider } from '@/components/layout/sidebar-controls';
import { api } from '@/lib/api';
import type { ProfileStatus } from '@/types';

let search = 'tab=differences';

vi.mock('next/navigation', () => ({
  useParams:       () => ({ slug: 'docs' }),
  useRouter:       () => ({ push: vi.fn(), back: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(search),
}));

vi.mock('@/lib/api', () => ({
  api: {
    getProfile:         vi.fn(),
    getBackupTargets:   vi.fn(),
    getProfileDiff:     vi.fn(),
    getRemotes:         vi.fn(),
    checkProfileSync:   vi.fn(),
    previewProfileSync: vi.fn(),
    startProfileSync:   vi.fn(),
  },
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

const mockedApi = vi.mocked(api);

const PROFILE: ProfileStatus = {
  id:                    1,
  slug:                  'docs',
  name:                  'Docs',
  local_dir:             '/data/docs',
  remote_dir:            'gdrive:docs',
  debounce_seconds:      5,
  pull_interval_minutes: 5,
  rclone_filter:         [],
  rclone_args:           [],
  max_retries:           3,
  enabled:               true,
  created_at:            '2026-01-01T00:00:00Z',
  updated_at:            '2026-01-01T00:00:00Z',
  state:                 'idle',
  last_sync:             null,
  current_job_id:        null,
  files_processed:       0,
  errors:                0,
  // The stored count can be stale: the diff below is empty.
  pending_changes:       3,
  intervals_paused:      false,
  paused_at:             null,
  last_error:            null,
  max_delete:            50,
  resync_required:       false,
  sync_mode:             'mirror',
};

function renderPage (ui: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <SidebarControlsProvider value={{ isSidebarCollapsed: false, toggleSidebar: () => {} }}>
          {ui}
        </SidebarControlsProvider>
      </I18nProvider>
    </QueryClientProvider>
  );
}

describe('Profile page with an empty diff', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getProfile.mockResolvedValue(PROFILE);
    mockedApi.getBackupTargets.mockResolvedValue([]);
    mockedApi.getRemotes.mockResolvedValue([]);
    mockedApi.previewProfileSync.mockResolvedValue({
      push:       { deletes: 1, replaces: 0, creates: 0, exceeds_max_delete: false },
      pull:       { deletes: 0, replaces: 0, creates: 1, exceeds_max_delete: false },
      excluded:   0,
      max_delete: 25,
      error:      null,
      sync_mode:  'mirror',
      two_way:    null,
    });
    mockedApi.startProfileSync.mockResolvedValue({ job_id: 1, state: 'pushing' });
    mockedApi.getProfileDiff.mockResolvedValue({
      files:      [],
      summary:    { local_only: 0, remote_only: 0, modified_local: 0, modified_remote: 0, modified_both: 0, manual: 0, total: 0 },
      error:      null,
      pagination: { offset: 0, limit: 100, total: 0, has_more: false },
    });
  });

  it('shows "no differences" instead of crashing', async () => {
    search = 'tab=differences';
    const { default: ProfileDetailPage } = await import('@/app/profiles/[slug]/page');
    renderPage(<ProfileDetailPage />);

    expect(await screen.findByText('No differences to resolve', undefined, { timeout: 5000 })).toBeInTheDocument();
    expect(mockedApi.getProfileDiff).toHaveBeenCalledTimes(1);
  });

  it('does not compute the diff until the Differences tab is opened', async () => {
    search = '';
    const user = userEvent.setup();
    const { default: ProfileDetailPage } = await import('@/app/profiles/[slug]/page');
    renderPage(<ProfileDetailPage />);

    await screen.findByText('Docs');
    expect(mockedApi.getProfileDiff).not.toHaveBeenCalled();

    await user.click(screen.getByRole('tab', { name: /Differences/ }));
    await waitFor(() => expect(mockedApi.getProfileDiff).toHaveBeenCalledTimes(1));
  });

  it('Push opens a confirmation naming the profile; Cancel sends no sync request', async () => {
    search = '';
    const user = userEvent.setup();
    const { default: ProfileDetailPage } = await import('@/app/profiles/[slug]/page');
    renderPage(<ProfileDetailPage />);

    await user.click(await screen.findByRole('button', { name: 'Push' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Push Docs?')).toBeInTheDocument();
    expect(await within(dialog).findByText('1 file would be deleted from the remote')).toBeInTheDocument();
    expect(within(dialog).getByText('Delete limit: 25 files')).toBeInTheDocument();
    // Counting uses the side-effect-free preview, which does not pause the profile.
    expect(mockedApi.checkProfileSync).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mockedApi.startProfileSync).not.toHaveBeenCalled();
  });

  it('a confirmed push of a paused profile is sent with force', async () => {
    search = '';
    mockedApi.getProfile.mockResolvedValue({ ...PROFILE, intervals_paused: true, paused_at: '2026-01-01T00:00:00Z' });
    const user = userEvent.setup();
    const { default: ProfileDetailPage } = await import('@/app/profiles/[slug]/page');
    renderPage(<ProfileDetailPage />);

    await user.click(await screen.findByRole('button', { name: 'Push' }));
    const dialog = await screen.findByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: 'Push now' });
    await waitFor(() => expect(confirm).toBeEnabled());
    await user.click(confirm);

    await waitFor(() => expect(mockedApi.startProfileSync).toHaveBeenCalledExactlyOnceWith('docs', 'push', true));
  });

  it('shows why the last sync failed from the profile itself, without another status call', async () => {
    search = '';
    mockedApi.getProfile.mockResolvedValue({ ...PROFILE, state: 'error', last_error: 'Local folder is not mounted.' });
    const { default: ProfileDetailPage } = await import('@/app/profiles/[slug]/page');
    renderPage(<ProfileDetailPage />);

    expect(await screen.findByText('Local folder is not mounted.')).toBeInTheDocument();
    expect(mockedApi).not.toHaveProperty('getProfileSyncStatus');
  });
});
