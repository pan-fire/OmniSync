import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nProvider } from '@/i18n';
import { useBackupTargets, useCreateBackupTarget } from '@/hooks/use-backups';
import type { BackupTarget, BackupTargetCreateRequest } from '@/types';

const TARGET: BackupTarget = {
  id:                  1,
  profile_id:          1,
  name:                'Nightly',
  target_path:         '/backups/docs',
  target_type:         'local',
  remote_name:         null,
  retention_days:      30,
  keep_last:           3,
  frequency_hours:     24,
  backup_mode:         'archive',
  enabled:             true,
  last_liveness_ok:    null,
  last_liveness_error: null,
  last_backup_at:      null,
  last_backup_status:  null,
  next_scheduled_at:   null,
  overdue:             false,
  encrypted:           false,
  verify_after_backup: false,
  last_verify_status:  null,
  last_verify_message: null,
  created_at:          '2026-01-01T00:00:00Z',
  updated_at:          '2026-01-01T00:00:00Z',
};

function jsonResponse (data: unknown, status = 200) {
  return Promise.resolve({
    ok:   status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(data),
  });
}

let fetchMock: ReturnType<typeof vi.fn>;

function targetsCalls () {
  return fetchMock.mock.calls.filter(
    ([url, init]) => url === '/api/profiles/docs/backups' && (init?.method ?? 'GET') === 'GET'
  ).length;
}

function createWrapper (queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
})) {
  return function Wrapper ({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <I18nProvider>{children}</I18nProvider>
      </QueryClientProvider>
    );
  };
}

beforeEach(() => {
  fetchMock = vi.fn().mockImplementation((url: string, init?: globalThis.RequestInit) => {
    if (url === '/api/profiles/docs/backups' && init?.method === 'POST') {
      return jsonResponse({ ...TARGET, id: 2, name: 'Weekly' }, 201);
    }
    if (url === '/api/profiles/docs/backups') return jsonResponse([TARGET]);
    return jsonResponse({});
  });
  global.fetch = fetchMock as typeof fetch;
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useBackupTargets', () => {
  it('returns the profile backup targets', async () => {
    const { result } = renderHook(() => useBackupTargets('docs'), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([TARGET]);
  });

  it('does not fetch without a slug', async () => {
    const { result } = renderHook(() => useBackupTargets(''), { wrapper: createWrapper() });

    await act(async () => {});
    expect(result.current.fetchStatus).toBe('idle');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('polls every 30 seconds', async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useBackupTargets('docs'), { wrapper: createWrapper() });

    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.isSuccess).toBe(true);
    expect(targetsCalls()).toBe(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(29_000); });
    expect(targetsCalls()).toBe(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(targetsCalls()).toBe(2);

    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(targetsCalls()).toBe(3);
  });
});

describe('useCreateBackupTarget', () => {
  const request: BackupTargetCreateRequest = {
    name:        'Weekly',
    target_path: '/backups/weekly',
    target_type: 'local',
  };

  it('POSTs the target and invalidates the targets cache on success', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    const wrapper = createWrapper(queryClient);

    const targets = renderHook(() => useBackupTargets('docs'), { wrapper });
    await waitFor(() => expect(targets.result.current.isSuccess).toBe(true));
    expect(targetsCalls()).toBe(1);

    const { result } = renderHook(() => useCreateBackupTarget('docs'), { wrapper });
    await act(async () => {
      await result.current.mutateAsync(request);
    });

    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(post?.[0]).toBe('/api/profiles/docs/backups');
    expect(JSON.parse(post?.[1].body)).toEqual(request);

    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['backups', 'docs'] });
    // The mounted targets query refetches after the invalidation.
    await waitFor(() => expect(targetsCalls()).toBe(2));
  });

  it('leaves the cache alone when the create fails', async () => {
    fetchMock.mockImplementation(() => jsonResponse({ detail: 'bad path' }, 422));
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');

    const { result } = renderHook(() => useCreateBackupTarget('docs'), {
      wrapper: createWrapper(queryClient),
    });
    await act(async () => {
      await result.current.mutateAsync(request).catch(() => {});
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(invalidate).not.toHaveBeenCalled();
  });
});
