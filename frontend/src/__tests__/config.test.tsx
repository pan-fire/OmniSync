import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nProvider } from '@/i18n';
import { SidebarControlsProvider } from '@/components/layout/sidebar-controls';
import ConfigPage from '@/app/config/page';
import { stubBackend } from './helpers/fake-backend';

vi.mock('next/navigation', () => ({
  useParams:       () => ({}),
  useRouter:       () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname:     () => '/config',
  useSearchParams: () => new URLSearchParams(),
}));

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

let putBodies: unknown[];

function stubConfigBackend () {
  putBodies = [];
  return stubBackend({
    'GET /config': { log_level: 'INFO' },
    'PUT /config': (_url: URL, init?: globalThis.RequestInit) => {
      const body = JSON.parse(String(init?.body));
      putBodies.push(body);
      return body;
    },
    'GET /remotes': [{ name: 'gdrive', type: 'drive', last_verified: null }],
  });
}

beforeEach(() => {
  // Radix Select uses pointer capture and scrollIntoView, which jsdom lacks.
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.releasePointerCapture ??= () => {};
  Element.prototype.scrollIntoView ??= () => {};
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** The Global Settings card, found by its title. */
async function globalSettings () {
  const title = await screen.findByText('Global Settings');
  return title.closest('[data-slot="card"]') as HTMLElement;
}

describe('Config page', () => {
  it('shows only global settings: the log level and the history retention', async () => {
    stubConfigBackend();
    render(<ConfigPage />, { wrapper });

    const card = await globalSettings();
    const level = within(card).getByRole('combobox', { name: 'Log level' });
    expect(level).toHaveTextContent('INFO');

    // The global settings only; the per-profile fields moved to the profiles.
    expect(within(card).getAllByRole('combobox')).toHaveLength(1);
    expect(within(card).queryAllByRole('textbox')).toHaveLength(0);
    expect(within(card).getAllByRole('spinbutton')).toHaveLength(1);
    expect(within(card).queryAllByRole('switch')).toHaveLength(0);
    for (const label of ['Local directory', 'Remote directory', 'Debounce (seconds)', 'Max retries']) {
      expect(screen.queryByLabelText(label)).not.toBeInTheDocument();
    }
  });

  it('keeps the remote manager', async () => {
    stubConfigBackend();
    render(<ConfigPage />, { wrapper });

    expect(await screen.findByText('gdrive')).toBeInTheDocument();
    expect(screen.getByText('drive')).toBeInTheDocument();
  });

  it('Save is disabled until the level changes, then PUTs /config with the chosen level', async () => {
    const { requests } = stubConfigBackend();
    const user = userEvent.setup();
    render(<ConfigPage />, { wrapper });

    const card = await globalSettings();
    const save = within(card).getByRole('button', { name: 'Save' });
    expect(save).toBeDisabled();

    await user.click(within(card).getByRole('combobox', { name: 'Log level' }));
    await user.click(await screen.findByRole('option', { name: 'WARNING' }));
    expect(within(card).getByRole('combobox', { name: 'Log level' })).toHaveTextContent('WARNING');
    expect(save).toBeEnabled();

    await user.click(save);

    await waitFor(() => expect(putBodies).toEqual([{ log_level: 'WARNING', history_days: 90 }]));
    expect(requests.filter((r) => r.startsWith('PUT'))).toEqual(['PUT /config']);
  });
});
