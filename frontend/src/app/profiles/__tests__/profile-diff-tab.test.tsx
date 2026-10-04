import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nProvider } from '@/i18n';
import { SidebarControlsProvider } from '@/components/layout/sidebar-controls';
import { EMPTY_DIFF, stubBackend } from '@/__tests__/helpers/fake-backend';

// Mock next/navigation
vi.mock('next/navigation', () => ({
  useParams:       () => ({ slug: 'test-profile' }),
  useRouter:       () => ({ push: vi.fn(), back: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams('tab=differences'),
}));

// The page runs its real data hooks against this stubbed backend.
const MOCK_PROFILE = {
  id:                    1,
  slug:                  'test-profile',
  name:                  'Test Profile',
  local_dir:             '/tmp/local',
  remote_dir:            'remote:path',
  debounce_seconds:      5,
  pull_interval_minutes: 5,
  rclone_filter:         [],
  rclone_args:           [],
  max_retries:           3,
  enabled:               true,
  created_at:            new Date().toISOString(),
  updated_at:            new Date().toISOString(),
  state:                 'idle',
  last_sync:             null,
  current_job_id:        null,
  files_processed:       0,
  errors:                0,
  pending_changes:       5,
  intervals_paused:      true,
  paused_at:             '2025-01-01T00:00:00Z',
  sync_mode:             'mirror',
  resync_required:       false,
};

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

function createWrapper () {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper ({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <I18nProvider>
          <SidebarControlsProvider value={{ isSidebarCollapsed: false, toggleSidebar: () => {} }}>
            {children}
          </SidebarControlsProvider>
        </I18nProvider>
      </QueryClientProvider>
    );
  };
}

let backend: ReturnType<typeof stubBackend>;

beforeEach(() => {
  backend = stubBackend({
    'GET /profiles/test-profile':              MOCK_PROFILE,
    'GET /profiles/test-profile/backups':      [],
    'GET /profiles/test-profile/manual-flags': { flags: [] },
    'POST /profiles/test-profile/diff':        EMPTY_DIFF,
    'GET /remotes':                            [],
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Profile detail Differences tab', () => {
  it('renders Differences tab trigger with pending badge', async () => {
    const { default: ProfileDetailPage } = await import('@/app/profiles/[slug]/page');
    render(<ProfileDetailPage />, { wrapper: createWrapper() });
    // Tab trigger should exist with pending count badge
    expect(await screen.findByText('Differences')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
  });

  it('shows a pulsing indicator with a text alternative when intervals are paused', async () => {
    const { default: ProfileDetailPage } = await import('@/app/profiles/[slug]/page');
    render(<ProfileDetailPage />, { wrapper: createWrapper() });
    await screen.findByText('Differences');
    const dot = screen.getByRole('img', { name: 'Automatic syncing paused' });
    expect(dot).toHaveClass('motion-safe:animate-pulse', 'bg-amber-500');
  });

  it('opens the Differences tab for ?tab=differences and loads the diff through the real hook', async () => {
    const { default: ProfileDetailPage } = await import('@/app/profiles/[slug]/page');
    render(<ProfileDetailPage />, { wrapper: createWrapper() });
    // The stored count (5) is stale: the diff the backend computes is empty.
    expect(await screen.findByText('No differences to resolve')).toBeInTheDocument();
    const tabTrigger = screen.getByText('Differences').closest('[role="tab"]');
    expect(tabTrigger).toHaveAttribute('data-state', 'active');
    expect(backend.requests.filter((r) => r.startsWith('POST /profiles/test-profile/diff'))).toEqual([
      'POST /profiles/test-profile/diff?offset=0&limit=100',
    ]);
  });
});
