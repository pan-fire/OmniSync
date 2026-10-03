'use client';

import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';

/**
 * GET /health. Refreshed every minute so the dashboard's checks, uptime
 * and the sidebar's version line do not go stale while the page stays open.
 */
export function useHealth () {
  return useQuery({
    queryKey:        ['health'],
    queryFn:         api.getHealth,
    refetchInterval: 60_000,
  });
}

/**
 * GET /health/remotes: reachability of the remotes running profiles use.
 * Each check is a provider call, so the result is kept for a minute.
 */
export function useRemoteHealth () {
  return useQuery({
    queryKey:  ['health', 'remotes'],
    queryFn:   api.getRemoteHealth,
    staleTime: 60_000,
  });
}
