import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import { SnapshotBrowser } from '@/components/profiles/snapshot-browser';
import { RestoreDialog } from '@/components/profiles/restore-dialog';
import { BackupTargetCard } from '@/components/profiles/backup-target-card';
import type { BackupTarget, Snapshot, SnapshotFileEntry } from '@/types';

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

const snapshot: Snapshot = {
  snapshot_id: '2026-09-01T10-00-00',
  created_at:  '2026-09-01T10:00:00Z',
  size_bytes:  1024,
  status:      'available',
  kind:        'full',
  latest:      true,
};

const FILES = '/api/profiles/docs/backups/3/snapshots/2026-09-01T10-00-00/files';

const folder = (path: string, count: number): SnapshotFileEntry => ({
  path, name: path.split('/').pop()!, is_dir: true, size: 100, mod_time: null, file_count: count,
});
const file = (path: string): SnapshotFileEntry => ({
  path, name: path.split('/').pop()!, is_dir: false, size: 10, mod_time: '2026-09-01T09:00:00Z', file_count: null,
});

function listing (entries: SnapshotFileEntry[], path = '', total = entries.length) {
  return { snapshot_id: snapshot.snapshot_id, path, search: null, entries, total, offset: 0, limit: 100, snapshot_files: 3 };
}

const originalFetch = globalThis.fetch;
let fetchMock: ReturnType<typeof vi.fn>;

function respond (data: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(data) });
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchMock = vi.fn((url: string, init?: { method?: string; body?: string }) => {
    if (url.startsWith(FILES)) {
      const query = new URLSearchParams(url.split('?')[1] ?? '');
      if (query.get('search')) return respond({ ...listing([file('docs/report.txt')]), search: query.get('search') });
      if (query.get('path') === 'docs') return respond(listing([file('docs/report.txt'), file('docs/b.txt')], 'docs'));
      return respond(listing([folder('docs', 2), file('top.txt')]));
    }
    if (url.endsWith('/restore/preview')) {
      const { restore_scope: scope } = JSON.parse(init?.body ?? '{}');
      const side = (name: string, path: string) => ({
        side:              name,
        path,
        added:             1,
        replaced:          2,
        removed:           3,
        unchanged:         4,
        added_examples:    ['new.txt'],
        replaced_examples: ['a.txt', 'b.txt'],
        removed_examples:  ['x.txt'],
      });
      const sides = scope === 'both'
        ? [side('local', '/data/docs'), side('remote', 'gdrive:Docs')]
        : [side(scope === 'remote_only' ? 'remote' : 'local', scope === 'remote_only' ? 'gdrive:Docs' : '/data/docs')];
      return respond({ snapshot_id: snapshot.snapshot_id, restore_scope: scope, exact: true, sides });
    }
    if (init?.method === 'POST') {
      return respond({
        id:            1,
        target_id:     3,
        started_at:    '',
        finished_at:   '',
        status:        'completed',
        direction:     'restore',
        size_bytes:    null,
        snapshot_id:   snapshot.snapshot_id,
        error_message: null,
      }, 202);
    }
    return respond([]);
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function wrapper ({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return <QueryClientProvider client={qc}><I18nProvider>{children}</I18nProvider></QueryClientProvider>;
}

const posts = (suffix: string) => fetchMock.mock.calls.filter(
  ([url, init]) => init?.method === 'POST' && (url as string).endsWith(suffix)
);

describe('SnapshotBrowser', () => {
  function renderBrowser () {
    const onOpenChange = vi.fn();
    render(
      <SnapshotBrowser profileSlug="docs" targetId={3} snapshot={snapshot} open onOpenChange={onOpenChange} />,
      { wrapper }
    );
    return { dialog: screen.getByRole('dialog'), onOpenChange };
  }

  it('lists a folder, opens subfolders and searches the whole snapshot', async () => {
    const { dialog } = renderBrowser();
    expect(await within(dialog).findByText('top.txt')).toBeInTheDocument();
    expect(dialog).toHaveTextContent('2 files');

    fireEvent.click(within(dialog).getByRole('button', { name: 'Open folder docs' }));
    expect(await within(dialog).findByText('b.txt')).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => (url as string).includes('path=docs'))).toBe(true);

    fireEvent.change(within(dialog).getByLabelText('Search this snapshot'), { target: { value: 'report' } });
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => (url as string).includes('search=report'))).toBe(true));
    expect(await within(dialog).findByText('docs/report.txt')).toBeInTheDocument();
  });

  it('restores the selected files and folders to their original place', async () => {
    const { dialog, onOpenChange } = renderBrowser();
    await within(dialog).findByText('top.txt');
    const restoreBtn = within(dialog).getByRole('button', { name: /^Restore \d+ item/ });
    expect(restoreBtn).toBeDisabled();
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Select docs' }));
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Select top.txt' }));
    expect(dialog).toHaveTextContent('2 selected');
    expect(dialog).toHaveTextContent('automatic syncing is paused so the next pull does not remove them');

    fireEvent.click(within(dialog).getByRole('button', { name: 'Restore 2 items' }));
    await waitFor(() => expect(posts('/restore-files')).toHaveLength(1));
    expect(JSON.parse(posts('/restore-files')[0][1].body)).toEqual({
      snapshot_id: snapshot.snapshot_id, paths: ['docs', 'top.txt'], target_dir: null,
    });
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Restore completed'));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('restores into another folder only with an absolute path', async () => {
    const { dialog } = renderBrowser();
    await within(dialog).findByText('top.txt');
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Select top.txt' }));
    fireEvent.click(within(dialog).getByLabelText(/Another local folder/, { selector: 'input[type="radio"]' }));
    const restoreBtn = within(dialog).getByRole('button', { name: 'Restore 1 item' });
    const folderInput = within(dialog).getByRole('textbox', { name: 'Another local folder' });
    fireEvent.change(folderInput, { target: { value: 'relative/dir' } });
    expect(restoreBtn).toBeDisabled();
    fireEvent.change(folderInput, { target: { value: '/home/me/Restored' } });
    fireEvent.click(restoreBtn);
    await waitFor(() => expect(posts('/restore-files')).toHaveLength(1));
    expect(JSON.parse(posts('/restore-files')[0][1].body)).toMatchObject({
      paths: ['top.txt'], target_dir: '/home/me/Restored',
    });
  });
});

