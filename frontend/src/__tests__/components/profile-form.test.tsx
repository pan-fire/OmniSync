import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nProvider } from '@/i18n';
import { ProfileForm } from '@/components/profiles/profile-form';
import { fill } from '../helpers/fill';
import type { BrowseResponse, Remote } from '@/types';

// Radix Switch measures itself inside a form; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe () {}
  unobserve () {}
  disconnect () {}
} as unknown as typeof ResizeObserver;

const REMOTES: Remote[] = [
  { name: 'gdrive', type: 'drive', last_verified: null },
  { name: 'nas', type: 'sftp', last_verified: null },
];

const LOCAL_LISTING: BrowseResponse = {
  current: '/data',
  parent:  '/',
  entries: [{ name: 'docs', path: '/data/docs' }],
};
const LOCAL_DOCS: BrowseResponse = { current: '/data/docs', parent: '/data', entries: [] };
const REMOTE_LISTING: BrowseResponse = {
  current: 'gdrive:',
  parent:  null,
  entries: [{ name: 'Backup', path: 'gdrive:Backup' }],
};
const REMOTE_BACKUP: BrowseResponse = { current: 'gdrive:Backup', parent: 'gdrive:', entries: [] };

function jsonResponse (data: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(data) });
}

let fetchMock: ReturnType<typeof vi.fn>;
let remotes: Remote[];
const originalFetch = globalThis.fetch;

