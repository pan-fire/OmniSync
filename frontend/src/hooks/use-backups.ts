'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { api, waitForBackupJob } from '@/lib/api';
import { useTranslation } from '@/i18n';
import { ApiError } from '@/types';
import type {
  BackupJob, BackupTargetCreateRequest, BackupTargetUpdateRequest, RestoreFilesRequest, RestoreRequest, RestoreScope,
  SnapshotFilesQuery,
} from '@/types';

type Translate = ReturnType<typeof useTranslation>['t'];
type JobKind = 'backup' | 'restore';

/** How often a running backup or restore is polled. */
export const BACKUP_POLL_MS = 3000;

/** The run was started, but following it failed (the backend went away meanwhile). */
class FollowLostError extends Error {}

/**
 * Follow a started backup or restore until it ends and return the ended job.
 * The routes answer 202 at once with the running job (or, refused before
 * any work, an ended one).
 */
async function followBackupJob (
  queryClient: ReturnType<typeof useQueryClient>, slug: string, targetId: number, started: BackupJob,
  kind: JobKind, t: Translate, intervalMs: number
): Promise<BackupJob> {
  if (started.status !== 'running') return started;
  toast.info(t(`backups.toast.${kind}Started`));
  // The targets list now shows the running job.
  queryClient.invalidateQueries({ queryKey: ['backups', slug] });
  try {
    return await waitForBackupJob(slug, targetId, started.id, { intervalMs });
  } catch {
    throw new FollowLostError(t('backups.toast.followLost'));
  }
}

/**
 * Toast what a run or restore did, once it ended. A failed job's
 * error_message is a fixed text for its error_code (or OmniSync's own
 * explanation), never rclone's output.
 */
export function toastBackupJob (job: BackupJob, kind: JobKind, t: Translate) {
  const reason = job.error_message ?? '';
  switch (job.status) {
    case 'completed':
      toast.success(t(`backups.toast.${kind}Completed`));
      break;
    case 'failed':
      toast.error(reason ? t(`backups.toast.${kind}FailedWith`, { error: reason }) : t(`backups.toast.${kind}FailedPlain`));
      break;
    case 'skipped':
      toast.warning(reason ? t('backups.toast.backupSkippedWith', { reason }) : t('backups.toast.backupSkipped'));
      break;
    case 'running':
      toast.info(t(`backups.toast.${kind}Running`));
      break;
  }
}

function toastBackupError (error: Error, kind: JobKind, t: Translate) {
  // Other 409s (a snapshot that cannot be restored as asked) carry their own reason.
  if (error instanceof ApiError && error.code === 'backup_running') {
    toast.error(t(`backups.toast.${kind}AlreadyRunning`));
    return;
  }
  if (error instanceof ApiError && error.code === 'sync_busy') {
    toast.error(t('backups.toast.profileBusy'));
    return;
  }
  if (error instanceof FollowLostError) {
    toast.warning(error.message);
    return;
  }
  toast.error(error.message || t(kind === 'backup' ? 'backups.toast.startFailed' : 'backups.toast.restoreFailed'));
}

/** mutationKey of a manual run; the variables are the target id. */
export const runBackupKey = (slug: string) => ['backups', slug, 'run'];

export function useBackupTargets (slug: string) {
  return useQuery({
    queryKey:        ['backups', slug],
    queryFn:         () => api.getBackupTargets(slug),
    enabled:         !!slug,
    refetchInterval: 30_000,
  });
}

export function useSnapshots (slug: string, id: number) {
  return useQuery({
    queryKey: ['backups', slug, id, 'snapshots'],
    queryFn:  () => api.getSnapshots(slug, id),
    enabled:  !!slug && id > 0,
  });
}

