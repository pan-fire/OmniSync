'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useTranslation } from '@/i18n';
import { formatBytes, formatDateTime } from '@/lib/format';
import { JobStatusBadge } from './job-history-table';
import type { SyncJob, FileChange } from '@/types';
import { PathText } from '@/components/shared/path-text';
import { SyncWarnings } from '@/components/sync/sync-warnings';

interface JobDetailProps {
  job:           SyncJob;
  files:         FileChange[] | undefined;
  filesLoading?: boolean;
  filesError?:   boolean;
}

export function JobDetail ({ job, files, filesLoading, filesError }: JobDetailProps) {
  const { t, locale } = useTranslation();

  let fileContent;
  if (filesError) {
    fileContent = <p className="text-destructive" role="alert">{t('common.error')}</p>;
  } else if (filesLoading) {
    fileContent = <p className="text-muted-foreground" role="status">{t('common.loading')}</p>;
  } else if (!files || files.length === 0) {
    fileContent = <p className="text-muted-foreground">{t('common.noData')}</p>;
  } else {
    // Older jobs recorded no side; the column appears once any row has one.
    const showSide = files.some((f) => f.side);
    fileContent = (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t('jobs.filePath')}</TableHead>
            {showSide && <TableHead>{t('jobs.side')}</TableHead>}
            <TableHead>{t('jobs.action')}</TableHead>
            <TableHead>{t('jobs.size')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {files.map((file) => (
            <TableRow key={file.id}>
              <TableCell className="break-all"><PathText>{file.file_path}</PathText></TableCell>
              {showSide && (
                <TableCell>{file.side ? t(`jobs.sides.${file.side}`) : '—'}</TableCell>
              )}
              <TableCell>{t(`jobs.actions.${file.action}`)}</TableCell>
              <TableCell>{formatBytes(file.size_bytes, locale)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    );
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{t('jobs.detail')}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-2 text-sm">
            <span className="text-muted-foreground">{t('jobs.id')}</span>
            <span>{job.id}</span>
            <span className="text-muted-foreground">{t('jobs.profile')}</span>
            <span>{job.profile_name ?? job.profile_slug ?? '—'}</span>
            <span className="text-muted-foreground">{t('jobs.direction')}</span>
            <span>{t(`jobs.directions.${job.direction}`)}</span>
            <span className="text-muted-foreground">{t('jobs.startedAt')}</span>
            <span>{formatDateTime(job.started_at, locale)}</span>
            <span className="text-muted-foreground">{t('jobs.finishedAt')}</span>
            <span>{formatDateTime(job.finished_at, locale)}</span>
            <span className="text-muted-foreground">{t('jobs.status')}</span>
            <span>
              <JobStatusBadge job={job} />
            </span>
            <span className="text-muted-foreground">{t('jobs.filesChanged')}</span>
            <span>{job.files_changed}</span>
            <span className="text-muted-foreground">{t('jobs.conflicts')}</span>
            <span>{job.conflicts}</span>
            <span className="text-muted-foreground">{t('jobs.errors')}</span>
            <span>{job.errors}</span>
          </div>
        </CardContent>
      </Card>

      {job.warnings && job.warnings.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>{t('syncWarnings.title')}</CardTitle>
          </CardHeader>
          <CardContent>
            <SyncWarnings warnings={job.warnings} />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>{t('jobs.fileChanges')}</CardTitle>
        </CardHeader>
        <CardContent>{fileContent}</CardContent>
      </Card>
    </div>
  );
}
