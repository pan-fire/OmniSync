import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import fc from 'fast-check';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider } from '@/i18n';
import { ConflictList } from '@/components/conflicts/conflict-list';
import ConflictsPage from '@/app/conflicts/page';
import { SidebarControlsProvider } from '@/components/layout/sidebar-controls';
import type { Conflict } from '@/types';

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

// --- Arbitraries ---

function uniqueUnresolvedConflictsArb (min: number, max: number): fc.Arbitrary<Conflict[]> {
  return fc
    .uniqueArray(
      fc.record({
        job_id:         fc.oneof(fc.constant(null), fc.integer({ min: 1, max: 10000 })),
        file_path:      fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9/_.-]{0,49}$/),
        local_modified: fc.oneof(
          fc.constant(null),
          fc.integer({ min: 1577836800000, max: 1893456000000 }).map((ms) => new Date(ms).toISOString())
        ),
        remote_modified: fc.oneof(
          fc.constant(null),
          fc.integer({ min: 1577836800000, max: 1893456000000 }).map((ms) => new Date(ms).toISOString())
        ),
      }),
      { minLength: min, maxLength: max, selector: (c) => c.file_path }
    )
    .map((items) =>
      items.map((item, i) => ({
        ...item,
        id:             i + 1,
        resolved:       false as boolean,
        resolution:     null,
        profile_slug:   'docs',
        profile_name:   'Docs',
        local_kept_as:  null,
        remote_kept_as: null,
      }))
    );
}

const ACTION_LABELS = ['Keep Local', 'Keep Remote', 'Keep Both', 'Dismiss'];

describe('Conflict list rendering completeness', () => {
  it("for any non-empty unresolved Conflict array, the rendered list contains each conflict's file_path and its four actions", () => {
    fc.assert(
      fc.property(uniqueUnresolvedConflictsArb(1, 5), (conflicts: Conflict[]) => {
        const { unmount, container } = render(
          <I18nProvider>
            <ConflictList conflicts={conflicts} onResolve={vi.fn()} />
          </I18nProvider>
        );

        const view = within(container);

        for (const conflict of conflicts) {
          expect(view.getAllByText(conflict.file_path).length).toBeGreaterThanOrEqual(1);
          const group = view.getByRole('group', { name: `Actions for ${conflict.file_path}` });
          for (const label of ACTION_LABELS) {
            expect(within(group).getByRole('button', { name: label })).toBeInTheDocument();
          }
        }

        unmount();
      }),
      { numRuns: 30 }
    );
  }, 30000);
});

const CONFLICT: Conflict = {
  id:              7,
  job_id:          null,
  file_path:       'notes/plan.md',
  local_modified:  '2026-09-27T08:30:00Z',
  remote_modified: '2026-09-27T09:45:30Z',
  resolved:        false,
  resolution:      null,
  profile_slug:    'docs',
  profile_name:    'Docs',
  local_kept_as:   null,
  remote_kept_as:  null,
};

function renderList (onResolve = vi.fn()) {
  render(<I18nProvider><ConflictList conflicts={[CONFLICT]} onResolve={onResolve} /></I18nProvider>);
  return onResolve;
}

describe('ConflictList actions', () => {
  it.each([
    ['Keep Local', 'keep_local', 'Keep the local version of notes/plan.md?', /remote version is moved to \.omnisync-trash/],
    ['Keep Remote', 'keep_remote', 'Keep the remote version of notes/plan.md?', /local version is moved to \.omnisync-trash/],
    ['Keep Both', 'keep_both', 'Keep both versions of notes/plan.md?', /\.conflict-<date> copy/],
    ['Dismiss', 'dismiss', 'Dismiss the conflict for notes/plan.md?', /No file is copied, moved or deleted/],
  ])('%s asks first, explains what happens to the files, then resolves', async (label, resolution, title, explain) => {
    const user = userEvent.setup();
    const onResolve = renderList();

    await user.click(screen.getByRole('button', { name: label }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(title)).toBeInTheDocument();
    expect(within(dialog).getByText(explain)).toBeInTheDocument();
    expect(within(dialog).getByText('Profile: Docs')).toBeInTheDocument();
    expect(onResolve).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: label }));
    expect(onResolve).toHaveBeenCalledExactlyOnceWith(7, resolution);
  });

  it('cancelling the confirmation resolves nothing', async () => {
    const user = userEvent.setup();
    const onResolve = renderList();

    await user.click(screen.getByRole('button', { name: 'Keep Remote' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(onResolve).not.toHaveBeenCalled();
  });

  it('names the profile with a link to it', () => {
    renderList();
    expect(screen.getByRole('link', { name: 'Docs' })).toHaveAttribute('href', '/profiles/docs');
  });
});

describe('ConflictList two-way conflicts', () => {
  const TWO_WAY: Conflict = {
    ...CONFLICT,
    id:             9,
    file_path:      'report.txt',
    local_kept_as:  'report.local-conflict1.txt',
    remote_kept_as: 'report.txt',
  };

  it('says both versions were kept and under which names', () => {
    render(<I18nProvider><ConflictList conflicts={[TWO_WAY]} onResolve={vi.fn()} /></I18nProvider>);
    expect(screen.getByTestId('two-way-kept')).toHaveTextContent(
      'Both versions were kept: local version as report.local-conflict1.txt, remote version as report.txt'
    );
  });

  it('offers Keep only local, Keep only remote and Keep both', () => {
    render(<I18nProvider><ConflictList conflicts={[TWO_WAY, CONFLICT]} onResolve={vi.fn()} /></I18nProvider>);
    const group = screen.getByRole('group', { name: 'Actions for report.txt' });
    expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual([
      'Keep only local', 'Keep only remote', 'Keep Both',
    ]);
    // A diff conflict keeps the usual wording and actions.
    const other = screen.getByRole('group', { name: 'Actions for notes/plan.md' });
    expect(within(other).getAllByRole('button').map((b) => b.textContent)).toEqual(ACTION_LABELS);
    expect(screen.getAllByTestId('two-way-kept')).toHaveLength(1);
  });

  it.each([
    ['Keep only local', 'keep_local', 'Keep only the local version of report.txt?', /The remote version is moved to \.omnisync-trash/],
    ['Keep only remote', 'keep_remote', 'Keep only the remote version of report.txt?', /The local version is moved to \.omnisync-trash/],
    ['Keep Both', 'keep_both', 'Keep both versions of report.txt?', /Both files stay on both sides/],
  ])('%s asks first and explains, then resolves', async (label, resolution, title, explain) => {
    const user = userEvent.setup();
    const onResolve = vi.fn();
    render(<I18nProvider><ConflictList conflicts={[TWO_WAY]} onResolve={onResolve} /></I18nProvider>);

    await user.click(screen.getByRole('button', { name: label }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(title)).toBeInTheDocument();
    expect(within(dialog).getByText(explain)).toBeInTheDocument();
    expect(onResolve).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: label }));
    expect(onResolve).toHaveBeenCalledExactlyOnceWith(9, resolution);
  });
});

