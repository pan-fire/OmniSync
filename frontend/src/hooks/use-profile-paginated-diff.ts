'use client';

import { useCallback, useMemo } from 'react';
import { useInfiniteQuery, useQueryClient, type InfiniteData } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type { FileDiff, DiffSummary, DiffResponse, SyncWarning } from '@/types';

const PAGE_SIZE = 100;
const NO_WARNINGS: SyncWarning[] = [];

export interface ProfilePaginatedDiff {
  allFiles:      FileDiff[];
  summary:       DiffSummary | null;
  hasMore:       boolean;
  isLoading:     boolean;
  isLoadingMore: boolean;
  /** Request failure, or the error rclone reported while computing the diff. */
  error:         string | null;
  /** Local names a sync cannot carry as they are (from the first page). */
  warnings:      SyncWarning[];
  loadMore:      () => Promise<void>;
  reset:         () => void;
  removeFiles:   (paths: Set<string>) => void;
}

export function diffQueryKey (slug: string) {
  return ['profiles', slug, 'diff'] as const;
}

type DiffPages = InfiniteData<DiffResponse, number>;

function subtractFromSummary (summary: DiffSummary, removed: FileDiff[]): DiffSummary {
  const next = { ...summary };
  for (const f of removed) {
    next[f.category] = Math.max(0, next[f.category] - 1);
    if (f.manual_flag) next.manual = Math.max(0, next.manual - 1);
    next.total = Math.max(0, next.total - 1);
  }
  return next;
}

/**
 * Loads a profile's diff page by page. Nothing is fetched until `enabled` is
 * true, because computing a diff runs `rclone check` and two recursive
 * listings on the backend.
 */
export function useProfilePaginatedDiff (slug: string, enabled = true): ProfilePaginatedDiff {
  const queryClient = useQueryClient();
  const queryKey = diffQueryKey(slug);

  const query = useInfiniteQuery({
    queryKey,
    queryFn:          ({ pageParam }) => api.getProfileDiff(slug, pageParam, PAGE_SIZE),
    initialPageParam: 0,
    // The next offset is the number of files already loaded. Files removed
    // after a selective sync are gone on the server too, so counting what is
    // still loaded keeps the offset aligned; duplicates are dropped below.
    getNextPageParam: (lastPage, pages) => {
      if (!lastPage.pagination?.has_more) return undefined;
      return pages.reduce((n, p) => n + p.files.length, 0);
    },
    staleTime: 30_000,
    enabled:   enabled && !!slug,
  });

  const pages = query.data?.pages;

  const allFiles = useMemo(() => {
    if (!pages) return [];
    const seen = new Set<string>();
    const out: FileDiff[] = [];
    for (const page of pages) {
      for (const f of page.files) {
        if (seen.has(f.path)) continue;
        seen.add(f.path);
        out.push(f);
      }
    }
    return out;
  }, [pages]);

  const lastPage = pages?.[pages.length - 1];
  const serverError = pages?.find((p) => p.error)?.error ?? null;

  const { fetchNextPage, hasNextPage, isFetchingNextPage } = query;

  const loadMore = useCallback(async () => {
    if (!hasNextPage || isFetchingNextPage) return;
    await fetchNextPage();
  }, [fetchNextPage, hasNextPage, isFetchingNextPage]);

  const reset = useCallback(() => {
    queryClient.resetQueries({ queryKey: diffQueryKey(slug) });
  }, [queryClient, slug]);

  const removeFiles = useCallback((paths: Set<string>) => {
    queryClient.setQueryData<DiffPages>(diffQueryKey(slug), (old) => {
      if (!old) return old;
      const removed = new Map<string, FileDiff>();
      const newPages = old.pages.map((p) => {
        const kept: FileDiff[] = [];
        for (const f of p.files) {
          if (paths.has(f.path)) removed.set(f.path, f);
          else kept.push(f);
        }
        return { ...p, files: kept };
      });
      const removedFiles = [...removed.values()];
      return {
        ...old,
        pages: newPages.map((p) => ({ ...p, summary: subtractFromSummary(p.summary, removedFiles) })),
      };
    });
  }, [queryClient, slug]);

  return {
    allFiles,
    summary:       lastPage?.summary ?? null,
    hasMore:       !!hasNextPage,
    isLoading:     query.isLoading,
    isLoadingMore: isFetchingNextPage,
    error:         query.error?.message ?? serverError,
    warnings:      pages?.[0]?.warnings ?? NO_WARNINGS,
    loadMore,
    reset,
    removeFiles,
  };
}
