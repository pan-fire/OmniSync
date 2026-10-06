import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider, type Locale } from '@/i18n';
import en from '@/i18n/locales/en.json';
import fa from '@/i18n/locales/fa.json';
import { PendingChangesBanner } from '@/components/sync/pending-changes-banner';
import type { ProfileSummary, SyncCheckResult, SyncPreview, SyncPreviewCounts } from '@/types';
import { stubBackend } from '../helpers/fake-backend';

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function profile (slug: string, name: string, pending: number): ProfileSummary {
  return {
    slug,
    name,
    state:            'idle',
    last_sync:        null,
    pending_changes:  pending,
    intervals_paused: false,
    last_error:       null,
    resync_required:  false,
  };
}

const NONE: SyncPreviewCounts = { deletes: 0, replaces: 0, creates: 0, exceeds_max_delete: false };

const PREVIEW: SyncPreview = {
  push:       NONE,
  pull:       NONE,
  excluded:   0,
  max_delete: 50,
  error:      null,
  sync_mode:  'mirror',
  two_way:    null,
};

function check (overrides: Partial<SyncCheckResult> = {}): SyncCheckResult {
  return { has_changes: true, local_only: [], remote_only: [], differ: [], error: null, ...overrides };
}

function renderBanner (profiles: ProfileSummary[], locale: Locale = 'en') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  function Wrapper ({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}><I18nProvider initialLocale={locale}>{children}</I18nProvider></QueryClientProvider>;
  }
  return render(<PendingChangesBanner profiles={profiles} />, { wrapper: Wrapper });
}

/** Backend with previews and a start that refuses (so no job is followed). */
function backend (extra: Record<string, unknown> = {}) {
  const starts: { slug: string; body: unknown }[] = [];
  const startRoute = (slug: string) => (_: URL, init?: globalThis.RequestInit) => {
    starts.push({ slug, body: JSON.parse(String(init?.body)) });
    return { job_id: 1, state: 'error', error: 'stub' };
  };
  const stub = stubBackend({
    'POST /profiles/docs/sync/preview':   PREVIEW,
    'POST /profiles/photos/sync/preview': PREVIEW,
    'POST /profiles/docs/sync/start':     startRoute('docs'),
    'POST /profiles/photos/sync/start':   startRoute('photos'),
    ...extra,
  });
  return { ...stub, starts };
}

const checkRequests = (requests: string[]) => requests.filter((r) => r.endsWith('/sync/check'));

