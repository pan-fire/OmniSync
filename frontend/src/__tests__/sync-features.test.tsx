import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nProvider } from '@/i18n';
import {
  effectiveChoice, folderState, generateRules, parseRules, setChoice, type FolderChoices,
} from '@/lib/folder-rules';
import { SyncProgressView, formatDuration, progressPercent } from '@/components/sync/sync-progress';
import {
  initialSyncLimits, isValidBwlimit, syncLimitsErrors, syncLimitsPayload,
} from '@/components/profiles/sync-limits-fields';
import { FolderRulesDialog } from '@/components/profiles/folder-rules-dialog';
import { TrashPanel } from '@/components/profiles/trash-panel';
import { api } from '@/lib/api';
import type { SyncProgress } from '@/types';

function wrap (ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><I18nProvider>{ui}</I18nProvider></QueryClientProvider>);
}

describe('folder rules', () => {
  it('unchecking a folder excludes it; checking one inside it brings that back first', () => {
    let c: FolderChoices = {};
    c = setChoice(c, 'Big', 'off');
    expect(generateRules(c)).toEqual(['- /Big/**']);
    c = setChoice(c, 'Big/keep', 'on');
    expect(generateRules(c)).toEqual(['+ /Big/keep/**', '- /Big/**']);
    expect(folderState(c, 'Big')).toBe('mixed');
    expect(folderState(c, '')).toBe('mixed');
    expect(effectiveChoice(c, 'Big/keep/deep')).toBe('on');
    // Checking Big again drops the choices inside it.
    expect(generateRules(setChoice(c, 'Big', 'on'))).toEqual([]);
  });

  it('only some folders: the folder itself unchecked ends with "- **"', () => {
    let c = setChoice({}, '', 'off');
    c = setChoice(c, 'Docs', 'on');
    c = setChoice(c, 'Docs/sub', 'off');
    expect(generateRules(c)).toEqual(['- /Docs/sub/**', '+ /Docs/**', '- **']);
    expect(folderState(c, 'Other')).toBe('off');
  });

  it('escapes wildcard characters in folder names', () => {
    expect(generateRules(setChoice({}, 'a[1]*{x}', 'off'))).toEqual(['- /a\\[1\\]\\*\\{x\\}/**']);
  });

  it('reads its own rules back and keeps other rules', () => {
    const parsed = parseRules(['- *.tmp', '+ /Big/keep/**', '- /Big/**', '']);
    expect(parsed.other).toEqual(['- *.tmp']);
    expect(parsed.choices).toEqual({ 'Big/keep': 'on', Big: 'off' });
    expect(parseRules(['- /a\\[1\\]/**']).choices).toEqual({ 'a[1]': 'off' });
    expect(parseRules(['+ /Docs/**', '- **']).choices).toEqual({ Docs: 'on', '': 'off' });
  });

  it('refuses rules whose meaning would change when rewritten', () => {
    expect(parseRules(['- /Big/**', '+ /Big/keep/**']).choices).toBeNull(); // shallow first
    expect(parseRules(['- /*.git/**']).choices).toBeNull(); // a pattern, not a folder
    expect(parseRules(['- **', '+ /Docs/**']).choices).toBeNull(); // "- **" not last
  });
});

const PROGRESS: SyncProgress = {
  bytes:         50 * 1024 * 1024,
  total_bytes:   200 * 1024 * 1024,
  speed:         2 * 1024 * 1024,
  eta_seconds:   75,
  files_done:    3,
  files_total:   10,
  checks:        0,
  total_checks:  0,
  current_files: [{ name: 'Photos/a.jpg', size: 1024 * 1024, bytes: 512 * 1024, percentage: 50 }],
};

