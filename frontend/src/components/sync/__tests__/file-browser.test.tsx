import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { FileBrowser, sortFiles, filterFiles, computeSummary, canApplyAction } from '../file-browser';
import type { ChangeCategory, FileDiff, DiffSummary } from '@/types';
vi.mock('@/i18n', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock('sonner', () => ({ toast: { warning: vi.fn() } }));

vi.mock('../batch-toolbar', () => ({
  BatchToolbar: () => <div data-testid="batch-toolbar" />,
}));

vi.mock('../conflict-dialog', () => ({
  ConflictDialog: () => null,
}));

function makeFile (path: string, category: ChangeCategory = 'local_only'): FileDiff {
  return {
    path,
    category,
    local_size:      100,
    remote_size:     null,
    local_mod_time:  '2024-01-01T00:00:00Z',
    remote_mod_time: null,
    is_conflict:     false,
    manual_flag:     false,
  };
}

function makeSummary (total: number): DiffSummary {
  return { local_only: total, remote_only: 0, modified_local: 0, modified_remote: 0, modified_both: 0, manual: 0, total };
}

describe('FileBrowser virtualization', () => {
  it('wraps table body in virtual scroll container', () => {
    const count = 50;
    const files = Array.from({ length: count }, (_, i) => makeFile(`file-${i}.txt`));
    const summary = makeSummary(count);

    render(
      <FileBrowser files={files} summary={summary} onSync={vi.fn()} />
    );

    const container = screen.getByTestId('virtual-scroll-container');
    expect(container).toBeInTheDocument();

    // Rows should be rendered inside the virtual container
    const rows = container.querySelectorAll('tr');
    expect(rows.length).toBeGreaterThan(0);
  });

  it('shows empty message when no files', () => {
    render(
      <FileBrowser files={[]} summary={makeSummary(0)} onSync={vi.fn()} />
    );
    expect(screen.getByText('granular.allResolved')).toBeInTheDocument();
  });
});

describe('sortFiles helper', () => {
  const files = [makeFile('b.txt'), makeFile('a.txt'), makeFile('c.txt')];

  it('sorts ascending by path', () => {
    const result = sortFiles(files, 'path', 'asc');
    expect(result.map(f => f.path)).toEqual(['a.txt', 'b.txt', 'c.txt']);
  });

  it('sorts descending by path', () => {
    const result = sortFiles(files, 'path', 'desc');
    expect(result.map(f => f.path)).toEqual(['c.txt', 'b.txt', 'a.txt']);
  });
});

describe('filterFiles helper', () => {
  const files = [
    makeFile('a.txt', 'local_only'),
    makeFile('b.txt', 'remote_only'),
  ];

  it('returns all for "all" filter', () => {
    expect(filterFiles(files, 'all')).toHaveLength(2);
  });

  it('filters by category', () => {
    expect(filterFiles(files, 'local_only')).toHaveLength(1);
  });
});

describe('computeSummary helper', () => {
  it('computes correct summary', () => {
    const files = [
      makeFile('a.txt', 'local_only'),
      makeFile('b.txt', 'local_only'),
      makeFile('c.txt', 'remote_only'),
    ];
    const summary = computeSummary(files);
    expect(summary.total).toBe(3);
    expect(summary.local_only).toBe(2);
    expect(summary.remote_only).toBe(1);
  });
});

describe('actions that cannot apply', () => {
  it('knows there is nothing to pull for a local-only file or to push for a remote-only one', () => {
    expect(canApplyAction({ category: 'local_only' }, 'pull')).toBe(false);
    expect(canApplyAction({ category: 'local_only' }, 'push')).toBe(true);
    expect(canApplyAction({ category: 'remote_only' }, 'push')).toBe(false);
    expect(canApplyAction({ category: 'remote_only' }, 'pull')).toBe(true);
    expect(canApplyAction({ category: 'modified_both' }, 'pull')).toBe(true);
    expect(canApplyAction({ category: 'local_only' }, 'skip')).toBe(true);
  });

  it('disables Pull on a local-only row and says why', async () => {
    const user = userEvent.setup();
    const onSync = vi.fn();
    render(<FileBrowser files={[makeFile('only-here.txt', 'local_only')]} summary={makeSummary(1)} onSync={onSync} />);
    await user.click(screen.getByRole('button', { name: 'granular.action' }));
    const menu = await screen.findByRole('menu');
    const pull = within(menu).getByRole('menuitem', { name: /granular\.pull/ });
    expect(pull).toHaveAttribute('data-disabled');
    expect(pull).toHaveTextContent('granular.pullUnavailable');
    expect(within(menu).getByRole('menuitem', { name: 'granular.push' })).not.toHaveAttribute('data-disabled');
  });
});

describe('summary badges', () => {
  it('are focusable and carry their explanation for screen readers', () => {
    render(<FileBrowser files={[makeFile('a.txt')]} summary={makeSummary(1)} onSync={vi.fn()} />);
    const summary = screen.getByTestId('diff-summary');
    const badge = within(summary).getByText('granular.localOnly');
    expect(badge).toHaveAttribute('tabindex', '0');
    expect(badge).toHaveTextContent('granular.tipLocalOnly');
  });

  it('keeps file paths left to right', () => {
    render(<FileBrowser files={[makeFile('dir/a.txt')]} summary={makeSummary(1)} onSync={vi.fn()} />);
    expect(screen.getByText('dir/a.txt').closest('bdi')).toHaveAttribute('dir', 'ltr');
  });
});
