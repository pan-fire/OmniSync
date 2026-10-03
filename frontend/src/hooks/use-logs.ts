'use client';

import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';

export const LOGS_PAGE_SIZE = 100;

/**
 * One page of log entries, newest first. `level` ('ALL' for every level)
 * is filtered on the server, so a page holds up to LOGS_PAGE_SIZE entries
 * of that level however far back they are.
 */
export function useLogs (page = 0, level = 'ALL') {
  return useQuery({
    queryKey:        ['logs', level, page],
    queryFn:         () => api.getLogs(page * LOGS_PAGE_SIZE, LOGS_PAGE_SIZE, level === 'ALL' ? undefined : level),
    placeholderData: keepPreviousData,
  });
}