describe('sync progress', () => {
  it('shows the percentage, sizes, files, speed and time left', () => {
    wrap(<SyncProgressView progress={PROGRESS} />);
    const bar = screen.getByRole('progressbar');
    expect(bar).toHaveAttribute('aria-valuenow', '25');
    const text = screen.getByTestId('sync-progress').textContent ?? '';
    expect(text).toContain('25%');
    expect(text).toContain('3/10 files');
    expect(text).toContain('/s');
    expect(text).toContain('left');
    expect(screen.getByText('Photos/a.jpg')).toBeInTheDocument();
  });

  it('compact: one line, no file list; nothing without progress', () => {
    const { container } = wrap(<SyncProgressView progress={PROGRESS} compact />);
    expect(screen.queryByText('Photos/a.jpg')).toBeNull();
    expect(container.querySelector('[data-testid="sync-progress"]')).not.toBeNull();
    const empty = wrap(<SyncProgressView progress={null} />);
    expect(empty.container.querySelector('[data-testid="sync-progress"]')).toBeNull();
  });

  it('percent falls back to files, and is unknown without totals', () => {
    expect(progressPercent({ ...PROGRESS, total_bytes: 0 })).toBe(30);
    expect(progressPercent({ ...PROGRESS, total_bytes: 0, files_total: 0 })).toBeNull();
    expect(formatDuration(3725, 'en')).toMatch(/1\s?h 2\s?m/);
    expect(formatDuration(42, 'en')).toMatch(/42\s?s/);
  });
});

describe('bandwidth limit and sync window fields', () => {
  it('accepts what rclone accepts', () => {
    for (const v of ['', '10M', 'off', '512k', '10M:1M', '1.5M', '10MiB', '08:00,512k 19:00,10M 23:00,off', 'Mon-08:00,1M']) {
      expect(isValidBwlimit(v)).toBe(true);
    }
    for (const v of ['10x', '25:00,1M', '8:00,1M', '-1', '10kb', '10M 20M']) {
      expect(isValidBwlimit(v)).toBe(false);
    }
  });

  it('refuses the limit twice and an empty window', () => {
    const value = { ...initialSyncLimits(), bwlimit: '1M' };
    expect(syncLimitsErrors(value, '--transfers=4\n--bwlimit=2M').bwlimit).toBe('syncLimits.bwlimitTwice');
    expect(syncLimitsErrors(value, '--transfers=4')).toEqual({});
    expect(syncLimitsErrors({ ...value, windowOn: true, start: '22:00', end: '22:00' }, '').sync_window)
      .toBe('syncLimits.windowInvalid');
    expect(syncLimitsErrors({ ...value, windowOn: true, days: [] }, '').sync_window).toBe('syncLimits.windowNoDays');
  });

  it('sends null to clear, and the window only when switched on', () => {
    expect(syncLimitsPayload(initialSyncLimits())).toEqual({ bwlimit: null, sync_window: null });
    expect(syncLimitsPayload({ bwlimit: ' 08:00,1M   09:00,off ', windowOn: true, days: [6, 0], start: '22:00', end: '06:00' }))
      .toEqual({ bwlimit: '08:00,1M 09:00,off', sync_window: { days: [0, 6], start: '22:00', end: '06:00' } });
  });
});

