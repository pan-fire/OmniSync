'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import { useTranslation } from '@/i18n';
import type { ConflictResolution } from '@/types';

/** Unresolved conflicts, of one profile (by slug) or of all. */
export function useConflicts (profile?: string) {
  return useQuery({
    queryKey: ['conflicts', profile ?? 'all'],
    queryFn:  () => api.getConflicts(profile),
  });
}

/**
 * POST /conflicts/{id}/resolve. keep_local / keep_remote / keep_both change
 * the files on the server (the replaced version goes to .omnisync-trash);
 * dismiss only closes the record. When the action is not possible now (the
 * profile is not running, a file changed since the diff) the backend says
 * why, and that message is shown.
 */
export function useResolveConflict () {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({ id, resolution }: { id: number; resolution: ConflictResolution }) =>
      api.resolveConflict(id, resolution),
    onSuccess: (conflict, { resolution }) => {
      queryClient.invalidateQueries({ queryKey: ['conflicts'] });
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
      toast.success(resolution === 'dismiss'
        ? t('conflicts.dismissed')
        : t('conflicts.resolvedFile', { path: conflict.file_path }));
    },
    onError: (error: Error) => {
      queryClient.invalidateQueries({ queryKey: ['conflicts'] });
      toast.error(error.message || t('conflicts.resolveFailed'));
    },
  });
}
