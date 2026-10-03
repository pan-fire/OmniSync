'use client';

import { useQuery, useMutation } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type { WizardSessionStatus } from '@/types';

export function useProviders () {
  return useQuery({
    queryKey: ['wizard', 'providers'],
    queryFn:  api.fetchProviders,
  });
}

export function useStartAuthorize () {
  return useMutation({
    mutationFn: api.startAuthorize,
  });
}

/** The redirect URI to register with the user's own OAuth app, as the server uses it. */
export function useOAuthRedirectUri () {
  return useQuery({
    queryKey:  ['wizard', 'oauth-redirect-uri'],
    queryFn:   api.getOAuthRedirectUri,
    staleTime: Infinity,
  });
}

/** The wizard session. While it is pending it is polled every 2 seconds. */
export function useWizardSession (sessionId: string | null) {
  return useQuery({
    queryKey:        ['wizard', 'session', sessionId],
    queryFn:         () => api.getWizardSession(sessionId!),
    enabled:         !!sessionId,
    refetchInterval: (query) => {
      const status = query.state.data?.status as WizardSessionStatus | undefined;
      return status === 'pending' ? 2000 : false;
    },
  });
}

export function useCancelSession () {
  return useMutation({
    mutationFn: api.cancelWizardSession,
  });
}

export function useCreateRemote () {
  return useMutation({
    mutationFn: api.createRemote,
  });
}

/** Reconnect: stores the token of a completed session in the existing remote. */
export function useReconnectRemote () {
  return useMutation({
    mutationFn: api.reconnectRemote,
  });
}
