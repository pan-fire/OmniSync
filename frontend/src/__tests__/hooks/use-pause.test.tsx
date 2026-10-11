import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import { api } from '@/lib/api';
import { usePauseAll, usePauseProfile, useResumeAll } from '@/hooks/use-pause';

vi.mock('@/lib/api', () => ({
  api: {
    pauseAllProfiles:  vi.fn(),
    resumeAllProfiles: vi.fn(),
    pauseProfile:      vi.fn(),
  },
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));

const mockedApi = vi.mocked(api);
let queryClient: QueryClient;

function wrapper ({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider initialLocale="en">{children}</I18nProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
});

describe('usePauseAll', () => {
  it('pauses every profile, refreshes the status and counts the paused ones', async () => {
    mockedApi.pauseAllProfiles.mockResolvedValue({ changed: ['docs', 'photos'], unchanged: [], still_paused: {} });
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    const { result } = renderHook(() => usePauseAll(), { wrapper });

    await act(async () => { await result.current.mutateAsync(); });

    expect(toast.success).toHaveBeenCalledWith('Paused 2 profiles');
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['profiles'] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['sync', 'status'] });
  });

  it('shows the server error', async () => {
    mockedApi.pauseAllProfiles.mockRejectedValue(new Error('daemon offline'));
    const { result } = renderHook(() => usePauseAll(), { wrapper });

    act(() => { result.current.mutate(); });

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('daemon offline'));
    expect(toast.success).not.toHaveBeenCalled();
  });

  // An error without a message must still tell the user something failed.
  it('falls back to a generic error for an empty message', async () => {
    mockedApi.pauseAllProfiles.mockRejectedValue(new Error(''));
    const { result } = renderHook(() => usePauseAll(), { wrapper });

    act(() => { result.current.mutate(); });

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Something went wrong'));
  });
});

describe('useResumeAll', () => {
  it('names each profile OmniSync keeps paused', async () => {
    mockedApi.resumeAllProfiles.mockResolvedValue({
      changed:      ['docs'],
      unchanged:    [],
      still_paused: { photos: 'differences to review', music: 'a restore runs' },
    });
    const { result } = renderHook(() => useResumeAll(), { wrapper });

    await act(async () => { await result.current.mutateAsync(); });

    expect(toast.success).toHaveBeenCalledWith('Resumed 1 profile');
    expect(toast.warning).toHaveBeenCalledWith('photos stays paused: differences to review');
    expect(toast.warning).toHaveBeenCalledWith('music stays paused: a restore runs');
  });

  it('shows the server error', async () => {
    mockedApi.resumeAllProfiles.mockRejectedValue(new Error('daemon offline'));
    const { result } = renderHook(() => useResumeAll(), { wrapper });

    act(() => { result.current.mutate(); });

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('daemon offline'));
    expect(toast.warning).not.toHaveBeenCalled();
  });

  it('falls back to a generic error for an empty message', async () => {
    mockedApi.resumeAllProfiles.mockRejectedValue(new Error(''));
    const { result } = renderHook(() => useResumeAll(), { wrapper });

    act(() => { result.current.mutate(); });

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Something went wrong'));
  });
});

describe('usePauseProfile', () => {
  it('pauses the one profile', async () => {
    mockedApi.pauseProfile.mockResolvedValue({ detail: 'paused' });
    const { result } = renderHook(() => usePauseProfile('docs'), { wrapper });

    await act(async () => { await result.current.mutateAsync(); });

    expect(mockedApi.pauseProfile).toHaveBeenCalledWith('docs');
    expect(toast.success).toHaveBeenCalledWith('Automatic syncing paused');
  });

  it('shows the server error', async () => {
    mockedApi.pauseProfile.mockRejectedValue(new Error('not found'));
    const { result } = renderHook(() => usePauseProfile('docs'), { wrapper });

    act(() => { result.current.mutate(); });

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('not found'));
  });

  it('falls back to a generic error for an empty message', async () => {
    mockedApi.pauseProfile.mockRejectedValue(new Error(''));
    const { result } = renderHook(() => usePauseProfile('docs'), { wrapper });

    act(() => { result.current.mutate(); });

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Something went wrong'));
  });
});
