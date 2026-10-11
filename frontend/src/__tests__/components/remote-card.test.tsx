import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider } from '@/i18n';
import { RemoteCard } from '@/components/remotes/remote-card';
import type { Remote, RemoteDependencies } from '@/types';
import { stubBackend } from '../helpers/fake-backend';

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() } }));
// The dialogs the card opens have their own tests; here they only show that they opened.
vi.mock('@/components/config/wizard/remote-wizard', () => ({
  RemoteWizard: ({ reconnect, onOpenChange }: { reconnect: { name: string; providerId: string }; onOpenChange: (o: boolean) => void }) => (
    <div role="dialog" aria-label="wizard">
      reconnect {reconnect.name} via {reconnect.providerId}
      <button onClick={() => onOpenChange(false)}>close wizard</button>
    </div>
  ),
}));
vi.mock('@/components/remotes/remote-edit-dialog', () => ({
  RemoteEditDialog: ({ open, name }: { open: boolean; name: string }) => (open ? <div role="dialog" aria-label="edit">edit {name}</div> : null),
}));
vi.mock('@/components/shared/dir-browser', () => ({
  DirBrowser: ({ open, initialPath }: { open: boolean; initialPath: string }) => (open ? <div role="dialog" aria-label="browse">browse {initialPath}</div> : null),
}));
vi.mock('@/components/remotes/remote-storage-bar', () => ({ RemoteStorageBar: () => null }));

const NO_DEPS: RemoteDependencies = { profiles: [], backup_targets: [] };
const DEPS: RemoteDependencies = {
  profiles:       [{ slug: 'docs', name: 'Docs' }],
  backup_targets: [{ profile_slug: 'photos', target_name: 'Offsite', target_id: 4 }],
};

