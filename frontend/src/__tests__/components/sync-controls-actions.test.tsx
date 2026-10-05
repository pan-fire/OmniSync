import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import { SyncControls } from '@/components/sync/sync-controls';
import { syncCheckQueryKey } from '@/hooks/use-profile-sync';
import { api } from '@/lib/api';
import type { AggregateStatus, ProfileStatus, ProfileSummary } from '@/types';

vi.mock('@/lib/api', () => ({
  api: {
    getProfiles:        vi.fn(),
    getAggregateStatus: vi.fn(),
    startProfileSync:   vi.fn(),
    stopProfileSync:    vi.fn(),
    checkProfileSync:   vi.fn(),
    previewProfileSync: vi.fn(),
    pauseAllProfiles:   vi.fn(),
    resumeAllProfiles:  vi.fn(),
  },
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock('@/components/sync/profile-diff-tabs', () => ({
  ProfileDiffTabs: () => <div data-testid="profile-diff-tabs" />,
}));

const mockedApi = vi.mocked(api);

function makeProfile (slug: string, name: string): ProfileStatus {
  return {
    id:                    1,
    slug,
    name,
    local_dir:             `/data/${slug}`,
    remote_dir:            `remote:${slug}`,
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
    pending_changes:       0,
    intervals_paused:      false,
    paused_at:             null,
    last_error:            null,
    max_delete:            50,
    resync_required:       false,
    sync_mode:             'mirror',
  };
}

function summary (slug: string, name: string, state: ProfileSummary['state']): ProfileSummary {
  return { slug, name, state, last_sync: null, pending_changes: 0, intervals_paused: false, last_error: null, resync_required: false };
}

function aggregate (profiles: ProfileSummary[]): AggregateStatus {
  return { overall_state: 'idle', total_pending_changes: 0, paused_profiles: [], profiles_summary: profiles };
}

function renderControls () {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  function Wrapper ({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}><I18nProvider>{children}</I18nProvider></QueryClientProvider>;
  }
  render(<SyncControls />, { wrapper: Wrapper });
  return queryClient;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.getProfiles.mockResolvedValue([makeProfile('docs', 'Docs'), makeProfile('photos', 'Photos')]);
});

describe('SyncControls check for changes', () => {
  beforeEach(() => {
    mockedApi.getAggregateStatus.mockResolvedValue(aggregate([summary('docs', 'Docs', 'idle'), summary('photos', 'Photos', 'idle')]));
  });

  // A failed check of one profile must not hide the results of the others.
  it('stores each result and names the profile whose check failed', async () => {
    const result = { has_changes: true, local_only: ['/data/docs/a.txt'], remote_only: [], differ: [], error: null };
    mockedApi.checkProfileSync.mockImplementation(async (slug: string) => {
      if (slug === 'photos') throw new Error('remote unreachable');
      return result;
    });
    const user = userEvent.setup();
    const queryClient = renderControls();

    const button = await screen.findByRole('button', { name: 'Check for changes' });
    await waitFor(() => expect(button).toBeEnabled());
    await user.click(button);

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Photos: remote unreachable'));
    expect(mockedApi.checkProfileSync).toHaveBeenCalledWith('docs');
    expect(mockedApi.checkProfileSync).toHaveBeenCalledWith('photos');
    expect(queryClient.getQueryData(syncCheckQueryKey('docs'))).toEqual(result);
    expect(queryClient.getQueryData(syncCheckQueryKey('photos'))).toBeUndefined();
    await waitFor(() => expect(button).toBeEnabled());
  });

  it('falls back to a generic message when the failure is not an Error', async () => {
    mockedApi.checkProfileSync.mockRejectedValue('boom');
    const user = userEvent.setup();
    renderControls();

    const button = await screen.findByRole('button', { name: 'Check for changes' });
    await waitFor(() => expect(button).toBeEnabled());
    await user.click(button);
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Docs: Something went wrong'));
    expect(toast.error).toHaveBeenCalledWith('Photos: Something went wrong');
  });
});

describe('SyncControls while a profile syncs', () => {
  beforeEach(() => {
    mockedApi.getAggregateStatus.mockResolvedValue(aggregate([summary('docs', 'Docs', 'pushing'), summary('photos', 'Photos', 'idle')]));
  });

  /** Clicks Stop and confirms in the dialog. */
  async function stopAndConfirm (user: ReturnType<typeof userEvent.setup>, confirm = 'Stop sync') {
    await user.click(await screen.findByRole('button', { name: 'Stop' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: confirm }));
  }

  it('disables push and pull and stops only the running profiles', async () => {
    mockedApi.stopProfileSync.mockResolvedValue(undefined as never);
    const user = userEvent.setup();
    renderControls();

    const stop = await screen.findByRole('button', { name: 'Stop' });
    expect(screen.getByRole('button', { name: 'Push' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Pull' })).toBeDisabled();
    await stopAndConfirm(user);

    await waitFor(() => expect(mockedApi.stopProfileSync).toHaveBeenCalledTimes(1));
    expect(mockedApi.stopProfileSync).toHaveBeenCalledWith('docs');
    expect(toast.error).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(stop).toBeEnabled());
  });

  // Stop cancels transfers in flight: like the TUI, it asks first and
  // names the running profile (not the idle one).
  it('asks before stopping and names the running profile', async () => {
    const user = userEvent.setup();
    renderControls();

    await user.click(await screen.findByRole('button', { name: 'Stop' }));
    const dialog = await screen.findByRole('dialog', { name: 'Stop the sync of "Docs"?' });
    expect(within(dialog).getByText(/Automatic syncing of this profile goes on afterwards/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/Photos/)).not.toBeInTheDocument();
    expect(mockedApi.stopProfileSync).not.toHaveBeenCalled();
  });

  it('cancel stops nothing', async () => {
    const user = userEvent.setup();
    renderControls();

    await user.click(await screen.findByRole('button', { name: 'Stop' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mockedApi.stopProfileSync).not.toHaveBeenCalled();
  });

  it('Escape stops nothing', async () => {
    const user = userEvent.setup();
    renderControls();

    await user.click(await screen.findByRole('button', { name: 'Stop' }));
    await screen.findByRole('dialog');
    await user.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mockedApi.stopProfileSync).not.toHaveBeenCalled();
  });

  it('names the profile that could not be stopped', async () => {
    mockedApi.stopProfileSync.mockRejectedValueOnce(new Error('not running'));
    const user = userEvent.setup();
    renderControls();

    await stopAndConfirm(user);
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Docs: not running'));
  });

  it('uses a generic message for a non-Error stop failure', async () => {
    mockedApi.stopProfileSync.mockRejectedValueOnce(42);
    const user = userEvent.setup();
    renderControls();

    await stopAndConfirm(user);
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Docs: Something went wrong'));
  });
});

describe('SyncControls while several profiles sync', () => {
  beforeEach(() => {
    mockedApi.getProfiles.mockResolvedValue([
      makeProfile('docs', 'Docs'), makeProfile('photos', 'Photos'), makeProfile('music', 'Music'),
    ]);
    mockedApi.getAggregateStatus.mockResolvedValue(aggregate([
      summary('docs', 'Docs', 'pushing'), summary('photos', 'Photos', 'syncing'), summary('music', 'Music', 'idle'),
    ]));
  });

  // "Stop all": one confirmation lists every running profile, and exactly
  // those are stopped.
  it('lists every running profile and stops them all on confirm', async () => {
    mockedApi.stopProfileSync.mockResolvedValue(undefined as never);
    const user = userEvent.setup();
    renderControls();

    await user.click(await screen.findByRole('button', { name: 'Stop' }));
    const dialog = await screen.findByRole('dialog', { name: 'Stop 2 running syncs?' });
    const listed = within(within(dialog).getByTestId('stop-sync-profiles')).getAllByRole('listitem');
    expect(listed.map((li) => li.textContent)).toEqual(['Docs', 'Photos']);
    expect(mockedApi.stopProfileSync).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: 'Stop syncs' }));
    await waitFor(() => expect(mockedApi.stopProfileSync).toHaveBeenCalledTimes(2));
    expect(mockedApi.stopProfileSync).toHaveBeenCalledWith('docs');
    expect(mockedApi.stopProfileSync).toHaveBeenCalledWith('photos');
    expect(mockedApi.stopProfileSync).not.toHaveBeenCalledWith('music');
  });
});

describe('SyncControls pull needs confirmation', () => {
  beforeEach(() => {
    mockedApi.getAggregateStatus.mockResolvedValue(aggregate([summary('docs', 'Docs', 'idle')]));
    mockedApi.getProfiles.mockResolvedValue([makeProfile('docs', 'Docs')]);
    mockedApi.previewProfileSync.mockResolvedValue({
      push:       { deletes: 0, replaces: 0, creates: 0, exceeds_max_delete: false },
      pull:       { deletes: 3, replaces: 1, creates: 0, exceeds_max_delete: false },
      excluded:   0,
      max_delete: 50,
      error:      null,
      sync_mode:  'mirror',
      two_way:    null,
    });
    mockedApi.startProfileSync.mockResolvedValue({ job_id: 7, state: 'error', error: 'stub' } as never);
  });

  it('sends nothing until Pull now is clicked, then pulls with force', async () => {
    const user = userEvent.setup();
    renderControls();

    const pull = await screen.findByRole('button', { name: 'Pull' });
    await waitFor(() => expect(pull).toBeEnabled());
    await user.click(pull);
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('3 files would be deleted from the local folder')).toBeInTheDocument();
    expect(mockedApi.startProfileSync).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: 'Pull now' }));
    await waitFor(() => expect(mockedApi.startProfileSync).toHaveBeenCalledWith('docs', 'pull', true));
  });
});
