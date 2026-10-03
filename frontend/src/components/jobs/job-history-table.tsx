'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { useTranslation } from '@/i18n';
import { formatDateTime } from '@/lib/format';
import type { SyncJob, JobStatus } from '@/types';

interface JobHistoryTableProps {
  jobs:         SyncJob[] | undefined;
  isError:      boolean;
  refetch:      () => void;
  page:         number;
  /** Whether a next page may exist (the last page came back full). */
  hasMore?:     boolean;
  onPageChange: (page: number) => void;
  /** Off for the history of one profile, where every row has the same. */
  showProfile?: boolean;
}

export function jobStatusVariant (status: JobStatus): 'default' | 'secondary' | 'destructive' {
  switch (status) {
    case 'running':
      return 'default';
    case 'failed':
      return 'destructive';
    default:
      return 'secondary';
  }
}

export function JobHistoryTable ({
  jobs,
  isError,
  refetch,
  page,
  hasMore = false,
  onPageChange,
  showProfile = true,
}: JobHistoryTableProps) {
  const { t, locale } = useTranslation();
  const router = useRouter();

  if (isError) {
    return (
      <div className="flex flex-col items-center gap-4 py-8" role="alert">
        <p className="text-destructive">{t('common.error')}</p>
        <Button variant="outline" onClick={() => refetch()}>
          {t('common.retry')}
        </Button>
      </div>
    );
  }

  if (!jobs) {
    return <p className="text-muted-foreground py-4" role="status">{t('common.loading')}</p>;
  }

  if (jobs.length === 0 && page <= 1) {
    return <p className="text-muted-foreground py-4">{t('jobs.noJobs')}</p>;
  }

  return (
    <div className="space-y-4">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t('jobs.id')}</TableHead>
            {showProfile && <TableHead>{t('jobs.profile')}</TableHead>}
            <TableHead>{t('jobs.direction')}</TableHead>
            <TableHead>{t('jobs.startedAt')}</TableHead>
            <TableHead>{t('jobs.status')}</TableHead>
            <TableHead>{t('jobs.filesChanged')}</TableHead>
            <TableHead>{t('jobs.conflicts')}</TableHead>
            <TableHead>{t('jobs.errors')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {jobs.map((job) => (
            // The whole row is clickable with the mouse; keyboard and
            // screen-reader users get a real link in the ID cell.
            <TableRow
              key={job.id}
              className="cursor-pointer"
              onClick={() => router.push(`/jobs/${job.id}`)}
            >
              <TableCell>
                <Link
                  href={`/jobs/${job.id}`}
                  className="font-medium text-primary hover:underline"
                  aria-label={t('jobs.openJob', { id: job.id })}
                  onClick={(e) => e.stopPropagation()}
                >
                  {job.id}
                </Link>
              </TableCell>
              {showProfile && <TableCell>{job.profile_name ?? job.profile_slug ?? '—'}</TableCell>}
              <TableCell>{t(`jobs.directions.${job.direction}`)}</TableCell>
              <TableCell>
                <time dateTime={job.started_at}>{formatDateTime(job.started_at, locale)}</time>
              </TableCell>
              <TableCell>
                <Badge variant={jobStatusVariant(job.status)}>
                  {t(`jobs.statuses.${job.status}`)}
                </Badge>
              </TableCell>
              <TableCell>{job.files_changed}</TableCell>
              <TableCell>{job.conflicts}</TableCell>
              <TableCell>{job.errors}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <div className="flex items-center justify-end gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          {t('jobs.previous')}
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(page + 1)}
          disabled={!hasMore}
        >
          {t('jobs.next')}
        </Button>
      </div>
    </div>
  );
}
