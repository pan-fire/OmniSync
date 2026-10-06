import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { toast } from 'sonner';
import { I18nProvider, type Locale } from '@/i18n';
import { SidebarControlsProvider } from '@/components/layout/sidebar-controls';
import ProfilesPage from '@/app/profiles/page';
import { stubBackend } from '../helpers/fake-backend';
import type { ProfileStatus } from '@/types';

vi.mock('next/navigation', () => ({
  useParams:       () => ({}),
  useRouter:       () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname:     () => '/profiles',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

const confirmed = vi.hoisted(() => ({ request: vi.fn(), syncNow: vi.fn() }));
vi.mock('@/components/sync/sync-confirm-dialog', () => ({
  useConfirmedSync: () => ({ ...confirmed, isStarting: false, dialog: null }),
}));

// The form is tested on its own; here it only hands the page a submission.
const BACKUP = { name: 'Nightly', target_path: '/backups/docs', target_type: 'local' };
vi.mock('@/components/profiles/profile-form', () => ({
  ProfileForm: ({ onSubmit, onCancel }: { onSubmit: (s: unknown) => void; onCancel: () => void }) => (
    <div>
      <button type="button" onClick={() => onSubmit({ profile: { name: 'Docs' } })}>submit plain</button>
      <button type="button" onClick={() => onSubmit({ profile: { name: 'Docs' }, initialBackupTarget: BACKUP })}>
        submit with backup
      </button>
      <button type="button" onClick={onCancel}>cancel form</button>
    </div>
  ),
}));

function profile (over: Partial<ProfileStatus> = {}): ProfileStatus {
  return {
    id:                    1,
    slug:                  'docs',
    name:                  'Docs',
    local_dir:             '/data/docs',
    remote_dir:            'gdrive:docs',
    debounce_seconds:      5,
    pull_interval_minutes: 5,
    rclone_filter:         [],
    rclone_args:           [],
    max_retries:           3,
    enabled:               true,
    created_at:            '2026-01-01T00:00:00Z',
    updated_at:            '2026-01-01T00:00:00Z',
    sync_mode:             'two_way',
    state:                 'idle',
    last_sync:             null,
    current_job_id:        null,
    files_processed:       0,
    errors:                0,
    pending_changes:       0,
    intervals_paused:      false,
    paused_at:             null,
    last_error:            null,
    max_delete:            null,
    resync_required:       false,
    ...over,
  };
}

function renderPage (locale: Locale = 'en') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  function Wrapper ({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <I18nProvider initialLocale={locale}>
          <SidebarControlsProvider value={{ isSidebarCollapsed: false, toggleSidebar: () => {} }}>
            {children}
          </SidebarControlsProvider>
        </I18nProvider>
      </QueryClientProvider>
    );
  }
  return render(<ProfilesPage />, { wrapper: Wrapper });
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, '', '/profiles');
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ProfilesPage loading and errors', () => {
  // A failed load must not look like "no profiles": it says so and can retry.
  it('explains a failed load and retries into the list', async () => {
    const routes: Record<string, unknown> = {};
    const { requests } = stubBackend(routes);
    const user = userEvent.setup();
    renderPage();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load the profiles: no route for GET /profiles');
    expect(screen.queryByText('No sync profiles configured yet')).not.toBeInTheDocument();

    routes['GET /profiles'] = [profile()];
    await user.click(screen.getByRole('button', { name: 'Retry' }));

    expect(await screen.findByText('/data/docs')).toBeInTheDocument();
    expect(requests.filter((r) => r === 'GET /profiles')).toHaveLength(2);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows the failed load in Persian, right to left', async () => {
    stubBackend({});
    renderPage('fa');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('بارگذاری پروفایل‌ها ممکن نشد');
    expect(screen.getByRole('button', { name: 'تلاش مجدد' })).toBeInTheDocument();
    expect(document.documentElement.dir).toBe('rtl');
  });
});

describe('ProfilesPage sync buttons', () => {
  it('asks to confirm push and pull, and starts a two-way sync now', async () => {
    stubBackend({ 'GET /profiles': [profile({ intervals_paused: true })] });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole('button', { name: 'Push' }));
    expect(confirmed.request).toHaveBeenCalledWith('push', [{ slug: 'docs', name: 'Docs' }]);
    await user.click(screen.getByRole('button', { name: 'Pull' }));
    expect(confirmed.request).toHaveBeenCalledWith('pull', [{ slug: 'docs', name: 'Docs' }]);
    await user.click(screen.getByRole('button', { name: 'Sync now' }));
    expect(confirmed.syncNow).toHaveBeenCalledWith({ slug: 'docs', name: 'Docs' }, true);
  });
});

describe('ProfilesPage create dialog', () => {
  it('opens from the header, creates the profile and closes', async () => {
    const { requests } = stubBackend({ 'GET /profiles': [], 'POST /profiles': profile() });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole('button', { name: 'Create Profile' }));
    expect(await screen.findByRole('dialog', { name: 'Create Sync Profile' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'submit plain' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(requests).toContain('POST /profiles');
    expect(requests.some((r) => r.endsWith('/backups'))).toBe(false);
    expect(toast.success).toHaveBeenCalledWith('Profile "Docs" created');
  });

  it('cancel closes the dialog without creating anything', async () => {
    const { requests } = stubBackend({ 'GET /profiles': [] });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole('button', { name: 'Create Profile' }));
    await user.click(await screen.findByRole('button', { name: 'cancel form' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(requests.some((r) => r.startsWith('POST'))).toBe(false);
  });

  it('creates the initial backup target after the profile', async () => {
    const { requests } = stubBackend({
      'GET /profiles':               [],
      'POST /profiles':              profile(),
      'POST /profiles/docs/backups': { id: 1, name: 'Nightly' },
    });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole('button', { name: 'Create Profile' }));
    await user.click(await screen.findByRole('button', { name: 'submit with backup' }));

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Initial backup target created'));
    expect(requests.indexOf('POST /profiles')).toBeLessThan(requests.indexOf('POST /profiles/docs/backups'));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  // The profile exists even when its backup fails, so the dialog still closes.
  it('reports a failed initial backup but keeps the created profile', async () => {
    stubBackend({ 'GET /profiles': [], 'POST /profiles': profile() });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole('button', { name: 'Create Profile' }));
    await user.click(await screen.findByRole('button', { name: 'submit with backup' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      'Profile created, but initial backup setup failed: no route for POST /profiles/docs/backups'
    ));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('keeps the dialog open when creating the profile fails', async () => {
    const { requests } = stubBackend({ 'GET /profiles': [] });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole('button', { name: 'Create Profile' }));
    await user.click(await screen.findByRole('button', { name: 'submit with backup' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('no route for POST /profiles'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(requests.some((r) => r.endsWith('/backups'))).toBe(false);
  });
});
