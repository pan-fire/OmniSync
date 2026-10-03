import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import userEvent from '@testing-library/user-event';
import DashboardPage from '@/app/page';
import type { PausedProfileSummary, ProfileSummary } from '@/types';

vi.mock('@/i18n', () => ({
  useTranslation: () => ({
    t:      (k: string, v?: Record<string, string | number>) => (v?.name ? `${k}:${v.name}` : k),
    locale: 'en',
  }),
}));

const request = vi.fn();
vi.mock('@/components/sync/sync-confirm-dialog', () => ({
  useConfirmedSync: () => ({ request, syncNow: vi.fn(), isStarting: false, dialog: null }),
}));

let summaries: ProfileSummary[] = [];
let paused: PausedProfileSummary[] = [];
let profileList: unknown[] | undefined = [];
vi.mock('@/hooks/use-aggregate-status', () => ({
  useAggregateStatus: () => ({
    data: {
      overall_state:         'idle',
      total_pending_changes: 0,
      paused_profiles:       paused,
      profiles_summary:      summaries,
    },
    isError: false,
  }),
}));
vi.mock('@/hooks/use-profiles', () => ({
  useProfiles: () => ({ data: profileList, isSuccess: profileList !== undefined }),
}));
vi.mock('@/hooks/use-health', () => ({ useHealth: () => ({ data: undefined, isError: false }) }));
let remoteList: unknown[] | undefined = [];
vi.mock('@/hooks/use-remotes', () => ({ useRemotes: () => ({ data: remoteList }) }));
vi.mock('@/components/sync/sync-controls', () => ({ SyncControls: () => <div data-testid="sync-controls" /> }));
vi.mock('@/components/sync/aggregate-status-card', () => ({ AggregateStatusCard: () => <div data-testid="aggregate-status" /> }));
vi.mock('@/components/profiles/mirror-notice', () => ({ MirrorProfilesNotice: () => null }));
vi.mock('@/components/sync/health-indicators', () => ({ HealthIndicators: () => null }));
vi.mock('@/components/layout/page-header', () => ({ PageHeader: ({ title }: { title: string }) => <h1>{title}</h1> }));
vi.mock('@/components/layout/page-help', () => ({ PageHelp: () => null }));

function summary (over: Partial<ProfileSummary>): ProfileSummary {
  return {
    slug:             'docs',
    name:             'Docs',
    state:            'idle',
    last_sync:        null,
    pending_changes:  0,
    intervals_paused: false,
    last_error:       null,
    resync_required:  false,
    ...over,
  };
}

beforeEach(() => {
  request.mockClear();
  summaries = [];
  paused = [];
  profileList = [];
  remoteList = [];
});

describe('Dashboard profile rows', () => {
  it('shows the last sync as relative time with the exact time as a tooltip', () => {
    profileList = [{}];
    const lastSync = new Date(Date.now() - 5 * 60_000).toISOString();
    summaries = [summary({ last_sync: lastSync })];
    render(<DashboardPage />);
    const time = screen.getByText('5 minutes ago');
    expect(time.tagName).toBe('TIME');
    expect(time).toHaveAttribute('dateTime', lastSync);
    expect(time.getAttribute('title')).toMatch(/\d{4}/);
  });

  it('pushes and pulls one profile through the confirmation', async () => {
    profileList = [{}, {}];
    summaries = [summary({}), summary({ slug: 'work', name: 'Work' })];
    const user = userEvent.setup();
    render(<DashboardPage />);

    await user.click(screen.getByRole('button', { name: 'dashboard.pushProfile:Work' }));
    expect(request).toHaveBeenLastCalledWith('push', [{ slug: 'work', name: 'Work' }]);
    await user.click(screen.getByRole('button', { name: 'dashboard.pullProfile:Docs' }));
    expect(request).toHaveBeenLastCalledWith('pull', [{ slug: 'docs', name: 'Docs' }]);
  });

  it('lists disabled profiles too, marked and without quick actions', () => {
    profileList = [{ slug: 'docs', name: 'Docs', enabled: true }, { slug: 'old', name: 'Old', enabled: false, state: 'idle' }];
    summaries = [summary({})];
    render(<DashboardPage />);
    const row = screen.getByTestId('dashboard-profile-old');
    expect(row).toHaveTextContent('profiles.disabled');
    expect(screen.queryByRole('button', { name: 'dashboard.pushProfile:Old' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'dashboard.pushProfile:Docs' })).toBeInTheDocument();
  });

  it('disables the quick actions while the profile syncs', () => {
    profileList = [{}];
    summaries = [summary({ state: 'pushing' })];
    render(<DashboardPage />);
    expect(screen.getByRole('button', { name: 'dashboard.pushProfile:Docs' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'dashboard.pullProfile:Docs' })).toBeDisabled();
  });
});

