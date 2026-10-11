import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import { SnapshotBrowser, SNAPSHOT_PAGE_SIZE } from '@/components/profiles/snapshot-browser';
import type { Snapshot, SnapshotFileEntry } from '@/types';

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

const snapshot: Snapshot = {
  snapshot_id: '2026-09-01T10-00-00',
  created_at:  '2026-09-01T10:00:00Z',
  size_bytes:  1024,
  status:      'available',
  latest:      true,
};

const FILES = '/api/profiles/docs/backups/3/snapshots/2026-09-01T10-00-00/files';

const file = (path: string): SnapshotFileEntry => ({
  path, name: path.split('/').pop()!, is_dir: false, size: null, mod_time: null, file_count: null,
});
const folder = (path: string): SnapshotFileEntry => ({
  path, name: path.split('/').pop()!, is_dir: true, size: null, mod_time: null, file_count: null,
});

const originalFetch = globalThis.fetch;
let fetchMock: ReturnType<typeof vi.fn>;
let filesRoute: (query: URLSearchParams) => [unknown, number];
let restoreStatus: string;

function respond (data: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(data) });
}

function listing (entries: SnapshotFileEntry[], query: URLSearchParams, total = entries.length) {
  return {
    snapshot_id:    snapshot.snapshot_id,
    path:           query.get('path') ?? '',
    search:         query.get('search'),
    entries,
    total,
    offset:         Number(query.get('offset') ?? 0),
    limit:          SNAPSHOT_PAGE_SIZE,
    snapshot_files: total,
  };
}

const filesCalls = () => fetchMock.mock.calls
  .map(([url]) => url as string)
  .filter((url) => url.startsWith(FILES))
  .map((url) => new URLSearchParams(url.split('?')[1] ?? ''));

beforeEach(() => {
  vi.clearAllMocks();
  restoreStatus = 'completed';
  filesRoute = (query) => [listing([file('top.txt')], query), 200];
  fetchMock = vi.fn((url: string, init?: { method?: string }) => {
    if (url.startsWith(FILES)) {
      const [body, status] = filesRoute(new URLSearchParams(url.split('?')[1] ?? ''));
      return respond(body, status);
    }
    if (url.startsWith('/api/browse/local')) return respond({ current: '/data/restored', parent: '/data', entries: [] });
    if (init?.method === 'POST' && url.endsWith('/restore-files')) {
      return respond({
        id:            5,
        target_id:     3,
        started_at:    '',
        finished_at:   '',
        status:        restoreStatus,
        direction:     'restore',
        size_bytes:    null,
        snapshot_id:   snapshot.snapshot_id,
        error_message: restoreStatus === 'failed' ? 'Remote unreachable' : null,
      }, 202);
    }
    return respond([]);
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  vi.useRealTimers();
  globalThis.fetch = originalFetch;
});

function renderBrowser () {
  const onOpenChange = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  function Wrapper ({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}><I18nProvider>{children}</I18nProvider></QueryClientProvider>;
  }
  render(
    <SnapshotBrowser profileSlug="docs" targetId={3} snapshot={snapshot} open onOpenChange={onOpenChange} />,
    { wrapper: Wrapper }
  );
  return { dialog: screen.getByRole('dialog', { name: 'Browse snapshot' }), onOpenChange };
}

describe('SnapshotBrowser states', () => {
  it('shows loading, then an empty folder', async () => {
    filesRoute = (query) => [listing([], query), 200];
    const { dialog } = renderBrowser();
    expect(within(dialog).getByRole('status')).toHaveTextContent('Loading the files');
    expect(await within(dialog).findByText('This folder is empty.')).toBeInTheDocument();
  });

  it('explains a listing that failed', async () => {
    filesRoute = () => [{ detail: 'Archive is damaged' }, 500];
    const { dialog } = renderBrowser();
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Could not list the snapshot: Archive is damaged');
  });

  it('says when a search matches nothing', async () => {
    filesRoute = (query) => [listing(query.get('search') ? [] : [file('top.txt')], query), 200];
    const { dialog } = renderBrowser();
    await within(dialog).findByText('top.txt');
    fireEvent.change(within(dialog).getByLabelText('Search this snapshot'), { target: { value: 'nothing' } });
    expect(await within(dialog).findByText('No file matches the search.')).toBeInTheDocument();
  });
});

describe('SnapshotBrowser navigation', () => {
  it('pages through a large folder', async () => {
    filesRoute = (query) => {
      const offset = Number(query.get('offset') ?? 0);
      return [listing([file(offset ? 'page2.txt' : 'page1.txt')], query, 150), 200];
    };
    const { dialog } = renderBrowser();
    await within(dialog).findByText('page1.txt');
    expect(dialog).toHaveTextContent('1–1 of 150');
    expect(within(dialog).getByRole('button', { name: 'Previous page' })).toBeDisabled();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Next page' }));
    expect(await within(dialog).findByText('page2.txt')).toBeInTheDocument();
    expect(filesCalls().at(-1)?.get('offset')).toBe(String(SNAPSHOT_PAGE_SIZE));
    expect(within(dialog).getByRole('button', { name: 'Next page' })).toBeDisabled();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Previous page' }));
    expect(await within(dialog).findByText('page1.txt')).toBeInTheDocument();
  });

  // Regression: the search debounce also fired 300 ms after the dialog
  // opened and reset the offset, so an early "Next page" jumped back to page 1.
  it('stays on the next page when it is opened within the search debounce', async () => {
    filesRoute = (query) => {
      const offset = Number(query.get('offset') ?? 0);
      return [listing([file(offset ? 'page2.txt' : 'page1.txt')], query, 150), 200];
    };
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { dialog } = renderBrowser();
    await within(dialog).findByText('page1.txt');

    fireEvent.click(within(dialog).getByRole('button', { name: 'Next page' }));
    expect(await within(dialog).findByText('page2.txt')).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });

    expect(within(dialog).getByText('page2.txt')).toBeInTheDocument();
    expect(within(dialog).queryByText('page1.txt')).toBeNull();
    expect(filesCalls().at(-1)?.get('offset')).toBe(String(SNAPSHOT_PAGE_SIZE));
  });

  it('goes back up through the breadcrumbs', async () => {
    filesRoute = (query) => {
      const path = query.get('path') ?? '';
      if (path === 'docs/2026') return [listing([file('docs/2026/a.txt')], query), 200];
      if (path === 'docs') return [listing([folder('docs/2026')], query), 200];
      return [listing([folder('docs')], query), 200];
    };
    const { dialog } = renderBrowser();
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Open folder docs' }));
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Open folder docs/2026' }));
    expect(await within(dialog).findByText('a.txt')).toBeInTheDocument();

    const crumbs = within(dialog).getByRole('navigation', { name: 'Folder in the snapshot' });
    fireEvent.click(within(crumbs).getByRole('button', { name: 'docs' }));
    expect(await within(dialog).findByRole('button', { name: 'Open folder docs/2026' })).toBeInTheDocument();
    fireEvent.click(within(crumbs).getByRole('button', { name: 'Top' }));
    expect(await within(dialog).findByRole('button', { name: 'Open folder docs' })).toBeInTheDocument();
  });
});

