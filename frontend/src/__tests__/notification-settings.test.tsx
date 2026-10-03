import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import { ChannelCard } from '@/components/notifications/channel-card';
import NotificationsPage from '@/app/notifications/page';
import { subscribeToPush, syncPushSubscription, unsubscribeFromPush } from '@/lib/push';
import type { ChannelConfig, ChannelStatus, NotificationConfig } from '@/types';

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

vi.mock('@/components/layout/page-header', () => ({
  PageHeader: ({ title }: { title: ReactNode }) => <h1>{title}</h1>,
}));

// --- fetch: a tiny fake of the notification API ---

type Call = { method: string; url: string; body?: unknown };
let calls: Call[];
let routes: Record<string, (body?: unknown) => { status?: number; body: unknown }>;

function json (status: number, body: unknown) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
}

beforeEach(() => {
  calls = [];
  routes = {};
  global.fetch = vi.fn((url: string, init?: globalThis.RequestInit) => {
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    const path = url.replace(/^\/api/, '').split('?')[0];
    calls.push({ method, url: path, body });
    const route = routes[`${method} ${path}`];
    if (!route) return json(200, path === '/notifications/history' ? { items: [], total: 0 } : {});
    const res = route(body);
    return json(res.status ?? 200, res.body);
  }) as unknown as typeof fetch;
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
});

function wrapper () {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return function Wrapper ({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <I18nProvider>{children}</I18nProvider>
      </QueryClientProvider>
    );
  };
}

// --- browser push stubs ---

type FakeSub = { endpoint: string; getKey: () => ArrayBuffer; unsubscribe: ReturnType<typeof vi.fn> };

function fakeSub (endpoint: string): FakeSub {
  return { endpoint, getKey: () => new Uint8Array([1, 2, 3]).buffer, unsubscribe: vi.fn().mockResolvedValue(true) };
}

type Permission = 'default' | 'granted' | 'denied';

function installPush (permission: Permission, existing: FakeSub | null, afterPrompt: Permission = 'granted') {
  let current = existing;
  const pushManager = {
    getSubscription: vi.fn(() => Promise.resolve(current)),
    subscribe:       vi.fn(() => { current = fakeSub('https://fcm.googleapis.com/fcm/send/new'); return Promise.resolve(current); }),
  };
  const reg = { pushManager };
  const serviceWorker = {
    register:        vi.fn().mockResolvedValue(reg),
    ready:           Promise.resolve(reg),
    getRegistration: vi.fn().mockResolvedValue(reg),
  };
  Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: serviceWorker });
  Object.defineProperty(window, 'PushManager', { configurable: true, value: function PushManager () {} });
  const notification = {
    permission,
    requestPermission: vi.fn(() => { notification.permission = afterPrompt; return Promise.resolve(afterPrompt); }),
  };
  Object.defineProperty(globalThis, 'Notification', { configurable: true, writable: true, value: notification });
  return { pushManager, serviceWorker, notification };
}

afterEach(() => {
  delete (navigator as unknown as Record<string, unknown>).serviceWorker;
  delete (window as unknown as Record<string, unknown>).PushManager;
  delete (globalThis as unknown as Record<string, unknown>).Notification;
});

// --- ChannelCard: status indicators and guidance ---

