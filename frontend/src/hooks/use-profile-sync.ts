'use client';

import { useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { api, waitForSelectiveSync, waitForSyncJob } from '@/lib/api';
import type { SyncDirection, SelectiveSyncItem, SyncStartResponse, SyncState } from '@/types';
import { useTranslation, type TranslationVars } from '@/i18n';

/** A push, a pull or a two-way sync (or resync) is running. */
export function isActiveState (state: SyncState | undefined): boolean {
  return state === 'pushing' || state === 'pulling' || state === 'syncing';
}

/** Poll every 2 s while a sync runs, otherwise every 30 s. */
export function getPollingInterval (state: SyncState | undefined): number {
  return isActiveState(state) ? 2000 : 30000;
}

export function syncCheckQueryKey (slug: string) {
  return ['profiles', slug, 'sync', 'check'] as const;
}

export function syncPreviewQueryKey (slug: string) {
  return ['profiles', slug, 'sync', 'preview'] as const;
}

export interface SyncTarget {
  slug: string;
  name: string;
}

type Translate = (key: string, vars?: TranslationVars) => string;

function refreshSyncQueries (queryClient: QueryClient) {
  queryClient.invalidateQueries({ queryKey: ['profiles'] });
  queryClient.invalidateQueries({ queryKey: ['sync', 'status'] });
  queryClient.invalidateQueries({ queryKey: ['jobs'] });
}

/** The refusal of a start the server answered at once (it ended before changing anything). */
export function startRefusal (resp: SyncStartResponse): string | null {
  if (resp.error) return resp.error;
  return resp.state === 'error' ? '' : null;
}

/**
 * Follow a started sync (or resync) until its job ends, then refresh the
 * status queries and tell the user how it went. Polls at the fast refetch
 * interval of a running sync. It runs outside any component, so the outcome
 * is shown even when the page that started it is left. It never rejects.
 */
export async function followSyncJob (
  queryClient: QueryClient,
  target: SyncTarget,
  jobId: number,
  t: Translate,
  intervalMs = getPollingInterval('syncing')
): Promise<void> {
  // The status queries now see the running sync and poll fast.
  refreshSyncQueries(queryClient);
  let job;
  try {
    job = await waitForSyncJob(jobId, { intervalMs });
  } catch {
    toast.error(t('dashboard.syncFollowLost', { name: target.name }));
    refreshSyncQueries(queryClient);
    return;
  }
  refreshSyncQueries(queryClient);
  if (job.status === 'completed') {
    toast.success(t('dashboard.syncFinished', { name: target.name, count: job.files_changed }));
    return;
  }
  // Why it failed or was stopped is the profile's last_error.
  let reason: string | null | undefined = null;
  try {
    reason = (await api.getProfile(target.slug)).last_error;
  } catch {
    reason = null;
  }
  toast.error(reason ? `${target.name}: ${reason}` : t('dashboard.syncEndedFailed', { name: target.name }));
}

/**
 * Start a push, pull or two-way sync on each target profile via
 * POST /profiles/{slug}/sync/start. Profiles run in parallel; one failing
 * does not stop the others. `force` starts a paused profile too; send it
 * only for a sync the user has confirmed. The server answers once each run
 * is under way; every started job is then followed until it ends
 * (followSyncJob), and a run refused at once shows its reason.
 */
export function useStartSyncs () {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: async ({ direction, targets, force = false }: { direction: SyncDirection; targets: SyncTarget[]; force?: boolean }) => {
      const results = await Promise.allSettled(
        targets.map((p) => api.startProfileSync(p.slug, direction, force))
      );
      return results.map((r, i) => ({ target: targets[i], result: r }));
    },
    onSuccess: (outcomes) => {
      refreshSyncQueries(queryClient);
      const running = outcomes.flatMap((o) =>
        o.result.status === 'fulfilled' && startRefusal(o.result.value) === null
          ? [{ target: o.target, jobId: o.result.value.job_id }]
          : []
      );
      if (running.length > 0) {
        toast.success(t('dashboard.syncStarted', { count: running.length }));
      }
      for (const o of outcomes) {
        if (o.result.status === 'rejected') {
          const reason = o.result.reason;
          const message = reason instanceof Error ? reason.message : t('dashboard.syncFailed');
          toast.error(`${o.target.name}: ${message}`);
        } else {
          const refusal = startRefusal(o.result.value);
          if (refusal !== null) {
            toast.error(`${o.target.name}: ${refusal || t('dashboard.syncFailed')}`);
          }
        }
      }
      for (const o of outcomes) {
        if (o.result.status === 'fulfilled' && o.result.value.note) toast.info(`${o.target.name}: ${o.result.value.note}`);
      }
      for (const { target, jobId } of running) {
        followSyncJob(queryClient, target, jobId, t);
      }
    },
  });
}

export function useStopProfileSync (slug: string) {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: () => api.stopProfileSync(slug),
    onSuccess:  () => {
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
      queryClient.invalidateQueries({ queryKey: ['sync', 'status'] });
      toast.success(t('dashboard.syncStopped'));
    },
    onError: (error: Error) => {
      toast.error(error.message || t('common.error'));
    },
  });
}

/** How often per-file actions are polled while they run. */
export const SELECTIVE_POLL_MS = 1000;

/**
 * Per-file actions (POST .../sync/selective). The server answers at once
 * with the running job; the mutation follows it (waitForSelectiveSync) and
 * resolves with the result and its per-file errors.
 */
export function useProfileSelectiveSync (slug: string, intervalMs = SELECTIVE_POLL_MS) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (items: SelectiveSyncItem[]) => {
      const started = await api.profileSelectiveSync(slug, items);
      if (started.status !== 'running') return started;
      queryClient.invalidateQueries({ queryKey: ['profiles', slug] });
      return waitForSelectiveSync(slug, started.job_id, { intervalMs });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
      queryClient.invalidateQueries({ queryKey: ['profiles', slug] });
    },
    // No toast here: the caller knows the action and names it in its
    // own success and error toasts (ProfileDiffPanel).
  });
}

export function useResumeProfileIntervals (slug: string) {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: () => api.resumeProfileIntervals(slug),
    onSuccess:  () => {
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
      queryClient.invalidateQueries({ queryKey: ['sync', 'status'] });
      toast.success(t('intervalsPaused.resumed'));
    },
    onError: (error: Error) => {
      toast.error(error.message || t('common.error'));
    },
  });
}

/**
 * POST /profiles/{slug}/sync/resync: the union of both sides, nothing
 * deleted, then automatic syncing resumes. Call it only after the user
 * confirmed (the resync dialog). The resync job is followed until it ends.
 */
export function useResyncProfile (slug: string, name: string = slug) {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: () => api.resyncProfile(slug),
    onSuccess:  (resp) => {
      refreshSyncQueries(queryClient);
      const refusal = startRefusal(resp);
      if (refusal !== null) {
        toast.error(`${name}: ${refusal || t('common.error')}`);
        return;
      }
      toast.success(t('twoWay.resyncStarted'));
      followSyncJob(queryClient, { slug, name }, resp.job_id, t);
    },
    onError: (error: Error) => {
      toast.error(error.message || t('common.error'));
    },
  });
}
