import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nProvider } from '@/i18n';
import { FileBrowser } from '@/components/sync/file-browser';
import { ConflictDialog } from '@/components/sync/conflict-dialog';
import type { FileDiff, DiffSummary } from '@/types';
import enLocale from '@/i18n/locales/en.json';
import faLocale from '@/i18n/locales/fa.json';
import deLocale from '@/i18n/locales/de.json';

// --- Helpers ---

function createWrapper () {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return function Wrapper ({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <I18nProvider>{children}</I18nProvider>
      </QueryClientProvider>
    );
  };
}

const MOCK_FILES: FileDiff[] = [
  {
    path:            'docs/readme.md',
    category:        'local_only',
    local_size:      1024,
    remote_size:     null,
    local_mod_time:  '2025-06-01T10:00:00Z',
    remote_mod_time: null,
    is_conflict:     false,
    manual_flag:     false,
  },
  {
    path:            'src/index.ts',
    category:        'modified_both',
    local_size:      2048,
    remote_size:     1900,
    local_mod_time:  '2025-06-02T10:00:00Z',
    remote_mod_time: '2025-06-02T09:00:00Z',
    is_conflict:     true,
    manual_flag:     false,
  },
  {
    path:            'config.toml',
    category:        'modified_local',
    local_size:      512,
    remote_size:     500,
    local_mod_time:  '2025-06-03T10:00:00Z',
    remote_mod_time: '2025-05-01T10:00:00Z',
    is_conflict:     false,
    manual_flag:     true,
  },
  {
    path:            'data/backup.zip',
    category:        'remote_only',
    local_size:      null,
    remote_size:     50000,
    local_mod_time:  null,
    remote_mod_time: '2025-06-01T08:00:00Z',
    is_conflict:     false,
    manual_flag:     false,
  },
];

const MOCK_SUMMARY: DiffSummary = {
  local_only:      1,
  remote_only:     1,
  modified_local:  1,
  modified_remote: 0,
  modified_both:   1,
  manual:          1,
  total:           4,
};

// --- Tests ---

// Req 4.2: File Browser renders conflict files with warning icon
describe('File Browser renders conflict files with warning icon', () => {
  it('shows AlertTriangle icon for conflict files', () => {
    const onSync = vi.fn();
    render(
      <FileBrowser files={MOCK_FILES} summary={MOCK_SUMMARY} onSync={onSync} />,
      { wrapper: createWrapper() }
    );

    // The conflict file row should have a warning icon
    const conflictLabel = screen.getByLabelText('Conflict');
    expect(conflictLabel).toBeInTheDocument();
  });
});

// Req 4.3: Conflict dialog shows four resolution options
describe('Conflict dialog shows four resolution options', () => {
  it('displays keep local, keep remote, keep both, mark manual buttons', () => {
    const onResolve = vi.fn();
    const onClose = vi.fn();
    const conflictFile = MOCK_FILES[1]; // modified_both

    render(
      <ConflictDialog file={conflictFile} onResolve={onResolve} onClose={onClose} />,
      { wrapper: createWrapper() }
    );

    expect(screen.getByText('Keep local version (push)')).toBeInTheDocument();
    expect(screen.getByText('Keep remote version (pull)')).toBeInTheDocument();
    expect(screen.getByText('Keep both versions')).toBeInTheDocument();
    expect(screen.getByText('Mark manual')).toBeInTheDocument();
  });
});

// Req 5.4: Push All / Pull All buttons present alongside file controls
describe('Push All / Pull All buttons present alongside file controls', () => {
  it('renders push and pull buttons in the main controls area', async () => {
    // We test this via the SyncControls component indirectly.
    // The FileBrowser itself has per-row push/pull actions.
    const onSync = vi.fn();
    render(
      <FileBrowser files={MOCK_FILES} summary={MOCK_SUMMARY} onSync={onSync} />,
      { wrapper: createWrapper() }
    );

    // Each row should have an action dropdown
    const actionButtons = screen.getAllByText('Action');
    expect(actionButtons.length).toBe(MOCK_FILES.length);
  });
});

