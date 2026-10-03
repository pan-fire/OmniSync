import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useProfileSelectiveSync } from '@/hooks/use-profile-sync';
import type { SelectiveSyncResponse } from '@/types';

const originalFetch = globalThis.fetch;
let fetchMock: ReturnType<typeof vi.fn>;
let queryClient: QueryClient;

function respond (data: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(data) });
}

function result (over: Partial<SelectiveSyncResponse>): SelectiveSyncResponse {
  return { job_id: 4, status: 'running', total: 2, succeeded: 0, failed: 0, errors: [], ...over };
}

function wrapper ({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  fetchMock = vi.fn();
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe('useProfileSelectiveSync follows the per-file actions', () => {
  it('starts them, polls their result and resolves with the per-file errors', async () => {
    const polls = [
      result({}),
      result({
        status:    'failed',
        succeeded: 1,
        failed:    1,
        errors:    [{ path: 'b.txt', error: 'Copying the file failed. The OmniSync log has the details.' }],
      }),
    ];
    fetchMock.mockImplementation((url: string, init?: { method?: string }) =>
      init?.method === 'POST' ? respond(result({}), 202) : respond(polls.shift() ?? result({ status: 'completed' })));
    const { result: hook } = renderHook(() => useProfileSelectiveSync('docs', 1), { wrapper });

    let final: SelectiveSyncResponse | undefined;
    await act(async () => {
      final = await hook.current.mutateAsync([
        { path: 'a.txt', action: 'push' }, { path: 'b.txt', action: 'push' },
      ]);
    });

    expect(fetchMock.mock.calls[0][0]).toBe('/api/profiles/docs/sync/selective');
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/profiles/docs/sync/selective/4')).toHaveLength(2);
    expect(final).toMatchObject({ status: 'failed', succeeded: 1, failed: 1 });
    expect(final?.errors[0].path).toBe('b.txt');
  });

  it('a refusal (409 sync_busy) rejects at once', async () => {
    fetchMock.mockImplementation(() => respond({ detail: 'A sync of this profile is running.', code: 'sync_busy' }, 409));
    const { result: hook } = renderHook(() => useProfileSelectiveSync('docs', 1), { wrapper });
    await act(async () => {
      await expect(hook.current.mutateAsync([{ path: 'a.txt', action: 'push' }])).rejects.toThrow('A sync of this profile is running.');
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
