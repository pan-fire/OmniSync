'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import { useTranslation } from '@/i18n';

function useRefresh () {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ['profiles'] });
    queryClient.invalidateQueries({ queryKey: ['sync', 'status'] });
  };
}

/** Pause automatic syncing of every enabled profile (stored, until resumed). */
export function usePauseAll () {
  const refresh = useRefresh();
  const { t } = useTranslation();
  return useMutation({
    mutationFn: () => api.pauseAllProfiles(),
    onSuccess:  (resp) => {
      refresh();
      toast.success(t('pause.pausedAll', { count: resp.changed.length }));
    },
    onError: (error: Error) => toast.error(error.message || t('common.error')),
  });
}

/**
 * Lift the user's pause of every profile. Pauses OmniSync set itself
 * (differences to review, a restore, a needed resync) stay; each is named
 * in a toast, since it needs the profile's own resume.
 */
export function useResumeAll () {
  const refresh = useRefresh();
  const { t } = useTranslation();
  return useMutation({
    mutationFn: () => api.resumeAllProfiles(),
    onSuccess:  (resp) => {
      refresh();
      toast.success(t('pause.resumedAll', { count: resp.changed.length }));
      for (const [slug, reason] of Object.entries(resp.still_paused)) {
        toast.warning(t('pause.stillPaused', { name: slug, reason }));
      }
    },
    onError: (error: Error) => toast.error(error.message || t('common.error')),
  });
}

/** Pause automatic syncing of one profile (resume with useResumeProfileIntervals). */
export function usePauseProfile (slug: string) {
  const refresh = useRefresh();
  const { t } = useTranslation();
  return useMutation({
    mutationFn: () => api.pauseProfile(slug),
    onSuccess:  () => {
      refresh();
      toast.success(t('pause.paused'));
    },
    onError: (error: Error) => toast.error(error.message || t('common.error')),
  });
}
