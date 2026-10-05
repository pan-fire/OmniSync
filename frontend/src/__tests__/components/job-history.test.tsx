import { describe, it, expect, vi } from 'vitest';
import fc from 'fast-check';
import { render, screen, within } from '@testing-library/react';
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

describe('Job history table rendering completeness', () => {
  it('for any non-empty SyncJob array, the rendered table contains all required fields for each job', () => {
    fc.assert(
      fc.property(nonEmptyJobsArb, (jobs: SyncJob[]) => {
        const { unmount, container } = render(
          <I18nProvider>
            <JobHistoryTable
              jobs={jobs}
              isError={false}
              refetch={() => {}}
              page={1}
              onPageChange={() => {}}
            />
          </I18nProvider>
        );

        const view = within(container);

        for (const job of jobs) {
          // ID, as a link to the job (keyboard accessible)
          expect(view.getAllByText(String(job.id)).length).toBeGreaterThanOrEqual(1);
          expect(container.querySelector(`a[href="/jobs/${job.id}"]`)).toHaveAttribute('aria-label', `Open job ${job.id}`);
          // direction (translated)
          expect(view.getAllByText(DIRECTION_LABELS[job.direction]).length).toBeGreaterThanOrEqual(1);
          // started_at, formatted in the app locale
          expect(view.getAllByText(formatDateTime(job.started_at, 'en')).length).toBeGreaterThanOrEqual(1);
          // status (translated)
          expect(view.getAllByText(STATUS_LABELS[job.status]).length).toBeGreaterThanOrEqual(1);
          // files_changed
          expect(view.getAllByText(String(job.files_changed)).length).toBeGreaterThanOrEqual(1);
          // conflicts
          expect(view.getAllByText(String(job.conflicts)).length).toBeGreaterThanOrEqual(1);
          // errors
          expect(view.getAllByText(String(job.errors)).length).toBeGreaterThanOrEqual(1);
        }

        unmount();
      }),
      { numRuns: 100 }
    );
  });
});

describe('Job detail rendering completeness', () => {
  it('for any SyncJob and FileChange array, the rendered view contains all metadata and file change fields', () => {
    fc.assert(
      fc.property(syncJobArb, nonEmptyFilesArb, (job: SyncJob, files: FileChange[]) => {
        const { unmount, container } = render(
          <I18nProvider>
            <JobDetail job={job} files={files} />
          </I18nProvider>
        );

        const view = within(container);

        // Job metadata
        expect(view.getAllByText(String(job.id)).length).toBeGreaterThanOrEqual(1);
        expect(view.getAllByText(DIRECTION_LABELS[job.direction]).length).toBeGreaterThanOrEqual(1);
        expect(view.getAllByText(formatDateTime(job.started_at, 'en')).length).toBeGreaterThanOrEqual(1);
        expect(view.getAllByText(STATUS_LABELS[job.status]).length).toBeGreaterThanOrEqual(1);
        expect(view.getAllByText(String(job.files_changed)).length).toBeGreaterThanOrEqual(1);
        expect(view.getAllByText(String(job.conflicts)).length).toBeGreaterThanOrEqual(1);
        expect(view.getAllByText(String(job.errors)).length).toBeGreaterThanOrEqual(1);

        // File changes
        for (const file of files) {
          expect(view.getAllByText(file.file_path).length).toBeGreaterThanOrEqual(1);
          expect(view.getAllByText(ACTION_LABELS[file.action]).length).toBeGreaterThanOrEqual(1);
          if (file.side) {
            expect(view.getAllByText(SIDE_LABELS[file.side]).length).toBeGreaterThanOrEqual(1);
          }
        }

        unmount();
      }),
      { numRuns: 100 }
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

describe('Jobs that left files alone', () => {
  const job: SyncJob = {
    id:            7,
    direction:     'pull',
    started_at:    '2026-10-05T10:00:00Z',
    finished_at:   '2026-10-05T10:01:00Z',
    status:        'completed',
    files_changed: 2,
    conflicts:     0,
    errors:        0,
    warnings:      [{ code: 'symlink_kept', count: 1, paths: ['clash.txt'] },
      { code: 'name_not_utf8', count: 2, paths: ['bad\\xff.txt', 'dir\\xfe/'] }],
  };

  it('say "Completed with warnings" and list the warnings in the detail', () => {
    render(<I18nProvider><JobDetail job={job} files={[]} /></I18nProvider>);
    expect(screen.getByText('Completed with warnings')).toBeInTheDocument();
    const list = screen.getByTestId('sync-warnings');
    expect(list).toHaveTextContent('1 remote file or folder was not synced because a local symbolic link has its name.');
    expect(list).toHaveTextContent('2 local names are not valid UTF-8.');
    expect(within(list).getByText('bad\\xff.txt')).toBeInTheDocument();
    expect(within(list).getByText('dir\\xfe/')).toBeInTheDocument();
  });

  it('show the status in the history table; without warnings a job is plainly completed', () => {
    render(
      <I18nProvider>
        <JobHistoryTable jobs={[job, { ...job, id: 8, warnings: [] }, { ...job, id: 9, status: 'failed' }]}
          isError={false} refetch={() => {}} page={1} onPageChange={() => {}} />
      </I18nProvider>
    );
    expect(screen.getAllByText('Completed with warnings')).toHaveLength(1);
    expect(screen.getByText('Completed')).toBeInTheDocument();
    expect(screen.getByText('Failed')).toBeInTheDocument();
  });

  it('a detail without warnings has no warnings card', () => {
    render(<I18nProvider><JobDetail job={{ ...job, warnings: undefined }} files={[]} /></I18nProvider>);
    expect(screen.queryByTestId('sync-warnings')).toBeNull();
    expect(screen.getByText('Completed')).toBeInTheDocument();
  });
});
