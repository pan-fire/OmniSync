'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Separator } from '@/components/ui/separator';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { useTranslation } from '@/i18n';
import {
  useNotificationConfig,
  useChannelStatus,
  useUpdateNotificationConfig,
} from '@/hooks/use-notifications';
import { ChannelCard } from '@/components/notifications/channel-card';
import { TestNotificationButton } from '@/components/notifications/test-notification-button';
import { NotificationHistory } from '@/components/notifications/notification-history';
import { PageHeader } from '@/components/layout/page-header';
import {
  browserPushState,
  subscribeToPush,
  syncPushSubscription,
  unsubscribeFromPush,
  type BrowserPushState,
} from '@/lib/push';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import type { NotificationSeverity } from '@/types';

export default function NotificationsPage () {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const config = useNotificationConfig();
  const status = useChannelStatus();
  const updateConfig = useUpdateNotificationConfig();
  const [browserPush, setBrowserPush] = useState<BrowserPushState | undefined>(undefined);

  const channels = useMemo(() => config.data?.channels ?? {}, [config.data?.channels]);
  const channelStatuses = status.data?.channels ?? {};
  const channelNames = Object.keys(channels);
  const webpushEnabled = channels.webpush?.enabled === true;

  const refreshStatus = useCallback(
    () => queryClient.invalidateQueries({ queryKey: ['notifications', 'channels'] }),
    [queryClient]
  );

  // While Web Push is on, keep this browser's subscription registered with
  // the backend (no permission prompt: that needs a click, see below).
  useEffect(() => {
    let cancelled = false;
    const run = webpushEnabled ? syncPushSubscription() : browserPushState();
    run.then((state) => {
      if (cancelled) return;
      setBrowserPush(state);
      if (webpushEnabled && state === 'subscribed') refreshStatus();
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [webpushEnabled, refreshStatus]);

  const enableBrowserPush = useCallback(async (): Promise<boolean> => {
    const result = await subscribeToPush();
    setBrowserPush(await browserPushState());
    if (!result.ok) {
      const text = t(`notifications.pushError.${result.reason}`);
      toast.error(result.message ? `${text}: ${result.message}` : text);
      return false;
    }
    refreshStatus();
    return true;
  }, [t, refreshStatus]);

  const handleToggle = useCallback(
    async (channelName: string, enabled: boolean) => {
      if (channelName === 'webpush') {
        if (enabled) {
          if (!(await enableBrowserPush())) return;
        } else {
          await unsubscribeFromPush().catch(() => {});
          setBrowserPush(await browserPushState());
        }
      }
      updateConfig.mutate({ channels: { [channelName]: { enabled } } });
    },
    [updateConfig, enableBrowserPush]
  );

  const handleSeverityChange = useCallback(
    (channelName: string, severity: NotificationSeverity) => {
      updateConfig.mutate({ channels: { [channelName]: { min_severity: severity } } });
    },
    [updateConfig]
  );

  const unknownChannels = status.data?.unknown_channels ?? [];

  return (
    <div className="space-y-6">
      <PageHeader title={t('notifications.title')} />

      {config.isLoading && <p className="text-muted-foreground" role="status">{t('common.loading')}</p>}
      {config.isError && (
        <div className="flex items-center gap-3" role="alert">
          <p className="text-destructive">{t('notifications.loadFailed')}</p>
          <Button variant="outline" size="sm" onClick={() => config.refetch()}>{t('common.retry')}</Button>
        </div>
      )}
      {status.isError && (
        <div className="flex items-center gap-3" role="alert">
          <p className="text-destructive">{t('notifications.statusFailed')}</p>
          <Button variant="outline" size="sm" onClick={() => status.refetch()}>{t('common.retry')}</Button>
        </div>
      )}
      {status.data?.config_error && (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{t('notifications.configError')}</AlertDescription>
        </Alert>
      )}
      {unknownChannels.length > 0 && (
        <Alert variant="default" className="border-amber-500/30 bg-amber-500/5" role="alert">
          <AlertDescription>
            {t('notifications.unknownChannels', { names: unknownChannels.join(', ') })}
          </AlertDescription>
        </Alert>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        {channelNames.map((name) => (
          <ChannelCard
            key={name}
            channelName={name}
            config={channels[name]}
            status={channelStatuses[name] ?? { available: false }}
            statusLoading={status.isLoading}
            browserPush={name === 'webpush' ? browserPush : undefined}
            onEnableBrowser={name === 'webpush' ? () => { enableBrowserPush().catch(() => {}); } : undefined}
            onToggle={(enabled) => handleToggle(name, enabled)}
            onSeverityChange={(severity) => handleSeverityChange(name, severity)}
          />
        ))}
      </div>

      <Separator />

      <div className="flex items-center gap-3">
        <TestNotificationButton />
      </div>

      <NotificationHistory />
    </div>
  );
}
