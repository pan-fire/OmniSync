import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, renderHook, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nProvider } from '@/i18n';
import { toast } from 'sonner';
import { useJob } from '@/hooks/use-jobs';
import { SidebarControlsProvider } from '@/components/layout/sidebar-controls';

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

const originalFetch = globalThis.fetch;
let fetchMock: ReturnType<typeof vi.fn>;

function respond (data: unknown, ok = true, status = 200) {
  return Promise.resolve({ ok, status, json: () => Promise.resolve(data) });
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchMock = vi.fn(() => respond({}));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function wrapper ({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <SidebarControlsProvider value={{ isSidebarCollapsed: false, toggleSidebar: () => {} }}>
          {children}
        </SidebarControlsProvider>
      </I18nProvider>
    </QueryClientProvider>
  );
}

describe('job detail polling', () => {
  it('refetches a running job', async () => {
    fetchMock.mockImplementation(() => respond({
      id:            5,
      direction:     'push',
      started_at:    '2026-09-27T10:00:00Z',
      finished_at:   null,
      status:        'running',
      files_changed: 0,
      conflicts:     0,
      errors:        0,
    }));
    renderHook(() => useJob(5), { wrapper });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2), { timeout: 3500 });
  });
});

describe('remotes page', () => {
  it('shows an error, not "No remotes configured", when loading fails', async () => {
    fetchMock.mockImplementation(() => respond({ detail: 'boom' }, false, 500));
    const { default: RemotesPage } = await import('@/app/remotes/page');
    render(<RemotesPage />, { wrapper });
    expect(await screen.findByText('Could not load the remotes.')).toBeInTheDocument();
    expect(screen.queryByText('No remotes configured')).not.toBeInTheDocument();
  });
});

describe('notifications page', () => {
  it('reports a failed push subscription instead of swallowing it', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes('/notifications/config')) {
        return respond({ channels: { webpush: { enabled: false, min_severity: 'warning' } } });
      }
      if (url.includes('/notifications/channels/status')) return respond({ channels: { webpush: { available: true } } });
      return respond({ items: [], total: 0 });
    });
    const user = userEvent.setup();
    const { default: NotificationsPage } = await import('@/app/notifications/page');
    render(<NotificationsPage />, { wrapper });

    // jsdom has no Notification / service worker support.
    await user.click(await screen.findByRole('switch', { name: 'Web Push' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('This browser does not support push notifications'));
    expect(fetchMock.mock.calls.some(([, init]) => (init as globalThis.RequestInit | undefined)?.method === 'PUT')).toBe(false);
  });
});
