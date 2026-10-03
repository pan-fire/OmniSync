'use client';

import { use } from 'react';
import { useJob, useJobFiles } from '@/hooks/use-jobs';
import { JobDetail } from '@/components/jobs/job-detail';
import { PageHeader } from '@/components/layout/page-header';
import { useTranslation } from '@/i18n';
import { Button } from '@/components/ui/button';

export default function JobDetailPage ({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { t } = useTranslation();
  const jobId = Number(id);
  const job = useJob(jobId);
  const files = useJobFiles(jobId, job.data?.status === 'running');

  if (!Number.isInteger(jobId) || jobId <= 0) {
    return <p className="text-destructive">{t('jobs.notFound')}</p>;
  }

  if (job.isError) {
    return (
      <div className="space-y-3" role="alert">
        <p className="text-destructive">{job.error.message || t('common.error')}</p>
        <Button variant="outline" size="sm" onClick={() => job.refetch()}>{t('common.retry')}</Button>
      </div>
    );
  }

  if (!job.data) {
    return <p className="text-muted-foreground">{t('common.loading')}</p>;
  }

  return (
    <div className="space-y-6">
      <PageHeader title={t('jobs.detail')} />
      <JobDetail job={job.data} files={files.data} filesLoading={files.isLoading} filesError={files.isError} />
    </div>
  );
}
