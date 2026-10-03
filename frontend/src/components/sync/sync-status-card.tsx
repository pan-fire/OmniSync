'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { useTranslation } from '@/i18n';
import { formatDateTime } from '@/lib/format';
import type { SyncStatus } from '@/types';
import { LastErrorNotice } from './last-error-notice';
import { SyncProgressView } from './sync-progress';

interface SyncStatusCardProps {
  status:  SyncStatus | undefined;
  isError: boolean;
}

export function stateBadgeVariant (state: string): 'default' | 'secondary' | 'destructive' {
  switch (state) {
    case 'pushing':
    case 'pulling':
    case 'syncing':
      return 'default';
    case 'error':
      return 'destructive';
    default:
      return 'secondary';
  }
}

export function SyncStatusCard ({ status, isError }: SyncStatusCardProps) {
  const { t, locale } = useTranslation();

  if (isError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t('dashboard.title')}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-destructive">{t('common.error')}</p>
        </CardContent>
      </Card>
    );
  }

  if (!status) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t('dashboard.title')}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground">{t('common.loading')}</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('dashboard.title')}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={stateBadgeVariant(status.state)}>
            {t(`dashboard.state.${status.state}`)}
          </Badge>
          {status.user_paused && <Badge variant="outline">{t('pause.byUser')}</Badge>}
        </div>
        <SyncProgressView progress={status.progress} />
        {status.outside_sync_window && (
          <p className="text-xs text-muted-foreground" data-testid="sync-window-closed">
            {status.waiting_for_window ? t('syncLimits.waiting') : t('syncLimits.closed')}
            {status.next_window_start && ` ${t('syncLimits.opensAt', { time: formatDateTime(status.next_window_start, locale) })}`}
          </p>
        )}
        <div className="grid grid-cols-2 gap-2 text-sm">
          <span className="text-muted-foreground">{t('dashboard.lastSync')}</span>
          <span>{formatDateTime(status.last_sync, locale, t('dashboard.noSync'))}</span>
          <span className="text-muted-foreground">{t('dashboard.filesProcessed')}</span>
          <span>{status.files_processed}</span>
          <span className="text-muted-foreground">{t('dashboard.errors')}</span>
          <span>{status.errors}</span>
        </div>
        <LastErrorNotice error={status.last_error} />
      </CardContent>
    </Card>
  );
}
