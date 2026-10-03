import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import fc from 'fast-check';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider } from '@/i18n';
import { RemoteCard } from '@/components/remotes/remote-card';
import { RemoteStorageBar } from '@/components/remotes/remote-storage-bar';
import RemotesPage from '@/app/remotes/page';
import { SidebarControlsProvider } from '@/components/layout/sidebar-controls';
import type { Remote } from '@/types';

// Mock next/navigation for RemotesPage
vi.mock('next/navigation', () => ({
  useParams:       () => ({}),
  useRouter:       () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname:     () => '/',
  useSearchParams: () => new URLSearchParams(),
}));

const originalFetch = globalThis.fetch;

beforeEach(() => {
  // Default mock: return empty arrays for any fetch
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok:   true,
    json: () => Promise.resolve([]),
  });
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function wrapper ({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <I18nProvider>{children}</I18nProvider>
    </QueryClientProvider>
  );
}

// --- RemoteCard tests ---
describe('RemoteCard', () => {
  it('renders remote name and type', () => {
    const remote: Remote = { name: 'gdrive', type: 'drive', last_verified: null };
    const { unmount } = render(<RemoteCard remote={remote} />, { wrapper });
    expect(screen.getByText('gdrive')).toBeInTheDocument();
    expect(screen.getByText('drive')).toBeInTheDocument();
    unmount();
  });

  it('renders last_verified when present', () => {
    const remote: Remote = { name: 'gdrive', type: 'drive', last_verified: '2024-01-15T10:30:00Z' };
    const { unmount } = render(<RemoteCard remote={remote} />, { wrapper });
    expect(screen.getByText(/Last verified/)).toBeInTheDocument();
    unmount();
  });

  it('has a Test button', () => {
    const remote: Remote = { name: 'gdrive', type: 'drive', last_verified: null };
    const { unmount } = render(<RemoteCard remote={remote} />, { wrapper });
    expect(screen.getByText('Test')).toBeInTheDocument();
    unmount();
  });

  it('Browse opens the folder browser at the remote\'s root, without a Select action', async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => ({
      ok:   true,
      json: () => Promise.resolve(url.startsWith('/api/browse/remote')
        ? { current: 'gdrive:', parent: null, entries: [{ name: 'Photos', path: 'gdrive:Photos' }] }
        : url.endsWith('/dependencies') ? { profiles: [], backup_targets: [] } : []),
    }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const user = userEvent.setup();
    const remote: Remote = { name: 'gdrive', type: 'drive', last_verified: null };
    render(<RemoteCard remote={remote} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Browse' }));
    expect(await screen.findByText('Photos')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/browse/remote?path=gdrive%3A', expect.anything());
    expect(screen.queryByRole('button', { name: 'Select this folder' })).not.toBeInTheDocument();
    await user.click(screen.getAllByRole('button', { name: 'Close' })[0]);
    expect(screen.queryByText('Photos')).not.toBeInTheDocument();
  });

  it('has a labelled delete button', () => {
    const remote: Remote = { name: 'gdrive', type: 'drive', last_verified: null };
    const { unmount } = render(<RemoteCard remote={remote} />, { wrapper });
    expect(screen.getByRole('button', { name: 'Delete remote gdrive' })).toBeInTheDocument();
    unmount();
  });

  it('has a Used by section that can be expanded', () => {
    const remote: Remote = { name: 'gdrive', type: 'drive', last_verified: null };
    const { unmount } = render(<RemoteCard remote={remote} />, { wrapper });
    expect(screen.getByText('Used by')).toBeInTheDocument();
    unmount();
  });
});

// --- RemoteStorageBar tests ---
describe('RemoteStorageBar', () => {
  it('shows unavailable text when fetch returns unsupported data', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok:   true,
      json: () => Promise.resolve({ supported: false, total_bytes: null, used_bytes: null, free_bytes: null, trashed_bytes: null }),
    });
    const { unmount } = render(<RemoteStorageBar remoteName="gdrive" />, { wrapper });
    // Eventually shows unavailable
    expect(await screen.findByText('Storage info unavailable')).toBeInTheDocument();
    unmount();
  });
});

// --- Property-based tests ---
// Units as lib/format's formatBytes writes them in English.
const STORAGE_UNITS = ['byte', 'kB', 'MB', 'GB', 'TB', 'PB', 'EB'];

/** "1.5 GB" -> the byte count it stands for, and the most the rounding can hide. */
function parseShownSize (text: string): { bytes: number; slack: number } {
  const match = /^([\d,]+(?:\.\d)?) (byte|kB|MB|GB|TB|PB|EB)$/.exec(text);
  expect(match, `unexpected size text ${JSON.stringify(text)}`).not.toBeNull();
  const scale = 1024 ** STORAGE_UNITS.indexOf(match![2]);
  return { bytes: Number(match![1].replaceAll(',', '')) * scale, slack: 0.05 * scale };
}

