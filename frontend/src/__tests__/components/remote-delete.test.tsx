import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import { RemoteManager } from '@/components/config/remote-manager';
import { RemoteCard } from '@/components/remotes/remote-card';
import type { RemoteDependencies } from '@/types';

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

const REMOTE = { name: 'gdrive', type: 'drive', last_verified: null };
const NO_DEPS: RemoteDependencies = { profiles: [], backup_targets: [] };
const DEPS: RemoteDependencies = {
  profiles:       [{ slug: 'docs', name: 'Docs' }],
  backup_targets: [{ profile_slug: 'photos', target_name: 'Offsite', target_id: 4 }],
};

const originalFetch = globalThis.fetch;
let fetchMock: ReturnType<typeof vi.fn>;

function respond (data: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(data) });
}

/** Answers GET .../dependencies from `deps` (one entry per call, last repeats) and DELETE with `deleteStatus`. */
function backend (deps: RemoteDependencies[], deleteStatus: (url: string) => number = () => 200) {
  let depCalls = 0;
  fetchMock.mockImplementation((url: string, init?: { method?: string }) => {
    if (init?.method === 'DELETE') {
      const status = deleteStatus(url);
      return status === 409
        ? respond({ detail: 'Remote has dependencies. Use ?force=true to delete anyway.' }, 409)
        : respond({ detail: "Remote 'gdrive' deleted" }, status);
    }
    if (url.endsWith('/dependencies')) {
      const answer = deps[Math.min(depCalls, deps.length - 1)];
      depCalls += 1;
      return respond(answer);
    }
    return respond([]);
  });
}

const deletes = () => fetchMock.mock.calls.filter(([, init]) => init?.method === 'DELETE').map(([url]) => url as string);

beforeEach(() => {
  vi.clearAllMocks();
  fetchMock = vi.fn(() => respond([]));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function wrapper ({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return <QueryClientProvider client={qc}><I18nProvider>{children}</I18nProvider></QueryClientProvider>;
}

function openDeleteOnConfigPage () {
  render(<RemoteManager remotes={[REMOTE]} />, { wrapper });
  fireEvent.click(screen.getByRole('button', { name: 'Delete remote gdrive' }));
  return screen.getByRole('dialog');
}

describe('Config page: deleting a remote that is still used', () => {
  it('lists the dependent profiles and backup targets and explains what force does', async () => {
    backend([DEPS]);
    const dialog = openDeleteOnConfigPage();
    expect(await within(dialog).findByText('Profile Docs (docs)')).toBeInTheDocument();
    expect(within(dialog).getByText('Backup target Offsite of profile photos')).toBeInTheDocument();
    expect(dialog).toHaveTextContent('Delete anyway removes only the rclone configuration of the remote');
    expect(dialog).toHaveTextContent('their syncs and backups fail until you point them at another remote');
    expect(within(dialog).queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument();
    expect(deletes()).toEqual([]);
  });

  it('sends ?force=true only after the explicit "Delete anyway"', async () => {
    backend([DEPS]);
    const dialog = openDeleteOnConfigPage();
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Delete anyway' }));
    await waitFor(() => expect(deletes()).toEqual(['/api/remotes/gdrive?force=true']));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Remote deleted successfully'));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('cancel sends nothing', async () => {
    backend([DEPS]);
    const dialog = openDeleteOnConfigPage();
    await within(dialog).findByRole('button', { name: 'Delete anyway' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(deletes()).toEqual([]);
  });

  it('an unused remote is deleted without force', async () => {
    backend([NO_DEPS]);
    const dialog = openDeleteOnConfigPage();
    const del = within(dialog).getByRole('button', { name: 'Delete' });
    await waitFor(() => expect(del).toBeEnabled());
    expect(within(dialog).queryByRole('button', { name: 'Delete anyway' })).not.toBeInTheDocument();
    fireEvent.click(del);
    await waitFor(() => expect(deletes()).toEqual(['/api/remotes/gdrive']));
  });

  it('a 409 from the server shows the dependencies and waits for "Delete anyway"', async () => {
    // Nothing used the remote when the dialog opened; a profile appeared since.
    backend([NO_DEPS, DEPS], (url) => (url.includes('force=true') ? 200 : 409));
    const dialog = openDeleteOnConfigPage();
    const del = within(dialog).getByRole('button', { name: 'Delete' });
    await waitFor(() => expect(del).toBeEnabled());
    fireEvent.click(del);

    expect(await within(dialog).findByText('Profile Docs (docs)')).toBeInTheDocument();
    expect(deletes()).toEqual(['/api/remotes/gdrive']);
    expect(toast.error).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete anyway' }));
    await waitFor(() => expect(deletes()).toEqual(['/api/remotes/gdrive', '/api/remotes/gdrive?force=true']));
  });

  it('a 409 is explained even when the dependency list cannot be loaded', async () => {
    fetchMock.mockImplementation((url: string, init?: { method?: string }) => {
      if (init?.method === 'DELETE') return respond({ detail: 'Remote has dependencies.' }, 409);
      if (url.endsWith('/dependencies')) return respond({ detail: 'boom' }, 500);
      return respond([]);
    });
    const dialog = openDeleteOnConfigPage();
    expect(await within(dialog).findByText('Could not check what uses this remote.')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    expect(await within(dialog).findByText('The server reports that profiles or backup targets still use this remote.')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Delete anyway' })).toBeInTheDocument();
    expect(deletes()).toEqual(['/api/remotes/gdrive']);
  });
});

describe('Remotes page card uses the same confirmation', () => {
  it('does not force on its own', async () => {
    backend([DEPS]);
    render(<RemoteCard remote={REMOTE} />, { wrapper });
    fireEvent.click(screen.getByRole('button', { name: 'Delete remote gdrive' }));
    const dialog = screen.getByRole('dialog');
    expect(await within(dialog).findByText('Backup target Offsite of profile photos')).toBeInTheDocument();
    expect(deletes()).toEqual([]);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete anyway' }));
    await waitFor(() => expect(deletes()).toEqual(['/api/remotes/gdrive?force=true']));
  });
});
