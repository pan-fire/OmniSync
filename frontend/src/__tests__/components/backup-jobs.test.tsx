import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import { useRestore, useRunBackup } from '@/hooks/use-backups';
import { BackupTargetCard } from '@/components/profiles/backup-target-card';
import type { BackupJob, BackupJobStatus, BackupTarget } from '@/types';

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

const originalFetch = globalThis.fetch;
let fetchMock: ReturnType<typeof vi.fn>;
let queryClient: QueryClient;

function respond (data: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(data) });
}

function job (status: BackupJobStatus, errorMessage: string | null = null, direction = 'backup'): BackupJob {
  return {
    id:            9,
    target_id:     3,
    started_at:    '2026-09-27T10:00:00Z',
    finished_at:   status === 'running' ? null : '2026-09-27T10:01:00Z',
    status,
    direction,
    size_bytes:    null,
    snapshot_id:   null,
    error_message: errorMessage,
  };
}

function wrapper ({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider>{children}</I18nProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  fetchMock = vi.fn(() => respond({}));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

const JOB_URL = '/api/profiles/docs/backups/3/jobs/9';

/**
 * The start answers `start`; GET of the job answers `polls` in turn (the
 * last one again after that). Starts answer at once with the running job.
 */
function serve (start: Promise<unknown>, ...polls: (() => Promise<unknown>)[]) {
  let polled = 0;
  fetchMock.mockImplementation((url: string, init?: { method?: string }) => {
    if (init?.method === 'POST') return start;
    if (url === JOB_URL && polls.length > 0) return polls[Math.min(polled++, polls.length - 1)]();
    return respond({});
  });
}

async function runNow (start: Promise<unknown>, ...polls: (() => Promise<unknown>)[]) {
  serve(start, ...polls);
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  const { result } = renderHook(() => useRunBackup('docs', 1), { wrapper });
  await act(async () => {
    await result.current.mutateAsync(3).catch(() => undefined);
  });
  return { invalidate };
}

describe('Run now follows the backup until it ends', () => {
  it('posts to the run route of the target, then polls the job', async () => {
    await runNow(respond(job('running'), 202), () => respond(job('completed')));
    expect(fetchMock.mock.calls[0][0]).toBe('/api/profiles/docs/backups/3/run');
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'POST' });
    expect(fetchMock.mock.calls.some(([url]) => url === JOB_URL)).toBe(true);
  });

  it('started, then completed: info toast, success toast, history refreshed', async () => {
    const { invalidate } = await runNow(
      respond(job('running'), 202), () => respond(job('running')), () => respond(job('completed'))
    );
    expect(toast.info).toHaveBeenCalledWith('Backup started');
    expect(toast.success).toHaveBeenCalledWith('Backup completed');
    expect(toast.error).not.toHaveBeenCalled();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['backups', 'docs'] });
  });

  it('failed: error toast with the job error, no success toast', async () => {
    const { invalidate } = await runNow(
      respond(job('running'), 202), () => respond(job('failed', 'The backup failed. The OmniSync log has the details.'))
    );
    expect(toast.error).toHaveBeenCalledWith('Backup failed: The backup failed. The OmniSync log has the details.');
    expect(toast.success).not.toHaveBeenCalled();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['backups', 'docs'] });
  });

  it('failed without a message still says it failed', async () => {
    await runNow(respond(job('running'), 202), () => respond(job('failed')));
    expect(toast.error).toHaveBeenCalledWith('Backup failed');
  });

  it('skipped: warning with the reason', async () => {
    await runNow(respond(job('running'), 202), () => respond(job('skipped', 'The backup target could not be reached.')));
    expect(toast.warning).toHaveBeenCalledWith(
      'Backup skipped, the target is not reachable: The backup target could not be reached.'
    );
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('an ended job in the answer needs no polling', async () => {
    await runNow(respond(job('completed'), 202));
    expect(fetchMock.mock.calls.some(([url]) => url === JOB_URL)).toBe(false);
    expect(toast.success).toHaveBeenCalledWith('Backup completed');
  });

  it('losing track of the run says so, not that it failed', async () => {
    await runNow(respond(job('running'), 202), () => respond({ detail: 'Bad gateway' }, 502));
    expect(toast.warning).toHaveBeenCalledWith(
      'The run continues, but its progress could not be followed. Check the backup history.'
    );
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('409: a backup of this target is already running', async () => {
    const { invalidate } = await runNow(respond({ detail: 'Backup already running for this target', code: 'backup_running' }, 409));
    expect(toast.error).toHaveBeenCalledWith('A backup of this target is already running. Wait for it to finish.');
    expect(toast.success).not.toHaveBeenCalled();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['backups', 'docs'] });
  });

  it('409: the profile is busy', async () => {
    await runNow(respond({ detail: 'A sync of this profile is running.', code: 'sync_busy' }, 409));
    expect(toast.error).toHaveBeenCalledWith(
      'A sync, backup or restore of this profile is running. Try again when it is done.'
    );
  });

  it('other errors show the server message', async () => {
    await runNow(respond({ detail: 'Backup service not available' }, 503));
    expect(toast.error).toHaveBeenCalledWith('Backup service not available');
  });
});