describe('SnapshotBrowser selection and restore', () => {
  it('unselects and clears the selection', async () => {
    const { dialog } = renderBrowser();
    const box = await within(dialog).findByRole('checkbox', { name: 'Select top.txt' });
    fireEvent.click(box);
    expect(dialog).toHaveTextContent('1 selected');
    fireEvent.click(box);
    expect(dialog).not.toHaveTextContent('1 selected');

    fireEvent.click(box);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Clear selection' }));
    expect(within(dialog).getByRole('button', { name: 'Restore 0 items' })).toBeDisabled();
  });

  it('picks the destination folder with the folder browser', async () => {
    const { dialog } = renderBrowser();
    fireEvent.click(await within(dialog).findByRole('checkbox', { name: 'Select top.txt' }));
    fireEvent.click(within(dialog).getByLabelText(/Another local folder/, { selector: 'input[type="radio"]' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Choose the folder' }));

    const picker = await screen.findByRole('dialog', { name: 'Browse local directory' });
    await within(picker).findByText('/data/restored');
    fireEvent.click(within(picker).getByRole('button', { name: 'Select this folder' }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Browse local directory' })).not.toBeInTheDocument());
    expect(within(dialog).getByRole('textbox', { name: 'Another local folder' })).toHaveValue('/data/restored');
    expect(within(dialog).getByRole('button', { name: 'Restore 1 item' })).toBeEnabled();
  });

  // A failed restore leaves the dialog open so the user can try again.
  it('keeps the dialog open when the restore fails', async () => {
    restoreStatus = 'failed';
    const { dialog, onOpenChange } = renderBrowser();
    fireEvent.click(await within(dialog).findByRole('checkbox', { name: 'Select top.txt' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Restore 1 item' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Restore failed: Remote unreachable'));
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it('cancel closes without restoring', async () => {
    const { dialog, onOpenChange } = renderBrowser();
    await within(dialog).findByText('top.txt');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
  });
});