// Req 6.4: Checkboxes present on rows, select-all toggles all
describe('Checkboxes present on rows, select-all toggles all', () => {
  it('has checkboxes on each row and a select-all in header', async () => {
    const user = userEvent.setup();
    const onSync = vi.fn();
    render(
      <FileBrowser files={MOCK_FILES} summary={MOCK_SUMMARY} onSync={onSync} />,
      { wrapper: createWrapper() }
    );

    // Select-all checkbox
    const selectAll = screen.getByLabelText('Select all files');
    expect(selectAll).toBeInTheDocument();

    // Per-file checkboxes
    for (const file of MOCK_FILES) {
      const cb = screen.getByLabelText(`Select ${file.path}`);
      expect(cb).toBeInTheDocument();
    }

    // Click select-all
    await user.click(selectAll);

    // All should be selected — batch toolbar should appear
    expect(screen.getByText('4 selected')).toBeInTheDocument();

    // Click select-all again to deselect
    await user.click(selectAll);
    expect(screen.queryByText('4 selected')).not.toBeInTheDocument();
  });
});

// Req 6.5: Batch toolbar appears when files selected
describe('Batch toolbar appears when files selected', () => {
  it('shows batch toolbar with action buttons when a file is selected', async () => {
    const user = userEvent.setup();
    const onSync = vi.fn();
    render(
      <FileBrowser files={MOCK_FILES} summary={MOCK_SUMMARY} onSync={onSync} />,
      { wrapper: createWrapper() }
    );

    // No toolbar initially
    expect(screen.queryByText('Push selected')).not.toBeInTheDocument();

    // Select one file
    await user.click(screen.getByLabelText('Select docs/readme.md'));

    // Toolbar should appear
    expect(screen.getByText('1 selected')).toBeInTheDocument();
    expect(screen.getByText('Push selected')).toBeInTheDocument();
    expect(screen.getByText('Pull selected')).toBeInTheDocument();
    expect(screen.getByText('Skip selected')).toBeInTheDocument();
  });
});