/** One folder (or search) of a snapshot, a page at a time. Snapshots never change. */
export function useSnapshotFiles (slug: string, id: number, snapshotId: string, query: SnapshotFilesQuery, enabled = true) {
  return useQuery({
    queryKey:        ['backups', slug, id, 'files', snapshotId, query],
    queryFn:         () => api.getSnapshotFiles(slug, id, snapshotId, query),
    enabled:         enabled && !!slug && id > 0 && !!snapshotId,
    staleTime:       5 * 60_000,
    // Keep the previous page on screen while the next one loads.
    placeholderData: (previous) => previous,
  });
}

/** What a full restore would change (POST .../restore/preview); changes nothing. */
export function useRestorePreview (slug: string, id: number, snapshotId: string, scope: RestoreScope, enabled = true) {
  return useQuery({
    queryKey:  ['backups', slug, id, 'preview', snapshotId, scope],
    queryFn:   () => api.previewRestore(slug, id, { snapshot_id: snapshotId, restore_scope: scope }),
    enabled:   enabled && !!slug && id > 0 && !!snapshotId,
    staleTime: 0,
    gcTime:    0,
  });
}

export function useCreateBackupTarget (slug: string) {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (data: BackupTargetCreateRequest) => api.createBackupTarget(slug, data),
    onSuccess:  (target) => {
      queryClient.invalidateQueries({ queryKey: ['backups', slug] });
      toast.success(t('backups.toast.created', { name: target.name }));
    },
    onError: (error: Error) => {
      toast.error(error.message || t('backups.toast.createFailed'));
    },
  });
}

export function useUpdateBackupTarget (slug: string) {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: BackupTargetUpdateRequest }) =>
      api.updateBackupTarget(slug, id, data),
    onSuccess: (target) => {
      queryClient.invalidateQueries({ queryKey: ['backups', slug] });
      toast.success(t('backups.toast.updated', { name: target.name }));
    },
    onError: (error: Error) => {
      toast.error(error.message || t('backups.toast.updateFailed'));
    },
  });
}

export function useDeleteBackupTarget (slug: string) {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (id: number) => api.deleteBackupTarget(slug, id),
    onSuccess:  () => {
      queryClient.invalidateQueries({ queryKey: ['backups', slug] });
      toast.success(t('backups.toast.deleted'));
    },
    onError: (error: Error) => {
      toast.error(error.message || t('backups.toast.deleteFailed'));
    },
  });
}

export function useRunBackup (slug: string, intervalMs = BACKUP_POLL_MS) {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationKey: runBackupKey(slug),
    mutationFn:  async (id: number) =>
      followBackupJob(queryClient, slug, id, await api.runBackup(slug, id), 'backup', t, intervalMs),
    onSuccess: (job) => toastBackupJob(job, 'backup', t),
    onError:   (error: Error) => toastBackupError(error, 'backup', t),
    // Targets (last status) and snapshots (history) change either way.
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['backups', slug] }),
  });
}

/** Restore chosen files and folders of a snapshot (POST .../restore-files). */
export function useRestoreFiles (slug: string, intervalMs = BACKUP_POLL_MS) {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: async ({ id, data }: { id: number; data: RestoreFilesRequest }) =>
      followBackupJob(queryClient, slug, id, await api.restoreFiles(slug, id, data), 'restore', t, intervalMs),
    onSuccess: (job) => toastBackupJob(job, 'restore', t),
    onError:   (error: Error) => toastBackupError(error, 'restore', t),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['backups', slug] });
      // Restored into a mirror profile's folder, automatic syncing is paused.
      queryClient.invalidateQueries({ queryKey: ['profiles', slug] });
    },
  });
}

export function useRestore (slug: string, intervalMs = BACKUP_POLL_MS) {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: async ({ id, data }: { id: number; data: RestoreRequest }) =>
      followBackupJob(queryClient, slug, id, await api.restoreBackup(slug, id, data), 'restore', t, intervalMs),
    onSuccess: (job) => toastBackupJob(job, 'restore', t),
    onError:   (error: Error) => toastBackupError(error, 'restore', t),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['backups', slug] });
      // A one-sided restore pauses automatic syncing of the profile.
      queryClient.invalidateQueries({ queryKey: ['profiles', slug] });
    },
  });
}
