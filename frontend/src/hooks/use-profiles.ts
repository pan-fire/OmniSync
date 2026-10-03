'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import type { ProfileCreateRequest, ProfileUpdateRequest } from '@/types';
import { useTranslation } from '@/i18n';
import { getPollingInterval, isActiveState } from './use-profile-sync';

export function useProfiles () {
  return useQuery({
    queryKey:        ['profiles'],
    queryFn:         api.getProfiles,
    refetchInterval: (query) => {
      const profiles = query.state.data ?? [];
      return getPollingInterval(profiles.find((p) => isActiveState(p.state))?.state);
    },
  });
}

export function useProfile (slug: string) {
  return useQuery({
    queryKey:        ['profiles', slug],
    queryFn:         () => api.getProfile(slug),
    enabled:         !!slug,
    refetchInterval: (query) => getPollingInterval(query.state.data?.state),
  });
}

export function useCreateProfile () {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (data: ProfileCreateRequest) => api.createProfile(data),
    onSuccess:  (profile) => {
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
      toast.success(t('profiles.created', { name: profile.name }));
    },
    onError: (error: Error) => {
      toast.error(error.message || t('profiles.createFailed'));
    },
  });
}

export function useUpdateProfile (slug: string) {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (data: ProfileUpdateRequest) => api.updateProfile(slug, data),
    onSuccess:  (profile) => {
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
      toast.success(t('profiles.updated', { name: profile.name }));
    },
    onError: (error: Error) => {
      toast.error(error.message || t('profiles.updateFailed'));
    },
  });
}

export function useDeleteProfile () {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (slug: string) => api.deleteProfile(slug),
    onSuccess:  () => {
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
      toast.success(t('profiles.deleted'));
    },
    onError: (error: Error) => {
      toast.error(error.message || t('profiles.deleteFailed'));
    },
  });
}

export function useToggleProfile () {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ slug, enabled }: { slug: string; enabled: boolean }) =>
      enabled ? api.enableProfile(slug) : api.disableProfile(slug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
    },
    onError: (error: Error) => {
      toast.error(error.message);
    },
  });
}
