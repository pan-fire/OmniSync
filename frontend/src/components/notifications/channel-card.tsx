'use client';

import { useState } from 'react';
import { Radio, Monitor, Webhook, Bell, Mail, Settings2 } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { TestNotificationButton } from '@/components/notifications/test-notification-button';
import { ChannelSettingsForm, isConfigurable } from '@/components/notifications/channel-settings-form';
import { useTranslation } from '@/i18n';
import type { BrowserPushState } from '@/lib/push';
import type { ChannelConfig, ChannelStatusInfo, NotificationSeverity } from '@/types';

const CHANNEL_ICONS: Record<string, typeof Radio> = {
  webpush:     Radio,
  host_native: Monitor,
  webhook:     Webhook,
  ntfy:        Bell,
  email:       Mail,
};

const SEVERITIES: NotificationSeverity[] = ['debug', 'info', 'warning', 'error'];

interface ChannelCardProps {
  channelName:      string;
  config:           ChannelConfig;
  status:           ChannelStatusInfo;
  /** The status query has not answered yet. */
  statusLoading?:   boolean;
  /** Web Push only: what this browser can do. */
  browserPush?:     BrowserPushState;
  /** Web Push only: subscribe this browser (asks for the permission). */
  onEnableBrowser?: () => void;
  onToggle:         (enabled: boolean) => void;
  onSeverityChange: (severity: NotificationSeverity) => void;
}

type BadgeInfo = { label: string; variant: 'default' | 'secondary' | 'destructive' | 'outline' };

export function ChannelCard ({
  channelName,
  config,
  status,
  statusLoading = false,
  browserPush,
  onEnableBrowser,
  onToggle,
  onSeverityChange,
}: ChannelCardProps) {
  const { t } = useTranslation();
  const Icon = CHANNEL_ICONS[channelName] ?? Radio;
  const configurable = isConfigurable(channelName);
  const [showSettings, setShowSettings] = useState(false);

  // A translation, or the code itself when there is none (a newer backend).
  const tr = (prefix: string, code: string) => {
    const key = `${prefix}.${code}`;
    const text = t(key);
    return text === key ? code : text;
  };

  let badge: BadgeInfo;
  if (statusLoading) {
    badge = { label: t('notifications.status.checking'), variant: 'outline' };
  } else if (channelName === 'webpush' && browserPush === 'denied') {
    badge = { label: t('notifications.status.permissionDenied'), variant: 'destructive' };
  } else if (status.available) {
    badge = { label: t('notifications.status.available'), variant: 'default' };
  } else {
    badge = { label: t('notifications.status.unavailable'), variant: 'secondary' };
  }

  const missing = status.missing_dependencies ?? [];
  const showProblem = config.enabled && !statusLoading && !status.available;

  return (
    <Card className="border-sidebar-border bg-card">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-md bg-muted">
            <Icon className="h-4 w-4 text-muted-foreground" />
          </div>
          <div>
            <CardTitle className="text-sm font-medium">
              {t(`notifications.channels.${channelName}`)}
            </CardTitle>
            <Badge variant={badge.variant} className="mt-1 text-[10px] px-1.5 py-0">
              {badge.label}
            </Badge>
          </div>
        </div>
        <Switch
          checked={config.enabled}
          onCheckedChange={onToggle}
          aria-label={t(`notifications.channels.${channelName}`)}
        />
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted-foreground" id={`severity-${channelName}`}>
            {t('notifications.minSeverity')}
          </span>
          <Select
            value={config.min_severity}
            onValueChange={(v) => onSeverityChange(v as NotificationSeverity)}
          >
            <SelectTrigger className="w-28 h-7 text-xs" aria-labelledby={`severity-${channelName}`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SEVERITIES.map((s) => (
                <SelectItem key={s} value={s} className="text-xs">
                  {t(`notifications.severity.${s}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {status.host_os && (
          <p className="text-xs text-muted-foreground">
            {t('notifications.hostDetected', {
              os:     tr('notifications.hostOs', status.host_os),
              method: tr('notifications.detection', status.detection_method || 'none'),
            })}
          </p>
        )}

        {channelName === 'webpush' && browserPush && (
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs text-muted-foreground">
              {t(`notifications.browser.${browserPush}`)}
              {typeof status.subscriptions === 'number' && (
                <> · {t('notifications.subscriptions', { count: status.subscriptions })}</>
              )}
            </p>
            {config.enabled && (browserPush === 'prompt' || browserPush === 'unsubscribed') && onEnableBrowser && (
              <Button variant="outline" size="sm" className="h-7 text-xs" onClick={onEnableBrowser}>
                {t('notifications.enableBrowser')}
              </Button>
            )}
          </div>
        )}

        {showProblem && (
          <Alert variant="default" className="border-amber-500/30 bg-amber-500/5" role="alert">
            <AlertDescription className="text-xs text-muted-foreground">
              {missing.length > 0 && (
                <ul className="list-disc space-y-1 ps-4">
                  {missing.map((code) => (
                    <li key={code}>{tr('notifications.missing', code)}</li>
                  ))}
                </ul>
              )}
              {missing.length === 0 && t(`notifications.setup.${channelName}`)}
            </AlertDescription>
          </Alert>
        )}

        {configurable && showSettings && (
          <ChannelSettingsForm channelName={channelName} config={config} />
        )}

        <div className="flex flex-wrap items-center gap-2">
          <TestNotificationButton channel={channelName} />
          {configurable && (
            <Button
              variant="ghost"
              size="sm"
              className="gap-2"
              aria-expanded={showSettings}
              onClick={() => setShowSettings((v) => !v)}
            >
              <Settings2 className="h-3.5 w-3.5" />
              {showSettings ? t('notifications.form.hide') : t('notifications.form.configure')}
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
