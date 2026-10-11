import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ComponentProps } from 'react';
import { I18nProvider } from '@/i18n';
import { JobHistoryTable } from '@/components/jobs/job-history-table';
import type { SyncJob } from '@/types';

const mockPush = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mockPush }) }));

const JOB: SyncJob = {
  id:            12,
  direction:     'pull',
  started_at:    '2026-09-27T08:30:00Z',
  finished_at:   null,
  status:        'failed',
  files_changed: 0,
  conflicts:     0,
  errors:        1,
  profile_slug:  'docs',
};

function renderTable (props: Partial<ComponentProps<typeof JobHistoryTable>> = {}, locale: 'en' | 'fa' = 'en') {
  const all = {
    jobs:         [JOB],
    isError:      false,
    refetch:      vi.fn(),
    page:         1,
    onPageChange: vi.fn(),
    ...props,
  };
  render(<I18nProvider initialLocale={locale}><JobHistoryTable {...all} /></I18nProvider>);
  return all;
}

beforeEach(() => {
  mockPush.mockReset();
});

describe('JobHistoryTable states', () => {
  it('an error offers a retry', async () => {
    const user = userEvent.setup();
    const { refetch } = renderTable({ isError: true });
    const alert = screen.getByRole('alert');
    expect(within(alert).getByText('Something went wrong')).toBeInTheDocument();
    await user.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it('shows loading before the first page arrives', () => {
    renderTable({ jobs: undefined });
    expect(screen.getByRole('status')).toHaveTextContent('Loading...');
  });

  it('an empty first page says there are no jobs, without paging buttons', () => {
    renderTable({ jobs: [] });
    expect(screen.getByText('No sync jobs yet')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument();
  });

  // An empty later page (the last page was exactly full) must still let the user go back.
  it('an empty later page keeps the paging buttons', async () => {
    const user = userEvent.setup();
    const { onPageChange } = renderTable({ jobs: [], page: 3 });
    expect(screen.queryByText('No sync jobs yet')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Previous' }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });

  it('renders the empty state in Persian', () => {
    renderTable({ jobs: [] }, 'fa');
    expect(screen.getByText('هنوز کاری انجام نشده')).toBeInTheDocument();
  });
});

describe('JobHistoryTable paging and navigation', () => {
  it('Next and Previous ask for the neighbouring pages', async () => {
    const user = userEvent.setup();
    const { onPageChange } = renderTable({ page: 2, hasMore: true });
    await user.click(screen.getByRole('button', { name: 'Next' }));
    expect(onPageChange).toHaveBeenLastCalledWith(3);
    await user.click(screen.getByRole('button', { name: 'Previous' }));
    expect(onPageChange).toHaveBeenLastCalledWith(1);
  });

  it('on the first page without more, both buttons are disabled', () => {
    renderTable();
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
  });

  it('clicking a row opens the job', async () => {
    const user = userEvent.setup();
    renderTable();
    await user.click(screen.getByText('Failed'));
    expect(mockPush).toHaveBeenCalledWith('/jobs/12');
  });

  // The link navigates by itself; the row must not push a second time.
  it('the ID link does not also trigger the row navigation', async () => {
    const user = userEvent.setup();
    renderTable();
    const link = screen.getByRole('link', { name: 'Open job 12' });
    expect(link).toHaveAttribute('href', '/jobs/12');
    await user.click(link);
    expect(mockPush).not.toHaveBeenCalled();
  });

  it('names the profile by its slug when it has no name', () => {
    renderTable();
    expect(screen.getByRole('columnheader', { name: 'Profile' })).toBeInTheDocument();
    expect(screen.getByText('docs')).toBeInTheDocument();
  });

  it('the history of one profile hides the profile column', () => {
    renderTable({ showProfile: false });
    expect(screen.queryByRole('columnheader', { name: 'Profile' })).not.toBeInTheDocument();
    expect(screen.queryByText('docs')).not.toBeInTheDocument();
  });
});
