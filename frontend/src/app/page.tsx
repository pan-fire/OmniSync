'use client';

import Link from 'next/link';
import { ArrowDown, ArrowUp, PauseCircle } from 'lucide-react';
import { useHealth } from '@/hooks/use-health';
import { useHydrated } from '@/hooks/use-hydrated';
import { useAggregateStatus } from '@/hooks/use-aggregate-status';
import { useProfiles } from '@/hooks/use-profiles';
import { isActiveState } from '@/hooks/use-profile-sync';
import { useConfirmedSync } from '@/components/sync/sync-confirm-dialog';
import { AggregateStatusCard } from '@/components/sync/aggregate-status-card';
import { stateBadgeVariant } from '@/components/sync/sync-status-card';
import { LastErrorNotice } from '@/components/sync/last-error-notice';
import { SyncProgressView } from '@/components/sync/sync-progress';
import { SyncControls } from '@/components/sync/sync-controls';
import { HealthIndicators } from '@/components/sync/health-indicators';
import { PendingChangesBanner } from '@/components/sync/pending-changes-banner';
import { IntervalsPausedBanner } from '@/components/sync/intervals-paused-banner';
import { MirrorProfilesNotice } from '@/components/profiles/mirror-notice';
import { FirstRunChecklist } from '@/components/profiles/first-run-checklist';
import { PageHelp } from '@/components/layout/page-help';
import { PageHeader } from '@/components/layout/page-header';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import { useTranslation } from '@/i18n';
import { formatDateTime, formatRelativeTime } from '@/lib/format';
import type { ProfileSummary } from '@/types';

const STATE_PRIORITY: Record<string, number> = {
  error: 4, pushing: 3, pulling: 3, syncing: 3, idle: 1,
};

export function sortProfileCards (profiles: ProfileSummary[]): ProfileSummary[] {
  return [...profiles].sort((a, b) => {
    // Paused with pending first
    const aPausedPending = a.intervals_paused && a.pending_changes > 0 ? 1 : 0;
    const bPausedPending = b.intervals_paused && b.pending_changes > 0 ? 1 : 0;
    if (aPausedPending !== bPausedPending) return bPausedPending - aPausedPending;
    // Then by state priority (error > active > idle)
    const aPrio = STATE_PRIORITY[a.state] ?? 0;
    const bPrio = STATE_PRIORITY[b.state] ?? 0;
    if (aPrio !== bPrio) return bPrio - aPrio;
    // Then by name
    return a.name.localeCompare(b.name);
  });
}

