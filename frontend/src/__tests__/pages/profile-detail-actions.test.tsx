import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider, type Locale } from '@/i18n';
import { ApiError } from '@/types';
import type { BackupTarget, ProfileStatus } from '@/types';
import ProfileDetailPage from '@/app/profiles/[slug]/page';

// The Radix switches and checkboxes measure themselves; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe () {}
  unobserve () {}
  disconnect () {}
} as unknown as typeof ResizeObserver;

const nav = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), search: '' }));
vi.mock('next/navigation', () => ({
  useRouter:       () => ({ replace: nav.replace, push: nav.push }),
  useParams:       () => ({ slug: 'docs' }),
  useSearchParams: () => new URLSearchParams(nav.search),
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
  verify_after_backup: false,
  last_verify_status:  null,
  last_verify_message: null,
  created_at:          '2026-01-01T00:00:00Z',
  updated_at:          '2026-01-01T00:00:00Z',
};

/** A mutation whose mutate is a spy; `succeed` runs the caller's onSuccess. */
function spyMutation () {
  const state = { succeed: false };
  const mutate = vi.fn((_arg?: unknown, opts?: { onSuccess?: (v: unknown) => void }) => {
    if (state.succeed) opts?.onSuccess?.(PROFILE);
  });
  return { mutate, isPending: false, state };
}

const m = vi.hoisted(() => ({} as Record<string, ReturnType<typeof spyMutation>>));
const q = vi.hoisted(() => ({ profile: {} as Record<string, unknown>, targets: {} as Record<string, unknown> }));
const sync = vi.hoisted(() => ({ request: vi.fn(), syncNow: vi.fn() }));

vi.mock('@/hooks/use-profiles', () => ({
  useProfile:       () => q.profile,
  useUpdateProfile: () => m.update,
  useDeleteProfile: () => m.del,
}));
vi.mock('@/hooks/use-profile-sync', () => ({
  isActiveState:             (s: string) => s === 'pushing' || s === 'pulling' || s === 'syncing',
  useStopProfileSync:        () => m.stop,
  useResumeProfileIntervals: () => m.resume,
}));
vi.mock('@/hooks/use-pause', () => ({ usePauseProfile: () => m.pause }));
vi.mock('@/hooks/use-backups', () => ({
  runBackupKey:          (slug: string) => ['backups', slug, 'run'],
  useBackupTargets:      () => q.targets,
  useDeleteBackupTarget: () => m.delTarget,
  useRunBackup:          () => m.run,
  useUpdateBackupTarget: () => m.updTarget,
}));
vi.mock('@/components/sync/sync-confirm-dialog', () => ({
  useConfirmedSync: () => ({ ...sync, isStarting: false, dialog: null }),
}));
vi.mock('@/components/profiles/profile-form', () => ({
  ProfileForm: ({ onCancel }: { onCancel: () => void }) => <button type="button" onClick={onCancel}>cancel-edit</button>,
}));
vi.mock('@/components/profiles/backup-target-form', () => ({
  BackupTargetForm: ({ open, target }: { open: boolean; target: BackupTarget | null }) =>
    (open ? <div data-testid="target-form">{target ? `edit ${target.name}` : 'new target'}</div> : null),
}));
vi.mock('@/components/sync/profile-diff-panel', () => ({
  ProfileDiffPanel: ({ emptyExtra }: { emptyExtra: ReactNode }) => <div data-testid="diff-panel">{emptyExtra}</div>,
}));
vi.mock('@/components/profiles/test-sync-button', () => ({ TestSyncButton: () => null }));
vi.mock('@/components/profiles/mirror-notice', () => ({ MirrorNotice: () => <div data-testid="mirror-notice" /> }));
vi.mock('@/components/sync/resync-action', () => ({ ResyncAction: () => null }));
vi.mock('@/components/jobs/profile-job-history', () => ({ ProfileJobHistory: () => null }));
vi.mock('@/components/profiles/trash-panel', () => ({ TrashPanel: () => <div data-testid="trash-panel" /> }));
vi.mock('@/components/layout/page-header', () => ({
  PageHeader: ({ title, afterTitle }: { title: string; afterTitle?: ReactNode }) => <header><h1>{title}</h1>{afterTitle}</header>,
}));

function renderPage (locale: Locale = 'en') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Wrapper ({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}><I18nProvider initialLocale={locale}>{children}</I18nProvider></QueryClientProvider>;
  }
  return render(<ProfileDetailPage />, { wrapper: Wrapper });
}

function withProfile (over: Partial<ProfileStatus>) {
  q.profile = { ...q.profile, data: { ...PROFILE, ...over } };
}

beforeEach(() => {
  vi.clearAllMocks();
  nav.search = '';
  for (const key of ['update', 'del', 'stop', 'resume', 'pause', 'delTarget', 'run', 'updTarget']) m[key] = spyMutation();
  q.profile = { data: PROFILE, isError: false, isLoading: false, refetch: vi.fn(), isFetching: false };
  q.targets = { data: [TARGET], isLoading: false, isError: false, isSuccess: true };
});

describe('Profile detail loading and errors', () => {
  it('shows a loading state', () => {
    q.profile = { isLoading: true };
    renderPage();
    expect(screen.getByRole('status')).toHaveTextContent('Loading...');
  });

  it('says "not found" in Persian with a way back', () => {
    q.profile = { isError: true, error: new ApiError(404, 'Not found'), isLoading: false };
    renderPage('fa');
    expect(screen.getByRole('alert')).toHaveTextContent('پروفایل یافت نشد');
    expect(screen.getByRole('link', { name: /بازگشت به پروفایل‌ها/ })).toHaveAttribute('href', '/profiles');
  });

  it('offers a retry without a message for an unknown error', async () => {
    const refetch = vi.fn();
    q.profile = { isError: true, error: new Error(''), isLoading: false, refetch, isFetching: false };
    const user = userEvent.setup();
    renderPage();
    expect(screen.getByRole('alert')).toHaveTextContent(/^Could not load the profileRetry$/);
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});

describe('Profile detail delete', () => {
  // Deleting a profile cannot be undone: nothing is sent before the confirm.
  it('deletes only after the confirmation, then goes back to the list', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole('button', { name: 'Delete' }));
    let dialog = await screen.findByRole('dialog', { name: 'Delete the profile "Docs"?' });
    expect(m.del.mutate).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(m.del.mutate).not.toHaveBeenCalled();

    m.del.state.succeed = true;
    await user.click(screen.getByRole('button', { name: 'Delete' }));
    dialog = await screen.findByRole('dialog', { name: 'Delete the profile "Docs"?' });
    await user.click(within(dialog).getByRole('button', { name: 'Delete profile' }));

    expect(m.del.mutate).toHaveBeenCalledWith('docs', expect.anything());
    expect(nav.push).toHaveBeenCalledWith('/profiles');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
});

describe('Profile detail sync controls', () => {
  it('stops a running sync and disables the sync buttons meanwhile', async () => {
    withProfile({ state: 'pushing' });
    const user = userEvent.setup();
    renderPage();
    expect(screen.getByRole('button', { name: 'Push' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Stop' }));
    expect(m.stop.mutate).toHaveBeenCalledTimes(1);
  });

  it('pauses an enabled profile; a paused one has no Pause', async () => {
    const user = userEvent.setup();
    const { unmount } = renderPage();
    await user.click(screen.getByRole('button', { name: 'Pause' }));
    expect(m.pause.mutate).toHaveBeenCalledTimes(1);
    unmount();

    withProfile({ user_paused: true });
    renderPage();
    expect(screen.queryByRole('button', { name: 'Pause' })).not.toBeInTheDocument();
  });

  it('asks before a push or pull of a mirror profile', async () => {
    withProfile({ sync_mode: 'mirror' });
    const user = userEvent.setup();
    renderPage();
    expect(screen.getByTestId('mirror-notice')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Push' }));
    expect(sync.request).toHaveBeenCalledWith('push', [{ slug: 'docs', name: 'Docs' }]);
    await user.click(screen.getByRole('button', { name: 'Pull' }));
    expect(sync.request).toHaveBeenCalledWith('pull', [{ slug: 'docs', name: 'Docs' }]);
  });

  it('resumes paused intervals from the controls and from the empty differences', async () => {
    withProfile({ intervals_paused: true });
    nav.search = 'tab=differences';
    const user = userEvent.setup();
    renderPage();

    const resumes = screen.getAllByRole('button', { name: 'Resume Intervals' });
    expect(resumes).toHaveLength(2);
    await user.click(resumes[0]);
    await user.click(within(screen.getByTestId('diff-panel')).getByRole('button', { name: 'Resume Intervals' }));
    expect(m.resume.mutate).toHaveBeenCalledTimes(2);
  });
});

describe('Profile detail overview', () => {
  it('shows the bandwidth limit and sync window, and opens and cancels the edit dialog', async () => {
    withProfile({ bwlimit: '10M', sync_window: { days: [0], start: '22:00', end: '06:00' } });
    const user = userEvent.setup();
    renderPage();
    expect(screen.getByText('10M')).toBeInTheDocument();
    expect(screen.getByText('22:00–06:00')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Edit' }));
    expect(await screen.findByRole('dialog', { name: 'Edit Sync Profile' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'cancel-edit' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(m.update.mutate).not.toHaveBeenCalled();
  });
});

describe('Profile detail backups tab', () => {
  beforeEach(() => {
    nav.search = 'tab=backups';
  });

  it('shows loading, an error and the empty state', () => {
    q.targets = { isLoading: true, isError: false, isSuccess: false };
    const { unmount } = renderPage();
    expect(screen.getByRole('status')).toHaveTextContent('Loading...');
    unmount();

    q.targets = { isLoading: false, isError: true, isSuccess: false, error: new Error('Backend down') };
    const second = renderPage();
    expect(screen.getByRole('alert')).toHaveTextContent('Backend down');
    second.unmount();

    q.targets = { data: [], isLoading: false, isError: false, isSuccess: true };
    renderPage();
    expect(screen.getByText('No backup targets configured yet')).toBeInTheDocument();
  });

  it('adds, edits, runs and toggles a target', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole('button', { name: 'Add Backup Target' }));
    expect(screen.getByTestId('target-form')).toHaveTextContent('new target');

    await user.click(screen.getByRole('button', { name: 'Edit' }));
    expect(screen.getByTestId('target-form')).toHaveTextContent('edit Nightly');

    await user.click(screen.getByRole('button', { name: 'Run Now' }));
    expect(m.run.mutate).toHaveBeenCalledWith(4);

    await user.click(screen.getByRole('switch', { name: 'Enable or disable backup target Nightly' }));
    expect(m.updTarget.mutate).toHaveBeenCalledWith({ id: 4, data: { enabled: false } });
  });

  it('deletes a target only after the confirmation', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole('button', { name: 'Delete' }));
    const dialog = await screen.findByRole('dialog', { name: 'Delete backup target' });
    expect(m.delTarget.mutate).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));
    expect(m.delTarget.mutate).toHaveBeenCalledWith(4);
  });
});
