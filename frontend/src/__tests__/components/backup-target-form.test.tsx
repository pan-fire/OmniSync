import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider } from '@/i18n';
import { BackupTargetForm } from '@/components/profiles/backup-target-form';
import {
  backupBrowseStart, backupTargetPathError, remoteOfPath, withRemote,
} from '@/components/profiles/backup-options';

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

// Radix Switch measures itself inside a form; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe () {}
  unobserve () {}
  disconnect () {}
} as unknown as typeof ResizeObserver;

describe('backup target path rules', () => {
  it('local paths must be absolute, as the backend requires', () => {
    expect(backupTargetPathError('local', '')).toBe('backups.validation.pathRequired');
    expect(backupTargetPathError('local', '/backups/docs')).toBeNull();
    expect(backupTargetPathError('local', 'backups/docs')).toBe('backups.validation.localPathAbsolute');
    expect(backupTargetPathError('local', '-backups')).toBe('backups.validation.localPathAbsolute');
    expect(backupTargetPathError('local', 'gdrive:backups')).toBe('backups.validation.localPathAbsolute');
  });

  it('remote kinds need remote:path in full', () => {
    for (const type of ['remote', 'custom_remote'] as const) {
      expect(backupTargetPathError(type, '   ')).toBe('backups.validation.pathRequired');
      expect(backupTargetPathError(type, 'backups/docs')).toBe('backups.validation.remotePathFormat');
      expect(backupTargetPathError(type, '/backups/docs')).toBe('backups.validation.remotePathFormat');
      expect(backupTargetPathError(type, ':backups')).toBe('backups.validation.remotePathFormat');
      expect(backupTargetPathError(type, 'my drive:backups')).toBe('backups.validation.remotePathFormat');
      expect(backupTargetPathError(type, 'gdrive:Backups/docs')).toBeNull();
      expect(backupTargetPathError(type, 'my-drive_2:')).toBeNull();
    }
  });

  it('a custom remote path must be on the chosen remote', () => {
    expect(backupTargetPathError('custom_remote', 'box:Backups', 'gdrive')).toBe('backups.validation.remotePathMismatch');
    expect(backupTargetPathError('custom_remote', 'gdrive:Backups', 'gdrive')).toBeNull();
  });

  it('withRemote fills or replaces the remote part', () => {
    expect(withRemote('', 'gdrive')).toBe('gdrive:');
    expect(withRemote('Backups/docs', 'gdrive')).toBe('gdrive:Backups/docs');
    expect(withRemote('/Backups/docs', 'gdrive')).toBe('gdrive:Backups/docs');
    expect(withRemote('box:Backups/docs', 'gdrive')).toBe('gdrive:Backups/docs');
    expect(remoteOfPath('gdrive:Backups')).toBe('gdrive');
    expect(remoteOfPath('/data')).toBeNull();
  });

  it('the folder browser opens locally, or on the target remote once it is known', () => {
    expect(backupBrowseStart('local', '', null)).toEqual({ mode: 'local', initialPath: '/' });
    expect(backupBrowseStart('local', ' /backups ', null)).toEqual({ mode: 'local', initialPath: '/backups' });
    expect(backupBrowseStart('custom_remote', '', '')).toBeNull();
    expect(backupBrowseStart('remote', 'Backups', null)).toBeNull();
    expect(backupBrowseStart('remote', 'gdrive:Backups/docs', 'gdrive'))
      .toEqual({ mode: 'remote', initialPath: 'gdrive:Backups/docs' });
    // A path on another remote, or none yet, opens the target remote's root.
    expect(backupBrowseStart('custom_remote', 'box:Backups', 'gdrive')).toEqual({ mode: 'remote', initialPath: 'gdrive:' });
    expect(backupBrowseStart('custom_remote', 'Backups', 'gdrive')).toEqual({ mode: 'remote', initialPath: 'gdrive:' });
  });
});

const REMOTES = [
  { name: 'gdrive', type: 'drive', last_verified: null },
  { name: 'box', type: 'box', last_verified: null },
];