async function restore (start: Promise<unknown>, ...polls: (() => Promise<unknown>)[]) {
  serve(start, ...polls);
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  const { result } = renderHook(() => useRestore('docs', 1), { wrapper });
  await act(async () => {
    await result.current
      .mutateAsync({ id: 3, data: { snapshot_id: '2026-09-01T10-00-00', restore_scope: 'local_only' } })
      .catch(() => undefined);
  });
  return { invalidate };
}

describe('Restore follows the restore until it ends', () => {
  it('completed: success toast; backups and the (possibly paused) profile refreshed', async () => {
    const { invalidate } = await restore(
      respond(job('running', null, 'restore'), 202), () => respond(job('completed', null, 'restore'))
    );
    expect(fetchMock.mock.calls[0][0]).toBe('/api/profiles/docs/backups/3/restore');
    expect(toast.info).toHaveBeenCalledWith('Restore started');
    expect(toast.success).toHaveBeenCalledWith('Restore completed');
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['backups', 'docs'] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['profiles', 'docs'] });
  });

  it('failed: error toast with the job error', async () => {
    await restore(
      respond(job('running', null, 'restore'), 202),
      () => respond(job('failed', "Local folder '/data/docs' is missing or not mounted; nothing was restored.", 'restore'))
    );
    expect(toast.error).toHaveBeenCalledWith("Restore failed: Local folder '/data/docs' is missing or not mounted; nothing was restored.");
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('409: already running', async () => {
    await restore(respond({ detail: 'busy', code: 'backup_running' }, 409));
    expect(toast.error).toHaveBeenCalledWith('A backup or restore of this target is already running. Wait for it to finish.');
  });

  it('409: the profile is busy (a sync runs); answered at once', async () => {
    await restore(respond({ detail: 'A sync of this profile is running.', code: 'sync_busy' }, 409));
    expect(toast.error).toHaveBeenCalledWith(
      'A sync, backup or restore of this profile is running. Try again when it is done.'
    );
  });

  it('another 409 shows its reason, not "already running"', async () => {
    const reason = "Local folder '/home/me/docs' is missing or not mounted; nothing was restored.";
    await restore(respond({ detail: reason, code: 'snapshot_unavailable' }, 409));
    expect(toast.error).toHaveBeenCalledWith(reason);
  });
});

describe('BackupTargetCard while a manual run is in flight', () => {
  const target: BackupTarget = {
    id:                  3,
    profile_id:          1,
    name:                'Nightly',
    target_path:         '/bak',
    target_type:         'local',
    remote_name:         null,
    retention_days:      30,
    keep_last:           3,
    overdue:             false,
    encrypted:           false,
    verify_after_backup: false,
    last_verify_status:  null,
    last_verify_message: null,
    frequency_hours:     24,
    backup_mode:         'archive',
    enabled:             true,
    last_liveness_ok:    null,
    last_liveness_error: null,
    last_backup_at:      null,
    last_backup_status:  null,
    next_scheduled_at:   null,
    created_at:          '2026-09-01T00:00:00Z',
    updated_at:          '2026-09-01T00:00:00Z',
  };

  function Harness () {
    const run = useRunBackup('docs', 1);
    return (
      <BackupTargetCard
        target={target}
        profileSlug="docs"
        onRunNow={(id) => run.mutate(id)}
        onEdit={() => {}}
        onToggleEnabled={() => {}}
      />
    );
  }

  it('shows Running… and blocks a second run until the job ends', async () => {
    let finish: (value: unknown) => void = () => {};
    const ended = new Promise((resolve) => { finish = resolve; });
    serve(respond(job('running'), 202), () => ended);
    render(<Harness />, { wrapper });

    fireEvent.click(screen.getByRole('button', { name: 'Run Now' }));
    const busy = await screen.findByRole('button', { name: 'Running…' });
    expect(busy).toBeDisabled();
    await waitFor(() => expect(toast.info).toHaveBeenCalledWith('Backup started'));
    expect(screen.getByRole('button', { name: 'Running…' })).toBeDisabled();

    await act(async () => {
      finish({ ok: true, status: 200, json: () => Promise.resolve(job('completed')) });
    });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Run Now' })).toBeEnabled());
    expect(toast.success).toHaveBeenCalledWith('Backup completed');
  });
});
