'use client';

import { Send } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useTestNotification } from '@/hooks/use-notifications';
import { useTranslation } from '@/i18n';

interface TestNotificationButtonProps {
  /** Test only this channel (even when it is turned off); all enabled channels when left out. */
  channel?: string;
}

export function TestNotificationButton ({ channel }: TestNotificationButtonProps = {}) {
  const { t } = useTranslation();
  const testMutation = useTestNotification();
  const label = channel ? t('notifications.sendTestChannel') : t('notifications.sendTest');

  return (
    <Button
      variant="outline"
      size="sm"
      onClick={() => testMutation.mutate(channel)}
      disabled={testMutation.isPending}
      className="gap-2"
      aria-label={channel ? `${label}: ${t(`notifications.channels.${channel}`)}` : undefined}
    >
      <Send className="h-3.5 w-3.5" />
      {testMutation.isPending ? t('common.loading') : label}
    </Button>
  );
}
