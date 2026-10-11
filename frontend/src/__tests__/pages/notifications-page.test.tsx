import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import NotificationsPage from '@/app/notifications/page';
import { browserPushState, subscribeToPush, syncPushSubscription, unsubscribeFromPush } from '@/lib/push';
import type { ChannelConfig, ChannelStatus, NotificationConfig, NotificationSeverity } from '@/types';
import { stubBackend } from '../helpers/fake-backend';

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));
vi.mock('@/components/layout/page-header', () => ({ PageHeader: ({ title }: { title: ReactNode }) => <h1>{title}</h1> }));
vi.mock('@/components/notifications/test-notification-button', () => ({ TestNotificationButton: () => null }));
vi.mock('@/components/notifications/notification-history', () => ({ NotificationHistory: () => null }));
vi.mock('@/lib/push', () => ({
  browserPushState:     vi.fn(),
  subscribeToPush:      vi.fn(),
  syncPushSubscription: vi.fn(),
  unsubscribeFromPush:  vi.fn(),
}));
// The card has its own tests; this stand-in exposes the callbacks the page passes.
vi.mock('@/components/notifications/channel-card', () => ({
  ChannelCard: ({ channelName, browserPush, onToggle, onSeverityChange, onEnableBrowser }: {
    channelName:      string;
    config:           ChannelConfig;
    browserPush?:     string;
    onToggle:         (enabled: boolean) => void;
    onSeverityChange: (severity: NotificationSeverity) => void;
    onEnableBrowser?: () => void;
  }) => (
    <section aria-label={channelName}>
      <span>push: {browserPush ?? 'n/a'}</span>
      <button onClick={() => onToggle(true)}>on</button>
      <button onClick={() => onToggle(false)}>off</button>
      <button onClick={() => onSeverityChange('error')}>errors only</button>
      {onEnableBrowser && <button onClick={onEnableBrowser}>enable browser</button>}
    </section>
  ),
}));

const CONFIG: NotificationConfig = {
  channels: {
    webpush:     { enabled: true, min_severity: 'warning' },
    host_native: { enabled: false, min_severity: 'warning' },
  },
};
const STATUS: ChannelStatus = { channels: {}, config_error: false, unknown_channels: [] };

let puts: unknown[];

function serve (overrides: Record<string, unknown> = {}) {
  puts = [];
  return stubBackend({
    'GET /notifications/config':          CONFIG,
    'GET /notifications/channels/status': STATUS,
    'PUT /notifications/config':          (_url: URL, init?: globalThis.RequestInit) => {
      puts.push(JSON.parse(String(init?.body)));
      return CONFIG;
    },
    ...overrides,
  });
}

function renderPage (locale: 'en' | 'fa' = 'en') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <I18nProvider initialLocale={locale}><NotificationsPage /></I18nProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(browserPushState).mockResolvedValue('unsubscribed');
  vi.mocked(syncPushSubscription).mockResolvedValue('subscribed');
  vi.mocked(subscribeToPush).mockResolvedValue({ ok: true });
  vi.mocked(unsubscribeFromPush).mockResolvedValue();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Notifications page', () => {
  it('shows loading, then a card per channel with this browser\'s push state', async () => {
    serve();
    renderPage();
    expect(screen.getByRole('status')).toHaveTextContent('Loading...');
    const webpush = await screen.findByRole('region', { name: 'webpush' });
    await waitFor(() => expect(webpush).toHaveTextContent('push: subscribed'));
    expect(within(screen.getByRole('region', { name: 'host_native' })).getByText('push: n/a')).toBeInTheDocument();
  });

  it('offers a retry when the settings cannot be loaded', async () => {
    let fail = true;
    serve({
      'GET /notifications/config': () => {
        if (fail) throw new Error('down');
        return CONFIG;
      },
    });
    // A throwing route is a failed fetch.
    const user = userEvent.setup();
    renderPage();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load the notification settings.');
    fail = false;
    await user.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('region', { name: 'webpush' })).toBeInTheDocument();
  });

  it('offers a retry when the channel status cannot be read', async () => {
    let fail = true;
    serve({
      'GET /notifications/channels/status': () => {
        if (fail) throw new Error('down');
        return STATUS;
      },
    });
    const user = userEvent.setup();
    renderPage();
    const alert = await screen.findByRole('alert');
    fail = false;
    await user.click(within(alert).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  });

  it('saves a new minimum severity', async () => {
    serve();
    const user = userEvent.setup();
    renderPage();
    const card = await screen.findByRole('region', { name: 'host_native' });
    await user.click(within(card).getByRole('button', { name: 'errors only' }));
    await waitFor(() => expect(puts).toEqual([{ channels: { host_native: { min_severity: 'error' } } }]));
  });

  it('"enable browser" subscribes without changing the channel setting', async () => {
    serve();
    const user = userEvent.setup();
    renderPage();
    const card = await screen.findByRole('region', { name: 'webpush' });
    await user.click(within(card).getByRole('button', { name: 'enable browser' }));
    await waitFor(() => expect(subscribeToPush).toHaveBeenCalledTimes(1));
    expect(toast.error).not.toHaveBeenCalled();
    expect(puts).toEqual([]);
  });

  it('names the browser\'s reason when subscribing fails', async () => {
    serve();
    vi.mocked(subscribeToPush).mockResolvedValue({ ok: false, reason: 'failed', message: 'push service down' });
    const user = userEvent.setup();
    renderPage();
    const card = await screen.findByRole('region', { name: 'webpush' });
    await user.click(within(card).getByRole('button', { name: 'enable browser' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Could not subscribe to push notifications: push service down'));
  });

  it('a browser that throws while subscribing changes nothing', async () => {
    serve();
    vi.mocked(subscribeToPush).mockRejectedValue(new Error('boom'));
    const user = userEvent.setup();
    renderPage();
    const card = await screen.findByRole('region', { name: 'webpush' });
    await user.click(within(card).getByRole('button', { name: 'enable browser' }));
    await waitFor(() => expect(subscribeToPush).toHaveBeenCalled());
    expect(puts).toEqual([]);
  });

  // The channel is switched off even when this browser cannot unsubscribe.
  it('turns Web Push off although unsubscribing this browser fails', async () => {
    serve();
    vi.mocked(unsubscribeFromPush).mockRejectedValue(new Error('gone'));
    const user = userEvent.setup();
    renderPage();
    const card = await screen.findByRole('region', { name: 'webpush' });
    await user.click(within(card).getByRole('button', { name: 'off' }));
    await waitFor(() => expect(puts).toEqual([{ channels: { webpush: { enabled: false } } }]));
  });

  it('a failed check of the subscription still shows the cards', async () => {
    serve();
    vi.mocked(syncPushSubscription).mockRejectedValue(new Error('no sw'));
    renderPage();
    const card = await screen.findByRole('region', { name: 'webpush' });
    await waitFor(() => expect(syncPushSubscription).toHaveBeenCalled());
    // The state read before the settings arrived stays.
    expect(card).toHaveTextContent('push: unsubscribed');
  });

  it('is titled in Persian', async () => {
    serve();
    renderPage('fa');
    expect(screen.getByRole('heading', { name: 'اعلان‌ها' })).toBeInTheDocument();
    await screen.findByRole('region', { name: 'webpush' });
  });
});
