'use client';

import { useState } from 'react';
import { JOBS_PAGE_SIZE, useJobs } from '@/hooks/use-jobs';
import { JobHistoryTable } from '@/components/jobs/job-history-table';
import { PageHelp } from '@/components/layout/page-help';
import { PageHeader } from '@/components/layout/page-header';
import { useTranslation } from '@/i18n';

export default function JobsPage () {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const jobs = useJobs(page);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('jobs.title')}
        actions={<PageHelp pageKey="jobs" />}
      />
      <JobHistoryTable
        jobs={jobs.data}
        isError={jobs.isError}
        refetch={jobs.refetch}
        page={page}
        hasMore={(jobs.data?.length ?? 0) === JOBS_PAGE_SIZE}
        onPageChange={setPage}
      />
    </div>
  );
}
