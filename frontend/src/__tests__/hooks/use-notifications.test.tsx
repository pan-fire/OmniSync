import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import { useTestNotification, useUpdateNotificationConfig } from '@/hooks/use-notifications';
import type { NotificationConfig } from '@/types';
import { stubBackend } from '../helpers/fake-backend';

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

const CONFIG: NotificationConfig = {
  channels: { email: { enabled: false, min_severity: 'warning' } },
};

function setup () {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  function Wrapper ({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <I18nProvider>{children}</I18nProvider>
      </QueryClientProvider>
    );
  }
  return { client, wrapper: Wrapper };
}

/** fetch that refuses every request with `status` and no reason. */
function refuseAll (status = 500) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status })));
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('useUpdateNotificationConfig', () => {
  // The switches move at once; a refusal must put them back.
  it('shows the change at once and rolls it back when the server refuses', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    vi.stubGlobal('fetch', vi.fn(async () => {
      await gate;
      return new Response('{}', { status: 500 });
    }));
    const { client, wrapper } = setup();
    client.setQueryData(['notifications', 'config'], CONFIG);
    const { result } = renderHook(() => useUpdateNotificationConfig(), { wrapper });

    act(() => {
      result.current.mutate({ channels: { email: { enabled: true, min_severity: 'error' }, ntfy: { enabled: true } } });
    });
    await waitFor(() => expect(client.getQueryData(['notifications', 'config'])).toEqual({
      channels: { email: { enabled: true, min_severity: 'error' } },
    }));

    release();
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(client.getQueryData(['notifications', 'config'])).toEqual(CONFIG);
    expect(toast.error).toHaveBeenCalledWith('Failed to save notification settings');
  });

  it('a refusal before the config was loaded only reports the error', async () => {
    refuseAll();
    const { client, wrapper } = setup();
    const { result } = renderHook(() => useUpdateNotificationConfig(), { wrapper });
    act(() => { result.current.mutate({ channels: { email: { enabled: true } } }); });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(client.getQueryData(['notifications', 'config'])).toBeUndefined();
    expect(toast.error).toHaveBeenCalledTimes(1);
  });
});

describe('useTestNotification', () => {
  it('says so when no channel is turned on', async () => {
    stubBackend({ 'POST /notifications/test': { channels_delivered: [], errors: {} } });
    const { wrapper } = setup();
    const { result } = renderHook(() => useTestNotification(), { wrapper });
    act(() => { result.current.mutate(undefined); });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(toast.error).toHaveBeenCalledWith('No channel is turned on');
  });

  it('names the channels that got it and explains unknown error codes as a failure', async () => {
    stubBackend({ 'POST /notifications/test': { channels_delivered: ['email'], errors: { ntfy: 'mystery' } } });
    const { wrapper } = setup();
    const { result } = renderHook(() => useTestNotification(), { wrapper });
    act(() => { result.current.mutate(undefined); });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(toast.success).toHaveBeenCalledWith('Test sent via Email (SMTP)');
    expect(toast.error).toHaveBeenCalledWith('ntfy: delivery failed (the server log has the details)');
  });

  it('reports a failed request with its message, or a generic one', async () => {
    stubBackend({});
    const { wrapper } = setup();
    const { result } = renderHook(() => useTestNotification(), { wrapper });
    act(() => { result.current.mutate('email'); });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(toast.error).toHaveBeenLastCalledWith('no route for POST /notifications/test');

    vi.unstubAllGlobals();
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error(''); }));
    act(() => { result.current.mutate('email'); });
    await waitFor(() => expect(toast.error).toHaveBeenLastCalledWith('Failed to send test notification'));
  });
});
