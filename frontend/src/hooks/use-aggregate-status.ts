'use client';

import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type { AggregateStatus } from '@/types';
import { getPollingInterval, isActiveState } from './use-profile-sync';

export function useAggregateStatus () {
  return useQuery<AggregateStatus>({
    queryKey:        ['sync', 'status', 'aggregate'],
    queryFn:         () => api.getAggregateStatus(),
    staleTime:       5_000,
    refetchInterval: (query) => {
      const summary = query.state.data?.profiles_summary ?? [];
      return getPollingInterval(summary.find((p) => isActiveState(p.state))?.state);
    },
  });
}