describe('ChannelCard status', () => {
  const on: ChannelConfig = { enabled: true, min_severity: 'warning' };

  function card (props: Partial<Parameters<typeof ChannelCard>[0]> = {}) {
    return render(
      <ChannelCard
        channelName="host_native"
        config={on}
        status={{ available: false }}
        onToggle={() => {}}
        onSeverityChange={() => {}}
        {...props}
      />,
      { wrapper: wrapper() }
    );
  }

  it('explains each missing dependency', () => {
    card({ status: { available: false, missing_dependencies: ['dbus_socket', 'notify-send'] } });
    expect(screen.getByText(/D-Bus socket is not mounted/)).toBeInTheDocument();
    expect(screen.getByText(/notify-send \(libnotify\) is not installed/)).toBeInTheDocument();
    // The generic text is replaced by the specific ones.
    expect(screen.queryByText(/Requires D-Bus on Linux/)).not.toBeInTheDocument();
  });

  it('shows an unknown dependency code as it is', () => {
    card({ status: { available: false, missing_dependencies: ['something_new'] } });
    expect(screen.getByText('something_new')).toBeInTheDocument();
  });

  it('shows the detected host and how it was detected', () => {
    card({ status: { available: true, host_os: 'linux', detection_method: 'env_var' } });
    expect(screen.getByText('Host: Linux (set by OMNISYNC_HOST_OS)')).toBeInTheDocument();
  });

  it('says "Checking…" instead of "Unavailable" while the status loads', () => {
    card({ statusLoading: true });
    expect(screen.getByText('Checking…')).toBeInTheDocument();
    expect(screen.queryByText('Unavailable')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows a blocked browser permission for Web Push', () => {
    card({ channelName: 'webpush', browserPush: 'denied' });
    expect(screen.getByText('Blocked in this browser')).toBeInTheDocument();
    expect(screen.getByText(/blocked for this site/)).toBeInTheDocument();
  });

  it('offers to enable Web Push in a browser that is not subscribed', async () => {
    const onEnable = vi.fn();
    card({
      channelName:     'webpush',
      browserPush:     'prompt',
      status:          { available: false, missing_dependencies: ['push_subscription'], subscriptions: 0 },
      onEnableBrowser: onEnable,
    });
    expect(screen.getByText(/0 browsers subscribed/)).toBeInTheDocument();
    expect(screen.getByText(/No browser is subscribed yet/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Enable in this browser' }));
    expect(onEnable).toHaveBeenCalled();
  });

  it('sends a test through this channel only and explains the error code', async () => {
    routes['POST /notifications/test'] = () => ({
      body: { success: false, channels_delivered: [], errors: { host_native: 'unavailable' } },
    });
    card({ config: { enabled: false, min_severity: 'warning' } });
    await userEvent.setup().click(screen.getByRole('button', { name: 'Test: Host Native' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Host Native: not available (see the channel card)'));
    expect(calls.find((c) => c.url === '/notifications/test')?.body).toEqual({ channel: 'host_native' });
  });

  it('reports a timed-out channel', async () => {
    routes['POST /notifications/test'] = () => ({
      body: { success: false, channels_delivered: [], errors: { host_native: 'timeout' } },
    });
    card();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Test: Host Native' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Host Native: did not respond in time'));
  });
});

// --- the settings page ---

const CONFIG: NotificationConfig = {
  channels: {
    webpush:     { enabled: false, min_severity: 'warning' },
    host_native: { enabled: false, min_severity: 'error' },
  },
};
const STATUS: ChannelStatus = {
  channels: {
    webpush:     { available: false, missing_dependencies: ['push_subscription'], subscriptions: 0 },
    host_native: { available: true, host_os: 'linux', detection_method: 'env_var' },
  },
  config_error:     false,
  unknown_channels: [],
};

function servePage (config: NotificationConfig = CONFIG, status: ChannelStatus = STATUS) {
  routes['GET /notifications/config'] = () => ({ body: config });
  routes['GET /notifications/channels/status'] = () => ({ body: status });
  routes['PUT /notifications/config'] = (body) => {
    const update = body as { channels: Record<string, Partial<ChannelConfig>> };
    const channels = { ...config.channels };
    for (const [name, fields] of Object.entries(update.channels)) channels[name] = { ...channels[name], ...fields };
    config = { channels };
    return { body: config };
  };
  routes['GET /notifications/vapid-public-key'] = () => ({ body: { public_key: 'BAECAw' } });
  routes['POST /notifications/push-subscription'] = () => ({ status: 201, body: { detail: 'Subscription created' } });
  routes['DELETE /notifications/push-subscription'] = () => ({ body: { detail: 'Subscription removed' } });
}

describe('Notifications page', () => {
  it('shows config errors and unknown channels from the startup validation', async () => {
    servePage(CONFIG, { ...STATUS, config_error: true, unknown_channels: ['hostnative'] });
    render(<NotificationsPage />, { wrapper: wrapper() });

    expect(await screen.findByText(/config.toml have errors/)).toBeInTheDocument();
    expect(screen.getByText(/unknown notification channels, which are ignored: hostnative/)).toBeInTheDocument();
  });

  it('shows an error with retry when the status cannot be read', async () => {
    servePage();
    routes['GET /notifications/channels/status'] = () => ({ status: 500, body: { detail: 'boom' } });
    render(<NotificationsPage />, { wrapper: wrapper() });
    expect(await screen.findByText('Could not check the notification channels.')).toBeInTheDocument();
  });

  it('saves only the field that changed (a partial update)', async () => {
    servePage();
    render(<NotificationsPage />, { wrapper: wrapper() });
    const toggle = await screen.findByRole('switch', { name: 'Host Native' });
    await userEvent.setup().click(toggle);

    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ channels: { host_native: { enabled: true } } });
  });

  it('subscribes this browser when Web Push is turned on', async () => {
    servePage();
    const push = installPush('default', null);
    render(<NotificationsPage />, { wrapper: wrapper() });
    await userEvent.setup().click(await screen.findByRole('switch', { name: 'Web Push' }));

    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(push.notification.requestPermission).toHaveBeenCalled();
    expect(push.pushManager.subscribe).toHaveBeenCalled();
    const posted = calls.find((c) => c.method === 'POST' && c.url === '/notifications/push-subscription');
    expect(posted?.body).toMatchObject({ endpoint: 'https://fcm.googleapis.com/fcm/send/new', keys: { p256dh: 'AQID', auth: 'AQID' } });
    expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ channels: { webpush: { enabled: true } } });
  });

  it('does not turn Web Push on when the permission is refused', async () => {
    servePage();
    installPush('default', null, 'denied');
    render(<NotificationsPage />, { wrapper: wrapper() });
    await userEvent.setup().click(await screen.findByRole('switch', { name: 'Web Push' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Notification permission was not granted'));
    expect(calls.some((c) => c.method === 'PUT')).toBe(false);
  });

  it('unsubscribes this browser when Web Push is turned off', async () => {
    const existing = fakeSub('https://fcm.googleapis.com/fcm/send/old');
    servePage({ channels: { ...CONFIG.channels, webpush: { enabled: true, min_severity: 'warning' } } });
    installPush('granted', existing);
    render(<NotificationsPage />, { wrapper: wrapper() });
    await userEvent.setup().click(await screen.findByRole('switch', { name: 'Web Push' }));

    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    const deleted = calls.find((c) => c.method === 'DELETE');
    expect(deleted?.body).toEqual({ endpoint: 'https://fcm.googleapis.com/fcm/send/old' });
    expect(existing.unsubscribe).toHaveBeenCalled();
    expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ channels: { webpush: { enabled: false } } });
  });

  it('re-registers an existing subscription when Web Push is on (no prompt)', async () => {
    servePage({ channels: { ...CONFIG.channels, webpush: { enabled: true, min_severity: 'warning' } } });
    const push = installPush('granted', fakeSub('https://fcm.googleapis.com/fcm/send/old'));
    render(<NotificationsPage />, { wrapper: wrapper() });

    await waitFor(() =>
      expect(calls.find((c) => c.method === 'POST' && c.url === '/notifications/push-subscription')?.body)
        .toMatchObject({ endpoint: 'https://fcm.googleapis.com/fcm/send/old' })
    );
    expect(push.notification.requestPermission).not.toHaveBeenCalled();
    expect(await screen.findByText(/This browser receives push notifications/)).toBeInTheDocument();
  });
});

// --- lib/push ---

describe('push subscription helpers', () => {
  it('report an unsupported browser', async () => {
    expect(await subscribeToPush()).toEqual({ ok: false, reason: 'unsupported' });
    expect(await syncPushSubscription()).toBe('unsupported');
  });

  it('never prompt from syncPushSubscription', async () => {
    const push = installPush('default', null);
    expect(await syncPushSubscription()).toBe('prompt');
    expect(push.notification.requestPermission).not.toHaveBeenCalled();
    expect(push.pushManager.subscribe).not.toHaveBeenCalled();
  });

  it('subscribe when the permission is already granted', async () => {
    servePage();
    const push = installPush('granted', null);
    expect(await syncPushSubscription()).toBe('subscribed');
    expect(push.pushManager.subscribe).toHaveBeenCalledOnce();
    expect(calls.some((c) => c.method === 'POST' && c.url === '/notifications/push-subscription')).toBe(true);
  });

  it('unsubscribe the browser even when the server no longer knows the endpoint', async () => {
    routes['DELETE /notifications/push-subscription'] = () => ({ status: 404, body: { detail: 'Subscription not found' } });
    const existing = fakeSub('https://fcm.googleapis.com/fcm/send/gone');
    installPush('granted', existing);
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    await unsubscribeFromPush();
    expect(existing.unsubscribe).toHaveBeenCalled();
  });
});
