import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'sonner';
import { I18nProvider, type Locale } from '@/i18n';
import { TrashPanel } from '@/components/profiles/trash-panel';
import { api } from '@/lib/api';
import type { TrashList } from '@/types';

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

const LISTING: TrashList = {
  side:        'local',
  total_files: 2,
  total_bytes: 2048,
  truncated:   false,
  entries:     [
    { id: 'T1/a.txt', folder: 'T1', path: 'a.txt', size: 1024, modified: null, trashed_at: '2026-10-01T10:00:00Z' },
    { id: 'T1/b.txt', folder: 'T1', path: 'b.txt', size: 1024, modified: '2026-09-30T10:00:00Z', trashed_at: null },
  ],
};

function renderPanel (locale: Locale = 'en') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  function Wrapper ({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}><I18nProvider initialLocale={locale}>{children}</I18nProvider></QueryClientProvider>;
  }
  return render(<TrashPanel slug="docs" />, { wrapper: Wrapper });
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('TrashPanel states', () => {
  it('shows loading, then an empty trash', async () => {
    vi.spyOn(api, 'getProfileTrash').mockResolvedValue({ ...LISTING, total_files: 0, total_bytes: 0, entries: [] });
    renderPanel();
    expect(screen.getByRole('status')).toHaveTextContent('Loading...');
    expect(await screen.findByText('The trash is empty.')).toBeInTheDocument();
  });

  it('explains a failed listing and refreshes into the list', async () => {
    const list = vi.spyOn(api, 'getProfileTrash').mockRejectedValueOnce(new Error('Remote unreachable')).mockResolvedValue(LISTING);
    const user = userEvent.setup();
    renderPanel();
    expect(await screen.findByRole('alert')).toHaveTextContent('Remote unreachable');

    await user.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(await screen.findByText('a.txt')).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it('shows the empty trash in Persian', async () => {
    vi.spyOn(api, 'getProfileTrash').mockResolvedValue({ ...LISTING, entries: [] });
    renderPanel('fa');
    expect(await screen.findByText('سطل زباله خالی است.')).toBeInTheDocument();
  });
});

describe('TrashPanel selection and restore', () => {
  it('toggles single files and reports files that could not be restored', async () => {
    vi.spyOn(api, 'getProfileTrash').mockResolvedValue(LISTING);
    const restore = vi.spyOn(api, 'restoreFromTrash').mockResolvedValue({
      done: [], failed: [{ id: 'T1/a.txt', code: 'io_error', message: 'Permission denied' }],
    });
    const user = userEvent.setup();
    renderPanel();

    const a = await screen.findByRole('checkbox', { name: 'Select a.txt' });
    await user.click(a);
    await user.click(screen.getByRole('checkbox', { name: 'Select b.txt' }));
    expect(screen.getByRole('checkbox', { name: 'Select all' })).toBeChecked();
    await user.click(screen.getByRole('checkbox', { name: 'Select b.txt' }));
    expect(screen.getByRole('checkbox', { name: 'Select all' })).not.toBeChecked();

    await user.click(screen.getByRole('button', { name: 'Restore 1 file' }));
    await waitFor(() => expect(restore).toHaveBeenCalledWith('docs', 'local', ['T1/a.txt'], false));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('T1/a.txt: Permission denied'));
    expect(toast.success).not.toHaveBeenCalled();
    // Nothing was newer, so nothing to confirm.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows a failed restore request', async () => {
    vi.spyOn(api, 'getProfileTrash').mockResolvedValue(LISTING);
    vi.spyOn(api, 'restoreFromTrash').mockRejectedValueOnce(new Error('Busy')).mockRejectedValueOnce(new Error(''));
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('checkbox', { name: 'Select a.txt' }));
    await user.click(screen.getByRole('button', { name: 'Restore 1 file' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Busy'));
    await user.click(screen.getByRole('button', { name: 'Restore 1 file' }));
    await waitFor(() => expect(toast.error).toHaveBeenLastCalledWith('Something went wrong'));
  });

  it('cancelling the replace question replaces nothing', async () => {
    vi.spyOn(api, 'getProfileTrash').mockResolvedValue(LISTING);
    const restore = vi.spyOn(api, 'restoreFromTrash').mockResolvedValue({
      done: [], failed: [{ id: 'T1/a.txt', code: 'target_newer', message: 'newer' }],
    });
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('checkbox', { name: 'Select a.txt' }));
    await user.click(screen.getByRole('button', { name: 'Restore 1 file' }));

    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(restore).toHaveBeenCalledTimes(1);
  });
});

describe('TrashPanel delete', () => {
  // Deleting from the trash is final, so it waits for the confirmation.
  it('closing the confirmation with Escape deletes nothing', async () => {
    vi.spyOn(api, 'getProfileTrash').mockResolvedValue(LISTING);
    const remove = vi.spyOn(api, 'deleteFromTrash');
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('checkbox', { name: 'Select all' }));
    await user.click(screen.getByRole('button', { name: 'Delete 2 files' }));

    expect(await screen.findByRole('dialog', { name: 'Delete 2 files for good?' })).toBeInTheDocument();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(remove).not.toHaveBeenCalled();
  });

  it('shows a failed delete', async () => {
    vi.spyOn(api, 'getProfileTrash').mockResolvedValue(LISTING);
    const remove = vi.spyOn(api, 'deleteFromTrash').mockRejectedValueOnce(new Error('Read-only file system')).mockRejectedValueOnce(new Error(''));
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('checkbox', { name: 'Select a.txt' }));
    await user.click(screen.getByRole('button', { name: 'Delete 1 file' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Read-only file system'));
    expect(remove).toHaveBeenCalledWith('docs', 'local', ['T1/a.txt']);

    await user.click(screen.getByRole('button', { name: 'Delete 1 file' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(toast.error).toHaveBeenLastCalledWith('Something went wrong'));
  });
});
