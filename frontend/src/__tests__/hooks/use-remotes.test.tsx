import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nProvider } from '@/i18n';
import { useRemotes, useTestRemote } from '@/hooks/use-remotes';
import { ApiError } from '@/types';
import type { Remote } from '@/types';

const REMOTES: Remote[] = [
  { name: 'gdrive', type: 'drive', last_verified: '2026-01-01T00:00:00Z' },
  { name: 'nas', type: 'sftp', last_verified: null },
];

function jsonResponse (data: unknown, status = 200) {
  return Promise.resolve({
    ok:   status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(data),
  });
}

let fetchMock: ReturnType<typeof vi.fn>;

function createWrapper () {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return function Wrapper ({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <I18nProvider>{children}</I18nProvider>
      </QueryClientProvider>
    );
  };
}

beforeEach(() => {
  fetchMock = vi.fn().mockImplementation(() => jsonResponse({}));
  global.fetch = fetchMock as typeof fetch;
});

describe('useRemotes', () => {
  it('returns the remote list from GET /remotes', async () => {
    fetchMock.mockImplementation((url: string) =>
      url === '/api/remotes' ? jsonResponse(REMOTES) : jsonResponse({})
    );
    const { result } = renderHook(() => useRemotes(), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(REMOTES);
    expect(fetchMock).toHaveBeenCalledWith('/api/remotes', expect.anything());
  });

  it('surfaces a failed request as an error', async () => {
    fetchMock.mockImplementation(() => jsonResponse({ detail: 'rclone missing' }, 500));
    const { result } = renderHook(() => useRemotes(), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect((result.current.error as ApiError).status).toBe(500);
  });
});

describe('useTestRemote', () => {
  it('POSTs to the remote test route and returns a success result', async () => {
    fetchMock.mockImplementation(() =>
      jsonResponse({ success: true, latency_ms: 42, error: null })
    );
    const { result } = renderHook(() => useTestRemote(), { wrapper: createWrapper() });

    await act(async () => {
      await result.current.mutateAsync('my remote');
    });

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/remotes/my%20remote/test',
      expect.objectContaining({ method: 'POST' })
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual({ success: true, latency_ms: 42, error: null });
  });

  it('returns a failed connection result as data', async () => {
    fetchMock.mockImplementation(() =>
      jsonResponse({ success: false, latency_ms: null, error: 'auth expired' })
    );
    const { result } = renderHook(() => useTestRemote(), { wrapper: createWrapper() });

    await act(async () => {
      await result.current.mutateAsync('gdrive');
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.success).toBe(false);
    expect(result.current.data?.error).toBe('auth expired');
  });

  it('reports an HTTP error as a mutation error', async () => {
    fetchMock.mockImplementation(() => jsonResponse({ detail: 'Remote not found' }, 404));
    const { result } = renderHook(() => useTestRemote(), { wrapper: createWrapper() });

    await act(async () => {
      await result.current.mutateAsync('ghost').catch(() => {});
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect((result.current.error as ApiError).status).toBe(404);
  });
});