const originalFetch = globalThis.fetch;
let fetchMock: ReturnType<typeof vi.fn>;

function respond (data: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(data) });
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchMock = vi.fn((url: string, init?: { method?: string; body?: string }) => {
    if (url === '/api/remotes') return respond(REMOTES);
    if (url === '/api/profiles/docs' && !init?.method) {
      return respond({ slug: 'docs', name: 'Docs', local_dir: '/data/docs', remote_dir: 'gdrive:Docs', state: 'idle' });
    }
    if (url.startsWith('/api/browse/')) {
      const current = decodeURIComponent(url.split('?path=')[1]);
      return respond({ current, parent: null, entries: [{ name: 'Backups', path: `${current}Backups` }] });
    }
    if (url === '/api/profiles/docs/backups' && init?.method === 'POST') {
      return respond({ ...JSON.parse(init.body ?? '{}'), id: 1 }, 201);
    }
    return respond([]);
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function renderForm () {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}><I18nProvider>{children}</I18nProvider></QueryClientProvider>
  );
  const onOpenChange = vi.fn();
  render(<BackupTargetForm profileSlug="docs" open onOpenChange={onOpenChange} />, { wrapper });
  return { dialog: screen.getByRole('dialog'), onOpenChange };
}

const posts = () => fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');

describe('BackupTargetForm remote paths', () => {
  it('same remote: prefills the profile remote and explains the remote:path format', async () => {
    const { dialog } = renderForm();
    // Wait for the profile, then switch the type.
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/profiles/docs', expect.anything()));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    fireEvent.click(within(dialog).getByLabelText('Same remote'));

    const path = within(dialog).getByLabelText('Target path') as HTMLInputElement;
    await waitFor(() => expect(path.value).toBe('gdrive:'));
    expect(dialog).toHaveTextContent('Backs up to the remote this profile syncs with (gdrive)');
    expect(dialog).toHaveTextContent('Type the full rclone path, remote name and folder, e.g. gdrive:Backups/docs');
    expect(path.placeholder).toBe('gdrive:Backups/docs');
  });

  it('refuses a remote target path without remote: and sends nothing', async () => {
    const { dialog } = renderForm();
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Offsite' } });
    fireEvent.click(within(dialog).getByLabelText('Same remote'));
    const path = within(dialog).getByLabelText('Target path');
    fireEvent.change(path, { target: { value: 'backups/docs' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));

    expect(await within(dialog).findByText('Enter the full rclone path as remote:folder, e.g. gdrive:Backups/docs.')).toBeInTheDocument();
    expect(path).toHaveAttribute('aria-invalid', 'true');
    expect(posts()).toHaveLength(0);

    // Fixing it clears the error live and the target is created.
    fireEvent.change(path, { target: { value: 'gdrive:backups/docs' } });
    expect(within(dialog).queryByText(/Enter the full rclone path/)).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(JSON.parse(posts()[0][1].body)).toMatchObject({
      target_type: 'remote', target_path: 'gdrive:backups/docs', remote_name: null,
    });
  });

  it('custom remote: picking a remote fills the remote: part and remote_name', async () => {
    const { dialog } = renderForm();
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Offsite' } });
    fireEvent.click(within(dialog).getByLabelText('Custom remote'));
    const path = within(dialog).getByLabelText('Target path') as HTMLInputElement;
    fireEvent.change(path, { target: { value: 'Backups/docs' } });

    fireEvent.click(await within(dialog).findByRole('button', { name: /box/ }));
    expect(path.value).toBe('box:Backups/docs');
    fireEvent.click(within(dialog).getByRole('button', { name: /gdrive/ }));
    expect(path.value).toBe('gdrive:Backups/docs');

    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(JSON.parse(posts()[0][1].body)).toMatchObject({
      target_type: 'custom_remote', target_path: 'gdrive:Backups/docs', remote_name: 'gdrive',
    });
  });

  it('custom remote: a path on another remote than the chosen one is refused', async () => {
    const { dialog } = renderForm();
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Offsite' } });
    fireEvent.click(within(dialog).getByLabelText('Custom remote'));
    fireEvent.click(await within(dialog).findByRole('button', { name: /gdrive/ }));
    fireEvent.change(within(dialog).getByLabelText('Target path'), { target: { value: 'box:Backups' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    expect(await within(dialog).findByText('The path must start with the chosen backup remote and a colon.')).toBeInTheDocument();
    expect(posts()).toHaveLength(0);
  });

  it('local targets show no remote hint', () => {
    const { dialog } = renderForm();
    expect(dialog).not.toHaveTextContent('Type the full rclone path');
  });
});

describe('BackupTargetForm folder browser', () => {
  const browseCalls = () => fetchMock.mock.calls.map(([url]) => url as string).filter((url) => url.startsWith('/api/browse/'));

  it('local targets browse the local file system', async () => {
    const { dialog } = renderForm();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Browse for the target folder' }));
    await waitFor(() => expect(browseCalls()).toEqual(['/api/browse/local?path=%2F']));
  });

  it('custom remote targets browse the chosen remote and fill the picked folder', async () => {
    const { dialog } = renderForm();
    fireEvent.click(within(dialog).getByLabelText('Custom remote'));
    // No remote chosen yet: nothing to browse.
    expect(within(dialog).queryByRole('button', { name: 'Browse for the target folder' })).not.toBeInTheDocument();

    fireEvent.click(await within(dialog).findByRole('button', { name: /gdrive/ }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Browse for the target folder' }));
    await waitFor(() => expect(browseCalls()).toEqual(['/api/browse/remote?path=gdrive%3A']));

    fireEvent.click(await screen.findByRole('button', { name: /Backups/ }));
    await waitFor(() => expect(browseCalls()).toContain('/api/browse/remote?path=gdrive%3ABackups'));
    fireEvent.click(await screen.findByRole('button', { name: 'Select this folder' }));
    const path = within(dialog).getByLabelText('Target path') as HTMLInputElement;
    await waitFor(() => expect(path.value).toBe('gdrive:Backups'));
  });
});

describe('BackupTargetForm encryption and verification', () => {
  const fillLocal = (dialog: HTMLElement) => {
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Vault' } });
    fireEvent.change(within(dialog).getByLabelText('Target path'), { target: { value: '/backups/vault' } });
  };

  it('verifies by default and sends no passphrase unless encryption is chosen', async () => {
    const { dialog } = renderForm();
    fillLocal(dialog);
    expect(within(dialog).getByRole('switch', { name: 'Verify after each backup' })).toBeChecked();
    expect(within(dialog).queryByLabelText('Passphrase')).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    const body = JSON.parse(posts()[0][1].body);
    expect(body.verify_after_backup).toBe(true);
    expect(body).not.toHaveProperty('encryption_passphrase');
  });

  it('asks for the passphrase twice, warns that a lost one loses the backups, and checks it', async () => {
    const { dialog } = renderForm();
    fillLocal(dialog);
    fireEvent.click(within(dialog).getByRole('switch', { name: 'Encrypt this backup' }));
    expect(dialog).toHaveTextContent('Without it these backups cannot be restored');

    const first = within(dialog).getByLabelText('Passphrase');
    const second = within(dialog).getByLabelText('Repeat the passphrase');
    fireEvent.change(first, { target: { value: 'short' } });
    fireEvent.change(second, { target: { value: 'short' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    expect(await within(dialog).findByText('The passphrase needs at least 8 characters.')).toBeInTheDocument();
    fireEvent.change(first, { target: { value: 'correct horse' } });
    fireEvent.change(second, { target: { value: 'correct hose' } });
    expect(within(dialog).getByText('The two passphrases differ.')).toBeInTheDocument();
    expect(posts()).toHaveLength(0);

    fireEvent.change(second, { target: { value: 'correct horse' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(JSON.parse(posts()[0][1].body)).toMatchObject({ encryption_passphrase: 'correct horse' });
  });
});
