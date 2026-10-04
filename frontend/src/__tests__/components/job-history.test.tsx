import { describe, it, expect, vi } from 'vitest';
import fc from 'fast-check';
import { render, within } from '@testing-library/react';
import { I18nProvider } from '@/i18n';
import { formatDateTime } from '@/lib/format';
import { JobHistoryTable } from '@/components/jobs/job-history-table';
import { JobDetail } from '@/components/jobs/job-detail';
import type {
  SyncJob,
  FileChange,
  FileSide,
  JobDirection,
  JobStatus,
  FileChangeAction,
} from '@/types';

// Mock next/navigation
const mockPush = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush }),
}));

const DIRECTION_LABELS: Record<string, string> = {
  push: 'Push', pull: 'Pull', selective: 'Selective', two_way: 'Two-way', resync: 'Resync',
};
const SIDE_LABELS: Record<string, string> = { local: 'Local', remote: 'Remote' };
const STATUS_LABELS: Record<string, string> = { running: 'Running', completed: 'Completed', failed: 'Failed' };
const ACTION_LABELS: Record<string, string> = { created: 'Created', modified: 'Modified', deleted: 'Deleted' };

// --- Arbitraries ---

const syncDirectionArb = fc.constantFrom<JobDirection>('push', 'pull', 'selective', 'two_way', 'resync');
const fileSideArb = fc.option(fc.constantFrom<FileSide>('local', 'remote'), { nil: null });
const jobStatusArb = fc.constantFrom<JobStatus>('running', 'completed', 'failed');
const fileChangeActionArb = fc.constantFrom<FileChangeAction>('created', 'modified', 'deleted');

const safeDateArb = fc.integer({ min: 946684800000, max: 4102444800000 }).map((ts) => new Date(ts).toISOString());

const syncJobArb: fc.Arbitrary<SyncJob> = fc.record({
  id:            fc.integer({ min: 1, max: 99999 }),
  direction:     syncDirectionArb,
  started_at:    safeDateArb,
  finished_at:   fc.option(safeDateArb, { nil: null }),
  status:        jobStatusArb,
  files_changed: fc.nat({ max: 1000 }),
  conflicts:     fc.nat({ max: 100 }),
  errors:        fc.nat({ max: 100 }),
});

const fileChangeArb: fc.Arbitrary<FileChange> = fc.record({
  id:         fc.integer({ min: 1, max: 99999 }),
  job_id:     fc.integer({ min: 1, max: 99999 }),
  file_path:  fc.stringMatching(/^[a-z][a-z0-9/._-]{0,49}$/),
  action:     fileChangeActionArb,
  size_bytes: fc.option(fc.nat({ max: 10_000_000 }), { nil: null }),
  side:       fileSideArb,
});

// Ensure unique IDs in job arrays
const nonEmptyJobsArb = fc
  .array(syncJobArb, { minLength: 1, maxLength: 5 })
  .map((jobs) => jobs.map((job, i) => ({ ...job, id: i + 1 })));

// Ensure unique IDs in file change arrays
const nonEmptyFilesArb = fc
  .array(fileChangeArb, { minLength: 1, maxLength: 5 })
  .map((files) => files.map((file, i) => ({ ...file, id: i + 1 })));

// The render properties mount once and rerender per run, and read each row
// once, cell by cell: a fresh mount and a text query per field made 100 runs
// take over 5 s under coverage on a loaded machine. Comparing cells is also
// stricter (a value must sit in its own row and column). 50 runs of up to 5
// jobs or files still hit every direction/status/action/side combination.
const RENDER_RUNS = 50;

/** The text of each body row's cells, in order. */
function bodyRows (container: HTMLElement): string[][] {
  return Array.from(container.querySelectorAll('tbody tr'), (row) =>
    Array.from((row as HTMLTableRowElement).cells, (cell) => cell.textContent ?? ''));
}

function JobTable ({ jobs }: { jobs: SyncJob[] }) {
  return (
    <I18nProvider>
      <JobHistoryTable jobs={jobs} isError={false} refetch={() => {}} page={1} onPageChange={() => {}} />
    </I18nProvider>
  );
}

