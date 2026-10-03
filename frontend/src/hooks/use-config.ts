'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import { useTranslation } from '@/i18n';
import type { GlobalConfig } from '@/types';

export function useConfig () {
  return useQuery({
    queryKey:  ['config'],
    queryFn:   api.getGlobalConfig,
    staleTime: 30_000,
  });
}

export function useUpdateConfig () {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (data: Partial<GlobalConfig>) => api.updateGlobalConfig(data),
    onSuccess:  () => {
      queryClient.invalidateQueries({ queryKey: ['config'] });
      toast.success(t('config.saved'));
    },
    onError: (error: Error) => {
      toast.error(error.message || t('config.saveFailed'));
    },
  });
}
