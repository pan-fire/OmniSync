'use client';

import { useState } from 'react';
import {
  AlertTriangle, ArrowUp, ArrowDown, RefreshCw,
  X, ChevronDown, ChevronUp,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { useTranslation, type TranslationVars } from '@/i18n';
import { PathText } from '@/components/shared/path-text';
import { api } from '@/lib/api';
import { syncCheckQueryKey } from '@/hooks/use-profile-sync';
import { useQueries, useQueryClient } from '@tanstack/react-query';
import { useConfirmedSync } from './sync-confirm-dialog';
import type { ProfileSummary, SyncCheckResult } from '@/types';

interface PendingChangesBannerProps {
  /** All profiles; the banner acts on those with pending changes. */
  profiles: ProfileSummary[];
}

export function PendingChangesBanner ({ profiles }: PendingChangesBannerProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const confirmedSync = useConfirmedSync();
  const [dismissed, setDismissed] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const withPending = profiles.filter((p) => p.pending_changes > 0);
  const pendingChanges = withPending.reduce((n, p) => n + p.pending_changes, 0);

  const checks = useQueries({
    queries: withPending.map((p) => ({
      queryKey:  syncCheckQueryKey(p.slug),
      queryFn:   () => api.checkProfileSync(p.slug),
      enabled:   expanded,
      staleTime: 60_000,
    })),
  });

  if (dismissed || pendingChanges === 0) return null;

  const isChecking = checks.some((c) => c.isFetching);

  const handleRecheck = () => {
    for (const p of withPending) {
      queryClient.invalidateQueries({ queryKey: syncCheckQueryKey(p.slug) });
    }
    queryClient.invalidateQueries({ queryKey: ['sync', 'status'] });
  };

  const handleSync = (direction: 'push' | 'pull') => {
    confirmedSync.request(direction, withPending.map((p) => ({ slug: p.slug, name: p.name })));
  };

  return (
    <div className="rounded-lg border border-yellow-500/30 bg-yellow-500/10 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-yellow-600 dark:text-yellow-500" aria-hidden="true" />
          <div className="space-y-1">
            <p className="text-sm font-medium">
              {t('syncCheck.title', { count: pendingChanges })}
            </p>
            <p className="text-xs text-muted-foreground">
              {t('syncCheck.description')}
            </p>
          </div>
        </div>
        <Button
          variant="ghost" size="icon"
          className="h-6 w-6 shrink-0"
          onClick={() => setDismissed(true)}
          aria-label={t('syncCheck.dismiss')}
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" onClick={() => handleSync('push')} disabled={confirmedSync.isStarting}>
          <ArrowUp className="h-3.5 w-3.5" />
          {t('syncCheck.pushAll')}
        </Button>
        <Button size="sm" variant="outline" onClick={() => handleSync('pull')} disabled={confirmedSync.isStarting}>
          <ArrowDown className="h-3.5 w-3.5" />
          {t('syncCheck.pullAll')}
        </Button>
        <Button size="sm" variant="ghost" onClick={handleRecheck} disabled={isChecking}>
          <RefreshCw className={`h-3.5 w-3.5 ${isChecking ? 'animate-spin' : ''}`} />
          {t('syncCheck.recheck')}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setExpanded(!expanded)}>
          {expanded
            ? <ChevronUp className="h-3.5 w-3.5" />
            : <ChevronDown className="h-3.5 w-3.5" />}
          {t('syncCheck.details')}
        </Button>
      </div>

      {expanded && withPending.map((p, i) => {
        const check = checks[i];
        return (
          <div key={p.slug} className="mt-3">
            {withPending.length > 1 && <p className="text-xs font-medium">{p.name}</p>}
            {check?.isFetching && (
              <p className="text-xs text-muted-foreground">{t('common.loading')}</p>
            )}
            {check?.isError && (
              <p className="text-xs text-destructive">{check.error.message}</p>
            )}
            {check?.data && !check.isFetching && <FileList detail={check.data} t={t} />}
          </div>
        );
      })}

      {confirmedSync.dialog}
    </div>
  );
}

function FileList ({ detail, t }: { detail: SyncCheckResult; t: (key: string, vars?: TranslationVars) => string }) {
  return (
    <div className="mt-1 space-y-2 text-xs">
      {detail.local_only.length > 0 && (
        <FileSection
          label={t('syncCheck.localOnly', { count: detail.local_only.length })}
          files={detail.local_only}
          t={t}
        />
      )}
      {detail.remote_only.length > 0 && (
        <FileSection
          label={t('syncCheck.remoteOnly', { count: detail.remote_only.length })}
          files={detail.remote_only}
          t={t}
        />
      )}
      {detail.differ.length > 0 && (
        <FileSection
          label={t('syncCheck.differ', { count: detail.differ.length })}
          files={detail.differ}
          t={t}
        />
      )}
      {detail.error && <p className="text-destructive">{detail.error}</p>}
    </div>
  );
}

function FileSection ({ label, files, t }: { label: string; files: string[]; t: (key: string, vars?: TranslationVars) => string }) {
  const MAX = 10;
  return (
    <div>
      <Badge variant="secondary" className="mb-1">{label}</Badge>
      <ul className="ms-4 list-disc text-muted-foreground">
        {files.slice(0, MAX).map((f) => <li key={f}><PathText>{f}</PathText></li>)}
        {files.length > MAX && (
          <li>…{t('syncCheck.andMore', { count: files.length - MAX })}</li>
        )}
      </ul>
    </div>
  );
}
