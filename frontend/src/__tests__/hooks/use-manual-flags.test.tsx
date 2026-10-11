import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import { api } from '@/lib/api';
import {
  manualFlagsQueryKey, useClearProfileManualFlag, useProfileManualFlags,
} from '@/hooks/use-manual-flags';
import { diffQueryKey } from '@/hooks/use-profile-paginated-diff';

vi.mock('@/lib/api', () => ({
  api: {
    getProfileManualFlags:  vi.fn(),
    clearProfileManualFlag: vi.fn(),
  },
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const mockedApi = vi.mocked(api);

function setup () {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  function wrapper ({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}><I18nProvider>{children}</I18nProvider></QueryClientProvider>;
  }
  return { queryClient, wrapper };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('useProfileManualFlags', () => {
  it('loads the flagged files of the profile', async () => {
    mockedApi.getProfileManualFlags.mockResolvedValue({ flags: ['/data/docs/a.txt'] });
    const { wrapper } = setup();
    const { result } = renderHook(() => useProfileManualFlags('docs'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual({ flags: ['/data/docs/a.txt'] });
    expect(mockedApi.getProfileManualFlags).toHaveBeenCalledWith('docs');
  });

  // No request without a profile, or while the caller holds it back.
  it.each([
    ['an empty slug', '', true],
    ['disabled', 'docs', false],
  ])('does not fetch when %s', (_label, slug, enabled) => {
    const { wrapper } = setup();
    const { result } = renderHook(() => useProfileManualFlags(slug, enabled), { wrapper });
    expect(result.current.fetchStatus).toBe('idle');
    expect(mockedApi.getProfileManualFlags).not.toHaveBeenCalled();
  });

  it('reports a failed load and can retry it', async () => {
    mockedApi.getProfileManualFlags
      .mockRejectedValueOnce(new Error('server down'))
      .mockResolvedValueOnce({ flags: [] });
    const { wrapper } = setup();
    const { result } = renderHook(() => useProfileManualFlags('docs'), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error?.message).toBe('server down');

    await act(async () => { await result.current.refetch(); });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual({ flags: [] });
  });
});

describe('useClearProfileManualFlag', () => {
  it('unmarks the file, refreshes flags and diff, and says so', async () => {
    mockedApi.clearProfileManualFlag.mockResolvedValue(undefined);
    const { queryClient, wrapper } = setup();
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    const { result } = renderHook(() => useClearProfileManualFlag('docs'), { wrapper });

    await act(async () => { await result.current.mutateAsync('/data/docs/a.txt'); });
    expect(mockedApi.clearProfileManualFlag).toHaveBeenCalledWith('docs', '/data/docs/a.txt');
    expect(invalidate).toHaveBeenCalledWith({ queryKey: manualFlagsQueryKey('docs') });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: diffQueryKey('docs') });
    expect(toast.success).toHaveBeenCalledWith('/data/docs/a.txt takes part in syncs again');
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('shows the error and allows another try', async () => {
    mockedApi.clearProfileManualFlag
      .mockRejectedValueOnce(new Error('flag not found'))
      .mockResolvedValueOnce(undefined);
    const { wrapper } = setup();
    const { result } = renderHook(() => useClearProfileManualFlag('docs'), { wrapper });

    act(() => { result.current.mutate('/data/docs/a.txt'); });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(toast.error).toHaveBeenCalledWith('flag not found');
    expect(toast.success).not.toHaveBeenCalled();

    act(() => { result.current.mutate('/data/docs/a.txt'); });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(toast.success).toHaveBeenCalledTimes(1);
  });

  it('falls back to a generic message for an error without text', async () => {
    mockedApi.clearProfileManualFlag.mockRejectedValueOnce(new Error(''));
    const { wrapper } = setup();
    const { result } = renderHook(() => useClearProfileManualFlag('docs'), { wrapper });

    act(() => { result.current.mutate('/data/docs/a.txt'); });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(toast.error).toHaveBeenCalledWith('Something went wrong');
  });
});
