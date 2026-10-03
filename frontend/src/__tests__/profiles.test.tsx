import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nProvider } from '@/i18n';
import { SidebarControlsProvider } from '@/components/layout/sidebar-controls';
import { ProfileCard } from '@/components/profiles/profile-card';
import ProfilesPage from '@/app/profiles/page';
import { stubBackend } from './helpers/fake-backend';
import type { ProfileStatus } from '@/types';

// The create form's Radix Switch measures itself; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe () {}
  unobserve () {}
  disconnect () {}
} as unknown as typeof ResizeObserver;

vi.mock('next/navigation', () => ({
  useParams:       () => ({}),
  useRouter:       () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname:     () => '/profiles',
  useSearchParams: () => new URLSearchParams(),
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
    backup_dir:            null,
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

function wrapper ({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <SidebarControlsProvider value={{ isSidebarCollapsed: false, toggleSidebar: () => {} }}>
          {children}
        </SidebarControlsProvider>
      </I18nProvider>
    </QueryClientProvider>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ProfileCard', () => {
  function renderCard (p: ProfileStatus, onToggle = vi.fn(), onDelete = vi.fn()) {
    render(
      <ProfileCard profile={p} onToggle={onToggle} onDelete={onDelete} onSync={vi.fn()} onSyncNow={vi.fn()} />,
      { wrapper }
    );
    return { onToggle, onDelete };
  }

  it('the toggle calls onToggle with the slug and the new enabled state', async () => {
    const user = userEvent.setup();
    const { onToggle } = renderCard(profile());

    const toggle = screen.getByRole('switch', { name: 'Enable or disable profile Docs' });
    expect(toggle).toBeChecked();
    await user.click(toggle);

    expect(onToggle).toHaveBeenCalledTimes(1);
    expect(onToggle).toHaveBeenCalledWith('docs', false);
  });

  it('the toggle of a disabled profile enables it', async () => {
    const user = userEvent.setup();
    const { onToggle } = renderCard(profile({ enabled: false }));

    const toggle = screen.getByRole('switch', { name: 'Enable or disable profile Docs' });
    expect(toggle).not.toBeChecked();
    await user.click(toggle);

    expect(onToggle).toHaveBeenCalledWith('docs', true);
  });

  it('the delete button calls onDelete with the slug', async () => {
    const user = userEvent.setup();
    const { onDelete, onToggle } = renderCard(profile());

    await user.click(screen.getByRole('button', { name: 'Delete profile Docs' }));
    expect(onDelete).toHaveBeenCalledWith('docs');
    expect(onToggle).not.toHaveBeenCalled();
  });
});

describe('ProfilesPage', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/profiles');
  });

  it('renders a card for each profile', async () => {
    stubBackend({
      'GET /profiles': [
        profile(),
        profile({ id: 2, slug: 'work', name: 'Work', local_dir: '/data/work', remote_dir: 'nas:work' }),
      ],
    });
    render(<ProfilesPage />, { wrapper });

    expect(await screen.findByRole('switch', { name: 'Enable or disable profile Docs' })).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Enable or disable profile Work' })).toBeInTheDocument();
    expect(screen.getByText('/data/work')).toBeInTheDocument();
    expect(screen.getByText('nas:work')).toBeInTheDocument();
    expect(screen.queryByText('No sync profiles configured yet')).not.toBeInTheDocument();
  });

  it('shows the empty state when there are no profiles', async () => {
    stubBackend({ 'GET /profiles': [] });
    const user = userEvent.setup();
    render(<ProfilesPage />, { wrapper });

    expect(await screen.findByText('No sync profiles configured yet')).toBeInTheDocument();
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Create your first profile' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getByLabelText('Profile name')).toBeInTheDocument();
  });

  it('toggling a card disables the profile through the API', async () => {
    const { requests } = stubBackend({
      'GET /profiles':               [profile()],
      'POST /profiles/docs/disable': profile({ enabled: false }),
    });
    const user = userEvent.setup();
    render(<ProfilesPage />, { wrapper });

    await user.click(await screen.findByRole('switch', { name: 'Enable or disable profile Docs' }));
    await waitFor(() => expect(requests).toContain('POST /profiles/docs/disable'));
  });

  describe('delete confirmation', () => {
    const routes = {
      'GET /profiles':         [profile(), profile({ id: 2, slug: 'work', name: 'Work' })],
      'DELETE /profiles/work': null,
    };

    it('asks before deleting and names the profile', async () => {
      const { requests } = stubBackend(routes);
      const user = userEvent.setup();
      render(<ProfilesPage />, { wrapper });

      await user.click(await screen.findByRole('button', { name: 'Delete profile Work' }));

      const dialog = await screen.findByRole('dialog');
      // Named, and clear that no files go with it.
      expect(within(dialog).getByText('Delete the profile "Work"?')).toBeInTheDocument();
      expect(within(dialog).getByText(/No files are deleted/)).toBeInTheDocument();
      expect(requests.some((r) => r.startsWith('DELETE'))).toBe(false);
    });

    it('cancel closes the dialog and deletes nothing', async () => {
      const { requests } = stubBackend(routes);
      const user = userEvent.setup();
      render(<ProfilesPage />, { wrapper });

      await user.click(await screen.findByRole('button', { name: 'Delete profile Work' }));
      const dialog = await screen.findByRole('dialog');
      await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      expect(requests.some((r) => r.startsWith('DELETE'))).toBe(false);
    });

    it('confirm deletes the profile with confirm=true and closes the dialog', async () => {
      const { requests } = stubBackend(routes);
      const user = userEvent.setup();
      render(<ProfilesPage />, { wrapper });

      await user.click(await screen.findByRole('button', { name: 'Delete profile Work' }));
      const dialog = await screen.findByRole('dialog');
      await user.click(within(dialog).getByRole('button', { name: 'Delete profile' }));

      await waitFor(() => expect(requests).toContain('DELETE /profiles/work?confirm=true'));
      expect(requests.filter((r) => r.startsWith('DELETE'))).toEqual(['DELETE /profiles/work?confirm=true']);
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    });
  });
});