describe('Property: the storage bar shows what the backend reported', () => {
  it('formats with lib/format: locale digits, and units up to EB', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok:   true,
      json: () => Promise.resolve({
        supported: true, total_bytes: 2 * 1024 ** 6, used_bytes: 1.5 * 1024 ** 5, free_bytes: 0, trashed_bytes: null,
      }),
    });
    render(<RemoteStorageBar remoteName="r" />, { wrapper });
    expect(await screen.findByText(/ used of /)).toHaveTextContent('1.5 PB used of 2 EB');
  });

  it('used and total survive formatting, and the bar width is the used share', async () => {
    await fc.assert(
      fc.asyncProperty(
        // up to 8 EiB, across the unit boundaries
        fc.oneof(
          fc.integer({ min: 1, max: 8 * 1024 ** 5 }),
          fc.double({ min: 1024 ** 5, max: 8 * 1024 ** 6, noNaN: true }),
          fc.constantFrom(1024 ** 4 - 1, 1024 ** 5 - 1, 1024 ** 5, 1024 ** 5 + 1, 1024 ** 6)
        ),
        fc.double({ min: 0, max: 1, noNaN: true }),
        async (total, share) => {
          const used = Math.floor(total * share);
          globalThis.fetch = vi.fn().mockResolvedValue({
            ok:   true,
            json: () => Promise.resolve({
              supported: true, total_bytes: total, used_bytes: used, free_bytes: total - used, trashed_bytes: null,
            }),
          });
          const { unmount, container } = render(<RemoteStorageBar remoteName="r" />, { wrapper });
          const line = await screen.findByText(/ used of /);
          const [shownUsed, shownTotal] = line.textContent!.split(' used of ');
          for (const [shown, actual] of [[shownUsed, used], [shownTotal, total]] as const) {
            const { bytes, slack } = parseShownSize(shown);
            expect(Math.abs(bytes - actual)).toBeLessThanOrEqual(slack + 1e-9 * actual);
          }
          const bar = container.querySelector<HTMLElement>('[style]');
          expect(parseFloat(bar!.style.width)).toBeCloseTo((used / total) * 100, 6);
          unmount();
        }
      ),
      { numRuns: 25 }
    );
  });
});

// --- Test connection: spinner, then result (task 15.4) ---

describe('RemoteCard connection test', () => {
  const remote: Remote = { name: 'gdrive', type: 'drive', last_verified: null };

  function mockTest (result: Promise<unknown>) {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url === '/api/remotes/gdrive/test') {
        return result.then((data) => ({ ok: true, status: 200, json: () => Promise.resolve(data) }));
      }
      return Promise.resolve({
        ok:   true,
        json: () => Promise.resolve(url.endsWith('/dependencies') ? { profiles: [], backup_targets: [] } : []),
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    return fetchMock;
  }

  it('shows a spinner while testing, then the latency on success', async () => {
    let finish!: (value: unknown) => void;
    const fetchMock = mockTest(new Promise((resolve) => { finish = resolve; }));
    const user = userEvent.setup();
    const { container } = render(<RemoteCard remote={remote} />, { wrapper });

    const button = screen.getByRole('button', { name: 'Test' });
    expect(container.querySelector('.animate-spin')).toBeNull();
    await user.click(button);

    await waitFor(() => expect(button).toBeDisabled());
    expect(button.querySelector('.animate-spin')).not.toBeNull();
    expect(fetchMock).toHaveBeenCalledWith('/api/remotes/gdrive/test', expect.objectContaining({ method: 'POST' }));
    expect(screen.queryByText('Connection OK')).not.toBeInTheDocument();

    finish({ success: true, latency_ms: 87, error: null });

    expect(await screen.findByText('Connection OK')).toBeInTheDocument();
    expect(screen.getByText('Latency: 87ms')).toBeInTheDocument();
    expect(button).toBeEnabled();
    expect(button.querySelector('.animate-spin')).toBeNull();
  });

  it('shows the error when the connection fails', async () => {
    mockTest(Promise.resolve({ success: false, latency_ms: null, error: 'token expired' }));
    const user = userEvent.setup();
    render(<RemoteCard remote={remote} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Test' }));

    expect(await screen.findByText('Connection failed: token expired')).toBeInTheDocument();
    expect(screen.queryByText('Connection OK')).not.toBeInTheDocument();
  });
});

// --- Remotes page (task 15.4) ---

describe('RemotesPage', () => {
  function pageWrapper ({ children }: { children: React.ReactNode }) {
    return wrapper({
      children: (
        <SidebarControlsProvider value={{ isSidebarCollapsed: false, toggleSidebar: () => {} }}>
          {children}
        </SidebarControlsProvider>
      ),
    });
  }

  function mockRemotes (remotes: Remote[]) {
    globalThis.fetch = vi.fn().mockImplementation((url: string) => Promise.resolve({
      ok:   true,
      json: () => Promise.resolve(
        url === '/api/remotes'
          ? remotes
          : url.endsWith('/dependencies') ? { profiles: [], backup_targets: [] } : []
      ),
    })) as unknown as typeof fetch;
  }

  it('renders a card for each remote', async () => {
    mockRemotes([
      { name: 'gdrive', type: 'drive', last_verified: null },
      { name: 'nas', type: 'sftp', last_verified: null },
      { name: 'b2', type: 'b2', last_verified: null },
    ]);
    render(<RemotesPage />, { wrapper: pageWrapper });

    expect(await screen.findByRole('button', { name: 'Delete remote gdrive' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete remote nas' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete remote b2' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Test' })).toHaveLength(3);
    expect(screen.getByText('sftp')).toBeInTheDocument();
    expect(screen.queryByText('No remotes configured')).not.toBeInTheDocument();
  });

  it('shows the empty state when there are no remotes', async () => {
    mockRemotes([]);
    render(<RemotesPage />, { wrapper: pageWrapper });

    expect(await screen.findByText('No remotes configured')).toBeInTheDocument();
    expect(screen.getByText('Add a cloud remote to get started with syncing')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Test' })).not.toBeInTheDocument();
  });

  it('the Setup Wizard button opens the remote wizard', async () => {
    mockRemotes([]);
    const user = userEvent.setup();
    render(<RemotesPage />, { wrapper: pageWrapper });

    await user.click(await screen.findByRole('button', { name: 'Setup Wizard' }));

    const dialog = await screen.findByRole('dialog', { name: 'Remote Setup Wizard' });
    expect(within(dialog).getByText('Step-by-step guide to configure a cloud storage remote')).toBeInTheDocument();
  });
});
