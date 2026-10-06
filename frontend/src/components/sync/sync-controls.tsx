'use client';

import { useState } from 'react';
import { ArrowUp, ArrowDown, ArrowUpDown, Pause, Play, Square, RefreshCw } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { useTranslation } from '@/i18n';
import { useProfiles } from '@/hooks/use-profiles';
import { useAggregateStatus } from '@/hooks/use-aggregate-status';
import { isActiveState, syncCheckQueryKey, type SyncTarget } from '@/hooks/use-profile-sync';
import { api } from '@/lib/api';
import { ProfileDiffTabs } from './profile-diff-tabs';
import { ShowDiffButton } from './show-diff-button';
import { useConfirmedSync } from './sync-confirm-dialog';
import { StopSyncDialog } from './stop-sync-dialog';
import { usePauseAll, useResumeAll } from '@/hooks/use-pause';

export { isActiveState };

/**
 * Dashboard controls. Every action works per profile through
 * /profiles/{slug}/sync/*: push and pull cover all enabled profiles behind
 * one confirmation, "Sync now" covers the enabled two-way profiles (except
 * those waiting for a resync) behind one confirmation, stop covers the
 * profiles that are running, after a confirmation that names them.
 */
export function SyncControls () {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const profiles = useProfiles();
  const aggregate = useAggregateStatus();
  const confirmedSync = useConfirmedSync();
  const [checking, setChecking] = useState(false);
  const [stopping, setStopping] = useState(false);
  // The running profiles when Stop was pressed: the dialog names these and
  // only these are stopped, even if the status changes while it is open.
  const [stopTargets, setStopTargets] = useState<SyncTarget[] | null>(null);
  const [showDiff, setShowDiff] = useState(false);
  const pauseAll = usePauseAll();
  const resumeAll = useResumeAll();

  const enabledProfiles = (profiles.data ?? []).filter((p) => p.enabled);
  const hasNoEnabledProfiles = profiles.isSuccess && enabledProfiles.length === 0;
  const runningProfiles = (aggregate.data?.profiles_summary ?? []).filter((p) => isActiveState(p.state));
  const active = runningProfiles.length > 0;
  const targets = enabledProfiles.map((p) => ({ slug: p.slug, name: p.name }));
  const twoWayTargets = enabledProfiles
    .filter((p) => p.sync_mode === 'two_way' && !p.resync_required)
    .map((p) => ({ slug: p.slug, name: p.name }));

  const handleCheck = async () => {
    setChecking(true);
    try {
      const results = await Promise.allSettled(targets.map((p) => api.checkProfileSync(p.slug)));
      results.forEach((r, i) => {
        if (r.status === 'fulfilled') {
          queryClient.setQueryData(syncCheckQueryKey(targets[i].slug), r.value);
        } else {
          const message = r.reason instanceof Error ? r.reason.message : t('common.error');
          toast.error(`${targets[i].name}: ${message}`);
        }
      });
      queryClient.invalidateQueries({ queryKey: ['sync', 'status'] });
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
    } finally {
      setChecking(false);
    }
  };

  const handleStop = async (stopped: SyncTarget[]) => {
    setStopTargets(null);
    setStopping(true);
    try {
      const results = await Promise.allSettled(stopped.map((p) => api.stopProfileSync(p.slug)));
      results.forEach((r, i) => {
        if (r.status === 'rejected') {
          const message = r.reason instanceof Error ? r.reason.message : t('common.error');
          toast.error(`${stopped[i].name}: ${message}`);
        }
      });
      queryClient.invalidateQueries({ queryKey: ['sync', 'status'] });
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
    } finally {
      setStopping(false);
    }
  };

  const disabled = active || hasNoEnabledProfiles || targets.length === 0;
  // Pause all holds every enabled profile's automatic syncs; resume all
  // lifts only those pauses (not a pause OmniSync set to protect files).
  const anyUserPaused = enabledProfiles.some((p) => p.user_paused);
  const anyRunningFree = enabledProfiles.some((p) => !p.user_paused);

  return (
    <div className="space-y-4">
      {hasNoEnabledProfiles && (
        <Alert>
          <AlertTitle>{t('dashboard.noActiveProfilesTitle')}</AlertTitle>
          <AlertDescription>{t('dashboard.noActiveProfilesHint')}</AlertDescription>
        </Alert>
      )}

      <div className="flex flex-wrap gap-2">
        {twoWayTargets.length > 0 && (
          <Button
            onClick={() => confirmedSync.request('two_way', twoWayTargets)}
            disabled={disabled || confirmedSync.isStarting}
          >
            <ArrowUpDown className="h-4 w-4" aria-hidden="true" />
            {t('twoWay.syncNow')}
          </Button>
        )}
        <Button
          onClick={() => confirmedSync.request('push', targets)}
          disabled={disabled || confirmedSync.isStarting}
        >
          <ArrowUp className="h-4 w-4" aria-hidden="true" />
          {t('dashboard.push')}
        </Button>
        <Button
          onClick={() => confirmedSync.request('pull', targets)}
          disabled={disabled || confirmedSync.isStarting}
        >
          <ArrowDown className="h-4 w-4" aria-hidden="true" />
          {t('dashboard.pull')}
        </Button>
        <Button
          variant="outline"
          onClick={handleCheck}
          disabled={disabled || checking}
        >
          <RefreshCw className={`h-4 w-4 ${checking ? 'animate-spin' : ''}`} aria-hidden="true" />
          {t('dashboard.check')}
        </Button>
        <ShowDiffButton
          shown={showDiff}
          onShow={() => setShowDiff(true)}
          onHide={() => setShowDiff(false)}
          disabled={active || hasNoEnabledProfiles}
        />
        {anyRunningFree && (
          <Button
            variant="outline"
            onClick={() => pauseAll.mutate()}
            disabled={pauseAll.isPending}
            title={t('pause.pauseAllHelp')}
          >
            <Pause className="h-4 w-4" aria-hidden="true" />
            {t('pause.pauseAll')}
          </Button>
        )}
        {anyUserPaused && (
          <Button
            variant="outline"
            onClick={() => resumeAll.mutate()}
            disabled={resumeAll.isPending}
            title={t('pause.resumeAllHelp')}
          >
            <Play className="h-4 w-4" aria-hidden="true" />
            {t('pause.resumeAll')}
          </Button>
        )}
        {active && (
          <Button
            variant="destructive"
            onClick={() => setStopTargets(runningProfiles.map((p) => ({ slug: p.slug, name: p.name })))}
            disabled={stopping}
          >
            <Square className="h-4 w-4" aria-hidden="true" />
            {t('dashboard.stop')}
          </Button>
        )}
      </div>

      {showDiff && aggregate.data && (
        <ProfileDiffTabs profiles={aggregate.data.profiles_summary} />
      )}

      {confirmedSync.dialog}

      <StopSyncDialog
        profiles={stopTargets}
        onCancel={() => setStopTargets(null)}
        isPending={stopping}
        onConfirm={handleStop}
      />
    </div>
  );
}
