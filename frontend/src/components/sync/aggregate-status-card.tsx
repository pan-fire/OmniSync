'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { useTranslation } from '@/i18n';
import { stateBadgeVariant } from './sync-status-card';
import { formatDateTime } from '@/lib/format';
import type { AggregateStatus } from '@/types';

interface AggregateStatusCardProps {
  status:  AggregateStatus | undefined;
  isError: boolean;
}

/** Status across all running profiles (GET /sync/status/aggregate). */
export function AggregateStatusCard ({ status, isError }: AggregateStatusCardProps) {
  const { t, locale } = useTranslation();

  let body;
  if (isError) {
    body = <p className="text-destructive">{t('common.error')}</p>;
  } else if (!status) {
    body = <p className="text-muted-foreground">{t('common.loading')}</p>;
  } else {
    const lastSync = status.profiles_summary
      .map((p) => p.last_sync)
      .filter((d): d is string => !!d)
      .sort()
      .pop();
    body = (
      <div className="space-y-3">
        <Badge variant={stateBadgeVariant(status.overall_state)}>
          {t(`dashboard.state.${status.overall_state}`)}
        </Badge>
        <div className="grid grid-cols-2 gap-2 text-sm">
          <span className="text-muted-foreground">{t('dashboard.enabledProfiles')}</span>
          <span>{status.profiles_summary.length}</span>
          <span className="text-muted-foreground">{t('dashboard.lastSync')}</span>
          <span>{formatDateTime(lastSync, locale, t('dashboard.noSync'))}</span>
          <span className="text-muted-foreground">{t('profiles.pendingChanges')}</span>
          <span>{status.total_pending_changes}</span>
        </div>
      </div>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('dashboard.syncStatusTitle')}</CardTitle>
      </CardHeader>
      <CardContent>{body}</CardContent>
    </Card>
  );
}
