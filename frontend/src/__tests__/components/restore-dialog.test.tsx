import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import en from '@/i18n/locales/en.json';
import de from '@/i18n/locales/de.json';
import fa from '@/i18n/locales/fa.json';
import { RestoreDialog } from '@/components/profiles/restore-dialog';
import { BackupHistory } from '@/components/profiles/backup-history';
import type { RestoreScope, Snapshot } from '@/types';

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

const snapshot: Snapshot = {
  snapshot_id: '2026-09-01T10-00-00',
  created_at:  '2026-09-01T10:00:00Z',
  size_bytes:  1024,
  status:      'available',
  latest:      false,
};

const originalFetch = globalThis.fetch;
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  fetchMock = vi.fn(() => Promise.resolve({
    ok:     true,
    status: 202,
    json:   () => Promise.resolve({
      id: 1, target_id: 3, started_at: '', finished_at: '', status: 'completed', direction: 'restore', size_bytes: null, snapshot_id: snapshot.snapshot_id, error_message: null,
    }),
  }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function wrapper ({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return <QueryClientProvider client={qc}><I18nProvider>{children}</I18nProvider></QueryClientProvider>;
}

function renderDialog (onOpenChange = vi.fn()) {
  render(
    <RestoreDialog
      profileSlug="docs"
      targetId={3}
      snapshot={snapshot}
      open
      onOpenChange={onOpenChange}
    />,
    { wrapper }
  );
  return { onOpenChange, dialog: screen.getByRole('dialog') };
}

function pickScope (dialog: HTMLElement, label: string) {
  fireEvent.click(within(dialog).getByLabelText(new RegExp(label)));
}

describe('RestoreDialog says what the backend does', () => {
  it('the folder becomes the tree right after that backup', () => {
    const { dialog } = renderDialog();
    expect(dialog).toHaveTextContent('exactly as they were right after the backup of');
    expect(dialog).toHaveTextContent('The local folder is made identical to the snapshot');
    expect(dialog).toHaveTextContent('files that are not in the snapshot are removed');
  });

  it('safety copies go to .omnisync-trash/pre-restore/ inside the restored folder, not the backup target', () => {
    const { dialog } = renderDialog();
    expect(dialog).toHaveTextContent('.omnisync-trash/pre-restore/<time>/ inside the restored folder');
    expect(dialog).not.toHaveTextContent('.omnisync-pre-restore');
    expect(dialog).not.toHaveTextContent('in the backup target');
  });

  it('a one-sided restore mentions that automatic syncing is paused afterwards; both sides does not', () => {
    const { dialog } = renderDialog();
    expect(dialog).toHaveTextContent('automatic syncing of this profile is paused');
    pickScope(dialog, 'Both');
    expect(dialog).not.toHaveTextContent('automatic syncing of this profile is paused');
    expect(dialog).toHaveTextContent('removed on both sides');
  });

  it('restores only after the confirmation, with the chosen scope', async () => {
    const { dialog, onOpenChange } = renderDialog();
    pickScope(dialog, 'Remote Only');
    const restoreBtn = within(dialog).getByRole('button', { name: 'Restore' });
    expect(restoreBtn).toBeDisabled();
    fireEvent.click(within(dialog).getByRole('checkbox'));
    fireEvent.click(restoreBtn);
    // The other calls are the side-effect-free previews.
    const restoreCall = () => fetchMock.mock.calls.find(([url]) => url === '/api/profiles/docs/backups/3/restore');
    await waitFor(() => expect(restoreCall()).toBeDefined());
    const [, init] = restoreCall()!;
    expect(JSON.parse(init.body)).toEqual({ snapshot_id: snapshot.snapshot_id, restore_scope: 'remote_only' });
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Restore completed'));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});

describe('restore wording in every locale', () => {
  const scopes: RestoreScope[] = ['local_only', 'remote_only', 'both'];
  for (const [name, locale] of Object.entries({ en, de, fa })) {
    it(`${name}: no old .omnisync-pre-restore wording, safety copies in .omnisync-trash/pre-restore/`, () => {
      const backups = locale.backups as unknown as {
        restoreWarning:      Record<RestoreScope, string>;
        restoreSafetyCopies: string;
      };
      expect(backups.restoreSafetyCopies).toContain('.omnisync-trash/pre-restore/');
      for (const scope of scopes) {
        expect(backups.restoreWarning[scope]).toBeTruthy();
        expect(backups.restoreWarning[scope]).not.toContain('.omnisync-pre-restore');
      }
      expect(JSON.stringify(locale)).not.toContain('.omnisync-pre-restore');
    });
  }
});

describe('BackupHistory labels the restore points honestly', () => {
  it('marks the latest backup and restores it', async () => {
    const snapshots: Snapshot[] = [
      { ...snapshot, snapshot_id: '2026-09-03T10-00-00', created_at: '2026-09-03T10:00:00Z', latest: true },
      { ...snapshot, snapshot_id: '2026-09-02T10-00-00', created_at: '2026-09-02T10:00:00Z' },
    ];
    fetchMock.mockImplementation(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(snapshots) }));
    render(<BackupHistory profileSlug="docs" targetId={3} />, { wrapper });

    const rows = await screen.findAllByRole('button', { name: 'Restore' });
    expect(rows).toHaveLength(2);
    const [latest, older] = rows.map((b) => b.parentElement as HTMLElement);
    expect(latest).toHaveTextContent('Latest backup');
    expect(older).not.toHaveTextContent('Latest backup');

    fireEvent.click(rows[0]);
    expect(screen.getByRole('dialog')).toHaveTextContent('exactly as they were right after the backup of');
  });
});
