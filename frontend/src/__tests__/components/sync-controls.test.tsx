import { describe, it, expect, vi, beforeEach } from 'vitest';
import fc from 'fast-check';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nProvider } from '@/i18n';
import { isActiveState, SyncControls } from '@/components/sync/sync-controls';
import type { SyncState } from '@/types';
import { api } from '@/lib/api';

vi.mock('@/lib/api', () => ({
  api: {
    getProfiles:        vi.fn(),
    getAggregateStatus: vi.fn(),
    startProfileSync:   vi.fn(),
    stopProfileSync:    vi.fn(),
    checkProfileSync:   vi.fn(),
    pauseAllProfiles:   vi.fn(),
    resumeAllProfiles:  vi.fn(),
  },
}));

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    warning: vi.fn(),
    error:   vi.fn(),
  },
}));

// ProfileDiffTabs is unit-tested separately — stub it here
vi.mock('@/components/sync/profile-diff-tabs', () => ({
  ProfileDiffTabs: ({ profiles }: { profiles: unknown[] }) => (
    <div data-testid="profile-diff-tabs">{profiles.length} profiles</div>
  ),
}));

const mockedApi = vi.mocked(api);

function createWrapper () {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return function Wrapper ({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <I18nProvider>{children}</I18nProvider>
      </QueryClientProvider>
    );
  };
}

const syncStateArb = fc.constantFrom<SyncState>('idle', 'pushing', 'pulling', 'error');

describe('Sync control button states reflect sync activity', () => {
  it('for any active state, Push/Pull should be disabled (isActive=true); for inactive, enabled (isActive=false)', () => {
    fc.assert(
      fc.property(syncStateArb, (state) => {
        const active = isActiveState(state);
        if (state === 'pushing' || state === 'pulling') {
          expect(active).toBe(true);
        } else {
          expect(active).toBe(false);
        }
      }),
      { numRuns: 100 }
    );
  });

  it('undefined state is not active', () => {
    expect(isActiveState(undefined)).toBe(false);
  });
});

// --- diff-ux: SyncControls delegates to ProfileDiffTabs ---

const MOCK_PROFILE_DATA = [
  {
    id:                    1,
    slug:                  'default',
    name:                  'Default',
    local_dir:             '/tmp/local',
    remote_dir:            'remote:path',
    debounce_seconds:      5,
    pull_interval_minutes: 5,
    rclone_filter:         [],
    rclone_args:           [],
    max_retries:           3,
    enabled:               true,
    created_at:            new Date().toISOString(),
    updated_at:            new Date().toISOString(),
    state:                 'idle' as const,
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
    sync_mode:             'mirror' as const,
  },
];

describe('SyncControls diff delegation to ProfileDiffTabs', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getProfiles.mockResolvedValue(MOCK_PROFILE_DATA);
    mockedApi.getAggregateStatus.mockResolvedValue({
      overall_state:         'idle',
      total_pending_changes: 5,
      paused_profiles:       [],
      profiles_summary:      [
        { slug: 'default', name: 'Default', state: 'idle', last_sync: null, pending_changes: 5, intervals_paused: false, last_error: null, resync_required: false },
      ],
    });
  });

  it('renders ProfileDiffTabs when Show Diff is clicked and aggregate data available', async () => {
    const user = userEvent.setup();
    render(<SyncControls />, { wrapper: createWrapper() });
    await user.click(screen.getByText('Show Diff'));

    await waitFor(() => {
      expect(screen.getByTestId('profile-diff-tabs')).toBeInTheDocument();
    });
  });

  it('hides ProfileDiffTabs when Hide Diff is clicked', async () => {
    const user = userEvent.setup();
    render(<SyncControls />, { wrapper: createWrapper() });
    await user.click(screen.getByText('Show Diff'));

    await waitFor(() => {
      expect(screen.getByTestId('profile-diff-tabs')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Hide Diff'));
    expect(screen.queryByTestId('profile-diff-tabs')).not.toBeInTheDocument();
  });
});

describe('SyncControls no-active-profiles guard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getProfiles.mockResolvedValue([
      {
        id:                    1,
        slug:                  'disabled',
        name:                  'Disabled profile',
        local_dir:             '/tmp/local',
        remote_dir:            'remote:path',
        debounce_seconds:      5,
        pull_interval_minutes: 5,
        rclone_filter:         [],
        rclone_args:           [],
        max_retries:           3,
        enabled:               false,
        created_at:            new Date().toISOString(),
        updated_at:            new Date().toISOString(),
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
      },
    ]);
  });

  it('disables sync actions and shows guidance when no profiles are enabled', async () => {
    render(<SyncControls />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('No active sync profiles')).toBeInTheDocument();
    });

    expect(screen.getByRole('button', { name: 'Push' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Pull' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Check for changes' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Show Diff' })).toBeDisabled();
  });
});

describe('SyncControls pause all / resume all', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getAggregateStatus.mockResolvedValue({
      overall_state: 'idle', total_pending_changes: 0, paused_profiles: [], profiles_summary: [],
    });
    mockedApi.pauseAllProfiles.mockResolvedValue({ changed: ['default'], unchanged: [], still_paused: {} });
    mockedApi.resumeAllProfiles.mockResolvedValue({
      changed: ['default'], unchanged: [], still_paused: { default: 'Restored the local side.' },
    });
  });

  it('offers Pause all while a profile syncs automatically', async () => {
    mockedApi.getProfiles.mockResolvedValue(MOCK_PROFILE_DATA);
    const user = userEvent.setup();
    render(<SyncControls />, { wrapper: createWrapper() });
    await user.click(await screen.findByRole('button', { name: 'Pause all' }));
    await waitFor(() => expect(mockedApi.pauseAllProfiles).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: 'Resume all' })).toBeNull();
  });

  it('offers Resume all once paused by the user and names what stays paused', async () => {
    const { toast } = await import('sonner');
    mockedApi.getProfiles.mockResolvedValue([{ ...MOCK_PROFILE_DATA[0], user_paused: true, intervals_paused: true }]);
    const user = userEvent.setup();
    render(<SyncControls />, { wrapper: createWrapper() });
    await user.click(await screen.findByRole('button', { name: 'Resume all' }));
    await waitFor(() => expect(mockedApi.resumeAllProfiles).toHaveBeenCalled());
    await waitFor(() => expect(toast.warning).toHaveBeenCalledWith('default stays paused: Restored the local side.'));
    expect(screen.queryByRole('button', { name: 'Pause all' })).toBeNull();
  });
});

// Integration tests for diff flow are now covered by profile-diff-tabs tests
