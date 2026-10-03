import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider } from '@/i18n';
import { RemoteCard } from '@/components/remotes/remote-card';
import { RemoteImportDialog } from '@/components/remotes/remote-import-dialog';
import { RemoteWizard } from '@/components/config/wizard/remote-wizard';
import { stubBackend } from '../helpers/fake-backend';
import type { Provider, Remote } from '@/types';

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

afterEach(() => {
  vi.unstubAllGlobals();
});

function wrapper ({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <I18nProvider>{children}</I18nProvider>
    </QueryClientProvider>
  );
}

const SFTP: Provider = {
  id:           'sftp',
  display_name: 'SFTP',
  icon:         'sftp',
  auth_type:    'key',
  default_name: 'sftp',
  setup_guide:  '',
  fields:       [
    { name: 'host', label: 'Host', field_type: 'text', required: true, help_text: 'Hostname.' },
    { name: 'user', label: 'Username', field_type: 'text', required: true, help_text: 'User.' },
    { name: 'pass', label: 'Password', field_type: 'password', required: false, help_text: 'Password.' },
    { name: 'port', label: 'Port', field_type: 'text', required: false, help_text: 'Port.' },
  ],
};

const DRIVE: Provider = {
  id:           'drive',
  display_name: 'Google Drive',
  icon:         'gdrive',
  auth_type:    'oauth',
  default_name: 'gdrive',
  setup_guide:  '',
  fields:       [
    { name: 'client_id', label: 'Client ID', field_type: 'text', required: true, help_text: 'Your app.' },
    { name: 'client_secret', label: 'Client Secret', field_type: 'password', required: true, help_text: 'Your app.' },
  ],
};

const CRYPT: Provider = {
  id:           'crypt',
  display_name: 'Encrypted (crypt)',
  icon:         'crypt',
  auth_type:    'key',
  default_name: 'secret',
  setup_guide:  '',
  fields:       [
    { name: 'remote', label: 'Encrypted folder', field_type: 'remote_path', required: true, help_text: 'Where.' },
    { name: 'password', label: 'Password', field_type: 'password', required: true, help_text: 'Pw.' },
    {
      name:       'filename_encryption',
      label:      'File name encryption',
      field_type: 'select',
      required:   false,
      help_text:  'Names.',
      options:    ['standard', 'obfuscate', 'off'],
      default:    'standard',
    },
  ],
};

const deps = { profiles: [], backup_targets: [] };

