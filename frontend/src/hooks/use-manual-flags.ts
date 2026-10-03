'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import { useTranslation } from '@/i18n';
import { diffQueryKey } from './use-profile-paginated-diff';

export function manualFlagsQueryKey (slug: string) {
  return ['profiles', slug, 'manual-flags'] as const;
}

/** Files flagged "manual" for a profile; every sync skips them until unmarked. */
export function useProfileManualFlags (slug: string, enabled = true) {
  return useQuery({
    queryKey: manualFlagsQueryKey(slug),
    queryFn:  () => api.getProfileManualFlags(slug),
    enabled:  enabled && !!slug,
  });
}

/** Undo "Mark manual": the file takes part in syncs again. */
export function useClearProfileManualFlag (slug: string) {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (filePath: string) => api.clearProfileManualFlag(slug, filePath),
    onSuccess:  (_data, filePath) => {
      queryClient.invalidateQueries({ queryKey: manualFlagsQueryKey(slug) });
      queryClient.invalidateQueries({ queryKey: diffQueryKey(slug) });
      toast.success(t('granular.toastUnmarked', { path: filePath }));
    },
    onError: (error: Error) => {
      toast.error(error.message || t('common.error'));
    },
  });
}
