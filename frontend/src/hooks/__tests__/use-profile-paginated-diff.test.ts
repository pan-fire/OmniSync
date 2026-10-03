import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { createElement } from 'react';
import { useProfilePaginatedDiff } from '../use-profile-paginated-diff';
import { api } from '@/lib/api';
import type { DiffResponse } from '@/types';

vi.mock('@/lib/api', () => ({
  api: {
    getProfileDiff: vi.fn(),
  },
}));

const mockedApi = vi.mocked(api);

function createWrapper () {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper ({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

function makeFile (path: string) {
  return {
    path,
    category:        'local_only' as const,
    local_size:      100,
    remote_size:     null,
    local_mod_time:  '2024-01-01T00:00:00Z',
    remote_mod_time: null,
    is_conflict:     false,
    manual_flag:     false,
  };
}

const PAGE_1: DiffResponse = {
  files:      Array.from({ length: 100 }, (_, i) => makeFile(`file-${i}.txt`)),
  summary:    { local_only: 150, remote_only: 0, modified_local: 0, modified_remote: 0, modified_both: 0, manual: 0, total: 150 },
  error:      null,
  pagination: { offset: 0, limit: 100, total: 150, has_more: true },
};

const PAGE_2: DiffResponse = {
  files:      Array.from({ length: 50 }, (_, i) => makeFile(`file-${100 + i}.txt`)),
  summary:    PAGE_1.summary,
  error:      null,
  pagination: { offset: 100, limit: 100, total: 150, has_more: false },
};

describe('useProfilePaginatedDiff', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads first page automatically', async () => {
    mockedApi.getProfileDiff.mockResolvedValue(PAGE_1);
    const { result } = renderHook(() => useProfilePaginatedDiff('default'), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.allFiles).toHaveLength(100);
    expect(result.current.hasMore).toBe(true);
    expect(result.current.summary?.total).toBe(150);
  });

  it('loadMore accumulates files', async () => {
    mockedApi.getProfileDiff
      .mockResolvedValueOnce(PAGE_1)
      .mockResolvedValueOnce(PAGE_2);

    const { result } = renderHook(() => useProfilePaginatedDiff('default'), { wrapper: createWrapper() });
    await waitFor(() => expect(result.current.allFiles).toHaveLength(100));

    await act(async () => {
      await result.current.loadMore();
    });

    await waitFor(() => expect(result.current.allFiles).toHaveLength(150));
    expect(result.current.hasMore).toBe(false);
    expect(mockedApi.getProfileDiff).toHaveBeenLastCalledWith('default', 100, 100);
  });

  it('reset refetches from the first page', async () => {
    mockedApi.getProfileDiff.mockResolvedValue(PAGE_1);
    const { result } = renderHook(() => useProfilePaginatedDiff('default'), { wrapper: createWrapper() });
    await waitFor(() => expect(result.current.allFiles).toHaveLength(100));

    act(() => {
      result.current.reset();
    });

    await waitFor(() => expect(mockedApi.getProfileDiff).toHaveBeenCalledTimes(2));
    expect(mockedApi.getProfileDiff).toHaveBeenLastCalledWith('default', 0, 100);
    await waitFor(() => expect(result.current.allFiles).toHaveLength(100));
  });

  it('handles an empty diff without re-rendering forever', async () => {
    mockedApi.getProfileDiff.mockResolvedValue({
      files:      [],
      summary:    { local_only: 0, remote_only: 0, modified_local: 0, modified_remote: 0, modified_both: 0, manual: 0, total: 0 },
      error:      null,
      pagination: { offset: 0, limit: 100, total: 0, has_more: false },
    });
    const { result } = renderHook(() => useProfilePaginatedDiff('default'), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.allFiles).toEqual([]);
    expect(result.current.hasMore).toBe(false);
    expect(result.current.summary?.total).toBe(0);
    expect(result.current.error).toBeNull();
    expect(mockedApi.getProfileDiff).toHaveBeenCalledTimes(1);
  });

  it('does not fetch while disabled', async () => {
    mockedApi.getProfileDiff.mockResolvedValue(PAGE_1);
    const { result } = renderHook(() => useProfilePaginatedDiff('default', false), { wrapper: createWrapper() });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(mockedApi.getProfileDiff).not.toHaveBeenCalled();
    expect(result.current.allFiles).toEqual([]);
  });

  it('reports the error rclone returned with the diff', async () => {
    mockedApi.getProfileDiff.mockResolvedValue({ ...PAGE_1, files: [], pagination: null, error: 'directory not found' });
    const { result } = renderHook(() => useProfilePaginatedDiff('default'), { wrapper: createWrapper() });
    await waitFor(() => expect(result.current.error).toBe('directory not found'));
  });

  it('removeFiles drops files and lowers the summary counts', async () => {
    mockedApi.getProfileDiff.mockResolvedValue(PAGE_1);
    const { result } = renderHook(() => useProfilePaginatedDiff('default'), { wrapper: createWrapper() });
    await waitFor(() => expect(result.current.allFiles).toHaveLength(100));

    act(() => {
      result.current.removeFiles(new Set(['file-0.txt', 'file-1.txt']));
    });

    await waitFor(() => expect(result.current.allFiles).toHaveLength(98));
    expect(result.current.summary?.total).toBe(148);
    expect(result.current.summary?.local_only).toBe(148);
  });
});