describe('RemoteCard: edit and reconnect', () => {
  it('shows Edit for editable remotes and Reconnect for OAuth remotes only', () => {
    stubBackend({ 'GET /remotes/box/dependencies': deps, 'GET /remotes/gdrive/dependencies': deps });
    const box: Remote = { name: 'box', type: 'sftp', last_verified: null, provider_id: 'sftp', editable: true, reconnectable: false };
    const gdrive: Remote = { name: 'gdrive', type: 'drive', last_verified: null, provider_id: 'drive', editable: false, reconnectable: true };
    const crypt: Remote = { name: 'vault', type: 'pcloud', last_verified: null, provider_id: null };
    const { unmount } = render(<><RemoteCard remote={box} /><RemoteCard remote={gdrive} /><RemoteCard remote={crypt} /></>, { wrapper });
    expect(screen.getByRole('button', { name: 'Edit remote box' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reconnect remote box' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reconnect remote gdrive' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit remote gdrive' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /(Edit|Reconnect) remote vault/ })).toBeNull();
    unmount();
  });

  it('puts a Reconnect alert on a remote whose sign-in failed', () => {
    stubBackend({ 'GET /remotes/gdrive/dependencies': deps });
    const gdrive: Remote = {
      name: 'gdrive', type: 'drive', last_verified: null, provider_id: 'drive', reconnectable: true, auth_error: true,
    };
    render(<RemoteCard remote={gdrive} />, { wrapper });
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('has expired or was revoked');
    expect(within(alert).getByRole('button', { name: 'Reconnect' })).toBeInTheDocument();
  });

  it('shows the alert after a test fails with an auth error', async () => {
    stubBackend({
      'GET /remotes/box/dependencies': deps,
      'GET /remotes':                  [],
      'POST /remotes/box/test':        { success: false, latency_ms: 5, error: 'Connection test failed.', auth_error: true },
    });
    const user = userEvent.setup();
    const box: Remote = { name: 'box', type: 'sftp', last_verified: null, provider_id: 'sftp', editable: true };
    render(<RemoteCard remote={box} />, { wrapper });
    await user.click(screen.getByRole('button', { name: /Test/ }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('rejected the credentials');
    expect(within(alert).getByRole('button', { name: 'Update credentials' })).toBeInTheDocument();
  });

  it('edit keeps an untouched secret and sends only changes', async () => {
    let body: unknown = null;
    stubBackend({
      'GET /remotes/box/dependencies': deps,
      'GET /remotes':                  [],
      'GET /wizard/providers':         [SFTP],
      'GET /remotes/box/config':       {
        name:        'box',
        type:        'sftp',
        provider_id: 'sftp',
        fields:      [
          { name: 'host', value: 'old.example', is_set: true, secret: false },
          { name: 'user', value: 'me', is_set: true, secret: false },
          { name: 'pass', value: '', is_set: true, secret: true },
          { name: 'port', value: '22', is_set: true, secret: false },
        ],
        other_keys: ['md5sum_command'],
      },
      'PUT /remotes/box': (_url: URL, init?: globalThis.RequestInit) => {
        body = JSON.parse(String(init?.body));
        return { detail: 'ok' };
      },
    });
    const user = userEvent.setup();
    const box: Remote = { name: 'box', type: 'sftp', last_verified: null, provider_id: 'sftp', editable: true };
    render(<RemoteCard remote={box} />, { wrapper });
    await user.click(screen.getByRole('button', { name: 'Edit remote box' }));

    const host = await screen.findByLabelText(/^Host/);
    expect(host).toHaveValue('old.example');
    const pass = screen.getByLabelText(/^Password/);
    expect(pass).toHaveValue('');
    expect(pass).toHaveAttribute('placeholder', 'Stored — leave empty to keep it');
    expect(screen.getByText(/md5sum_command/)).toBeInTheDocument();

    await user.clear(host);
    await user.type(host, 'new.example');
    await user.clear(screen.getByLabelText(/^Port/));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(body).toEqual({ params: { host: 'new.example', port: '' }, clear: [] }));
  });

  it('edit can remove a stored secret', async () => {
    let body: unknown = null;
    stubBackend({
      'GET /remotes/box/dependencies': deps,
      'GET /remotes':                  [],
      'GET /wizard/providers':         [SFTP],
      'GET /remotes/box/config':       {
        name:        'box',
        type:        'sftp',
        provider_id: 'sftp',
        fields:      [
          { name: 'host', value: 'h', is_set: true, secret: false },
          { name: 'user', value: 'me', is_set: true, secret: false },
          { name: 'pass', value: '', is_set: true, secret: true },
          { name: 'port', value: '', is_set: false, secret: false },
        ],
        other_keys: [],
      },
      'PUT /remotes/box': (_url: URL, init?: globalThis.RequestInit) => {
        body = JSON.parse(String(init?.body));
        return { detail: 'ok' };
      },
    });
    const user = userEvent.setup();
    const box: Remote = { name: 'box', type: 'sftp', last_verified: null, provider_id: 'sftp', editable: true };
    render(<RemoteCard remote={box} />, { wrapper });
    await user.click(screen.getByRole('button', { name: 'Edit remote box' }));
    await user.click(await screen.findByRole('checkbox', { name: /Remove the stored Password/ }));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(body).toEqual({ params: {}, clear: ['pass'] }));
  });
});

describe('Wizard reconnect mode', () => {
  it('signs the remote in again and stores only the token', async () => {
    const sent: Record<string, unknown>[] = [];
    const { requests } = stubBackend({
      'GET /wizard/providers':  [DRIVE],
      'POST /wizard/authorize': (_url: URL, init?: globalThis.RequestInit) => {
        sent.push(JSON.parse(String(init?.body)));
        return { session_id: 's1', auth_url: 'https://accounts.example/auth', redirect_uri: 'http://localhost:3000/api/wizard/oauth/callback' };
      },
      'GET /wizard/oauth/redirect-uri': { redirect_uri: 'http://localhost:3000/api/wizard/oauth/callback' },
      // The provider sent the browser to the callback: the sign-in is done.
      'GET /wizard/sessions/s1':        { session_id: 's1', status: 'completed', auth_url: null, error: null },
      'POST /wizard/reconnect':         (_url: URL, init?: globalThis.RequestInit) => {
        sent.push(JSON.parse(String(init?.body)));
        return { detail: 'ok' };
      },
      'POST /wizard/test': { success: true, error: null },
    });
    const user = userEvent.setup();
    render(<RemoteWizard open onOpenChange={() => {}} reconnect={{ name: 'gdrive', providerId: 'drive' }} />, { wrapper });

    expect(screen.getByText('Reconnect gdrive')).toBeInTheDocument();
    // Empty app fields keep the remote's stored app.
    expect(await screen.findByText(/leave the fields empty to keep the app/i)).toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: 'Start authorization' }));
    await waitFor(() => expect(sent[0]).toEqual({ provider_id: 'drive', remote_name: 'gdrive' }));

    await screen.findByText('Remote reconnected!');
    expect(sent[1]).toEqual({ name: 'gdrive', session_id: 's1' });
    expect(requests).not.toContain('POST /wizard/create');
  });
});