beforeEach(() => {
  remotes = REMOTES;
  fetchMock = vi.fn().mockImplementation((url: string) => {
    if (url === '/api/remotes') return jsonResponse(remotes);
    if (url.startsWith('/api/browse/local')) {
      const path = new URL(url, 'http://x').searchParams.get('path');
      return jsonResponse(path === '/data/docs' ? LOCAL_DOCS : LOCAL_LISTING);
    }
    if (url.startsWith('/api/browse/remote')) {
      const path = new URL(url, 'http://x').searchParams.get('path');
      return jsonResponse(path === 'gdrive:Backup' ? REMOTE_BACKUP : REMOTE_LISTING);
    }
    return jsonResponse({});
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function wrapper ({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nProvider>{children}</I18nProvider>
    </QueryClientProvider>
  );
}

function renderForm () {
  return render(<ProfileForm onSubmit={vi.fn()} onCancel={vi.fn()} />, { wrapper });
}

function browseCalls (kind: 'local' | 'remote') {
  return fetchMock.mock.calls
    .map(([url]) => url as string)
    .filter((url) => url.startsWith(`/api/browse/${kind}`));
}

describe('ProfileForm directory browsing', () => {
  it('has a browse button next to each directory input', () => {
    renderForm();

    const local = screen.getByLabelText('Local directory');
    const remote = screen.getByLabelText('Remote directory');
    expect(within(local.parentElement!).getByRole('button', { name: 'Browse local directory' })).toBeInTheDocument();
    expect(within(remote.parentElement!).getByRole('button', { name: 'Browse remote directory' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('the local browse button opens a local DirBrowser that fills the local directory', async () => {
    const user = userEvent.setup();
    renderForm();

    await fill(user, screen.getByLabelText('Local directory'), '/data');
    await user.click(screen.getByRole('button', { name: 'Browse local directory' }));

    const dialog = await screen.findByRole('dialog', { name: 'Browse local directory' });
    expect(browseCalls('local')[0]).toBe('/api/browse/local?path=%2Fdata');
    expect(browseCalls('remote')).toHaveLength(0);

    await user.click(await within(dialog).findByRole('button', { name: /docs/ }));
    await within(dialog).findByText('/data/docs');
    await user.click(within(dialog).getByRole('button', { name: 'Select this folder' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByLabelText('Local directory')).toHaveValue('/data/docs');
  });

  it('the remote browse button opens a remote DirBrowser that fills the remote directory', async () => {
    const user = userEvent.setup();
    renderForm();

    await fill(user, screen.getByLabelText('Remote directory'), 'gdrive:');
    await user.click(screen.getByRole('button', { name: 'Browse remote directory' }));

    const dialog = await screen.findByRole('dialog', { name: 'Browse remote directory' });
    expect(browseCalls('remote')[0]).toBe('/api/browse/remote?path=gdrive%3A');
    expect(browseCalls('local')).toHaveLength(0);

    await user.click(await within(dialog).findByRole('button', { name: /Backup/ }));
    await within(dialog).findByText('gdrive:Backup');
    await user.click(within(dialog).getByRole('button', { name: 'Select this folder' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByLabelText('Remote directory')).toHaveValue('gdrive:Backup');
  });

  it('cancelling the browser leaves the directory unchanged', async () => {
    const user = userEvent.setup();
    renderForm();

    await fill(user, screen.getByLabelText('Local directory'), '/data');
    await user.click(screen.getByRole('button', { name: 'Browse local directory' }));
    const dialog = await screen.findByRole('dialog', { name: 'Browse local directory' });
    await within(dialog).findByText('/data');
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByLabelText('Local directory')).toHaveValue('/data');
  });
});

describe('ProfileForm remote dropdown', () => {
  it('lists the configured remotes with their type', async () => {
    renderForm();

    const gdrive = await screen.findByRole('button', { name: /gdrive/ });
    expect(gdrive).toHaveTextContent('drive');
    expect(screen.getByRole('button', { name: /nas/ })).toHaveTextContent('sftp');
    expect(screen.getByText('2 configured')).toBeInTheDocument();
    expect(screen.getByText('Select a remote...')).toBeInTheDocument();
  });

  it('selecting a remote fills the remote: prefix of an empty remote directory', async () => {
    const user = userEvent.setup();
    renderForm();

    const gdrive = await screen.findByRole('button', { name: /gdrive/ });
    expect(gdrive).toHaveAttribute('aria-pressed', 'false');
    await user.click(gdrive);

    expect(screen.getByLabelText('Remote directory')).toHaveValue('gdrive:');
    expect(gdrive).toHaveAttribute('aria-pressed', 'true');
  });

  it('switching remotes keeps the path after the prefix', async () => {
    const user = userEvent.setup();
    renderForm();

    await fill(user, screen.getByLabelText('Remote directory'), 'gdrive:work/docs');
    expect(await screen.findByRole('button', { name: /gdrive/ })).toHaveAttribute('aria-pressed', 'true');

    await user.click(screen.getByRole('button', { name: /nas/ }));
    expect(screen.getByLabelText('Remote directory')).toHaveValue('nas:work/docs');
  });

  it('shows an empty state with the setup wizard when no remotes exist', async () => {
    remotes = [];
    renderForm();

    expect(await screen.findByText('Add a cloud remote to get started with syncing')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Setup Wizard' })).toBeInTheDocument();
    expect(screen.getByText('0 configured')).toBeInTheDocument();
  });
});

describe('ProfileForm helper text', () => {
  it('explains the local and remote directory formats', () => {
    renderForm();

    expect(screen.getByText('Full path on your machine, e.g. /home/user/Documents/gdrive')).toBeInTheDocument();
    expect(screen.getByText('Format: remotename:path — e.g. gdrive:backup/docs')).toBeInTheDocument();
  });

  it('replaces the helper text with the error once a directory is invalid', async () => {
    const user = userEvent.setup();
    renderForm();

    await fill(user, screen.getByLabelText('Profile name'), 'Docs');
    await fill(user, screen.getByLabelText('Local directory'), 'relative');
    await fill(user, screen.getByLabelText('Remote directory'), 'gdrive:docs');
    await user.click(screen.getByRole('button', { name: 'Create Profile' }));

    expect(screen.queryByText('Full path on your machine, e.g. /home/user/Documents/gdrive')).not.toBeInTheDocument();
    expect(screen.getByText('Format: remotename:path — e.g. gdrive:backup/docs')).toBeInTheDocument();
  });
});
