'use client';

import Link from 'next/link';
import { PauseCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { useTranslation } from '@/i18n';
import { useResumeProfileIntervals } from '@/hooks/use-profile-sync';
import { formatRelativeTime } from '@/lib/format';
import type { PausedProfileSummary } from '@/types';

interface IntervalsPausedBannerProps {
  pausedProfiles: PausedProfileSummary[];
}

function PausedProfileRow ({ profile }: { profile: PausedProfileSummary }) {
  const { t, locale } = useTranslation();
  const resumeMutation = useResumeProfileIntervals(profile.slug);
  const canResume = profile.pending_changes === 0;

  return (
    <div className="flex items-center justify-between gap-2 rounded-md border border-amber-500/20 bg-amber-500/5 p-2">
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-sm font-medium truncate">{profile.name}</span>
        {profile.pending_changes > 0 && (
          <Badge variant="secondary" className="text-xs shrink-0">
            {profile.pending_changes}
          </Badge>
        )}
        {profile.user_paused && (
          <Badge variant="outline" className="text-xs shrink-0">{t('pause.byUser')}</Badge>
        )}
        {profile.paused_at && (
          <span className="text-xs text-muted-foreground shrink-0">
            {formatRelativeTime(profile.paused_at, locale, '')}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <Button
          size="sm"
          variant="ghost"
          className="h-7 text-xs"
          asChild
        >
          <Link href={`/profiles/${profile.slug}?tab=differences`}>
            {t('intervalsPaused.review')}
          </Link>
        </Button>
        <Button
          size="sm"
          variant="default"
          className="h-7 text-xs"
          disabled={!canResume || resumeMutation.isPending}
          onClick={() => resumeMutation.mutate()}
          data-testid={`resume-${profile.slug}`}
        >
          {t('intervalsPaused.resume')}
        </Button>
      </div>
    </div>
  );
}

export function IntervalsPausedBanner ({ pausedProfiles }: IntervalsPausedBannerProps) {
  const { t } = useTranslation();

  if (pausedProfiles.length === 0) return null;

  const totalPending = pausedProfiles.reduce((sum, p) => sum + p.pending_changes, 0);
  const isSingle = pausedProfiles.length === 1;

  return (
    <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-4" data-testid="intervals-paused-banner">
      <div className="flex items-start gap-3">
        <PauseCircle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600 dark:text-amber-500" aria-hidden="true" />
        <div className="flex-1 space-y-1">
          <p className="text-sm font-medium">
            {isSingle
              ? t('intervalsPaused.titleSingle', { name: pausedProfiles[0].name })
              : t('intervalsPaused.titleMultiple', { count: pausedProfiles.length })}
          </p>
          <p className="text-xs text-muted-foreground">
            {pausedProfiles.every((p) => p.user_paused && p.pending_changes === 0)
              ? t('pause.bannerByUser')
              : t('intervalsPaused.description', { count: totalPending })}
          </p>
        </div>
      </div>

      <div className="mt-3 space-y-2">
        {pausedProfiles.map((profile) => (
          <PausedProfileRow key={profile.slug} profile={profile} />
        ))}
      </div>
    </div>
  );
}
