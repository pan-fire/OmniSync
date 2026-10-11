import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import { api, waitForBackupJob } from '@/lib/api';
import {
  toastBackupJob, useDeleteBackupTarget, useRestoreFiles, useRunBackup, useUpdateBackupTarget,
} from '@/hooks/use-backups';
import { ApiError } from '@/types';
import type { BackupJob, BackupTarget } from '@/types';

vi.mock('@/lib/api', () => ({
  api: {
    updateBackupTarget: vi.fn(),
    deleteBackupTarget: vi.fn(),
    restoreFiles:       vi.fn(),
    runBackup:          vi.fn(),
  },
  waitForBackupJob: vi.fn(),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

const mockedApi = vi.mocked(api);
let queryClient: QueryClient;

function job (over: Partial<BackupJob> = {}): BackupJob {
  return {
    id:            5,
    target_id:     3,
    started_at:    '2026-01-01T00:00:00Z',
    finished_at:   null,
    status:        'completed',
    direction:     'restore',
    size_bytes:    null,
    snapshot_id:   's1',
    error_message: null,
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
const RESTORE = { id: 3, data: { snapshot_id: 's1', paths: ['a.txt'], target_dir: null } };

beforeEach(() => {
  vi.clearAllMocks();
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
});

describe('toastBackupJob', () => {
  it('says how each ended job went', () => {
    toastBackupJob(job({ status: 'failed' }), 'backup', t);
    expect(toast.error).toHaveBeenCalledWith('backups.toast.backupFailedPlain');
    toastBackupJob(job({ status: 'failed', error_message: 'Disk full' }), 'restore', t);
    expect(toast.error).toHaveBeenCalledWith('backups.toast.restoreFailedWith{"error":"Disk full"}');
    toastBackupJob(job({ status: 'skipped' }), 'backup', t);
    expect(toast.warning).toHaveBeenCalledWith('backups.toast.backupSkipped');
    toastBackupJob(job({ status: 'skipped', error_message: 'NAS asleep' }), 'backup', t);
    expect(toast.warning).toHaveBeenCalledWith('backups.toast.backupSkippedWith{"reason":"NAS asleep"}');
    toastBackupJob(job({ status: 'running' }), 'backup', t);
    expect(toast.info).toHaveBeenCalledWith('backups.toast.backupRunning');
  });
});

describe('useUpdateBackupTarget', () => {
  it('saves the target and names it', async () => {
    mockedApi.updateBackupTarget.mockResolvedValue({ name: 'Nightly' } as BackupTarget);
    const { result } = renderHook(() => useUpdateBackupTarget('docs'), { wrapper });
    await act(async () => { await result.current.mutateAsync({ id: 3, data: { enabled: false } }); });
    expect(mockedApi.updateBackupTarget).toHaveBeenCalledWith('docs', 3, { enabled: false });
    expect(toast.success).toHaveBeenCalledWith('Backup target "Nightly" updated');
  });

  it('shows the server error, or the update fallback', async () => {
    mockedApi.updateBackupTarget.mockRejectedValueOnce(new Error('path in use')).mockRejectedValueOnce(new Error(''));
    const { result } = renderHook(() => useUpdateBackupTarget('docs'), { wrapper });
    act(() => { result.current.mutate({ id: 3, data: {} }); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('path in use'));
    act(() => { result.current.mutate({ id: 3, data: {} }); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Failed to update backup target'));
  });
});

describe('useDeleteBackupTarget', () => {
  it('deletes the target and refreshes the list', async () => {
    mockedApi.deleteBackupTarget.mockResolvedValue(undefined as never);
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    const { result } = renderHook(() => useDeleteBackupTarget('docs'), { wrapper });
    await act(async () => { await result.current.mutateAsync(3); });
    expect(mockedApi.deleteBackupTarget).toHaveBeenCalledWith('docs', 3);
    expect(toast.success).toHaveBeenCalledWith('Backup target deleted');
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['backups', 'docs'] });
  });

  it('shows the server error, or the delete fallback', async () => {
    mockedApi.deleteBackupTarget.mockRejectedValueOnce(new Error('backup running')).mockRejectedValueOnce(new Error(''));
    const { result } = renderHook(() => useDeleteBackupTarget('docs'), { wrapper });
    act(() => { result.current.mutate(3); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('backup running'));
    act(() => { result.current.mutate(3); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Failed to delete backup target'));
  });
});

describe('useRestoreFiles', () => {
  it('follows a running restore to its end', async () => {
    mockedApi.restoreFiles.mockResolvedValue(job({ status: 'running' }));
    vi.mocked(waitForBackupJob).mockResolvedValue(job({ status: 'completed' }));
    const { result } = renderHook(() => useRestoreFiles('docs', 1), { wrapper });
    await act(async () => { await result.current.mutateAsync(RESTORE); });
    expect(toast.info).toHaveBeenCalledWith('Restore started');
    expect(waitForBackupJob).toHaveBeenCalledWith('docs', 3, 5, { intervalMs: 1 });
    expect(toast.success).toHaveBeenCalledWith('Restore completed');
  });

  // Losing track of a run is not a failure: it keeps going on the server.
  it('warns when the restore can no longer be followed', async () => {
    mockedApi.restoreFiles.mockResolvedValue(job({ status: 'running' }));
    vi.mocked(waitForBackupJob).mockRejectedValue(new Error('offline'));
    const { result } = renderHook(() => useRestoreFiles('docs', 1), { wrapper });
    act(() => { result.current.mutate(RESTORE); });
    await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(
      'The run continues, but its progress could not be followed. Check the backup history.'
    ));
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('explains a busy target or profile', async () => {
    mockedApi.restoreFiles
      .mockRejectedValueOnce(new ApiError(409, 'busy', 'backup_running'))
      .mockRejectedValueOnce(new ApiError(409, 'busy', 'sync_busy'))
      .mockRejectedValueOnce(new Error(''));
    const { result } = renderHook(() => useRestoreFiles('docs', 1), { wrapper });
    act(() => { result.current.mutate(RESTORE); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      'A backup or restore of this target is already running. Wait for it to finish.'
    ));
    act(() => { result.current.mutate(RESTORE); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      'A sync, backup or restore of this profile is running. Try again when it is done.'
    ));
    act(() => { result.current.mutate(RESTORE); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Failed to start restore'));
  });
});

describe('useRunBackup', () => {
  it('falls back to a generic start failure', async () => {
    mockedApi.runBackup.mockRejectedValue(new Error(''));
    const { result } = renderHook(() => useRunBackup('docs', 1), { wrapper });
    act(() => { result.current.mutate(3); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Failed to start backup'));
  });
});