describe('RestoreDialog preview', () => {
  it('shows what the restore would change for the chosen scope before restoring', async () => {
    render(
      <RestoreDialog profileSlug="docs" targetId={3} snapshot={snapshot} open onOpenChange={vi.fn()} />,
      { wrapper }
    );
    const dialog = screen.getByRole('dialog');
    expect(await within(dialog).findByText('3 files removed')).toBeInTheDocument();
    expect(dialog).toHaveTextContent('Local folder');
    expect(dialog).toHaveTextContent('1 file added');
    expect(dialog).toHaveTextContent('2 files replaced');
    expect(dialog).toHaveTextContent('4 unchanged');
    expect(within(dialog).getByTitle('For example: a.txt, b.txt')).toBeInTheDocument();
    expect(posts('/restore')).toHaveLength(0);

    fireEvent.click(within(dialog).getByLabelText(/^Both/));
    expect(await within(dialog).findByText('Remote folder')).toBeInTheDocument();
    expect(JSON.parse(posts('/restore/preview').at(-1)![1].body)).toEqual({
      snapshot_id: snapshot.snapshot_id, restore_scope: 'both',
    });
  });
});

describe('BackupTargetCard', () => {
  const target: BackupTarget = {
    id:                  3,
    profile_id:          1,
    name:                'Vault',
    target_path:         '/backups/vault',
    target_type:         'local',
    remote_name:         null,
    retention_days:      7,
    keep_last:           3,
    frequency_hours:     24,
    backup_mode:         'mirror',
    enabled:             true,
    encrypted:           true,
    verify_after_backup: true,
    overdue:             false,
    last_liveness_ok:    true,
    last_liveness_error: null,
    last_backup_at:      '2026-09-01T10:00:00Z',
    last_backup_status:  'completed',
    last_verify_status:  'failed',
    last_verify_message: '1 file(s) differ from the folder, e.g. \'a.txt\'',
    next_scheduled_at:   null,
    created_at:          '2026-09-01T00:00:00Z',
    updated_at:          '2026-09-01T00:00:00Z',
  };

  it('shows encryption and a failed verification with its reason', () => {
    render(
      <BackupTargetCard
        target={target} profileSlug="docs" onRunNow={vi.fn()} onEdit={vi.fn()} onToggleEnabled={vi.fn()}
      />,
      { wrapper }
    );
    expect(screen.getByText('Encrypted')).toBeInTheDocument();
    expect(screen.getByText('Verification failed')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('The last backup does not match the folder: 1 file(s) differ');
  });

  it('shows a passed verification quietly', () => {
    render(
      <BackupTargetCard
        target={{ ...target, encrypted: false, last_verify_status: 'verified', last_verify_message: '12 file(s) match' }}
        profileSlug="docs" onRunNow={vi.fn()} onEdit={vi.fn()} onToggleEnabled={vi.fn()}
      />,
      { wrapper }
    );
    expect(screen.queryByText('Encrypted')).not.toBeInTheDocument();
    expect(screen.getByTitle('12 file(s) match')).toHaveTextContent('Verified');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