export default function DashboardPage () {
  const { t, locale } = useTranslation();
  const health = useHealth();
  // The sidebar runs the same query: see useHydrated.
  const hydrated = useHydrated();
  const aggregate = useAggregateStatus();
  const profiles = useProfiles();
  const confirmedSync = useConfirmedSync();

  const pausedProfiles = aggregate.data?.paused_profiles ?? [];
  const profilesSummary = aggregate.data?.profiles_summary ?? [];
  // The aggregate covers the running engines only, i.e. enabled profiles;
  // disabled ones come from the profile list and are listed last.
  const disabledProfiles = (profiles.data ?? [])
    .filter((p) => p.enabled === false)
    .sort((a, b) => a.name.localeCompare(b.name));
  const sortedProfiles: Array<ProfileSummary & { disabled?: boolean }> = [
    ...sortProfileCards(profilesSummary),
    ...disabledProfiles.map((p) => ({ ...p, disabled: true })),
  ];
  const totalPending = aggregate.data?.total_pending_changes ?? 0;
  const hasAnyPaused = pausedProfiles.length > 0;
  const noProfiles = profiles.isSuccess && profiles.data.length === 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('dashboard.title')}
        actions={<PageHelp pageKey="dashboard" />}
      />
      {noProfiles && <FirstRunChecklist />}
      {/* Push/Pull/Check and the aggregate status mean nothing before the
          first profile: the checklist above says what to do instead. */}
      {!noProfiles && <SyncControls />}
      {hasAnyPaused && (
        <IntervalsPausedBanner pausedProfiles={pausedProfiles} />
      )}
      {totalPending > 0 && !hasAnyPaused && (
        <PendingChangesBanner profiles={profilesSummary} />
      )}
      <MirrorProfilesNotice />
      <div className="grid gap-6 md:grid-cols-2">
        {!noProfiles && <AggregateStatusCard status={aggregate.data} isError={aggregate.isError} />}
        <HealthIndicators health={hydrated ? health.data : undefined} isError={hydrated && health.isError} />
      </div>

      {/* Profiles overview */}
      {sortedProfiles.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>{t('profiles.title')}</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {sortedProfiles.map((p) => (
                // The name is a stretched link, so the whole row is clickable
                // and keyboard users get one ordinary link per row.
                <div
                  key={p.slug}
                  className={cn(
                    'relative space-y-1 rounded-md border p-3 hover:bg-accent focus-within:ring-2 focus-within:ring-ring',
                    p.disabled && 'border-dashed'
                  )}
                  data-testid={`dashboard-profile-${p.slug}`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                      {p.intervals_paused && (
                        <PauseCircle className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-500" aria-label={t('intervalsPaused.paused')} />
                      )}
                      <Link
                        href={`/profiles/${p.slug}`}
                        className="truncate font-medium outline-none after:absolute after:inset-0"
                      >
                        {p.name}
                      </Link>
                      {p.disabled
                        ? <Badge variant="outline">{t('profiles.disabled')}</Badge>
                        : (
                          <Badge variant={stateBadgeVariant(p.state)}>
                            {t(`dashboard.state.${p.state}`)}
                          </Badge>
                          )}
                      {p.resync_required && (
                        <Badge variant="destructive">{t('twoWay.resyncRequiredBadge')}</Badge>
                      )}
                      {p.user_paused && <Badge variant="outline">{t('pause.byUser')}</Badge>}
                      {p.pending_changes > 0 && (
                        <Badge variant="outline">{p.pending_changes}</Badge>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-3">
                      {p.pending_changes > 0 && (
                        <Link
                          href={`/profiles/${p.slug}?tab=differences`}
                          className="relative z-10 text-xs text-primary hover:underline"
                        >
                          {t('granular.reviewDiffs')}
                        </Link>
                      )}
                      {p.last_sync && (
                        <time
                          className="text-sm text-muted-foreground"
                          dateTime={p.last_sync}
                          title={formatDateTime(p.last_sync, locale)}
                        >
                          {formatRelativeTime(p.last_sync, locale)}
                        </time>
                      )}
                      {!p.disabled && (
                      <div className="relative z-10 flex gap-1">
                        <Button
                          size="icon"
                          variant="ghost"
                          className="pointer-coarse:size-11"
                          disabled={isActiveState(p.state) || confirmedSync.isStarting}
                          onClick={() => confirmedSync.request('push', [{ slug: p.slug, name: p.name }])}
                          aria-label={t('dashboard.pushProfile', { name: p.name })}
                          title={t('dashboard.pushProfile', { name: p.name })}
                        >
                          <ArrowUp className="h-4 w-4" aria-hidden="true" />
                        </Button>
                        <Button
                          size="icon"
                          variant="ghost"
                          className="pointer-coarse:size-11"
                          disabled={isActiveState(p.state) || confirmedSync.isStarting}
                          onClick={() => confirmedSync.request('pull', [{ slug: p.slug, name: p.name }])}
                          aria-label={t('dashboard.pullProfile', { name: p.name })}
                          title={t('dashboard.pullProfile', { name: p.name })}
                        >
                          <ArrowDown className="h-4 w-4" aria-hidden="true" />
                        </Button>
                      </div>
                      )}
                    </div>
                  </div>
                  {isActiveState(p.state) && <SyncProgressView progress={p.progress} compact />}
                  <LastErrorNotice error={p.last_error} compact />
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
      {confirmedSync.dialog}
    </div>
  );
}
