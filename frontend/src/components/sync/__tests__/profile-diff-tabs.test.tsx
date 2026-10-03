import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render as rtlRender, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { EMPTY_DIFF, stubBackend } from '@/__tests__/helpers/fake-backend';
import { ProfileDiffTabs } from '../profile-diff-tabs';
import { ShowDiffButton } from '../show-diff-button';
import type { ProfileSummary } from '@/types';

vi.mock('@/i18n', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

// The panels run their real data hooks against a stubbed backend.
function render (ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

let backend: ReturnType<typeof stubBackend>;

beforeEach(() => {
  const routes: Record<string, unknown> = {};
  for (const slug of ['default', 'work', 'personal', 'backup', 'solo']) {
    routes[`POST /profiles/${slug}/diff`] = EMPTY_DIFF;
    routes[`GET /profiles/${slug}/manual-flags`] = { flags: [] };
  }
  backend = stubBackend(routes);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function makeProfile (overrides: Partial<ProfileSummary> = {}): ProfileSummary {
  return {
    slug:             'default',
    name:             'Default',
    state:            'idle',
    last_sync:        null,
    pending_changes:  5,
    intervals_paused: false,
    last_error:       null,
    resync_required:  false,
    ...overrides,
  };
}

describe('ProfileDiffTabs', () => {
  it('says so when no profile has pending changes', () => {
    const profiles = [makeProfile({ pending_changes: 0 })];
    render(<ProfileDiffTabs profiles={profiles} />);
    expect(screen.getByTestId('profile-diff-empty')).toHaveTextContent('granular.nothingPending');
    expect(screen.queryByTestId('profile-diff-tabs')).not.toBeInTheDocument();
    expect(backend.requests).toEqual([]);
  });

  it('renders one tab per profile with pending changes', () => {
    const profiles = [
      makeProfile({ slug: 'work', name: 'Work', pending_changes: 3 }),
      makeProfile({ slug: 'personal', name: 'Personal', pending_changes: 0 }),
      makeProfile({ slug: 'backup', name: 'Backup', pending_changes: 7 }),
    ];
    render(<ProfileDiffTabs profiles={profiles} />);
    expect(screen.getByTestId('profile-diff-tabs')).toBeInTheDocument();
    expect(screen.getByText('Work')).toBeInTheDocument();
    expect(screen.getByText('Backup')).toBeInTheDocument();
    expect(screen.queryByText('Personal')).not.toBeInTheDocument();
  });

  it('shows badge with pending count', () => {
    const profiles = [makeProfile({ pending_changes: 42 })];
    render(<ProfileDiffTabs profiles={profiles} />);
    expect(screen.getByText('42')).toBeInTheDocument();
  });

  it('renders single tab for single profile with pending changes', () => {
    const profiles = [makeProfile({ slug: 'solo', name: 'Solo', pending_changes: 1 })];
    render(<ProfileDiffTabs profiles={profiles} />);
    expect(screen.getByText('Solo')).toBeInTheDocument();
  });

  it('the open tab loads its profile\'s diff through the real hook', async () => {
    const profiles = [
      makeProfile({ slug: 'work', name: 'Work', pending_changes: 3 }),
      makeProfile({ slug: 'backup', name: 'Backup', pending_changes: 7 }),
    ];
    render(<ProfileDiffTabs profiles={profiles} />);
    await waitFor(() => expect(backend.requests).toContain('POST /profiles/work/diff?offset=0&limit=100'));
    expect(backend.requests.some((r) => r.startsWith('POST /profiles/backup/diff'))).toBe(false);
  });
});

describe('ShowDiffButton', () => {
  function renderButton (shown: boolean) {
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const onShow = vi.fn();
    const onHide = vi.fn();
    rtlRender(
      <QueryClientProvider client={client}>
        <ShowDiffButton shown={shown} disabled={false} onShow={onShow} onHide={onHide} />
      </QueryClientProvider>
    );
    return { invalidate, onShow, onHide };
  }

  it('opens the diff on the first click', () => {
    const { invalidate, onShow } = renderButton(false);
    fireEvent.click(screen.getByText('granular.showDiff'));
    expect(onShow).toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
    expect(screen.queryByText('granular.hideDiff')).not.toBeInTheDocument();
  });

  it('refreshes the open diff instead of hiding it', () => {
    const { invalidate, onShow, onHide } = renderButton(true);
    fireEvent.click(screen.getByText('granular.refreshDiff'));
    expect(onShow).not.toHaveBeenCalled();
    expect(onHide).not.toHaveBeenCalled();
    const predicate = (invalidate.mock.calls[0][0] as unknown as { predicate: (q: { queryKey: unknown[] }) => boolean }).predicate;
    expect(predicate({ queryKey: ['profiles', 'work', 'diff'] })).toBe(true);
    expect(predicate({ queryKey: ['profiles', 'work'] })).toBe(false);
    fireEvent.click(screen.getByText('granular.hideDiff'));
    expect(onHide).toHaveBeenCalled();
  });
});
