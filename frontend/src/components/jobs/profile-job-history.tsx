'use client';

import { useState } from 'react';
import { JOBS_PAGE_SIZE, useJobs } from '@/hooks/use-jobs';
import { JobHistoryTable } from '@/components/jobs/job-history-table';

/** The sync jobs of one profile, newest first, a page at a time. */
export function ProfileJobHistory ({ slug }: { slug: string }) {
  const [page, setPage] = useState(1);
  const jobs = useJobs(page, slug);

  return (
    <JobHistoryTable
      jobs={jobs.data}
      isError={jobs.isError}
      refetch={jobs.refetch}
      page={page}
      hasMore={(jobs.data?.length ?? 0) === JOBS_PAGE_SIZE}
      onPageChange={setPage}
      showProfile={false}
    />
  );
}