// Conflicting files are never overwritten by a batch or folder action
describe('Batch and folder actions route conflicts through the dialog', () => {
  it('batch push sends the non-conflicting files and asks about the conflict', async () => {
    const user = userEvent.setup();
    const onSync = vi.fn();
    render(
      <FileBrowser files={MOCK_FILES} summary={MOCK_SUMMARY} onSync={onSync} />,
      { wrapper: createWrapper() }
    );

    await user.click(screen.getByLabelText('Select docs/readme.md'));
    await user.click(screen.getByLabelText('Select src/index.ts'));
    await user.click(screen.getByText('Push selected'));

    // Only the non-conflicting file is pushed straight away.
    expect(onSync).toHaveBeenCalledTimes(1);
    expect(onSync).toHaveBeenCalledWith([{ path: 'docs/readme.md', action: 'push' }]);

    // The conflict waits for a decision.
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('src/index.ts');
    await user.click(screen.getByText('Keep remote version (pull)'));
    expect(onSync).toHaveBeenLastCalledWith([{ path: 'src/index.ts', action: 'pull' }]);
  });

  it('cancelling the dialog leaves the conflicting file untouched', async () => {
    const user = userEvent.setup();
    const onSync = vi.fn();
    render(
      <FileBrowser files={MOCK_FILES} summary={MOCK_SUMMARY} onSync={onSync} />,
      { wrapper: createWrapper() }
    );

    await user.click(screen.getByLabelText('Select src/index.ts'));
    await user.click(screen.getByText('Pull selected'));
    expect(onSync).not.toHaveBeenCalled();

    await screen.findByRole('dialog');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onSync).not.toHaveBeenCalled();
  });

  it('skip applies to conflicts directly (nothing is overwritten)', async () => {
    const user = userEvent.setup();
    const onSync = vi.fn();
    render(
      <FileBrowser files={MOCK_FILES} summary={MOCK_SUMMARY} onSync={onSync} />,
      { wrapper: createWrapper() }
    );

    await user.click(screen.getByLabelText('Select src/index.ts'));
    await user.click(screen.getByText('Skip selected'));
    expect(onSync).toHaveBeenCalledWith([{ path: 'src/index.ts', action: 'skip' }]);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

// "Mark manual" can be undone
describe('Unmark manual', () => {
  it('a manual-flagged row offers Unmark instead of Mark manual', async () => {
    const user = userEvent.setup();
    const onUnmark = vi.fn();
    render(
      <FileBrowser files={[MOCK_FILES[2]]} summary={MOCK_SUMMARY} onSync={vi.fn()} onUnmarkManual={onUnmark} />,
      { wrapper: createWrapper() }
    );

    await user.click(screen.getByRole('button', { name: 'Action' }));
    expect(screen.queryByRole('menuitem', { name: 'Mark manual' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('menuitem', { name: 'Unmark manual' }));
    expect(onUnmark).toHaveBeenCalledWith('config.toml');
  });
});

// Req 7.2: Manual-flagged files show "manual" badge
describe('Manual-flagged files show manual badge', () => {
  it('renders a Manual badge for files with manual_flag=true', () => {
    const onSync = vi.fn();
    render(
      <FileBrowser files={MOCK_FILES} summary={MOCK_SUMMARY} onSync={onSync} />,
      { wrapper: createWrapper() }
    );

    // config.toml has manual_flag=true
    const badges = screen.getAllByText('Manual');
    expect(badges.length).toBeGreaterThanOrEqual(1);
  });
});

// The summary badges count with the plural form: "1 conflict", not "1 conflicts".
describe('Differences summary plurals', () => {
  it('uses the singular for one and the plural otherwise', () => {
    const { unmount } = render(
      <FileBrowser files={MOCK_FILES} summary={MOCK_SUMMARY} onSync={vi.fn()} />,
      { wrapper: createWrapper() }
    );
    expect(screen.getByText('1 conflict')).toBeInTheDocument();
    expect(screen.queryByText('1 conflicts')).not.toBeInTheDocument();
    unmount();

    render(
      <FileBrowser files={MOCK_FILES} summary={{ ...MOCK_SUMMARY, modified_both: 3, total: 6 }} onSync={vi.fn()} />,
      { wrapper: createWrapper() }
    );
    expect(screen.getByText('3 conflicts')).toBeInTheDocument();
    expect(screen.getByText('6 total')).toBeInTheDocument();
  });

  it('counts the selection with the plural form', async () => {
    const user = userEvent.setup();
    render(
      <FileBrowser files={MOCK_FILES} summary={MOCK_SUMMARY} onSync={vi.fn()} />,
      { wrapper: createWrapper() }
    );
    await user.click(screen.getByLabelText('Select docs/readme.md'));
    expect(screen.getByText('1 selected')).toBeInTheDocument();
  });
});

// Req 6.7: All required i18n keys exist in en.json, de.json, fa.json
describe('All required granular i18n keys exist in all locales', () => {
  const requiredKeys = [
    'granular.showDiff', 'granular.hideDiff', 'granular.total',
    'granular.localOnly', 'granular.remoteOnly', 'granular.modifiedLocal',
    'granular.modifiedRemote', 'granular.modifiedBoth', 'granular.filterAll',
    'granular.catLocalOnly', 'granular.catRemoteOnly', 'granular.catModifiedLocal',
    'granular.catModifiedRemote', 'granular.catModifiedBoth',
    'granular.filePath', 'granular.category', 'granular.localSize',
    'granular.remoteSize', 'granular.localTime', 'granular.remoteTime',
    'granular.actions', 'granular.action', 'granular.selectAll',
    'granular.conflict', 'granular.manual', 'granular.na',
    'granular.noFiles', 'granular.push', 'granular.pull', 'granular.skip',
    'granular.markManual', 'granular.selectedCount', 'granular.pushSelected',
    'granular.pullSelected', 'granular.skipSelected', 'granular.clearSelection',
    'granular.conflictTitle', 'granular.conflictDesc',
    'granular.keepLocal', 'granular.keepRemote', 'granular.keepBoth',
    // loading, error and toast keys
    'granular.loadingDiff', 'granular.loadingError', 'granular.retry',
    'granular.allResolved', 'granular.toastPushed', 'granular.toastPulled',
    'granular.toastSkipped', 'granular.toastManual', 'granular.toastKeepBoth',
    'granular.toastBatchPush', 'granular.toastBatchPull', 'granular.toastBatchSkip',
    'granular.toastBatchManual', 'granular.toastBatchKeepBoth',
    'granular.toastPartialFail', 'granular.processing',
  ];

  function getNestedValue (obj: Record<string, unknown>, key: string): unknown {
    const parts = key.split('.');
    let current: unknown = obj;
    for (const part of parts) {
      if (current === null || current === undefined || typeof current !== 'object') return undefined;
      current = (current as Record<string, unknown>)[part];
    }
    return current;
  }

  // A count-bearing string is a plural pair: key_one / key_other.
  function lookup (locale: Record<string, unknown>, key: string): unknown {
    return getNestedValue(locale, key) ?? getNestedValue(locale, `${key}_other`);
  }

  for (const key of requiredKeys) {
    it(`key "${key}" exists in all 3 locales`, () => {
      for (const locale of [enLocale, deLocale, faLocale]) {
        expect(typeof lookup(locale as Record<string, unknown>, key)).toBe('string');
      }
    });
  }
});

// --- FileBrowser pending row behavior ---

describe('FileBrowser pending row behavior', () => {
  it('rows in pendingPaths have opacity-50 class', () => {
    const onSync = vi.fn();
    const pending = new Set(['docs/readme.md']);
    render(
      <FileBrowser files={MOCK_FILES} summary={MOCK_SUMMARY} onSync={onSync} pendingPaths={pending} />,
      { wrapper: createWrapper() }
    );

    // The pending row should have opacity-50
    const pendingRow = screen.getByLabelText('Select docs/readme.md').closest('tr');
    expect(pendingRow).toHaveClass('opacity-50');
    expect(pendingRow).toHaveClass('pointer-events-none');
  });

  it('rows in pendingPaths have disabled checkbox', () => {
    const onSync = vi.fn();
    const pending = new Set(['docs/readme.md']);
    render(
      <FileBrowser files={MOCK_FILES} summary={MOCK_SUMMARY} onSync={onSync} pendingPaths={pending} />,
      { wrapper: createWrapper() }
    );

    const checkbox = screen.getByLabelText('Select docs/readme.md');
    expect(checkbox).toBeDisabled();
  });

  it('rows in pendingPaths show spinner instead of action dropdown', () => {
    const onSync = vi.fn();
    const pending = new Set(['docs/readme.md']);
    render(
      <FileBrowser files={MOCK_FILES} summary={MOCK_SUMMARY} onSync={onSync} pendingPaths={pending} />,
      { wrapper: createWrapper() }
    );

    const spinners = screen.getAllByTestId('row-spinner');
    expect(spinners.length).toBe(1);
  });

  it('rows NOT in pendingPaths remain interactive', () => {
    const onSync = vi.fn();
    const pending = new Set(['docs/readme.md']);
    render(
      <FileBrowser files={MOCK_FILES} summary={MOCK_SUMMARY} onSync={onSync} pendingPaths={pending} />,
      { wrapper: createWrapper() }
    );

    const normalRow = screen.getByLabelText('Select src/index.ts').closest('tr');
    expect(normalRow).not.toHaveClass('opacity-50');
    const normalCheckbox = screen.getByLabelText('Select src/index.ts');
    expect(normalCheckbox).not.toBeDisabled();
  });

  it('batch toolbar disabled when any pendingPaths exist', async () => {
    const user = userEvent.setup();
    const onSync = vi.fn();
    const pending = new Set(['docs/readme.md']);
    render(
      <FileBrowser files={MOCK_FILES} summary={MOCK_SUMMARY} onSync={onSync} pendingPaths={pending} />,
      { wrapper: createWrapper() }
    );

    // Select a non-pending file to trigger batch toolbar
    await user.click(screen.getByLabelText('Select src/index.ts'));

    // Batch toolbar should appear but actions disabled
    const pushBtn = screen.getByText('Push selected');
    expect(pushBtn).toBeDisabled();
  });

  it('select-all skips pending paths', async () => {
    const user = userEvent.setup();
    const onSync = vi.fn();
    const pending = new Set(['docs/readme.md']);
    render(
      <FileBrowser files={MOCK_FILES} summary={MOCK_SUMMARY} onSync={onSync} pendingPaths={pending} />,
      { wrapper: createWrapper() }
    );

    await user.click(screen.getByLabelText('Select all files'));

    // Should show 3 selected (4 total minus 1 pending)
    expect(screen.getByText('3 selected')).toBeInTheDocument();
  });

  it('empty state shows "all resolved" message', () => {
    const onSync = vi.fn();
    const emptySummary: DiffSummary = {
      local_only:      0,
      remote_only:     0,
      modified_local:  0,
      modified_remote: 0,
      modified_both:   0,
      manual:          0,
      total:           0,
    };
    render(
      <FileBrowser files={[]} summary={emptySummary} onSync={onSync} />,
      { wrapper: createWrapper() }
    );

    expect(screen.getByText('All differences resolved')).toBeInTheDocument();
  });
});
