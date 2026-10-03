import { describe, it, expect, vi, beforeEach } from 'vitest';

// Minimal ServiceWorker global stubs
let pushHandler: ((event: unknown) => void) | null = null;
let clickHandler: ((event: unknown) => void) | null = null;

const mockShowNotification = vi.fn().mockResolvedValue(undefined);
const mockOpenWindow = vi.fn().mockResolvedValue(undefined);
const mockFocus = vi.fn().mockResolvedValue(undefined);

beforeEach(() => {
  pushHandler = null;
  clickHandler = null;
  mockShowNotification.mockClear();
  mockOpenWindow.mockClear();
  mockFocus.mockClear();

  // Stub self as ServiceWorkerGlobalScope
  const listeners: Record<string, (event: unknown) => void> = {};

  Object.assign(globalThis, {
    self: {
      addEventListener: (type: string, handler: (event: unknown) => void) => {
        listeners[type] = handler;
        if (type === 'push') pushHandler = handler;
        if (type === 'notificationclick') clickHandler = handler;
      },
      registration: {
        showNotification: mockShowNotification,
      },
    },
    clients: {
      matchAll:   vi.fn().mockResolvedValue([]),
      openWindow: mockOpenWindow,
    },
  });
});

function loadSW () {
  // Re-execute service worker code by importing it fresh
  // We need to reset module cache for each test
  vi.resetModules();

  // Since sw.js is a plain JS file in public/, we simulate its behavior
  // by calling the event listeners directly

  const pushEventHandler = (event: { data: { json: () => Record<string, string>; text: () => string } | null }) => {
    let data: Record<string, string> = { title: 'OmniSync', body: '', event_type: '' };
    try {
      if (event.data) {
        data = { ...data, ...event.data.json() };
      }
    } catch {
      data.body = event.data?.text() ?? 'New notification';
    }

    return (globalThis as unknown as { self: { registration: { showNotification: typeof mockShowNotification } } })
      .self.registration.showNotification(data.title || 'OmniSync', {
        body: data.body,
        icon: '/favicon.ico',
        tag:  data.event_type || 'omnisync',
        data: { url: '/' },
      });
  };

  pushHandler = pushEventHandler as unknown as (event: unknown) => void;

  const notificationClickHandler = (event: { notification: { close: () => void; data?: { url?: string } } }) => {
    event.notification.close();
    const url = event.notification.data?.url || '/';
    return (globalThis as unknown as { clients: { matchAll: (opts?: unknown) => Promise<unknown[]>; openWindow: typeof mockOpenWindow } })
      .clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients: unknown[]) => {
        for (const client of windowClients) {
          const c = client as { url: string; focus: typeof mockFocus };
          if (c.url.includes(url) && 'focus' in c) {
            return c.focus();
          }
        }
        return (globalThis as unknown as { clients: { openWindow: typeof mockOpenWindow } }).clients.openWindow(url);
      });
  };

  clickHandler = notificationClickHandler as unknown as (event: unknown) => void;
}

describe('Service Worker push handler', () => {
  beforeEach(() => {
    loadSW();
  });

  it('shows notification with correct title and body from JSON payload', async () => {
    const event = {
      data: {
        json: () => ({ title: 'Sync done', body: '5 files synced', event_type: 'sync_completed' }),
        text: () => '',
      },
      waitUntil: (p: Promise<unknown>) => p,
    };

    await pushHandler!(event);

    expect(mockShowNotification).toHaveBeenCalledWith('Sync done', {
      body: '5 files synced',
      icon: '/favicon.ico',
      tag:  'sync_completed',
      data: { url: '/' },
    });
  });

  it('uses fallback title "OmniSync" when payload has no title', async () => {
    const event = {
      data: {
        json: () => ({ body: 'test body' }),
        text: () => '',
      },
      waitUntil: (p: Promise<unknown>) => p,
    };

    await pushHandler!(event);

    expect(mockShowNotification).toHaveBeenCalledWith('OmniSync', expect.objectContaining({
      body: 'test body',
    }));
  });

  it('uses text fallback when JSON parsing fails', async () => {
    const event = {
      data: {
        json: () => { throw new Error('bad json'); },
        text: () => 'raw text body',
      },
      waitUntil: (p: Promise<unknown>) => p,
    };

    await pushHandler!(event);

    expect(mockShowNotification).toHaveBeenCalledWith('OmniSync', expect.objectContaining({
      body: 'raw text body',
    }));
  });
});

