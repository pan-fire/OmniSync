import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import { useConfig, useUpdateConfig } from '@/hooks/use-config';
import { stubBackend } from '../helpers/fake-backend';

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

let client: QueryClient;

beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
});

function wrapper ({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={client}>
      <I18nProvider>{children}</I18nProvider>
    </QueryClientProvider>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('useConfig', () => {
  it('reads the global config', async () => {
    stubBackend({ 'GET /config': { log_level: 'INFO' } });
    const { result } = renderHook(() => useConfig(), { wrapper });
    await waitFor(() => expect(result.current.data).toEqual({ log_level: 'INFO' }));
  });
});

describe('useUpdateConfig', () => {
  it('confirms a save', async () => {
    stubBackend({ 'PUT /config': {} });
    const { result } = renderHook(() => useUpdateConfig(), { wrapper });
    act(() => { result.current.mutate({}); });
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Configuration saved'));
  });

  it('reports a refused save with the server\'s reason or a generic one', async () => {
    stubBackend({});
    const { result } = renderHook(() => useUpdateConfig(), { wrapper });
    act(() => { result.current.mutate({}); });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(toast.error).toHaveBeenLastCalledWith('no route for PUT /config');

    vi.unstubAllGlobals();
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error(''); }));
    act(() => { result.current.mutate({}); });
    await waitFor(() => expect(toast.error).toHaveBeenLastCalledWith('Failed to save configuration'));
  });
});
