import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import { ProfileDiffPanel } from '@/components/sync/profile-diff-panel';
import type { FileDiff, SelectiveSyncItem, SelectiveSyncResponse } from '@/types';

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

const reset = vi.fn();
const loadMore = vi.fn();
let diffState: {
  allFiles:      FileDiff[];
  hasMore:       boolean;
  isLoading:     boolean;
  isLoadingMore: boolean;
  error:         string | null;
};
vi.mock('@/hooks/use-profile-paginated-diff', () => ({
  useProfilePaginatedDiff: () => ({ ...diffState, summary: null, loadMore, reset, removeFiles: vi.fn() }),
}));

let outcome: { ok: SelectiveSyncResponse } | { err: Error };
const selectiveMutate = vi.fn((_items: SelectiveSyncItem[], opts: { onSuccess: (r: SelectiveSyncResponse) => void; onError: (e: Error) => void }) => {
  if ('ok' in outcome) opts.onSuccess(outcome.ok);
  else opts.onError(outcome.err);
});
vi.mock('@/hooks/use-profile-sync', () => ({
  useProfileSelectiveSync: () => ({ isPending: false, mutate: selectiveMutate }),
}));

let flags: string[] = [];
const clearMutate = vi.fn();
let clearPending = false;
vi.mock('@/hooks/use-manual-flags', () => ({
  useProfileManualFlags:     () => ({ data: { flags } }),
  useClearProfileManualFlag: () => ({ mutate: clearMutate, isPending: clearPending }),
}));

vi.mock('@/components/sync/file-browser', () => ({
  FileBrowser: ({ onSync }: { onSync: (items: SelectiveSyncItem[]) => void }) => (
    <div>
      <button onClick={() => onSync([])}>sync-nothing</button>
      <button onClick={() => onSync([{ path: 'a.txt', action: 'skip' }, { path: 'b.txt', action: 'skip' }])}>skip-two</button>
    </div>
  ),
}));

const FILE: FileDiff = {
  path:            'a.txt',
  category:        'local_only',
  local_size:      1,
  remote_size:     null,
  local_mod_time:  null,
  remote_mod_time: null,
  is_conflict:     false,
  manual_flag:     false,
};

function renderPanel (extra?: ReactNode) {
  return render(<I18nProvider><ProfileDiffPanel slug="docs" emptyExtra={extra} /></I18nProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  diffState = { allFiles: [FILE], hasMore: false, isLoading: false, isLoadingMore: false, error: null };
  flags = [];
  clearPending = false;
  outcome = { ok: { job_id: 1, status: 'completed', total: 2, succeeded: 2, failed: 0, errors: [] } };
});

describe('ProfileDiffPanel states', () => {
  it('shows a loading status first', () => {
    diffState.isLoading = true;
    renderPanel();
    expect(screen.getByRole('status')).toHaveTextContent('Loading file differences…');
  });

  it('shows the empty message with the extra content', () => {
    diffState.allFiles = [];
    renderPanel(<p>Nothing to do</p>);
    expect(screen.getByText('No differences to resolve')).toBeInTheDocument();
    expect(screen.getByText('Nothing to do')).toBeInTheDocument();
  });

  // A failed diff must not look like "no differences".
  it('shows the error instead of the empty message, with a retry', async () => {
    const user = userEvent.setup();
    diffState.allFiles = [];
    diffState.error = 'rclone: remote not found';
    renderPanel();
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Could not compute the differences');
    expect(alert).toHaveTextContent('rclone: remote not found');
    expect(screen.queryByText('No differences to resolve')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it('offers Load more while pages remain and shows when it is loading', async () => {
    const user = userEvent.setup();
    diffState.hasMore = true;
    const { rerender } = renderPanel();
    await user.click(screen.getByRole('button', { name: 'Load more' }));
    expect(loadMore).toHaveBeenCalledTimes(1);

    diffState.isLoadingMore = true;
    rerender(<I18nProvider><ProfileDiffPanel slug="docs" /></I18nProvider>);
    expect(screen.getByRole('button', { name: 'Loading...' })).toBeDisabled();
  });

  it('lists manually flagged files with an Unmark button each', async () => {
    const user = userEvent.setup();
    diffState.allFiles = [];
    flags = ['/data/docs/x.txt', '/data/docs/y.txt'];
    renderPanel();
    expect(screen.getByText('2 files are excluded from sync (manual)')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Unmark /data/docs/y.txt as manual' }));
    expect(clearMutate).toHaveBeenCalledWith('/data/docs/y.txt');
  });

  it('disables Unmark while an unmark is running', () => {
    flags = ['/data/docs/x.txt'];
    clearPending = true;
    renderPanel();
    expect(screen.getByRole('button', { name: 'Unmark /data/docs/x.txt as manual' })).toBeDisabled();
  });
});

describe('ProfileDiffPanel selective sync', () => {
  it('sends nothing for an empty action', async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole('button', { name: 'sync-nothing' }));
    expect(selectiveMutate).not.toHaveBeenCalled();
  });

  it('confirms a batch of the same action with its count', async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole('button', { name: 'skip-two' }));
    expect(toast.success).toHaveBeenCalledTimes(1);
    expect(toast.success).toHaveBeenCalledWith('Skipped 2 files');
    expect(selectiveMutate.mock.calls[0][0]).toEqual([{ path: 'a.txt', action: 'skip' }, { path: 'b.txt', action: 'skip' }]);
  });

  it('falls back to a generic error when the request fails without a message', async () => {
    const user = userEvent.setup();
    outcome = { err: new Error('') };
    renderPanel();
    await user.click(screen.getByRole('button', { name: 'skip-two' }));
    expect(toast.error).toHaveBeenCalledWith('Skip failed: Something went wrong');
  });

  it('falls back to a generic error when every file fails without a reason', async () => {
    const user = userEvent.setup();
    outcome = { ok: { job_id: 1, status: 'failed', total: 2, succeeded: 0, failed: 2, errors: [] } };
    renderPanel();
    await user.click(screen.getByRole('button', { name: 'skip-two' }));
    expect(toast.error).toHaveBeenCalledWith('Skip failed: Something went wrong', { description: undefined });
    expect(toast.success).not.toHaveBeenCalled();
  });
});
