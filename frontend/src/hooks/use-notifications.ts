'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import { useTranslation } from '@/i18n';
import type { ChannelConfigUpdate, NotificationConfig, NotificationConfigUpdate } from '@/types';

export function useNotificationConfig () {
  return useQuery({
    queryKey:  ['notifications', 'config'],
    queryFn:   api.getNotificationConfig,
    staleTime: 30_000,
  });
}

export function useChannelStatus () {
  return useQuery({
    queryKey:        ['notifications', 'channels'],
    queryFn:         api.getChannelStatus,
    refetchInterval: 30_000,
  });
}

export function useNotificationHistory (limit = 50, offset = 0) {
  return useQuery({
    queryKey: ['notifications', 'history', limit, offset],
    queryFn:  () => api.getNotificationHistory(limit, offset),
  });
}

export function useUpdateNotificationConfig () {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (data: NotificationConfigUpdate) => api.updateNotificationConfig(data),
    onMutate:   async (update) => {
      await queryClient.cancelQueries({ queryKey: ['notifications', 'config'] });
      const previous = queryClient.getQueryData<NotificationConfig>(['notifications', 'config']);
      if (previous) {
        const channels = { ...previous.channels };
        // Only the switches are shown at once; settings come back from the server.
        for (const [name, fields] of Object.entries(update.channels)) {
          if (!channels[name]) continue;
          const next = { ...channels[name] };
          if (fields.enabled !== undefined) next.enabled = fields.enabled;
          if (fields.min_severity !== undefined) next.min_severity = fields.min_severity;
          channels[name] = next;
        }
        queryClient.setQueryData(['notifications', 'config'], { ...previous, channels });
      }
      return { previous };
    },
    onError: (_err, _vars, context) => {
      if (context?.previous) {
        queryClient.setQueryData(['notifications', 'config'], context.previous);
      }
      toast.error(t('notifications.saveFailed'));
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['notifications', 'config'] });
      queryClient.invalidateQueries({ queryKey: ['notifications', 'channels'] });
    },
  });
}

/**
 * Save one channel's settings (webhook, ntfy, email). The caller shows a
 * refusal (the server's reason) next to the form, so no toast for errors.
 */
export function useSaveChannelSettings () {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({ channel, update }: { channel: string; update: ChannelConfigUpdate }) =>
      api.updateNotificationConfig({ channels: { [channel]: update } }),
    onSuccess: (data) => {
      queryClient.setQueryData(['notifications', 'config'], data);
      toast.success(t('notifications.form.saved'));
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['notifications', 'channels'] });
    },
  });
}

/** Error text for a channel's test error code ("unavailable", "timeout", "failed"). */
function testErrorText (t: (key: string) => string, code: string): string {
  const key = `notifications.testError.${code}`;
  const text = t(key);
  return text === key ? t('notifications.testError.failed') : text;
}

/** Send a test notification: through every enabled channel, or through `channel` only. */
export function useTestNotification () {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (channel?: string) => api.sendTestNotification(channel),
    onSuccess:  (data) => {
      if (data.channels_delivered.length > 0) {
        toast.success(t('notifications.testSent', {
          channels: data.channels_delivered.map((c) => t(`notifications.channels.${c}`)).join(', '),
        }));
      } else if (Object.keys(data.errors).length === 0) {
        toast.error(t('notifications.testNoChannels'));
      }
      for (const [ch, code] of Object.entries(data.errors)) {
        toast.error(`${t(`notifications.channels.${ch}`)}: ${testErrorText(t, code)}`);
      }
      queryClient.invalidateQueries({ queryKey: ['notifications', 'history'] });
      queryClient.invalidateQueries({ queryKey: ['notifications', 'channels'] });
    },
    onError: (error: Error) => {
      toast.error(error.message || t('notifications.testFailed'));
    },
  });
}
