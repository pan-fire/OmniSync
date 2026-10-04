import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ApiError } from '@/types';
import type { ProfileStatus, ProfileUpdateRequest } from '@/types';
import ProfileDetailPage from '@/app/profiles/[slug]/page';

const replace = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter:       () => ({ replace, push: vi.fn() }),
  useParams:       () => ({ slug: 'docs' }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/i18n', () => ({
  useTranslation: () => ({
    t:      (k: string, v?: Record<string, string | number>) => (v ? `${k}:${Object.values(v).join(',')}` : k),
    locale: 'en',
  }),
}));

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
  max_delete:            null,
  resync_required:       false,
};

const { mutation } = vi.hoisted(() => ({ mutation: () => ({ mutate: () => {}, isPending: false }) }));
let updated = { ...PROFILE };
const refetch = vi.fn();
let profileQuery: Record<string, unknown> = {};
vi.mock('@/hooks/use-profiles', () => ({
  useProfile:       () => profileQuery,
  useUpdateProfile: () => ({
    isPending: false,
    mutate:    (_data: ProfileUpdateRequest, opts: { onSuccess: (p: ProfileStatus) => void }) => opts.onSuccess(updated),
  }),
  useDeleteProfile: mutation,
}));
vi.mock('@/hooks/use-profile-sync', () => ({
  isActiveState:             () => false,
  useStopProfileSync:        mutation,
  useResumeProfileIntervals: mutation,
}));
vi.mock('@/hooks/use-pause', () => ({ usePauseProfile: mutation }));
vi.mock('@/components/profiles/trash-panel', () => ({ TrashPanel: () => <div data-testid="trash-panel" /> }));
vi.mock('@/hooks/use-backups', () => ({
  useBackupTargets:      () => ({ data: [], isLoading: false, isError: false, isSuccess: true }),
  useDeleteBackupTarget: mutation,
  useRunBackup:          mutation,
  useUpdateBackupTarget: mutation,
}));
const { useJobs } = vi.hoisted(() => ({
  useJobs: vi.fn(() => ({
    data: [{
      id:            7,
      profile_slug:  'docs',
      profile_name:  'Docs',
      direction:     'push',
      status:        'completed',
      started_at:    '2026-01-02T00:00:00Z',
      finished_at:   '2026-01-02T00:01:00Z',
      files_changed: 3,
      conflicts:     0,
      errors:        0,
    }],
    isError: false,
    refetch: () => {},
  })),
}));
vi.mock('@/hooks/use-jobs', () => ({ JOBS_PAGE_SIZE: 20, useJobs }));
vi.mock('@/components/sync/sync-confirm-dialog', () => ({
  useConfirmedSync: () => ({ request: vi.fn(), syncNow: vi.fn(), isStarting: false, dialog: null }),
}));
vi.mock('@/components/profiles/profile-form', () => ({
  ProfileForm: ({ onSubmit }: { onSubmit: (d: { profile: ProfileUpdateRequest }) => void }) => (
    <button onClick={() => onSubmit({ profile: { name: 'Work docs' } })}>submit-edit</button>
  ),
}));
vi.mock('@/components/profiles/test-sync-button', () => ({
  TestSyncButton: ({ slug }: { slug?: string }) => <div data-testid="test-sync-button">{slug}</div>,
}));
vi.mock('@/components/profiles/backup-target-form', () => ({ BackupTargetForm: () => null }));
vi.mock('@/components/sync/profile-diff-panel', () => ({ ProfileDiffPanel: () => null }));
vi.mock('@/components/sync/resync-action', () => ({ ResyncAction: () => null }));
vi.mock('@/components/profiles/switch-to-two-way', () => ({ SwitchToTwoWayHint: () => null }));
vi.mock('@/components/layout/page-header', () => ({ PageHeader: ({ title }: { title: string }) => <h1>{title}</h1> }));

beforeEach(() => {
  replace.mockClear();
  refetch.mockClear();
  updated = { ...PROFILE };
  profileQuery = { data: PROFILE, isError: false, isLoading: false, refetch };
});

