import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nProvider } from '@/i18n';
import { SidebarControlsProvider } from '@/components/layout/sidebar-controls';
import { api } from '@/lib/api';
import { ProfileCard } from '@/components/profiles/profile-card';
import type { ProfileStatus, SyncPreview } from '@/types';

vi.mock('next/navigation', () => ({
  useParams:       () => ({ slug: 'docs' }),
  useRouter:       () => ({ push: vi.fn(), back: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(''),
}));

vi.mock('@/lib/api', () => ({
  api: {
    getProfile:         vi.fn(),
    getBackupTargets:   vi.fn(),
    getRemotes:         vi.fn(),
    previewProfileSync: vi.fn(),
    startProfileSync:   vi.fn(),
    resyncProfile:      vi.fn(),
    updateProfile:      vi.fn(),
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
  backup_dir:            null,
  max_retries:           3,
  enabled:               true,
  created_at:            '2026-01-01T00:00:00Z',
  updated_at:            '2026-01-01T00:00:00Z',
  sync_mode:             'two_way',
  state:                 'idle',
  last_sync:             null,
  current_job_id:        null,
  files_processed:       0,
  errors:                0,
  pending_changes:       0,
  intervals_paused:      false,
  paused_at:             null,
  last_error:            null,
  max_delete:            10,
  resync_required:       false,
};

const counts = (deletes: number, replaces: number, creates: number, exceeds = false) => ({
  deletes, replaces, creates, exceeds_max_delete: exceeds,
});

const PREVIEW: SyncPreview = {
  push:       counts(0, 0, 0),
  pull:       counts(0, 0, 0),
  excluded:   0,
  max_delete: 10,
  error:      null,
  sync_mode:  'two_way',
  two_way:    {
    local:           counts(2, 1, 4),
    remote:          counts(12, 0, 1, true),
    conflicts:       3,
    resync:          false,
    resync_required: false,
    error:           null,
  },
};

function renderPage (ui: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
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

async function renderProfile (profile: ProfileStatus) {
  mockedApi.getProfile.mockResolvedValue(profile);
  const { default: ProfileDetailPage } = await import('@/app/profiles/[slug]/page');
  renderPage(<ProfileDetailPage />);
  await screen.findByRole('heading', { name: 'Docs' });
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.getBackupTargets.mockResolvedValue([]);
  mockedApi.getRemotes.mockResolvedValue([]);
  mockedApi.previewProfileSync.mockResolvedValue(PREVIEW);
  mockedApi.startProfileSync.mockResolvedValue({ job_id: 1, state: 'syncing' });
  mockedApi.resyncProfile.mockResolvedValue({ job_id: 2, state: 'syncing' });
  mockedApi.updateProfile.mockResolvedValue({ ...PROFILE, sync_mode: 'two_way' });
});

describe('Two-way profile page', () => {
  it('shows the mode badge and no switch hint', async () => {
    await renderProfile(PROFILE);
    expect(screen.getByTestId('sync-mode-badge')).toHaveTextContent('Two-way');
    expect(screen.queryByTestId('switch-to-two-way')).not.toBeInTheDocument();
  });

  it('Sync now starts a two-way sync right away when the profile is not paused', async () => {
    const user = userEvent.setup();
    await renderProfile(PROFILE);

    await user.click(screen.getByRole('button', { name: 'Sync now' }));

    await waitFor(() => expect(mockedApi.startProfileSync).toHaveBeenCalledExactlyOnceWith('docs', 'two_way', false));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('Sync now of a paused profile asks first, shows the two-way preview, then sends force', async () => {
    const user = userEvent.setup();
    await renderProfile({ ...PROFILE, intervals_paused: true, paused_at: '2026-01-01T00:00:00Z' });

    await user.click(screen.getByRole('button', { name: 'Sync now' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Sync Docs both ways?')).toBeInTheDocument();
    const preview = await within(dialog).findByTestId('two-way-preview');
    expect(within(preview).getByText('Local folder')).toBeInTheDocument();
    expect(within(preview).getByText('Remote folder')).toBeInTheDocument();
    expect(within(preview).getByText('2 files would be deleted')).toBeInTheDocument();
    expect(within(preview).getByText('12 files would be deleted')).toBeInTheDocument();
    expect(within(preview).getByText('4 new files would be copied')).toBeInTheDocument();
    expect(within(preview).getByText('1 new file would be copied')).toBeInTheDocument();
    expect(within(preview).getByText('3 files were changed on both sides: both versions will be kept')).toBeInTheDocument();
    // Only the remote side goes over the limit.
    expect(within(preview).getAllByText(/More than 10 files would be deleted here/)).toHaveLength(1);
    expect(within(preview).getByText('Delete limit: 10 files per side')).toBeInTheDocument();
    expect(mockedApi.startProfileSync).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: 'Sync now' }));
    await waitFor(() => expect(mockedApi.startProfileSync).toHaveBeenCalledExactlyOnceWith('docs', 'two_way', true));
  });

  it('Resync needs a confirmation that explains it; Cancel sends nothing', async () => {
    const user = userEvent.setup();
    await renderProfile(PROFILE);

    await user.click(screen.getByRole('button', { name: 'Resync' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Resync Docs?')).toBeInTheDocument();
    expect(within(dialog).getByText(/Nothing is deleted/)).toBeInTheDocument();
    expect(mockedApi.resyncProfile).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mockedApi.resyncProfile).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Resync' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Resync now' }));
    await waitFor(() => expect(mockedApi.resyncProfile).toHaveBeenCalledExactlyOnceWith('docs'));
  });

  it('a profile that needs a resync explains why, disables Sync now and offers Resync', async () => {
    await renderProfile({
      ...PROFILE,
      resync_required:  true,
      intervals_paused: true,
      last_error:       'The two-way sync state is missing.',
    });

    const notice = screen.getByTestId('resync-required');
    expect(within(notice).getByText('Resync needed')).toBeInTheDocument();
    expect(within(notice).getByText(/The two-way sync state is missing\./)).toBeInTheDocument();
    expect(within(notice).getByRole('button', { name: 'Resync' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Sync now' })).toBeDisabled();
    // One Resync action, the prominent one.
    expect(screen.getAllByRole('button', { name: 'Resync' })).toHaveLength(1);
  });
});

describe('Mirror profile page', () => {
  const MIRROR: ProfileStatus = { ...PROFILE, sync_mode: 'mirror' };

  it('has no Sync now or Resync, shows the Mirror badge and the mirror-mode note', async () => {
    await renderProfile(MIRROR);
    expect(screen.getByTestId('sync-mode-badge')).toHaveTextContent('Mirror');
    expect(screen.queryByRole('button', { name: 'Sync now' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Resync' })).not.toBeInTheDocument();
    const note = screen.getByTestId('mirror-notice');
    expect(note).toHaveTextContent('This profile syncs as a mirror');
    expect(note).toHaveTextContent(/overwrites or deletes changes that exist only on the other side/);
    expect(note).toHaveTextContent(/paused or offline/);
    expect(note).toHaveTextContent(/first run is a resync: both folders become the union of both, and nothing is deleted/);
  });

  it('hiding the note stores it on the profile', async () => {
    const user = userEvent.setup();
    await renderProfile(MIRROR);
    await user.click(within(screen.getByTestId('mirror-notice')).getByRole('button', { name: 'Hide this note' }));
    await waitFor(() => expect(mockedApi.updateProfile).toHaveBeenCalledExactlyOnceWith('docs', { mirror_notice_dismissed: true }));
  });

  it('a hidden note leaves only the short switch hint', async () => {
    await renderProfile({ ...MIRROR, mirror_notice_dismissed: true });
    expect(screen.queryByTestId('mirror-notice')).not.toBeInTheDocument();
    expect(screen.getByTestId('switch-to-two-way')).toHaveTextContent(/Two-way keeps the changes from both sides/);
    expect(screen.getByRole('button', { name: 'Switch to two-way' })).toBeInTheDocument();
  });

  it('switching to two-way asks first, then sends sync_mode two_way', async () => {
    const user = userEvent.setup();
    await renderProfile(MIRROR);

    await user.click(within(screen.getByTestId('switch-to-two-way')).getByRole('button', { name: 'Switch to two-way' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Switch Docs to two-way?')).toBeInTheDocument();
    expect(within(dialog).getByText(/The first two-way sync is a resync/)).toBeInTheDocument();
    expect(mockedApi.updateProfile).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: 'Switch to two-way' }));
    await waitFor(() => expect(mockedApi.updateProfile).toHaveBeenCalledExactlyOnceWith('docs', { sync_mode: 'two_way' }));
  });
});

describe('Profile card', () => {
  function renderCard (profile: ProfileStatus) {
    const onSync = vi.fn();
    const onSyncNow = vi.fn();
    renderPage(<ProfileCard profile={profile} onToggle={vi.fn()} onDelete={vi.fn()} onSync={onSync} onSyncNow={onSyncNow} />);
    return { onSync, onSyncNow };
  }

  it('a two-way card shows the mode and Sync now', async () => {
    const user = userEvent.setup();
    const { onSyncNow } = renderCard(PROFILE);
    expect(screen.getByTestId('sync-mode-badge')).toHaveTextContent('Two-way');
    await user.click(screen.getByRole('button', { name: 'Sync now' }));
    expect(onSyncNow).toHaveBeenCalledExactlyOnceWith(PROFILE);
  });

  it('a card that needs a resync offers Resync instead of Sync now', () => {
    renderCard({ ...PROFILE, resync_required: true });
    expect(screen.getByText('Resync needed')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Resync' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sync now' })).not.toBeInTheDocument();
  });

  it('a mirror card shows the mode and only Push and Pull', () => {
    renderCard({ ...PROFILE, sync_mode: 'mirror' });
    expect(screen.getByTestId('sync-mode-badge')).toHaveTextContent('Mirror');
    expect(screen.queryByRole('button', { name: 'Sync now' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Push' })).toBeInTheDocument();
  });
});
