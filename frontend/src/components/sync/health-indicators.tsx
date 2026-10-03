'use client';

import { CheckCircle, AlertTriangle, HelpCircle } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { useRemoteHealth } from '@/hooks/use-health';
import { useTranslation } from '@/i18n';
import type { Health } from '@/types';

interface HealthIndicatorsProps {
  health:  Health | undefined;
  isError: boolean;
}

export interface HealthCheck {
  labelKey: string;
  ok:       boolean;
}

/**
 * The local checks of GET /health. Remote reachability is not one of them:
 * /health no longer checks it (remote_accessible is always null), it comes
 * from GET /health/remotes per remote.
 */
export function getHealthChecks (health: Health): HealthCheck[] {
  return [
    { labelKey: 'health.rcloneInstalled', ok: health.rclone_installed },
    { labelKey: 'health.databaseOk', ok: health.database_ok },
  ];
}

function formatUptime (seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

/** One row per remote a running profile uses; unknown states say so. */
function RemoteChecks () {
  const { t } = useTranslation();
  const { data, isLoading, isError } = useRemoteHealth();

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
        <HelpCircle className="h-4 w-4" aria-hidden="true" />
        <span>{t('health.remotesChecking')}</span>
      </div>
    );
  }
  if (isError) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <HelpCircle className="h-4 w-4" aria-hidden="true" />
        <span>{t('health.remotesUnknown')}</span>
      </div>
    );
  }
  // No running profile uses a remote: nothing to report.
  const remotes = data?.remotes ?? [];
  if (remotes.length === 0) return null;

  return (
    <ul className="space-y-3" aria-label={t('health.remotesTitle')}>
      {remotes.map((r) => (
        <li key={r.remote} className="flex flex-wrap items-center gap-2">
          {r.accessible
            ? <CheckCircle className="h-4 w-4 text-green-600 dark:text-green-500" aria-hidden="true" />
            : <AlertTriangle className="h-4 w-4 text-destructive" aria-hidden="true" />}
          <span>
            {r.accessible
              ? t('health.remoteReachable', { remote: r.remote })
              : t('health.remoteUnreachable', { remote: r.remote })}
          </span>
          {!r.accessible && <Badge variant="destructive">{t('health.warning')}</Badge>}
          {r.profiles.length > 0 && (
            <span className="text-xs text-muted-foreground">
              {t('health.remoteUsedBy', { profiles: r.profiles.join(', ') })}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

export function HealthIndicators ({ health, isError }: HealthIndicatorsProps) {
  const { t } = useTranslation();

  if (isError || !health) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t('health.title')}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground">
            {isError ? t('common.error') : t('common.loading')}
          </p>
        </CardContent>
      </Card>
    );
  }

  const checks = getHealthChecks(health);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('health.title')}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {checks.map((check) => (
          <div key={check.labelKey} className="flex items-center gap-2">
            {check.ok
              ? (
              <CheckCircle className="h-4 w-4 text-green-600 dark:text-green-500" aria-hidden="true" />
                )
              : (
              <AlertTriangle className="h-4 w-4 text-destructive" aria-hidden="true" />
                )}
            <span>{t(check.labelKey)}</span>
            {!check.ok && (
              <Badge variant="destructive">{t('health.warning')}</Badge>
            )}
          </div>
        ))}
        <RemoteChecks />
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span>{t('health.uptime')}</span>
          <span>{formatUptime(health.uptime_seconds)}</span>
        </div>
      </CardContent>
    </Card>
  );
}