describe('Profile detail page', () => {
  it('offers a sync test for the saved profile', () => {
    render(<ProfileDetailPage />);
    expect(screen.getByTestId('test-sync-button')).toHaveTextContent('docs');
  });

  it('follows a rename to the new slug', async () => {
    updated = { ...PROFILE, name: 'Work docs', slug: 'work-docs' };
    const user = userEvent.setup();
    render(<ProfileDetailPage />);
    await user.click(screen.getByText('profiles.edit'));
    await user.click(await screen.findByText('submit-edit'));
    expect(replace).toHaveBeenCalledWith('/profiles/work-docs');
  });

  it('shows the jobs of this profile only in the History tab', async () => {
    const user = userEvent.setup();
    render(<ProfileDetailPage />);
    await user.click(screen.getByRole('tab', { name: 'profiles.history' }));
    expect(useJobs).toHaveBeenLastCalledWith(1, 'docs');
    expect(await screen.findByRole('link', { name: 'jobs.openJob:7' })).toHaveAttribute('href', '/jobs/7');
    // Every row is this profile's: no profile column.
    expect(screen.queryByRole('columnheader', { name: 'jobs.profile' })).not.toBeInTheDocument();
  });

  it('stays on the page when the slug is unchanged', async () => {
    const user = userEvent.setup();
    render(<ProfileDetailPage />);
    await user.click(screen.getByText('profiles.edit'));
    await user.click(await screen.findByText('submit-edit'));
    expect(replace).not.toHaveBeenCalled();
  });
});

describe('Profile detail states', () => {
  it('says "not found" for a 404', () => {
    profileQuery = { data: undefined, isError: true, isLoading: false, error: new ApiError(404, 'gone'), refetch };
    render(<ProfileDetailPage />);
    expect(screen.getByRole('alert')).toHaveTextContent('profiles.notFound');
    expect(screen.queryByRole('button', { name: 'common.retry' })).not.toBeInTheDocument();
  });

  it('offers a retry for other errors', async () => {
    profileQuery = { data: undefined, isError: true, isLoading: false, error: new ApiError(503, 'Backend down'), refetch };
    const user = userEvent.setup();
    render(<ProfileDetailPage />);
    expect(screen.getByRole('alert')).toHaveTextContent('profiles.loadFailed: Backend down');
    await user.click(screen.getByRole('button', { name: 'common.retry' }));
    expect(refetch).toHaveBeenCalled();
  });

  it('writes the selected tab to the address', async () => {
    const user = userEvent.setup();
    render(<ProfileDetailPage />);
    await user.click(screen.getByRole('tab', { name: 'backups.title' }));
    expect(replace).toHaveBeenLastCalledWith('/?tab=backups', { scroll: false });
    await user.click(screen.getByRole('tab', { name: 'backups.overview' }));
    expect(replace).toHaveBeenLastCalledWith('/', { scroll: false });
  });

  it('explains why Resume is disabled while differences remain', () => {
    profileQuery = { data: { ...PROFILE, intervals_paused: true, pending_changes: 2 }, isError: false, isLoading: false, refetch };
    render(<ProfileDetailPage />);
    const resume = screen.getByRole('button', { name: 'intervalsPaused.resume' });
    expect(resume).toBeDisabled();
    expect(resume).toHaveAccessibleDescription('intervalsPaused.resumeBlocked:2');
  });

  it('names the profile in the delete confirmation and says files stay', async () => {
    const user = userEvent.setup();
    render(<ProfileDetailPage />);
    await user.click(screen.getByRole('button', { name: 'common.delete' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('profiles.deleteTitle:Docs');
    expect(screen.getByTestId('profile-delete-keeps')).toHaveTextContent('profiles.deleteKeepsFiles');
    expect(screen.getByTestId('profile-delete-keeps')).toHaveTextContent('/data/docs');
    expect(screen.getByTestId('profile-delete-keeps')).toHaveTextContent('gdrive:docs');
  });
});