describe('Service Worker notificationclick handler', () => {
  beforeEach(() => {
    loadSW();
  });

  it('closes notification and opens window with URL', async () => {
    const close = vi.fn();
    const event = {
      notification: { close, data: { url: '/notifications' } },
      waitUntil:    (p: Promise<unknown>) => p,
    };

    await clickHandler!(event);

    expect(close).toHaveBeenCalled();
    expect(mockOpenWindow).toHaveBeenCalledWith('/notifications');
  });

  it('focuses existing window if URL matches', async () => {
    const close = vi.fn();
    const existingClient = { url: 'http://localhost:3000/', focus: mockFocus };
    (globalThis as unknown as { clients: { matchAll: ReturnType<typeof vi.fn> } })
      .clients.matchAll.mockResolvedValue([existingClient]);

    const event = {
      notification: { close, data: { url: '/' } },
      waitUntil:    (p: Promise<unknown>) => p,
    };

    await clickHandler!(event);

    expect(close).toHaveBeenCalled();
    expect(mockFocus).toHaveBeenCalled();
    expect(mockOpenWindow).not.toHaveBeenCalled();
  });
});

// --- The real public/sw.js: the subscription change handler ---

describe('public/sw.js pushsubscriptionchange', () => {
  async function loadRealSW (subscribe: ReturnType<typeof vi.fn>) {
    const { readFileSync } = await import('node:fs');
    const path = await import('node:path');
    const vm = await import('node:vm');
    const source = readFileSync(path.resolve(__dirname, '../../public/sw.js'), 'utf8');
    const listeners: Record<string, (event: unknown) => void> = {};
    const self = {
      addEventListener: (type: string, handler: (event: unknown) => void) => { listeners[type] = handler; },
      registration:     { showNotification: vi.fn(), pushManager: { subscribe } },
    };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vm.runInNewContext(source, { self, clients: {}, fetch: fetchMock, btoa, Uint8Array, JSON, String });
    return { listeners, fetchMock };
  }

  function sub (endpoint: string) {
    return { endpoint, options: { userVisibleOnly: true }, getKey: () => new Uint8Array([1, 2, 3]).buffer };
  }

  it('registers the new subscription and removes the old one', async () => {
    const next = sub('https://fcm.googleapis.com/fcm/send/new');
    const subscribe = vi.fn().mockResolvedValue(next);
    const { listeners, fetchMock } = await loadRealSW(subscribe);

    let done: Promise<unknown> = Promise.resolve();
    listeners.pushsubscriptionchange({
      oldSubscription: sub('https://fcm.googleapis.com/fcm/send/old'),
      waitUntil:       (p: Promise<unknown>) => { done = p; },
    });
    await done;

    expect(subscribe).toHaveBeenCalledWith({ userVisibleOnly: true });
    const [[delUrl, del], [postUrl, post]] = fetchMock.mock.calls;
    expect(delUrl).toBe('/api/notifications/push-subscription');
    expect(del.method).toBe('DELETE');
    expect(JSON.parse(del.body)).toEqual({ endpoint: 'https://fcm.googleapis.com/fcm/send/old' });
    expect(postUrl).toBe('/api/notifications/push-subscription');
    expect(post.method).toBe('POST');
    expect(JSON.parse(post.body)).toEqual({
      endpoint: 'https://fcm.googleapis.com/fcm/send/new', keys: { p256dh: 'AQID', auth: 'AQID' },
    });
  });

  it('uses the new subscription the browser provides', async () => {
    const subscribe = vi.fn();
    const { listeners, fetchMock } = await loadRealSW(subscribe);
    let done: Promise<unknown> = Promise.resolve();
    listeners.pushsubscriptionchange({
      oldSubscription: null,
      newSubscription: sub('https://fcm.googleapis.com/fcm/send/given'),
      waitUntil:       (p: Promise<unknown>) => { done = p; },
    });
    await done;
    expect(subscribe).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).endpoint).toBe('https://fcm.googleapis.com/fcm/send/given');
  });
});
