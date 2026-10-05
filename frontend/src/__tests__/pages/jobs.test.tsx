import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Suspense } from 'react';
import type { ReactNode } from 'react';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider } from '@/i18n';
import JobsPage from '@/app/jobs/page';
import JobDetailPage from '@/app/jobs/[id]/page';
import { api } from '@/lib/api';
import type { SyncJob } from '@/types';

vi.mock('@/lib/api', () => ({ api: { getJobs: vi.fn(), getJob: vi.fn(), getJobFiles: vi.fn() } }));
vi.mock('@/components/layout/page-header', () => ({ PageHeader: ({ title }: { title: string }) => <h1>{title}</h1> }));
vi.mock('@/components/layout/page-help', () => ({ PageHelp: () => null }));
const mockPush = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mockPush }) }));

const getJobs = vi.mocked(api.getJobs);
const getJob = vi.mocked(api.getJob);
const getJobFiles = vi.mocked(api.getJobFiles);

function job (id: number, status: SyncJob['status'] = 'completed'): SyncJob {
  return {
    id,
    direction:     'push',
    started_at:    '2026-09-27T08:30:00Z',
    finished_at:   status === 'running' ? null : '2026-09-27T08:31:00Z',
    status,
    files_changed: 2,
    conflicts:     0,
    errors:        0,
  };
}

function wrap (ui: ReactNode, locale: 'en' | 'fa' = 'en') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <I18nProvider initialLocale={locale}>
        <Suspense fallback={<p>suspended</p>}>{ui}</Suspense>
      </I18nProvider>
    </QueryClientProvider>
  );
}

// `use(params)` suspends once; an async act lets the resolved promise wake the page.
async function renderDetail (id: string, locale: 'en' | 'fa' = 'en') {
  let result!: ReturnType<typeof render>;
  await act(async () => {
    result = wrap(<JobDetailPage params={Promise.resolve({ id })} />, locale);
  });
  return result;
}

beforeEach(() => {
  getJobs.mockReset();
  getJob.mockReset();
  getJobFiles.mockReset();
  mockPush.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('Jobs page', () => {
  it('pages through the history: a full page enables Next, a short one ends it', async () => {
    getJobs.mockImplementation(async (skip = 0) => (skip === 0
      ? Array.from({ length: 20 }, (_, i) => job(100 - i))
      : [job(80)]));
    const user = userEvent.setup();
    wrap(<JobsPage />);

    expect(await screen.findByRole('heading', { name: 'Job History' })).toBeInTheDocument();
    await screen.findByRole('link', { name: 'Open job 100' });
    expect(getJobs).toHaveBeenLastCalledWith(0, 20, undefined);
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByRole('link', { name: 'Open job 80' });
    expect(getJobs).toHaveBeenLastCalledWith(20, 20, undefined);
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Previous' }));
    await screen.findByRole('link', { name: 'Open job 100' });
  });

  it('offers a retry when the history cannot be loaded', async () => {
    getJobs.mockRejectedValueOnce(new Error('down')).mockResolvedValue([]);
    const user = userEvent.setup();
    wrap(<JobsPage />);

    const alert = await screen.findByRole('alert');
    await user.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('No sync jobs yet')).toBeInTheDocument();
    expect(getJobs).toHaveBeenCalledTimes(2);
  });
});

describe('Job detail page', () => {
  it('an id that is not a positive integer is not found, and nothing is fetched', async () => {
    await renderDetail('abc');
    expect(await screen.findByText('Job not found')).toBeInTheDocument();
    expect(getJob).not.toHaveBeenCalled();
    expect(getJobFiles).not.toHaveBeenCalled();
  });

  it('shows loading until the job arrives, then the job and its files', async () => {
    let finish!: (j: SyncJob) => void;
    getJob.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    getJobFiles.mockResolvedValue([
      { id: 1, job_id: 7, file_path: 'docs/a.txt', action: 'created', size_bytes: 10, side: 'remote' },
    ]);
    await renderDetail('7');

    expect(await screen.findByText('Loading...')).toBeInTheDocument();
    await act(async () => finish(job(7)));
    expect(await screen.findByRole('heading', { name: 'Job Detail' })).toBeInTheDocument();
    expect(await screen.findByText('docs/a.txt')).toBeInTheDocument();
    expect(getJob).toHaveBeenCalledWith(7);
  });

  it('shows the server error with a retry that loads the job again', async () => {
    getJob.mockRejectedValueOnce(new Error('Job 7 is gone')).mockResolvedValue(job(7));
    getJobFiles.mockResolvedValue([]);
    const user = userEvent.setup();
    await renderDetail('7');

    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('Job 7 is gone')).toBeInTheDocument();
    await user.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('heading', { name: 'Job Detail' })).toBeInTheDocument();
  });

  it('an error without a message falls back to the generic text', async () => {
    getJob.mockRejectedValue(new Error(''));
    getJobFiles.mockResolvedValue([]);
    await renderDetail('7');
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
  });

  // A running job polls itself and its files every 2 s until it ends.
  it('polls a running job and its files until the job is done', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getJob
      .mockResolvedValueOnce(job(9, 'running'))
      .mockResolvedValueOnce(job(9, 'running'))
      .mockResolvedValue(job(9, 'completed'));
    getJobFiles.mockResolvedValue([]);
    await renderDetail('9');

    expect(await screen.findByText('Running')).toBeInTheDocument();
    await waitFor(() => expect(getJobFiles).toHaveBeenCalledTimes(1));
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    await waitFor(() => expect(getJobFiles).toHaveBeenCalledTimes(2));
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(await screen.findByText('Completed')).toBeInTheDocument();

    // Done: no more polling.
    const calls = getJob.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(6_000); });
    expect(getJob).toHaveBeenCalledTimes(calls);
  });

  it('says "not found" in Persian', async () => {
    await renderDetail('0', 'fa');
    expect(await screen.findByText('کار یافت نشد')).toBeInTheDocument();
  });
});