describe('Job history table rendering completeness', () => {
  it('for any non-empty SyncJob array, the rendered table contains all required fields for each job', () => {
    const { container, rerender } = render(<JobTable jobs={[]} />);
    fc.assert(
      fc.property(nonEmptyJobsArb, (jobs: SyncJob[]) => {
        rerender(<JobTable jobs={jobs} />);

        expect(bodyRows(container)).toEqual(jobs.map((job) => [
          String(job.id),
          '—', // no profile in the arbitrary
          DIRECTION_LABELS[job.direction],
          formatDateTime(job.started_at, 'en'),
          STATUS_LABELS[job.status],
          String(job.files_changed),
          String(job.conflicts),
          String(job.errors),
        ]));
        for (const job of jobs) {
          // ID, as a link to the job (keyboard accessible)
          expect(container.querySelector(`a[href="/jobs/${job.id}"]`)).toHaveAttribute('aria-label', `Open job ${job.id}`);
        }
      }),
      { numRuns: RENDER_RUNS }
    );
  });
});

function Detail ({ job, files }: { job: SyncJob; files: FileChange[] }) {
  return <I18nProvider><JobDetail job={job} files={files} /></I18nProvider>;
}

describe('Job detail rendering completeness', () => {
  it('for any SyncJob and FileChange array, the rendered view contains all metadata and file change fields', () => {
    const first = fc.sample(syncJobArb, 1)[0];
    const { container, rerender } = render(<Detail job={first} files={[]} />);
    fc.assert(
      fc.property(syncJobArb, nonEmptyFilesArb, (job: SyncJob, files: FileChange[]) => {
        rerender(<Detail job={job} files={files} />);

        const view = within(container);
        /** The value printed next to a metadata label. */
        const meta = (label: string) => view.getByText(label, { selector: 'span' }).nextElementSibling?.textContent;

        // Job metadata
        expect(meta('ID')).toBe(String(job.id));
        expect(meta('Direction')).toBe(DIRECTION_LABELS[job.direction]);
        expect(meta('Started')).toBe(formatDateTime(job.started_at, 'en'));
        expect(meta('Status')).toBe(STATUS_LABELS[job.status]);
        expect(meta('Files changed')).toBe(String(job.files_changed));
        expect(meta('Conflicts')).toBe(String(job.conflicts));
        expect(meta('Errors')).toBe(String(job.errors));

        // File changes: path, side (a column only once any row has one), action
        const showSide = files.some((f) => f.side);
        const rows = bodyRows(container);
        expect(rows).toHaveLength(files.length);
        files.forEach((file, i) => {
          const expected = [file.file_path];
          if (showSide) expected.push(file.side ? SIDE_LABELS[file.side] : '—');
          expected.push(ACTION_LABELS[file.action]);
          expect(rows[i].slice(0, -1)).toEqual(expected);
        });
      }),
      { numRuns: RENDER_RUNS }
    );
  });
});

describe('Jobs name their profile', () => {
  const job: SyncJob = {
    id:            3,
    direction:     'push',
    started_at:    '2026-09-27T08:30:00Z',
    finished_at:   null,
    status:        'completed',
    files_changed: 1,
    conflicts:     0,
    errors:        0,
    profile_slug:  'docs',
    profile_name:  'Dokumente',
  };

  it('the history table and the detail show the profile name', () => {
    const jobs = [job, { ...job, id: 4, profile_slug: null, profile_name: null }];
    const table = render(
      <I18nProvider>
        <JobHistoryTable jobs={jobs} isError={false} refetch={vi.fn()} page={1} onPageChange={vi.fn()} />
      </I18nProvider>
    );
    const rows = within(table.container).getAllByRole('row');
    expect(within(rows[1]).getByText('Dokumente')).toBeInTheDocument();
    expect(within(rows[2]).getByText('—')).toBeInTheDocument();
    table.unmount();

    const detail = render(<I18nProvider><JobDetail job={job} files={[]} /></I18nProvider>);
    expect(within(detail.container).getByText('Dokumente')).toBeInTheDocument();
  });
});
