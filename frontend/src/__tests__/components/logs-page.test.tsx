import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider } from '@/i18n';
import LogsPage from '@/app/logs/page';
import { api } from '@/lib/api';
import type { LogEntry } from '@/types';

vi.mock('@/lib/api', () => ({ api: { getLogs: vi.fn() } }));
vi.mock('@/components/layout/page-header', () => ({ PageHeader: ({ title }: { title: string }) => <h1>{title}</h1> }));
vi.mock('@/components/layout/page-help', () => ({ PageHelp: () => null }));
const getLogs = vi.mocked(api.getLogs);

function entries (n: number, level = 'INFO'): LogEntry[] {
  return Array.from({ length: n }, (_, i) => ({
    timestamp: new Date(Date.UTC(2026, 8, 27, 10, 0, i)).toISOString(),
    level,
    message:   `${level} message ${i}`,
  }));
}

function renderPage () {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <I18nProvider><LogsPage /></I18nProvider>
    </QueryClientProvider>
  );
}

describe('Logs page paging and level filter', () => {
  beforeEach(() => {
    getLogs.mockReset();
  });

  it('pages back through older entries and returns to the newest', async () => {
    getLogs.mockImplementation(async (skip = 0) => (skip === 0 ? entries(100) : entries(3)));
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('INFO message 99');
    expect(getLogs).toHaveBeenLastCalledWith(0, 100, undefined);
    expect(screen.getByRole('button', { name: /Newer/ })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: /Older/ }));
    await waitFor(() => expect(getLogs).toHaveBeenLastCalledWith(100, 100, undefined));
    await screen.findByText('Page 2');
    // A short page is the last one.
    await waitFor(() => expect(screen.getByRole('button', { name: /Older/ })).toBeDisabled());

    await user.click(screen.getByRole('button', { name: /Newer/ }));
    await screen.findByText('Page 1');
  });

  it('asks the server for one level and starts again at the newest page', async () => {
    getLogs.mockImplementation(async (skip = 0, _limit, level) => (level === 'ERROR' ? entries(2, 'ERROR') : entries(100)));
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('INFO message 0');
    await user.click(screen.getByRole('button', { name: /Older/ }));
    await waitFor(() => expect(getLogs).toHaveBeenLastCalledWith(100, 100, undefined));

    await user.click(screen.getByRole('tab', { name: 'Error' }));
    await screen.findByText('ERROR message 1');
    expect(getLogs).toHaveBeenLastCalledWith(0, 100, 'ERROR');
    expect(screen.getByText('Page 1')).toBeInTheDocument();
  });
});
