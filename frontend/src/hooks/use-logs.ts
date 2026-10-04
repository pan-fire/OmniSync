'use client';

import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type { LogCategory } from '@/types';

export const LOGS_PAGE_SIZE = 100;

/**
 * One page of log entries, newest first. `level` ('ALL' for every level)
 * and `category` ('all', the audit trail or errors) are filtered on the
 * server, so a page holds up to LOGS_PAGE_SIZE matching entries however
 * far back they are.
 */
export function useLogs (page = 0, level = 'ALL', category: LogCategory | 'all' = 'all') {
  return useQuery({
    queryKey: ['logs', level, category, page],
    queryFn:  () => api.getLogs(
      page * LOGS_PAGE_SIZE, LOGS_PAGE_SIZE, level === 'ALL' ? undefined : level,
      category === 'all' ? undefined : category
    ),
    placeholderData: keepPreviousData,
  });
}
