'use client';

import { useState } from 'react';
import { FolderSearch, RotateCcw, ShieldAlert, ShieldCheck } from 'lucide-react';
import { useSnapshots } from '@/hooks/use-backups';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useTranslation } from '@/i18n';
import { formatBytes, formatDateTime } from '@/lib/format';
import { RestoreDialog } from './restore-dialog';
import { SnapshotBrowser } from './snapshot-browser';
import type { BackupJob, Snapshot, VerifyStatus } from '@/types';

interface BackupHistoryProps {
  profileSlug: string;
  targetId:    number;
  jobs?:       BackupJob[];
}

function StatusBadge ({ status }: { status: string }) {
  const { t } = useTranslation();
  const label = t(`backups.status.${status}`);
  switch (status) {
    case 'completed':
      return <Badge className="bg-green-500/10 text-green-700 dark:text-green-400">{label}</Badge>;
    case 'failed':
      return <Badge variant="destructive">{label}</Badge>;
    case 'running':
      return <Badge className="bg-blue-500/10 text-blue-700 dark:text-blue-400">{label}</Badge>;
    default:
      return <Badge variant="outline">{label}</Badge>;
  }
}

/** Outcome of the verification after a backup; the summary is the tooltip. */
export function VerifyBadge ({ status, message }: { status: VerifyStatus; message: string | null }) {
  const { t } = useTranslation();
  if (status === 'failed') {
    return (
      <Badge variant="destructive" className="gap-1" title={message ?? undefined}>
        <ShieldAlert className="h-3 w-3" aria-hidden="true" /> {t('backups.verifyFailed')}
      </Badge>
    );
  }
  return (
    <Badge className="gap-1 bg-green-500/10 text-green-700 dark:text-green-400" title={message ?? undefined}>
      <ShieldCheck className="h-3 w-3" aria-hidden="true" /> {t('backups.verified')}
    </Badge>
  );
}

export function BackupHistory ({ profileSlug, targetId, jobs }: BackupHistoryProps) {
  const { t, locale } = useTranslation();
  const snapshots = useSnapshots(profileSlug, targetId);
  const [restoreSnap, setRestoreSnap] = useState<Snapshot | null>(null);
  const [browseSnap, setBrowseSnap] = useState<Snapshot | null>(null);
  const snapshotList = snapshots.data ?? [];

  return (
    <div className="space-y-3 border-t pt-2">
      {snapshots.isLoading && (
        <p className="py-2 text-xs text-muted-foreground" role="status">{t('common.loading')}</p>
      )}
      {snapshots.isError && (
        <p className="py-2 text-xs text-destructive" role="alert">
          {t('backups.snapshotsFailed')}: {snapshots.error.message}
        </p>
      )}

      {/* Snapshots */}
      {snapshotList.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-medium text-muted-foreground">{t('backups.snapshots')}</h4>
          <ScrollArea className="max-h-40">
            <div className="space-y-1.5">
              {snapshotList.map((snap) => (
                <div
                  key={snap.snapshot_id}
                  className="flex items-center gap-1 rounded-md bg-muted/50 px-3 py-2 text-xs"
                >
                  <div className="me-auto flex items-center gap-2">
                    <span>{formatDateTime(snap.created_at, locale)}</span>
                    {snap.latest && <Badge variant="outline">{t('backups.snapshotLatest')}</Badge>}
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6 px-2"
                    onClick={() => setBrowseSnap(snap)}
                    title={t('backups.browseHint')}
                  >
                    <FolderSearch className="h-3 w-3" aria-hidden="true" />
                    {t('backups.browse')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6 px-2"
                    onClick={() => setRestoreSnap(snap)}
                  >
                    <RotateCcw className="h-3 w-3" aria-hidden="true" />
                    {t('backups.restore')}
                  </Button>
                </div>
              ))}
            </div>
          </ScrollArea>
        </div>
      )}

      {/* Recent jobs */}
      {jobs && jobs.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-medium text-muted-foreground">{t('backups.recentJobs')}</h4>
          <ScrollArea className="max-h-40">
            <div className="space-y-1.5">
              {jobs.map((job) => (
                <div
                  key={job.id}
                  className="flex items-center gap-3 rounded-md bg-muted/50 px-3 py-2 text-xs"
                >
                  <StatusBadge status={job.status} />
                  <span className="text-muted-foreground">
                    {formatDateTime(job.started_at, locale)}
                  </span>
                  {job.size_bytes != null && <span>{formatBytes(job.size_bytes, locale)}</span>}
                  {job.error_message && (
                    <span
                      className="max-w-48 truncate text-destructive"
                      title={job.error_code ? `${job.error_message} (${job.error_code})` : job.error_message}
                    >
                      {job.error_message}
                    </span>
                  )}
                  {job.verify_status && (
                    <VerifyBadge status={job.verify_status} message={job.verify_message ?? null} />
                  )}
                </div>
              ))}
            </div>
          </ScrollArea>
        </div>
      )}

      {!snapshots.isLoading && !snapshots.isError && snapshotList.length === 0 && (!jobs || jobs.length === 0) && (
        <p className="py-2 text-xs text-muted-foreground">{t('backups.noHistory')}</p>
      )}

      {browseSnap && (
        <SnapshotBrowser
          profileSlug={profileSlug}
          targetId={targetId}
          snapshot={browseSnap}
          open={!!browseSnap}
          onOpenChange={(open) => { if (!open) setBrowseSnap(null); }}
        />
      )}

      {restoreSnap && (
        <RestoreDialog
          profileSlug={profileSlug}
          targetId={targetId}
          snapshot={restoreSnap}
          open={!!restoreSnap}
          onOpenChange={(open) => { if (!open) setRestoreSnap(null); }}
        />
      )}
    </div>
  );
}
