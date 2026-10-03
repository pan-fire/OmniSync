import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { toast } from 'sonner';
import { ProfileDiffPanel, failedFileList } from '../profile-diff-panel';
import type { SelectiveSyncItem, SelectiveSyncResponse } from '@/types';

// t() echoes the key and its variables so the tests can see both.
const t = (key: string, vars?: Record<string, string | number>) =>
  vars ? `${key} ${JSON.stringify(vars)}` : key;

vi.mock('@/i18n', () => ({ useTranslation: () => ({ t }) }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

const removeFiles = vi.fn();
vi.mock('@/hooks/use-profile-paginated-diff', () => ({
  useProfilePaginatedDiff: () => ({
    allFiles: [
      { path: 'a.txt', category: 'local_only' },
      { path: 'b.txt', category: 'local_only' },
      { path: 'c.txt', category: 'remote_only' },
    ],
    summary:       null,
    hasMore:       false,
    isLoading:     false,
    isLoadingMore: false,
    error:         null,
    loadMore:      vi.fn(),
    reset:         vi.fn(),
    removeFiles,
  }),
}));

type Outcome = { ok: SelectiveSyncResponse } | { err: Error };
let outcome: Outcome;
vi.mock('@/hooks/use-profile-sync', () => ({
  useProfileSelectiveSync: () => ({
    isPending: false,
    mutate:    (_items: SelectiveSyncItem[], opts: { onSuccess: (r: SelectiveSyncResponse) => void; onError: (e: Error) => void }) => {
      if ('ok' in outcome) opts.onSuccess(outcome.ok);
      else opts.onError(outcome.err);
    },
  }),
}));

vi.mock('@/hooks/use-manual-flags', () => ({
  useProfileManualFlags:     () => ({ data: { flags: [] } }),
  useClearProfileManualFlag: () => ({ mutate: vi.fn(), isPending: false }),
}));

// The file browser only needs to hand actions to the panel.
let lastPending: Set<string> = new Set();
vi.mock('../file-browser', () => ({
  FileBrowser: ({ onSync, pendingPaths }: { onSync: (items: SelectiveSyncItem[]) => void; pendingPaths: Set<string> }) => {
    lastPending = pendingPaths;
    return (
      <div>
        <button onClick={() => onSync([{ path: 'a.txt', action: 'push' }])}>push-one</button>
        <button onClick={() => onSync([
          { path: 'a.txt', action: 'push' },
          { path: 'b.txt', action: 'push' },
          { path: 'c.txt', action: 'push' },
        ])}>push-three</button>
        <button onClick={() => onSync([
          { path: 'a.txt', action: 'push' },
          { path: 'c.txt', action: 'pull' },
        ])}>mixed</button>
      </div>
    );
  },
}));

function response (over: Partial<SelectiveSyncResponse>): SelectiveSyncResponse {
  return { job_id: 1, status: 'completed', total: 3, succeeded: 3, failed: 0, errors: [], ...over };
}

beforeEach(() => {
  vi.clearAllMocks();
  lastPending = new Set();
});

describe('ProfileDiffPanel selective sync results', () => {
  it('removes every row and names the file on single success', () => {
    outcome = { ok: response({ total: 1, succeeded: 1 }) };
    render(<ProfileDiffPanel slug="work" />);
    fireEvent.click(screen.getByText('push-one'));
    expect(removeFiles).toHaveBeenCalledWith(new Set(['a.txt']));
    expect(toast.success).toHaveBeenCalledWith('granular.toastPushed {"path":"a.txt"}');
    expect(lastPending.size).toBe(0);
  });

  it('keeps failed rows and names them in a warning on partial failure', () => {
    outcome = {
      ok: response({
        succeeded: 1,
        failed:    2,
        errors:    [
          { path: 'b.txt', error: 'Permission denied' },
          { path: 'c.txt', error: 'Permission denied' },
        ]
      })
    };
    render(<ProfileDiffPanel slug="work" />);
    fireEvent.click(screen.getByText('push-three'));

    expect(removeFiles).toHaveBeenCalledWith(new Set(['a.txt']));
    expect(lastPending.size).toBe(0);
    const [title, opts] = vi.mocked(toast.warning).mock.calls[0] as [string, { description: string }];
    expect(title).toContain('granular.toastPartialFail');
    expect(title).toContain('"action":"granular.actionNames.push"');
    expect(title).toContain('"succeeded":1');
    expect(title).toContain('"failed":2');
    expect(opts.description).toBe('b.txt, c.txt');
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('shows an error toast with the message when every file fails', () => {
    outcome = {
      ok: response({
        succeeded: 0,
        failed:    3,
        errors:    [
          { path: 'a.txt', error: 'Remote is unreachable' },
          { path: 'b.txt', error: 'Remote is unreachable' },
          { path: 'c.txt', error: 'Remote is unreachable' },
        ]
      })
    };
    render(<ProfileDiffPanel slug="work" />);
    fireEvent.click(screen.getByText('push-three'));

    expect(removeFiles).toHaveBeenCalledWith(new Set());
    const [title] = vi.mocked(toast.error).mock.calls[0] as [string];
    expect(title).toContain('granular.toastActionFailed');
    expect(title).toContain('Remote is unreachable');
    expect(toast.warning).not.toHaveBeenCalled();
  });

  it('removes nothing when failures are not attributed to files', () => {
    outcome = { ok: response({ succeeded: 2, failed: 1, errors: [] }) };
    render(<ProfileDiffPanel slug="work" />);
    fireEvent.click(screen.getByText('push-three'));
    expect(removeFiles).not.toHaveBeenCalled();
    expect(toast.warning).toHaveBeenCalled();
  });

  it('names the action in the error toast when the request fails', () => {
    outcome = { err: new Error('Profile not found') };
    render(<ProfileDiffPanel slug="work" />);
    fireEvent.click(screen.getByText('push-one'));
    expect(removeFiles).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledTimes(1);
    const [title] = vi.mocked(toast.error).mock.calls[0] as [string];
    expect(title).toContain('"action":"granular.actionNames.push"');
    expect(title).toContain('Profile not found');
    expect(lastPending.size).toBe(0);
  });

  it('confirms a mixed batch as done, not as started', () => {
    outcome = { ok: response({ total: 2, succeeded: 2 }) };
    render(<ProfileDiffPanel slug="work" />);
    fireEvent.click(screen.getByText('mixed'));
    const [title] = vi.mocked(toast.success).mock.calls[0] as [string];
    expect(title).toContain('granular.toastBatchMixed');
  });
});

describe('failedFileList', () => {
  it('lists up to three paths, then counts the rest', () => {
    const errors = ['1', '2', '3', '4', '5'].map((p) => ({ path: p, error: 'x' }));
    expect(failedFileList(errors, t)).toBe('granular.failedFilesMore {"files":"1, 2, 3","count":2}');
    expect(failedFileList(errors.slice(0, 2), t)).toBe('1, 2');
    expect(failedFileList([], t)).toBeUndefined();
  });
});