describe('PendingChangesBanner', () => {
  it('renders nothing when no profile has pending changes', () => {
    backend();
    const { container } = renderBanner([profile('docs', 'Docs', 0)]);
    expect(container).toBeEmptyDOMElement();
  });

  it('adds up the pending changes of all profiles and can be dismissed', async () => {
    backend();
    const user = userEvent.setup();
    renderBanner([profile('docs', 'Docs', 2), profile('photos', 'Photos', 3), profile('idle', 'Idle', 0)]);
    expect(screen.getByText('5 unsynced changes detected')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: en.syncCheck.dismiss }));
    expect(screen.queryByText('5 unsynced changes detected')).toBeNull();
  });

  // A bulk push overwrites the remote: nothing may start before the user confirms.
  it('push all only starts the syncs after the confirmation, with force', async () => {
    const { starts } = backend();
    const user = userEvent.setup();
    renderBanner([profile('docs', 'Docs', 1), profile('photos', 'Photos', 1), profile('idle', 'Idle', 0)]);

    await user.click(screen.getByRole('button', { name: en.syncCheck.pushAll }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Push 2 profiles?')).toBeInTheDocument();
    expect(within(dialog).getByText('Docs')).toBeInTheDocument();
    expect(within(dialog).queryByText('Idle')).toBeNull();
    expect(starts).toEqual([]);

    const confirm = within(dialog).getByRole('button', { name: en.syncConfirm.confirmPush });
    await waitFor(() => expect(confirm).toBeEnabled());
    await user.click(confirm);
    await waitFor(() => expect(starts).toHaveLength(2));
    expect(starts).toEqual(expect.arrayContaining([
      { slug: 'docs', body: { direction: 'push', force: true } },
      { slug: 'photos', body: { direction: 'push', force: true } },
    ]));
  });

  it('cancelling pull all starts nothing', async () => {
    const { starts, requests } = backend();
    const user = userEvent.setup();
    renderBanner([profile('docs', 'Docs', 1)]);

    await user.click(screen.getByRole('button', { name: en.syncCheck.pullAll }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Pull Docs?')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: en.common.cancel }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(starts).toEqual([]);
    expect(requests.some((r) => r.includes('/sync/start'))).toBe(false);
  });

  it('checks nothing until the details are expanded, then lists the files per category', async () => {
    const many = Array.from({ length: 12 }, (_, i) => `/data/docs/file-${i}.txt`);
    const { requests } = backend({
      'POST /profiles/docs/sync/check': check({
        local_only:  many,
        remote_only: ['/data/docs/remote.txt'],
        differ:      ['/data/docs/changed.txt'],
        error:       'rclone reported 1 error',
      }),
    });
    const user = userEvent.setup();
    renderBanner([profile('docs', 'Docs', 14)]);
    expect(checkRequests(requests)).toEqual([]);

    await user.click(screen.getByRole('button', { name: en.syncCheck.details }));
    expect(await screen.findByText('12 local only')).toBeInTheDocument();
    expect(screen.getByText('1 remote only')).toBeInTheDocument();
    expect(screen.getByText('1 modified')).toBeInTheDocument();
    // Only the first ten files of a category are listed.
    expect(screen.getByText('/data/docs/file-9.txt')).toBeInTheDocument();
    expect(screen.queryByText('/data/docs/file-10.txt')).toBeNull();
    expect(screen.getByText('…2 more')).toBeInTheDocument();
    expect(screen.getByText('rclone reported 1 error')).toBeInTheDocument();
    // A single profile is not labelled with its name.
    expect(screen.queryByText('Docs')).toBeNull();

    // Collapsing hides the list again.
    await user.click(screen.getByRole('button', { name: en.syncCheck.details }));
    expect(screen.queryByText('12 local only')).toBeNull();
  });

  it('shows a loading line while checking and the error when a check fails', async () => {
    const { fetchMock } = backend({
      'POST /profiles/photos/sync/check': check({ differ: ['/data/photos/a.jpg'] }),
    });
    const user = userEvent.setup();
    renderBanner([profile('docs', 'Docs', 1), profile('photos', 'Photos', 1)]);

    // Hold the first check (docs) until the test releases it.
    let release: () => void = () => {};
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementationOnce((input, init) =>
      new Promise((resolve) => { release = () => resolve(original(input, init)); }));

    await user.click(screen.getByRole('button', { name: en.syncCheck.details }));
    // Several profiles: each list is labelled with the profile name.
    expect(screen.getByText('Docs')).toBeInTheDocument();
    expect(screen.getByText('Photos')).toBeInTheDocument();
    expect(await screen.findByText(en.common.loading)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: en.syncCheck.recheck })).toBeDisabled();
    expect(await screen.findByText('/data/photos/a.jpg')).toBeInTheDocument();

    release();
    // docs has no check route: the backend answers 404 and the banner shows why.
    expect(await screen.findByText(/no route for POST \/profiles\/docs\/sync\/check/)).toBeInTheDocument();
    expect(screen.queryByText(en.common.loading)).toBeNull();
  });

  it('re-check asks the backend again for the expanded details', async () => {
    let calls = 0;
    const { requests } = backend({
      'POST /profiles/docs/sync/check': () => {
        calls += 1;
        return check({ differ: [`/data/docs/run-${calls}.txt`] });
      },
    });
    const user = userEvent.setup();
    renderBanner([profile('docs', 'Docs', 1)]);

    await user.click(screen.getByRole('button', { name: en.syncCheck.details }));
    expect(await screen.findByText('/data/docs/run-1.txt')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: en.syncCheck.recheck }));
    expect(await screen.findByText('/data/docs/run-2.txt')).toBeInTheDocument();
    expect(checkRequests(requests)).toHaveLength(2);
  });

  it('speaks Persian in a right-to-left layout', async () => {
    backend({ 'POST /profiles/docs/sync/check': check({ local_only: ['/data/docs/a.txt'] }) });
    const user = userEvent.setup();
    renderBanner([profile('docs', 'Docs', 1)], 'fa');
    expect(document.documentElement).toHaveAttribute('dir', 'rtl');
    expect(screen.getByRole('button', { name: fa.syncCheck.dismiss })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: fa.syncCheck.details }));
    // Counts are wrapped in Unicode isolates inside a Persian sentence.
    expect(await screen.findByText((text) => text.includes('فقط محلی'))).toBeInTheDocument();
    expect(screen.getByText('/data/docs/a.txt')).toBeInTheDocument();
  });
});