function renderCard (remote: Partial<Remote> = {}, locale: 'en' | 'fa' = 'en') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <I18nProvider initialLocale={locale}>
        <RemoteCard remote={{ name: 'gdrive', type: 'drive', last_verified: null, ...remote }} />
      </I18nProvider>
    </QueryClientProvider>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('RemoteCard', () => {
  it('lists what uses the remote when expanded', async () => {
    stubBackend({ 'GET /remotes/gdrive/dependencies': DEPS });
    const user = userEvent.setup();
    renderCard();

    const toggle = screen.getByRole('button', { name: /Used by/ });
    await waitFor(() => expect(toggle).toHaveTextContent('2'));
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Docs')).toBeInTheDocument();
    expect(screen.getByText('Offsite (photos)')).toBeInTheDocument();

    await user.click(toggle);
    expect(screen.queryByText('Docs')).not.toBeInTheDocument();
  });

  it('says when nothing uses the remote', async () => {
    stubBackend({ 'GET /remotes/gdrive/dependencies': NO_DEPS });
    const user = userEvent.setup();
    renderCard();
    const toggle = screen.getByRole('button', { name: /Used by/ });
    await waitFor(() => expect(toggle).toHaveTextContent('0'));
    await user.click(toggle);
    expect(screen.getByText('Not used by any profiles')).toBeInTheDocument();
  });

  it('a test shows success with the latency', async () => {
    stubBackend({
      'GET /remotes/gdrive/dependencies': NO_DEPS,
      'POST /remotes/gdrive/test':        { success: true, latency_ms: 42, error: null },
    });
    const user = userEvent.setup();
    renderCard({ last_verified: '2026-09-27T08:30:00Z' });
    expect(screen.getByText(/Last verified/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Test' }));
    expect(await screen.findByText('Connection OK')).toBeInTheDocument();
    expect(screen.getByText(/42ms/)).toBeInTheDocument();
  });

  it('a failed test names the error', async () => {
    stubBackend({
      'GET /remotes/gdrive/dependencies': NO_DEPS,
      'POST /remotes/gdrive/test':        { success: false, latency_ms: null, error: 'timeout' },
    });
    const user = userEvent.setup();
    renderCard();
    await user.click(screen.getByRole('button', { name: 'Test' }));
    expect(await screen.findByText('Connection failed: timeout')).toBeInTheDocument();
  });

  it('opens the remote browser at the remote root', async () => {
    stubBackend({ 'GET /remotes/gdrive/dependencies': NO_DEPS });
    const user = userEvent.setup();
    renderCard();
    await user.click(screen.getByRole('button', { name: 'Browse' }));
    expect(screen.getByRole('dialog', { name: 'browse' })).toHaveTextContent('browse gdrive:');
  });

  it('an editable remote opens its edit dialog', async () => {
    stubBackend({ 'GET /remotes/gdrive/dependencies': NO_DEPS });
    const user = userEvent.setup();
    renderCard({ editable: true });
    await user.click(screen.getByRole('button', { name: 'Edit remote gdrive' }));
    expect(screen.getByRole('dialog', { name: 'edit' })).toBeInTheDocument();
  });

  it('a reconnectable remote opens the wizard at its sign-in, and the wizard closes again', async () => {
    stubBackend({ 'GET /remotes/gdrive/dependencies': NO_DEPS });
    const user = userEvent.setup();
    renderCard({ reconnectable: true, provider_id: 'drive' });
    await user.click(screen.getByRole('button', { name: 'Reconnect remote gdrive' }));
    expect(screen.getByRole('dialog', { name: 'wizard' })).toHaveTextContent('reconnect gdrive via drive');
    await user.click(screen.getByRole('button', { name: 'close wizard' }));
    expect(screen.queryByRole('dialog', { name: 'wizard' })).not.toBeInTheDocument();
  });

  // A refused sign-in puts the fix on the card: reconnect when possible.
  it('an auth error offers a reconnect, and a reconnect clears the failed test', async () => {
    stubBackend({
      'GET /remotes/gdrive/dependencies': NO_DEPS,
      'POST /remotes/gdrive/test':        { success: false, latency_ms: null, error: 'unauthorized', auth_error: true },
    });
    const user = userEvent.setup();
    renderCard({ reconnectable: true, provider_id: 'drive' });

    await user.click(screen.getByRole('button', { name: 'Test' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The sign-in of this remote has expired or was revoked.');
    // The header button gives way to the one in the alert.
    expect(screen.queryByRole('button', { name: 'Reconnect remote gdrive' })).not.toBeInTheDocument();
    await user.click(within(alert).getByRole('button', { name: 'Reconnect' }));
    await user.click(screen.getByRole('button', { name: 'close wizard' }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByText(/Connection failed/)).not.toBeInTheDocument();
  });

  it('an auth error without a reconnect offers new credentials', async () => {
    stubBackend({ 'GET /remotes/gdrive/dependencies': NO_DEPS });
    const user = userEvent.setup();
    renderCard({ editable: true, auth_error: true });
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('The provider rejected the credentials of this remote.');
    await user.click(within(alert).getByRole('button', { name: 'Update credentials' }));
    expect(screen.getByRole('dialog', { name: 'edit' })).toBeInTheDocument();
  });

  it('delete asks for confirmation before deleting', async () => {
    const { requests } = stubBackend({
      'GET /remotes/gdrive/dependencies': NO_DEPS,
      'DELETE /remotes/gdrive':           { detail: 'deleted' },
      'GET /remotes':                     [],
    });
    const user = userEvent.setup();
    renderCard();
    await user.click(screen.getByRole('button', { name: 'Delete remote gdrive' }));
    const dialog = await screen.findByRole('dialog');
    expect(requests.some((r) => r.startsWith('DELETE'))).toBe(false);

    const confirm = within(dialog).getByRole('button', { name: 'Delete' });
    await waitFor(() => expect(confirm).toBeEnabled());
    await user.click(confirm);
    await waitFor(() => expect(requests).toContain('DELETE /remotes/gdrive'));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('renders its buttons in Persian', () => {
    stubBackend({ 'GET /remotes/gdrive/dependencies': NO_DEPS });
    renderCard({}, 'fa');
    expect(screen.getByRole('button', { name: 'آزمایش' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^حذف ریموت .*gdrive/ })).toBeInTheDocument();
  });
});