describe('Choose folders dialog', () => {
  const originalFetch = globalThis.fetch;
  beforeEach(() => {
    globalThis.fetch = vi.fn(async (url: string) => {
      const path = decodeURIComponent(String(url).split('path=')[1] ?? '');
      const children: Record<string, string[]> = { '/sync': ['Big', 'Docs'], '/sync/Big': ['keep'] };
      const entries = (children[path] ?? []).map((name) => ({ name, path: `${path}/${name}` }));
      return { ok: true, status: 200, json: async () => ({ current: path, parent: null, entries }) } as Response;
    }) as unknown as typeof fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('turns checkboxes into rules and keeps other rules', async () => {
    const onApply = vi.fn();
    wrap(
      <FolderRulesDialog
        open
        onOpenChange={() => {}}
        localDir="/sync"
        rules={'- *.tmp'}
        syncMode="two_way"
        onApply={onApply}
      />
    );
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Big' }));
    expect(screen.getByTestId('folder-rules-preview').textContent).toBe('- *.tmp\n- /Big/**');
    fireEvent.click(screen.getByRole('button', { name: 'Show folders in Big' }));
    fireEvent.click(await screen.findByRole('checkbox', { name: 'keep' }));
    expect(screen.getByRole('checkbox', { name: 'Big' })).toHaveAttribute('aria-checked', 'mixed');
    expect(screen.getByText(/omnisync-check/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Use these rules' }));
    expect(onApply).toHaveBeenCalledWith('- *.tmp\n+ /Big/keep/**\n- /Big/**');
  });

  it('opens with the folders the current rules choose', async () => {
    wrap(
      <FolderRulesDialog open onOpenChange={() => {}} localDir="/sync" rules={'+ /Docs/**\n- **'} syncMode="mirror" onApply={() => {}} />
    );
    expect(await screen.findByRole('checkbox', { name: 'Docs' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('checkbox', { name: 'Big' })).toHaveAttribute('aria-checked', 'false');
    expect(screen.queryByTestId('folder-rules-replaced')).toBeNull();
  });
});

describe('Trash panel', () => {
  afterEach(() => vi.restoreAllMocks());

  it('lists the trash and asks before replacing a newer file', async () => {
    vi.spyOn(api, 'getProfileTrash').mockResolvedValue({
      side:        'local',
      total_files: 2,
      total_bytes: 3072,
      truncated:   false,
      entries:     [
        { id: 'T1/a.txt', folder: 'T1', path: 'a.txt', size: 1024, modified: null, trashed_at: '2026-10-01T10:00:00Z' },
        { id: 'T1/b.txt', folder: 'T1', path: 'b.txt', size: 2048, modified: null, trashed_at: '2026-10-01T10:00:00Z' },
      ],
    });
    const restore = vi.spyOn(api, 'restoreFromTrash')
      .mockResolvedValueOnce({ done: ['T1/b.txt'], failed: [{ id: 'T1/a.txt', code: 'target_newer', message: 'newer' }] })
      .mockResolvedValueOnce({ done: ['T1/a.txt'], failed: [] });
    wrap(<TrashPanel slug="docs" />);

    expect(await screen.findByText('a.txt')).toBeInTheDocument();
    expect(screen.getByTestId('trash-total').textContent).toContain('2 files');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all' }));
    fireEvent.click(screen.getByRole('button', { name: 'Restore 2 files' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('1 file is newer at its original place')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Replace' }));
    await waitFor(() => expect(restore).toHaveBeenLastCalledWith('docs', 'local', ['T1/a.txt'], true));
    expect(restore).toHaveBeenNthCalledWith(1, 'docs', 'local', ['T1/a.txt', 'T1/b.txt'], false);
  });

  it('deletes only after confirming, on the chosen side', async () => {
    const list = vi.spyOn(api, 'getProfileTrash').mockResolvedValue({
      side:        'remote',
      total_files: 1,
      total_bytes: 1,
      truncated:   true,
      entries:     [{ id: 'T1/x', folder: 'T1', path: 'x', size: 1, modified: null, trashed_at: null }],
    });
    const remove = vi.spyOn(api, 'deleteFromTrash').mockResolvedValue({ done: ['T1/x'], failed: [] });
    wrap(<TrashPanel slug="docs" />);
    fireEvent.click(screen.getByRole('button', { name: 'Remote folder' }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith('docs', 'remote'));
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select x' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete 1 file' }));
    expect(remove).not.toHaveBeenCalled();
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith('docs', 'remote', ['T1/x']));
    expect(screen.getByTestId('trash-total').textContent).toContain('newest 1');
  });
});

describe('API routes of the sync features', () => {
  const originalFetch = globalThis.fetch;
  let mockFetch: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    mockFetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve({}) });
    globalThis.fetch = mockFetch as unknown as typeof fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });
  const last = () => [mockFetch.mock.calls.at(-1)?.[0], mockFetch.mock.calls.at(-1)?.[1]?.method, mockFetch.mock.calls.at(-1)?.[1]?.body];

  it('match profiles.py', async () => {
    await api.pauseAllProfiles();
    expect(last().slice(0, 2)).toEqual(['/api/profiles/pause-all', 'POST']);
    await api.resumeAllProfiles();
    expect(last().slice(0, 2)).toEqual(['/api/profiles/resume-all', 'POST']);
    await api.pauseProfile('my docs');
    expect(last().slice(0, 2)).toEqual(['/api/profiles/my%20docs/sync/pause', 'POST']);
    await api.getProfileTrash('docs', 'remote');
    expect(last()[0]).toBe('/api/profiles/docs/trash?side=remote');
    await api.restoreFromTrash('docs', 'local', ['T/a'], true);
    expect(last()).toEqual(['/api/profiles/docs/trash/restore', 'POST', JSON.stringify({ side: 'local', ids: ['T/a'], overwrite: true })]);
    await api.deleteFromTrash('docs', 'local', ['T/a']);
    expect(last()).toEqual(['/api/profiles/docs/trash/delete', 'POST', JSON.stringify({ side: 'local', ids: ['T/a'] })]);
  });
});
