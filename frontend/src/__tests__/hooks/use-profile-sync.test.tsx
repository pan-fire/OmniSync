import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import { api, waitForSelectiveSync, waitForSyncJob } from '@/lib/api';
import {
  followSyncJob,
  startRefusal,
  useProfileSelectiveSync,
  useResumeProfileIntervals,
  useResyncProfile,
  useStartSyncs,
  useStopProfileSync,
} from '@/hooks/use-profile-sync';
import type { ProfileStatus, SyncJob } from '@/types';

vi.mock('@/lib/api', () => ({
  api: {
    startProfileSync:       vi.fn(),
    stopProfileSync:        vi.fn(),
    getProfile:             vi.fn(),
    resumeProfileIntervals: vi.fn(),
    resyncProfile:          vi.fn(),
    profileSelectiveSync:   vi.fn(),
  },
  waitForSyncJob:       vi.fn(),
  waitForSelectiveSync: vi.fn(),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const mockedApi = vi.mocked(api);
const mockedWaitForJob = vi.mocked(waitForSyncJob);
let queryClient: QueryClient;

const DOCS = { slug: 'docs', name: 'Docs' };
const PHOTOS = { slug: 'photos', name: 'Photos' };

function job (over: Partial<SyncJob> = {}): SyncJob {
  return {
    id:            7,
    direction:     'push',
    started_at:    '2026-01-01T00:00:00Z',
    finished_at:   '2026-01-01T00:01:00Z',
    status:        'completed',
    files_changed: 3,
    conflicts:     0,
    errors:        0,
    ...over,
  };
}

function wrapper ({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider initialLocale="en">{children}</I18nProvider>
    </QueryClientProvider>
  );
}

const t = (key: string, vars?: Record<string, unknown>) => `${key}${vars ? JSON.stringify(vars) : ''}`;

beforeEach(() => {
  vi.clearAllMocks();
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
});

describe('startRefusal', () => {
  it('returns the server error, an empty refusal for an error state, else null', () => {
    expect(startRefusal({ job_id: 1, state: 'error', error: 'remote missing' })).toBe('remote missing');
    expect(startRefusal({ job_id: 1, state: 'error' })).toBe('');
    expect(startRefusal({ job_id: 1, state: 'pushing' })).toBeNull();
  });
});

describe('followSyncJob', () => {
  it('reports a finished job with its changed files', async () => {
    mockedWaitForJob.mockResolvedValue(job({ files_changed: 3 }));
    await followSyncJob(queryClient, DOCS, 7, t, 1);
    expect(mockedWaitForJob).toHaveBeenCalledWith(7, { intervalMs: 1 });
    expect(toast.success).toHaveBeenCalledWith('dashboard.syncFinished{"name":"Docs","count":3}');
  });

  // Losing the poll must not look like a failed sync: the user is told to check.
  it('says it lost track when polling fails', async () => {
    mockedWaitForJob.mockRejectedValue(new Error('offline'));
    await followSyncJob(queryClient, DOCS, 7, t, 1);
    expect(toast.error).toHaveBeenCalledWith('dashboard.syncFollowLost{"name":"Docs"}');
    expect(mockedApi.getProfile).not.toHaveBeenCalled();
  });

  it('names the profile last_error when the job failed', async () => {
    mockedWaitForJob.mockResolvedValue(job({ status: 'failed' }));
    mockedApi.getProfile.mockResolvedValue({ last_error: 'quota exceeded' } as ProfileStatus);
    await followSyncJob(queryClient, DOCS, 7, t, 1);
    expect(toast.error).toHaveBeenCalledWith('Docs: quota exceeded');
  });

  it('falls back to a generic failure when there is no last_error', async () => {
    mockedWaitForJob.mockResolvedValue(job({ status: 'failed' }));
    mockedApi.getProfile.mockResolvedValue({ last_error: null } as ProfileStatus);
    await followSyncJob(queryClient, DOCS, 7, t, 1);
    expect(toast.error).toHaveBeenCalledWith('dashboard.syncEndedFailed{"name":"Docs"}');
  });

  it('falls back to a generic failure when the profile cannot be read', async () => {
    mockedWaitForJob.mockResolvedValue(job({ status: 'failed' }));
    mockedApi.getProfile.mockRejectedValue(new Error('gone'));
    await followSyncJob(queryClient, DOCS, 7, t, 1);
    expect(toast.error).toHaveBeenCalledWith('dashboard.syncEndedFailed{"name":"Docs"}');
  });

  it('polls at the running-sync interval by default', async () => {
    mockedWaitForJob.mockResolvedValue(job());
    await followSyncJob(queryClient, DOCS, 7, t);
    expect(mockedWaitForJob).toHaveBeenCalledWith(7, { intervalMs: 2000 });
  });
});

describe('useStartSyncs', () => {
  it('starts each profile, reports refusals, notes and failures, and follows the running ones', async () => {
    mockedWaitForJob.mockResolvedValue(job({ files_changed: 1 }));
    mockedApi.startProfileSync.mockImplementation(async (slug: string) => {
      if (slug === 'docs') return { job_id: 7, state: 'pushing', note: 'outside the sync window' };
      if (slug === 'photos') return { job_id: 0, state: 'error', error: 'remote missing' };
      if (slug === 'music') return { job_id: 0, state: 'error' };
      throw new Error('network down');
    });
    const { result } = renderHook(() => useStartSyncs(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({
        direction: 'push',
        targets:   [DOCS, PHOTOS, { slug: 'music', name: 'Music' }, { slug: 'books', name: 'Books' }],
        force:     true,
      });
    });

    expect(mockedApi.startProfileSync).toHaveBeenCalledWith('docs', 'push', true);
    expect(toast.success).toHaveBeenCalledWith('Sync started for 1 profile');
    expect(toast.error).toHaveBeenCalledWith('Photos: remote missing');
    expect(toast.error).toHaveBeenCalledWith('Music: Failed to start the sync');
    expect(toast.error).toHaveBeenCalledWith('Books: network down');
    expect(toast.info).toHaveBeenCalledWith('Docs: outside the sync window');
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Docs: sync finished, 1 file changed'));
    expect(mockedWaitForJob).toHaveBeenCalledTimes(1);
  });

  it('uses a generic message for a rejection that is not an Error', async () => {
    mockedApi.startProfileSync.mockRejectedValue('nope');
    const { result } = renderHook(() => useStartSyncs(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({ direction: 'pull', targets: [DOCS] });
    });

    expect(mockedApi.startProfileSync).toHaveBeenCalledWith('docs', 'pull', false);
    expect(toast.error).toHaveBeenCalledWith('Docs: Failed to start the sync');
    expect(toast.success).not.toHaveBeenCalled();
  });
});

describe('useStopProfileSync', () => {
  it('stops the sync and confirms it', async () => {
    mockedApi.stopProfileSync.mockResolvedValue({ state: 'idle', message: 'stopped' });
    const { result } = renderHook(() => useStopProfileSync('docs'), { wrapper });
    await act(async () => { await result.current.mutateAsync(); });
    expect(mockedApi.stopProfileSync).toHaveBeenCalledWith('docs');
    expect(toast.success).toHaveBeenCalledWith('Sync stopped');
  });

  it('shows the server error, or a generic one', async () => {
    mockedApi.stopProfileSync.mockRejectedValueOnce(new Error('not running')).mockRejectedValueOnce(new Error(''));
    const { result } = renderHook(() => useStopProfileSync('docs'), { wrapper });
    act(() => { result.current.mutate(); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('not running'));
    act(() => { result.current.mutate(); });
    await waitFor(() => expect(toast.error).toHaveBeenLastCalledWith('Something went wrong'));
  });
});

describe('useResumeProfileIntervals', () => {
  it('resumes the intervals and confirms it', async () => {
    mockedApi.resumeProfileIntervals.mockResolvedValue(undefined as never);
    const { result } = renderHook(() => useResumeProfileIntervals('docs'), { wrapper });
    await act(async () => { await result.current.mutateAsync(); });
    expect(mockedApi.resumeProfileIntervals).toHaveBeenCalledWith('docs');
    expect(toast.success).toHaveBeenCalledWith('Sync intervals resumed');
  });

  it('shows the server error, or a generic one', async () => {
    mockedApi.resumeProfileIntervals.mockRejectedValueOnce(new Error('locked')).mockRejectedValueOnce(new Error(''));
    const { result } = renderHook(() => useResumeProfileIntervals('docs'), { wrapper });
    act(() => { result.current.mutate(); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('locked'));
    act(() => { result.current.mutate(); });
    await waitFor(() => expect(toast.error).toHaveBeenLastCalledWith('Something went wrong'));
  });
});

describe('useResyncProfile', () => {
  it('starts the resync and follows it to the end', async () => {
    mockedApi.resyncProfile.mockResolvedValue({ job_id: 9, state: 'syncing' });
    mockedWaitForJob.mockResolvedValue(job({ id: 9, files_changed: 2 }));
    const { result } = renderHook(() => useResyncProfile('docs', 'Docs'), { wrapper });

    await act(async () => { await result.current.mutateAsync(); });

    expect(toast.success).toHaveBeenCalledWith('Resync started');
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Docs: sync finished, 2 files changed'));
  });

  it('reports a refused resync and does not follow it', async () => {
    mockedApi.resyncProfile.mockResolvedValue({ job_id: 0, state: 'error', error: 'already running' });
    const { result } = renderHook(() => useResyncProfile('docs'), { wrapper });

    await act(async () => { await result.current.mutateAsync(); });

    expect(toast.error).toHaveBeenCalledWith('docs: already running');
    expect(mockedWaitForJob).not.toHaveBeenCalled();
  });

  it('reports a refusal without a reason generically', async () => {
    mockedApi.resyncProfile.mockResolvedValue({ job_id: 0, state: 'error' });
    const { result } = renderHook(() => useResyncProfile('docs', 'Docs'), { wrapper });
    await act(async () => { await result.current.mutateAsync(); });
    expect(toast.error).toHaveBeenCalledWith('Docs: Something went wrong');
    expect(waitForSyncJob).not.toHaveBeenCalled();
  });

  it('shows the server error, or a generic one', async () => {
    mockedApi.resyncProfile.mockRejectedValueOnce(new Error('confirm required')).mockRejectedValueOnce(new Error(''));
    const { result } = renderHook(() => useResyncProfile('docs'), { wrapper });
    act(() => { result.current.mutate(); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('confirm required'));
    act(() => { result.current.mutate(); });
    await waitFor(() => expect(toast.error).toHaveBeenLastCalledWith('Something went wrong'));
  });
});

describe('useProfileSelectiveSync', () => {
  // A job that ended at once is returned without polling.
  it('returns a finished start without following it', async () => {
    const done = { job_id: 4, status: 'completed' as const, total: 1, succeeded: 1, failed: 0, errors: [] };
    mockedApi.profileSelectiveSync.mockResolvedValue(done);
    const { result } = renderHook(() => useProfileSelectiveSync('docs'), { wrapper });

    let final;
    await act(async () => { final = await result.current.mutateAsync([{ path: 'a.txt', action: 'push' }]); });

    expect(final).toEqual(done);
    expect(waitForSelectiveSync).not.toHaveBeenCalled();
  });
});
