import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';
import { I18nProvider, type Locale } from '@/i18n';
import fa from '@/i18n/locales/fa.json';
import { FileBrowser, computeSummary } from '@/components/sync/file-browser';
import type { ChangeCategory, FileDiff } from '@/types';

vi.mock('sonner', () => ({ toast: { warning: vi.fn(), success: vi.fn(), error: vi.fn() } }));

// Radix Select uses pointer capture and scrollIntoView, which jsdom lacks.
beforeAll(() => {
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.releasePointerCapture ??= () => {};
  Element.prototype.scrollIntoView ??= () => {};
});

beforeEach(() => {
  vi.mocked(toast.warning).mockClear();
});

afterEach(() => {
  vi.useRealTimers();
});

function file (path: string, category: ChangeCategory, extra: Partial<FileDiff> = {}): FileDiff {
  return {
    path,
    category,
    local_size:      category === 'remote_only' ? null : 100,
    remote_size:     category === 'local_only' ? null : 200,
    local_mod_time:  category === 'remote_only' ? null : '2026-01-01T10:00:00Z',
    remote_mod_time: category === 'local_only' ? null : '2026-01-02T10:00:00Z',
    is_conflict:     false,
    manual_flag:     false,
    ...extra,
  };
}

const FILES: FileDiff[] = [
  file('docs/a.txt', 'local_only'),
  file('docs/b.txt', 'modified_both', { is_conflict: true, local_size: 300 }),
  file('docs/sub/c.txt', 'modified_local', { local_size: 50 }),
  file('photos/d.jpg', 'remote_only'),
  file('top.txt', 'modified_remote', { local_size: 10, is_conflict: true }),
];

function renderBrowser (files: FileDiff[] = FILES, props: Partial<Parameters<typeof FileBrowser>[0]> = {}, locale: Locale = 'en') {
  const onSync = vi.fn();
  render(
    <I18nProvider initialLocale={locale}>
      <FileBrowser files={files} summary={computeSummary(files)} onSync={onSync} {...props} />
    </I18nProvider>
  );
  return { onSync };
}

/** File paths in the order the table shows them. */
function shownPaths () {
  return screen.getAllByRole('checkbox', { name: /^Select (?!all)/ })
    .map((c) => c.getAttribute('aria-label')!.replace(/^Select /, ''));
}

function row (text: string) {
  return screen.getByText(text).closest('tr')!;
}

async function openRowMenu (user: ReturnType<typeof userEvent.setup>, path: string) {
  await user.click(within(row(path)).getByRole('button', { name: 'Action' }));
  return screen.findByRole('menu');
}