describe('Wizard: select and remote-path fields', () => {
  it('starts selects at their default and builds remote:folder', async () => {
    let created: Record<string, unknown> | null = null;
    stubBackend({
      'GET /wizard/providers': [CRYPT],
      'GET /remotes':          [{ name: 'base', type: 'alias', last_verified: null }],
      'POST /wizard/create':   (_url: URL, init?: globalThis.RequestInit) => {
        created = JSON.parse(String(init?.body));
        return { detail: 'ok' };
      },
      'POST /wizard/test': { success: true, error: null },
    });
    const user = userEvent.setup();
    render(<RemoteWizard open onOpenChange={() => {}} />, { wrapper });
    await user.click(await screen.findByRole('button', { name: /Encrypted \(crypt\)/ }));
    await user.click(screen.getByRole('button', { name: 'Next' }));

    expect(await screen.findByLabelText(/^File name encryption/)).toHaveValue('standard');
    await user.selectOptions(await screen.findByLabelText(/^Encrypted folder/), 'base');
    await user.type(screen.getByRole('textbox', { name: 'Folder' }), 'vault');
    await user.type(screen.getByLabelText(/^Password/), 'pw');
    await user.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(created).toEqual({
      name:        'secret',
      provider_id: 'crypt',
      params:      { remote: 'base:vault', password: 'pw', filename_encryption: 'standard' },
    }));
  });
});

describe('RemoteImportDialog', () => {
  const CONF = '[gdrive]\ntype = drive\ntoken = {"access_token":"x"}\n';

  it('previews, renames a clash and imports the selection', async () => {
    let imported: unknown = null;
    stubBackend({
      'POST /remotes/import/preview': {
        remotes: [
          { name: 'gdrive', type: 'drive', exists: true, problems: [], keys: ['token'] },
          { name: 'vault', type: 'crypt', exists: false, problems: [], keys: ['remote', 'password'] },
          { name: 'evil', type: 'sftp', exists: false, problems: ["'ssh' runs a program on this machine and is not imported"], keys: ['ssh'] },
        ],
        errors: [],
      },
      'POST /remotes/import': (_url: URL, init?: globalThis.RequestInit) => {
        imported = JSON.parse(String(init?.body));
        return { imported: ['gdrive-imported', 'vault'] };
      },
    });
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(<RemoteImportDialog open onOpenChange={onOpenChange} existingNames={['gdrive']} />, { wrapper });
    await user.click(screen.getByLabelText('or paste its contents'));
    await user.paste(CONF);
    await user.click(screen.getByRole('button', { name: 'Check file' }));

    const list = await screen.findByRole('list', { name: 'Remotes in the file' });
    expect(within(list).getByText('Name already in use')).toBeInTheDocument();
    expect(within(list).getByText(/runs a program/)).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Import evil' })).toBeDisabled();
    // The clashing one starts unselected; selecting it offers a free name.
    await user.click(screen.getByRole('checkbox', { name: 'Import gdrive' }));
    expect(screen.getByLabelText('Import as', { selector: '#import-name-gdrive' })).toHaveValue('gdrive-imported');

    await user.click(screen.getByRole('button', { name: 'Import 2 remotes' }));
    await waitFor(() => expect(imported).toEqual({
      content: CONF,
      remotes: [{ source: 'gdrive', name: 'gdrive-imported' }, { source: 'vault', name: 'vault' }],
    }));
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it('blocks a name that is taken', async () => {
    stubBackend({
      'POST /remotes/import/preview': {
        remotes: [{ name: 'vault', type: 'crypt', exists: false, problems: [], keys: [] }],
        errors:  [],
      },
    });
    const user = userEvent.setup();
    render(<RemoteImportDialog open onOpenChange={() => {}} existingNames={['gdrive']} />, { wrapper });
    await user.click(screen.getByLabelText('or paste its contents'));
    await user.paste('[vault]\ntype = crypt\n');
    await user.click(screen.getByRole('button', { name: 'Check file' }));
    const name = await screen.findByLabelText('Import as');
    await user.clear(name);
    await user.type(name, 'gdrive');
    expect(screen.getByText('A remote with this name exists already.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import 1 remote' })).toBeDisabled();
  });

  it('shows why a file cannot be read', async () => {
    stubBackend({ 'POST /remotes/import/preview': { remotes: [], errors: ['This rclone.conf is encrypted.'] } });
    const user = userEvent.setup();
    render(<RemoteImportDialog open onOpenChange={() => {}} existingNames={[]} />, { wrapper });
    await user.click(screen.getByLabelText('or paste its contents'));
    await user.paste('RCLONE_ENCRYPT_V0:abc');
    await user.click(screen.getByRole('button', { name: 'Check file' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('encrypted');
  });
});
