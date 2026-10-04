import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import { api } from '@/lib/api';
import { MirrorProfilesNotice, showsMirrorNotice } from '@/components/profiles/mirror-notice';
import type { ProfileStatus } from '@/types';

vi.mock('@/lib/api', () => ({
  api: {
    getProfiles:   vi.fn(),
    updateProfile: vi.fn(),
  },
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

const mockedApi = vi.mocked(api);

function profile (slug: string, name: string, extra: Partial<ProfileStatus> = {}): ProfileStatus {
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
    max_retries:           3,
    enabled:               true,
    created_at:            '2026-01-01T00:00:00Z',
    updated_at:            '2026-01-01T00:00:00Z',
    sync_mode:             'mirror',
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
    ...extra,
  };
}

const DOCS = profile('docs', 'Docs');
const PICS = profile('pics', 'Pictures', { mirror_notice_dismissed: true, enabled: false });
const MUSIC = profile('music', 'Music', { sync_mode: 'two_way' });

function renderNotice () {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <MirrorProfilesNotice />
      </I18nProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.getProfiles.mockResolvedValue([DOCS, PICS, MUSIC]);
  mockedApi.updateProfile.mockImplementation(async (slug) => ({ ...profile(slug, slug), sync_mode: 'two_way' }));
});

describe('showsMirrorNotice', () => {
  it('is true only for a mirror profile whose note is not hidden', () => {
    expect(showsMirrorNotice(DOCS)).toBe(true);
    expect(showsMirrorNotice(PICS)).toBe(false);
    expect(showsMirrorNotice(MUSIC)).toBe(false);
  });
});

describe('MirrorProfilesNotice (dashboard)', () => {
  it('lists the mirror profiles whose note is shown and explains the risk', async () => {
    renderNotice();
    const card = await screen.findByTestId('mirror-profiles-notice');
    expect(card).toHaveTextContent('Profiles that sync as a mirror');
    expect(card).toHaveTextContent(/can be overwritten or deleted/);
    expect(card).toHaveTextContent(/nothing is deleted/);
    expect(within(card).getByRole('link', { name: 'Docs' })).toHaveAttribute('href', '/profiles/docs');
    expect(within(card).queryByText('Pictures')).not.toBeInTheDocument();
    expect(within(card).queryByText('Music')).not.toBeInTheDocument();
  });

  it('renders nothing without a shown mirror note', async () => {
    mockedApi.getProfiles.mockResolvedValue([PICS, MUSIC]);
    renderNotice();
    await waitFor(() => expect(mockedApi.getProfiles).toHaveBeenCalled());
    expect(screen.queryByTestId('mirror-profiles-notice')).not.toBeInTheDocument();
  });

  it('hiding a profile note stores it on that profile', async () => {
    const user = userEvent.setup();
    renderNotice();
    await user.click(await screen.findByRole('button', { name: 'Hide the mirror-mode note for Docs' }));
    await waitFor(() => expect(mockedApi.updateProfile).toHaveBeenCalledExactlyOnceWith('docs', { mirror_notice_dismissed: true }));
  });

  it('switch all lists every mirror profile, asks once, then updates each one', async () => {
    const user = userEvent.setup();
    renderNotice();
    await user.click(await screen.findByRole('button', { name: 'Switch all mirror profiles to two-way' }));
    const dialog = await screen.findByRole('dialog');
    const list = within(dialog).getByTestId('switch-all-list');
    expect(list).toHaveTextContent('Docs');
    expect(list).toHaveTextContent('Pictures');
    expect(list).toHaveTextContent('disabled');
    expect(list).not.toHaveTextContent('Music');
    expect(dialog).toHaveTextContent(/The first two-way sync is a resync/);
    expect(mockedApi.updateProfile).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: 'Switch all to two-way' }));
    await waitFor(() => expect(mockedApi.updateProfile).toHaveBeenCalledTimes(2));
    expect(mockedApi.updateProfile).toHaveBeenNthCalledWith(1, 'docs', { sync_mode: 'two_way' });
    expect(mockedApi.updateProfile).toHaveBeenNthCalledWith(2, 'pics', { sync_mode: 'two_way' });
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(expect.stringMatching(/^2 profiles now sync two-way/)));
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('cancelling switch all changes nothing', async () => {
    const user = userEvent.setup();
    renderNotice();
    await user.click(await screen.findByRole('button', { name: 'Switch all mirror profiles to two-way' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(mockedApi.updateProfile).not.toHaveBeenCalled();
  });

  it('switch all reports the profiles that could not be switched', async () => {
    const user = userEvent.setup();
    mockedApi.updateProfile.mockImplementation(async (slug) => {
      if (slug === 'pics') throw new Error('overlap');
      return { ...profile(slug, slug), sync_mode: 'two_way' };
    });
    renderNotice();
    await user.click(await screen.findByRole('button', { name: 'Switch all mirror profiles to two-way' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Switch all to two-way' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Could not switch Pictures to two-way'));
    expect(toast.success).toHaveBeenCalledWith(expect.stringMatching(/^1 profile now syncs two-way/));
  });
});