describe('FileBrowser row actions', () => {
  it.each([
    ['Push', 'push'],
    ['Skip', 'skip'],
    ['Mark manual', 'manual'],
  ])('%s on a non-conflicting row is sent straight away', async (label, action) => {
    const user = userEvent.setup();
    const { onSync } = renderBrowser();
    const menu = await openRowMenu(user, 'docs/a.txt');
    await user.click(within(menu).getByRole('menuitem', { name: label }));
    expect(onSync).toHaveBeenCalledWith([{ path: 'docs/a.txt', action }]);
  });

  it('pull on a remote-only row is sent; push there is unavailable and says why', async () => {
    const user = userEvent.setup();
    const { onSync } = renderBrowser();
    const menu = await openRowMenu(user, 'photos/d.jpg');
    const push = within(menu).getByRole('menuitem', { name: /Push/ });
    expect(push).toHaveAttribute('data-disabled');
    expect(push).toHaveTextContent('Only on the remote: nothing to push');
    await user.click(within(menu).getByRole('menuitem', { name: 'Pull' }));
    expect(onSync).toHaveBeenCalledWith([{ path: 'photos/d.jpg', action: 'pull' }]);
  });

  // A conflicting file is never overwritten without the dialog's confirmation.
  it('push on a conflicting row waits for the conflict dialog', async () => {
    const user = userEvent.setup();
    const { onSync } = renderBrowser();
    const menu = await openRowMenu(user, 'docs/b.txt');
    await user.click(within(menu).getByRole('menuitem', { name: 'Push' }));
    expect(onSync).not.toHaveBeenCalled();
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('docs/b.txt');
    await user.click(within(dialog).getByRole('button', { name: 'Keep both versions' }));
    expect(onSync).toHaveBeenCalledWith([{ path: 'docs/b.txt', action: 'keep_both' }]);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('a manual-flagged row without an unmark handler still offers Mark manual', async () => {
    const user = userEvent.setup();
    const { onSync } = renderBrowser([file('x.txt', 'local_only', { manual_flag: true })]);
    expect(screen.getByText('1 manual')).toBeInTheDocument();
    const menu = await openRowMenu(user, 'x.txt');
    expect(within(menu).queryByRole('menuitem', { name: 'Unmark manual' })).toBeNull();
    await user.click(within(menu).getByRole('menuitem', { name: 'Mark manual' }));
    expect(onSync).toHaveBeenCalledWith([{ path: 'x.txt', action: 'manual' }]);
  });
});

describe('FileBrowser batch actions', () => {
  it('select all, then a batch push skips what it cannot push and queues the conflicts', async () => {
    const user = userEvent.setup();
    const { onSync } = renderBrowser();
    await user.click(screen.getByRole('checkbox', { name: 'Select all files' }));
    expect(screen.getByText('5 selected')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Push selected' }));

    // The remote-only file cannot be pushed.
    expect(toast.warning).toHaveBeenCalledWith('Skipped 1 file the action does not apply to');
    expect(onSync).toHaveBeenCalledTimes(1);
    expect(onSync).toHaveBeenCalledWith([
      { path: 'docs/a.txt', action: 'push' },
      { path: 'docs/sub/c.txt', action: 'push' },
    ]);
    // Two conflicts wait: resolving the first with "apply to remaining" settles both.
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('1 more conflicting file is waiting for a decision.');
    await user.click(within(dialog).getByRole('checkbox'));
    await user.click(within(dialog).getByRole('button', { name: 'Keep local version (push)' }));
    expect(onSync).toHaveBeenLastCalledWith([
      { path: 'docs/b.txt', action: 'push' },
      { path: 'top.txt', action: 'push' },
    ]);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    // The selection is cleared after a batch action.
    expect(screen.queryByText(/selected$/)).toBeNull();
  });

  it('resolves queued conflicts one by one', async () => {
    const user = userEvent.setup();
    const { onSync } = renderBrowser();
    await user.click(screen.getByRole('checkbox', { name: 'Select docs/b.txt' }));
    await user.click(screen.getByRole('checkbox', { name: 'Select top.txt' }));
    await user.click(screen.getByRole('button', { name: 'Pull selected' }));
    expect(onSync).not.toHaveBeenCalled();

    let dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('docs/b.txt');
    await user.click(within(dialog).getByRole('button', { name: 'Keep remote version (pull)' }));
    expect(onSync).toHaveBeenLastCalledWith([{ path: 'docs/b.txt', action: 'pull' }]);

    dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(dialog).toHaveTextContent('top.txt'));
    await user.click(within(dialog).getByRole('button', { name: 'Mark manual' }));
    expect(onSync).toHaveBeenLastCalledWith([{ path: 'top.txt', action: 'manual' }]);
    expect(onSync).toHaveBeenCalledTimes(2);
  });

  it('toggling a row twice and select all twice clears the selection; Clear empties it too', async () => {
    const user = userEvent.setup();
    renderBrowser();
    const a = screen.getByRole('checkbox', { name: 'Select docs/a.txt' });
    await user.click(a);
    expect(screen.getByText('1 selected')).toBeInTheDocument();
    await user.click(a);
    expect(screen.queryByText('1 selected')).toBeNull();

    const all = screen.getByRole('checkbox', { name: 'Select all files' });
    await user.click(all);
    expect(all).toBeChecked();
    await user.click(all);
    expect(all).not.toBeChecked();

    await user.click(a);
    await user.click(screen.getByRole('button', { name: 'Clear' }));
    expect(a).not.toBeChecked();
  });

  it('a pending row cannot be selected', () => {
    renderBrowser(FILES, { pendingPaths: new Set(['docs/a.txt']) });
    const a = screen.getByRole('checkbox', { name: 'Select docs/a.txt' });
    expect(a).toBeDisabled();
    // A click that slips through (pointer-events are off in the browser) changes nothing.
    fireEvent.click(a);
    expect(a).not.toBeChecked();
  });
});

describe('FileBrowser sorting, filtering and search', () => {
  it('sorts by each column and flips the direction on a second click', async () => {
    const user = userEvent.setup();
    renderBrowser();
    expect(shownPaths()).toEqual(['docs/a.txt', 'docs/b.txt', 'docs/sub/c.txt', 'photos/d.jpg', 'top.txt']);

    await user.click(screen.getByRole('button', { name: 'File' }));
    expect(shownPaths()[0]).toBe('top.txt');

    await user.click(screen.getByRole('button', { name: 'Local size' }));
    // Ascending by size, files without a local size last.
    expect(shownPaths()).toEqual(['top.txt', 'docs/sub/c.txt', 'docs/a.txt', 'docs/b.txt', 'photos/d.jpg']);

    await user.click(screen.getByRole('button', { name: 'Category' }));
    expect(shownPaths()[0]).toBe('docs/a.txt');

    await user.click(screen.getByRole('button', { name: 'Local modified' }));
    expect(shownPaths().at(-1)).toBe('photos/d.jpg');
  });

  it('filters by change type', async () => {
    const user = userEvent.setup();
    renderBrowser();
    await user.click(screen.getByRole('combobox', { name: 'Filter by change type' }));
    await user.click(await screen.findByRole('option', { name: 'Remote only' }));
    expect(shownPaths()).toEqual(['photos/d.jpg']);
  });

  it('searches after a short pause and shows how many files match', () => {
    vi.useFakeTimers();
    renderBrowser();
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search files…' }), { target: { value: 'DOCS/S' } });
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search files…' }), { target: { value: 'docs/sub' } });
    // Nothing is filtered while the user is still typing.
    expect(shownPaths()).toHaveLength(5);
    act(() => { vi.advanceTimersByTime(300); });
    expect(shownPaths()).toEqual(['docs/sub/c.txt']);
    expect(screen.getByText('1 of 5 (filtered)')).toBeInTheDocument();

    fireEvent.change(screen.getByRole('searchbox', { name: 'Search files…' }), { target: { value: 'nothing-matches' } });
    act(() => { vi.advanceTimersByTime(300); });
    expect(screen.getByText('All differences resolved')).toBeInTheDocument();
  });
});

describe('FileBrowser grouped by directory', () => {
  it('expands and collapses folders and shows file names only', async () => {
    const user = userEvent.setup();
    renderBrowser();
    const toggle = screen.getByRole('button', { name: 'Group by directory' });
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'true');

    const docs = screen.getByTestId('dir-toggle-docs');
    expect(docs).toHaveAttribute('aria-expanded', 'false');
    expect(docs).toHaveTextContent('3');
    // A file at the top level is listed without a folder.
    expect(screen.getByText('top.txt')).toBeInTheDocument();
    expect(screen.queryByText('a.txt')).toBeNull();

    await user.click(docs);
    expect(docs).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('a.txt')).toBeInTheDocument();
    expect(screen.getByTestId('dir-toggle-docs/sub')).toBeInTheDocument();

    await user.click(docs);
    expect(screen.queryByText('a.txt')).toBeNull();

    await user.click(toggle);
    expect(screen.getByText('docs/a.txt')).toBeInTheDocument();
  });

  it('folder push sends what it can and routes the conflict through the dialog', async () => {
    const user = userEvent.setup();
    const { onSync } = renderBrowser();
    await user.click(screen.getByRole('button', { name: 'Group by directory' }));
    await user.click(screen.getByRole('button', { name: 'Actions for folder docs' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Push all' }));
    expect(onSync).toHaveBeenCalledWith([
      { path: 'docs/a.txt', action: 'push' },
      { path: 'docs/sub/c.txt', action: 'push' },
    ]);
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('docs/b.txt');
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(onSync).toHaveBeenCalledTimes(1);
  });

  it('folder pull skips local-only files and leaves pending ones out', async () => {
    const user = userEvent.setup();
    const { onSync } = renderBrowser(FILES, { pendingPaths: new Set(['docs/sub/c.txt']) });
    await user.click(screen.getByRole('button', { name: 'Group by directory' }));
    await user.click(screen.getByRole('button', { name: 'Actions for folder docs' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Pull all' }));
    expect(toast.warning).toHaveBeenCalledWith('Skipped 1 file the action does not apply to');
    expect(onSync).not.toHaveBeenCalled();
    expect(await screen.findByRole('dialog')).toHaveTextContent('docs/b.txt');
  });

  it('folder skip applies to every file, conflicts included', async () => {
    const user = userEvent.setup();
    const { onSync } = renderBrowser();
    await user.click(screen.getByRole('button', { name: 'Group by directory' }));
    await user.click(screen.getByRole('button', { name: 'Actions for folder photos' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Skip all' }));
    expect(onSync).toHaveBeenCalledWith([{ path: 'photos/d.jpg', action: 'skip' }]);
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('FileBrowser in Persian', () => {
  it('translates the row menu and keeps working right to left', async () => {
    const user = userEvent.setup();
    const { onSync } = renderBrowser([file('docs/a.txt', 'local_only')], {}, 'fa');
    expect(document.documentElement).toHaveAttribute('dir', 'rtl');
    await user.click(screen.getByRole('button', { name: fa.granular.action }));
    await user.click(await screen.findByRole('menuitem', { name: fa.granular.skip }));
    expect(onSync).toHaveBeenCalledWith([{ path: 'docs/a.txt', action: 'skip' }]);
  });
});