describe('ConflictList states', () => {
  it('shows loading, not "no conflicts", while the list loads', () => {
    const { getByText, queryByText } = render(
      <I18nProvider><ConflictList conflicts={undefined} isLoading onResolve={vi.fn()} /></I18nProvider>
    );
    expect(getByText('Loading...')).toBeInTheDocument();
    expect(queryByText('No unresolved conflicts')).toBeNull();
  });

  it('shows an error with retry, not "no conflicts", when loading fails', () => {
    const onRetry = vi.fn();
    const { getByRole, queryByText } = render(
      <I18nProvider><ConflictList conflicts={undefined} isError onRetry={onRetry} onResolve={vi.fn()} /></I18nProvider>
    );
    expect(getByRole('alert')).toBeInTheDocument();
    expect(queryByText('No unresolved conflicts')).toBeNull();
    getByRole('button', { name: 'Retry' }).click();
    expect(onRetry).toHaveBeenCalled();
  });
});

describe('Conflicts page', () => {
  type Call = { url: string; method: string; body?: string };
  let calls: Call[];
  let resolveStatus: number;
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    calls = [];
    resolveStatus = 200;
    globalThis.fetch = vi.fn((input: string | URL | Request, init?: globalThis.RequestInit) => {
      const url = String(input);
      calls.push({ url, method: init?.method ?? 'GET', body: typeof init?.body === 'string' ? init.body : undefined });
      if (url === '/api/profiles') {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
      }
      if (url === '/api/conflicts') {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([CONFLICT]) });
      }
      if (url.endsWith('/resolve')) {
        const detail = { detail: 'The remote file changed since the conflict was found; not overwriting it unseen.' };
        return Promise.resolve(resolveStatus === 200
          ? { ok: true, status: 200, json: () => Promise.resolve({ ...CONFLICT, resolved: true, resolution: 'keep_local' }) }
          : { ok: false, status: resolveStatus, json: () => Promise.resolve(detail) });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    }) as unknown as typeof fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  function renderPage () {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <I18nProvider>
          <SidebarControlsProvider value={{ isSidebarCollapsed: false, toggleSidebar: () => {} }}>
            <ConflictsPage />
          </SidebarControlsProvider>
        </I18nProvider>
      </QueryClientProvider>
    );
  }

  const resolveCalls = () => calls.filter((c) => c.url.endsWith('/resolve'));

  it('a confirmed action posts the resolution', async () => {
    const { toast } = await import('sonner');
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole('button', { name: 'Keep Local' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Keep Local' }));

    await waitFor(() => expect(resolveCalls()).toHaveLength(1));
    expect(resolveCalls()[0]).toMatchObject({ url: '/api/conflicts/7/resolve', method: 'POST' });
    expect(JSON.parse(resolveCalls()[0].body ?? '{}')).toEqual({ resolution: 'keep_local' });
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Conflict on notes/plan.md resolved'));
  });

  it("shows the backend's reason when the action is refused", async () => {
    const { toast } = await import('sonner');
    resolveStatus = 409;
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole('button', { name: 'Keep Local' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Keep Local' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      'The remote file changed since the conflict was found; not overwriting it unseen.'
    ));
  });

  it('cancel sends nothing', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole('button', { name: 'Keep Both' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(resolveCalls()).toEqual([]);
  });
});
