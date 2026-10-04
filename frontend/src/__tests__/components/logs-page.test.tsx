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

// Radix ScrollArea measures its content once it overflows; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe () {}
  unobserve () {}
  disconnect () {}
} as unknown as typeof ResizeObserver;

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
    expect(getLogs).toHaveBeenLastCalledWith(0, 100, undefined, undefined);
    expect(screen.getByRole('button', { name: /Newer/ })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: /Older/ }));
    await waitFor(() => expect(getLogs).toHaveBeenLastCalledWith(100, 100, undefined, undefined));
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
    await waitFor(() => expect(getLogs).toHaveBeenLastCalledWith(100, 100, undefined, undefined));

    await user.click(screen.getByRole('tab', { name: 'Error' }));
    await screen.findByText('ERROR message 1');
    expect(getLogs).toHaveBeenLastCalledWith(0, 100, 'ERROR', undefined);
    expect(screen.getByText('Page 1')).toBeInTheDocument();
  });

  it('asks the server for the audit trail or errors and shows details of an entry', async () => {
    getLogs.mockImplementation(async (_skip = 0, _limit, _level, category) => (category === 'audit'
      ? [{
          timestamp:  '2026-09-27T10:00:00Z',
          level:      'WARNING',
          message:    'profile.delete profile=docs outcome=refused',
          logger:     'backend.audit',
          request_id: '3f9c1a2b4d5e6f70',
          exc:        null,
        }]
      : [{
          timestamp:  '2026-09-27T10:00:00Z',
          level:      'ERROR',
          message:    'Sync crashed',
          logger:     'backend.engine',
          request_id: null,
          exc:        'Traceback (most recent call last):\nValueError: broken',
        }]));
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Sync crashed');
    await user.click(screen.getByText('Details (2 lines)'));
    expect(screen.getByText(/ValueError: broken/)).toBeVisible();

    await user.click(screen.getByRole('tab', { name: 'User actions' }));
    await screen.findByText('profile.delete profile=docs outcome=refused');
    expect(getLogs).toHaveBeenLastCalledWith(0, 100, undefined, 'audit');
    expect(screen.getByText(/Request 3f9c1a2b4d5e6f70/)).toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: 'Errors' }));
    await waitFor(() => expect(getLogs).toHaveBeenLastCalledWith(0, 100, undefined, 'errors'));
  });
});