describe('Dashboard without profiles', () => {
  it('shows the first-run checklist instead of the sync controls and status', () => {
    profileList = [];
    render(<DashboardPage />);
    const cta = screen.getByTestId('first-profile-cta');
    expect(cta).toHaveTextContent('dashboard.noProfilesTitle');
    expect(screen.getByRole('link', { name: /dashboard.firstRun.addRemote/ })).toHaveAttribute('href', '/remotes');
    expect(screen.getByRole('link', { name: /profiles.createFirst/ })).toHaveAttribute('href', '/profiles?create=1');
    expect(screen.queryByTestId('sync-controls')).not.toBeInTheDocument();
    expect(screen.queryByTestId('aggregate-status')).not.toBeInTheDocument();
  });

  it('ticks off the remote step once a remote exists', () => {
    profileList = [];
    remoteList = [{ name: 'gdrive', type: 'drive' }];
    render(<DashboardPage />);
    const steps = screen.getByTestId('first-profile-cta').querySelectorAll('li');
    expect(steps[0]).toHaveAttribute('data-done', 'true');
    expect(steps[1]).not.toHaveAttribute('data-done');
    expect(screen.getByRole('link', { name: /dashboard.firstRun.manageRemotes/ })).toHaveAttribute('href', '/remotes');
  });

  it('shows no invitation while the profiles load or when there are some', () => {
    profileList = undefined;
    const { rerender } = render(<DashboardPage />);
    expect(screen.queryByTestId('first-profile-cta')).not.toBeInTheDocument();
    profileList = [{}];
    rerender(<DashboardPage />);
    expect(screen.queryByTestId('first-profile-cta')).not.toBeInTheDocument();
    expect(screen.getByTestId('sync-controls')).toBeInTheDocument();
  });
});

/** The dashboard row of one profile, found by its name link. */
function row (name: string) {
  return screen.getByRole('link', { name }).closest('div.relative') as HTMLElement;
}

describe('Dashboard profile state indicators', () => {
  it('shows a profile in error with the red (destructive) state badge and its error', () => {
    profileList = [{}, {}];
    summaries = [
      summary({ slug: 'docs', name: 'Docs', state: 'error', last_error: 'local folder is not mounted' }),
      summary({ slug: 'work', name: 'Work', state: 'idle' }),
    ];
    render(<DashboardPage />);

    const errorRow = row('Docs');
    const badge = within(errorRow).getByText('dashboard.state.error');
    expect(badge).toHaveAttribute('data-variant', 'destructive');
    expect(within(errorRow).getByTestId('last-error')).toHaveTextContent('local folder is not mounted');
    expect(within(errorRow).getByTestId('last-error')).toHaveClass('text-destructive');

    const idleRow = row('Work');
    expect(within(idleRow).getByText('dashboard.state.idle')).toHaveAttribute('data-variant', 'secondary');
    expect(within(idleRow).queryByTestId('last-error')).not.toBeInTheDocument();
  });

  it('lists a profile in error before an idle one', () => {
    profileList = [{}, {}];
    summaries = [
      summary({ slug: 'alpha', name: 'Alpha', state: 'idle' }),
      summary({ slug: 'zulu', name: 'Zulu', state: 'error', last_error: 'boom' }),
    ];
    render(<DashboardPage />);

    const names = screen.getAllByRole('link')
      .map((link) => link.textContent)
      .filter((text) => text === 'Alpha' || text === 'Zulu');
    expect(names).toEqual(['Zulu', 'Alpha']);
  });

  it('marks a paused profile with the paused warning and shows the paused banner', () => {
    profileList = [{}, {}];
    summaries = [
      summary({ slug: 'docs', name: 'Docs', intervals_paused: true, pending_changes: 3 }),
      summary({ slug: 'work', name: 'Work' }),
    ];
    paused = [{ slug: 'docs', name: 'Docs', pending_changes: 3, paused_at: null }];
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <DashboardPage />
      </QueryClientProvider>
    );

    expect(within(row('Docs')).getByLabelText('intervalsPaused.paused')).toBeInTheDocument();
    expect(within(row('Work')).queryByLabelText('intervalsPaused.paused')).not.toBeInTheDocument();
    const banner = screen.getByTestId('intervals-paused-banner');
    expect(within(banner).getByText('Docs')).toBeInTheDocument();
  });
});
