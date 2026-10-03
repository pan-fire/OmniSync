'use client';

import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';

export const JOBS_PAGE_SIZE = 20;

/** One page of the job history, of every profile or of one; `page` starts at 1. */
export function useJobs (page = 1, profile?: string) {
  return useQuery({
    queryKey:        ['jobs', { page, profile }],
    queryFn:         () => api.getJobs((page - 1) * JOBS_PAGE_SIZE, JOBS_PAGE_SIZE, profile),
    placeholderData: keepPreviousData,
  });
}

/** A job; polled every 2 s while it is running. */
export function useJob (id: number) {
  return useQuery({
    queryKey:        ['jobs', id],
    queryFn:         () => api.getJob(id),
    enabled:         id > 0,
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 2_000 : false),
  });
}

/** The files a job changed; polled with the job while it is running. */
export function useJobFiles (id: number, running = false) {
  return useQuery({
    queryKey:        ['jobs', id, 'files'],
    queryFn:         () => api.getJobFiles(id),
    enabled:         id > 0,
    refetchInterval: running ? 2_000 : false,
  });
}
