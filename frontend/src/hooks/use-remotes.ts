'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import { useTranslation } from '@/i18n';
import { ApiError } from '@/types';

export function useRemotes () {
  return useQuery({
    queryKey:        ['remotes'],
    queryFn:         api.getRemotes,
    refetchInterval: 30_000,
  });
}

export function useDeleteRemote () {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({ name, force }: { name: string; force?: boolean }) =>
      api.deleteRemote(name, force),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['remotes'] });
      toast.success(t('remotes.deleteSuccess'));
    },
    onError: (error: Error) => {
      // 409: profiles or backup targets use the remote. The delete dialog
      // lists them and offers a forced delete instead of a toast.
      if (error instanceof ApiError && error.status === 409) return;
      toast.error(error.message || t('remotes.deleteFailed'));
    },
  });
}

export function useRemoteStorageInfo (name: string, enabled = false) {
  return useQuery({
    queryKey: ['remotes', name, 'about'],
    queryFn:  () => api.getRemoteStorageInfo(name),
    enabled:  !!name && enabled,
  });
}

export function useRemoteDependencies (name: string, enabled = true) {
  return useQuery({
    queryKey: ['remotes', name, 'dependencies'],
    queryFn:  () => api.getRemoteDependencies(name),
    enabled:  !!name && enabled,
  });
}

export function useTestRemote () {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api.testRemoteConnection(name),
    // The result changes the remote's auth_error flag on the server.
    onSettled:  () => queryClient.invalidateQueries({ queryKey: ['remotes'], exact: true }),
  });
}

/** GET /remotes/{name}/config for the edit form: secrets masked, no tokens. */
export function useRemoteConfig (name: string, enabled = true) {
  return useQuery({
    queryKey:  ['remotes', name, 'config'],
    queryFn:   () => api.getRemoteConfig(name),
    enabled:   !!name && enabled,
    // The form starts from what rclone.conf holds now.
    staleTime: 0,
    gcTime:    0,
  });
}

export function useUpdateRemote () {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.updateRemote,
    onSuccess:  (_data, { name }) => {
      queryClient.invalidateQueries({ queryKey: ['remotes'], exact: true });
      queryClient.removeQueries({ queryKey: ['remotes', name, 'config'] });
    },
  });
}

export function usePreviewImport () {
  return useMutation({ mutationFn: api.previewImport });
}

export function useImportRemotes () {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.importRemotes,
    onSuccess:  () => queryClient.invalidateQueries({ queryKey: ['remotes'], exact: true }),
  });
}
