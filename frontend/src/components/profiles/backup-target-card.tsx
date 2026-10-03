'use client';

import { useState } from 'react';
import { useMutationState } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Play, Pencil, ChevronDown, ChevronUp, AlertTriangle, Clock, Trash2, Loader2, Lock } from 'lucide-react';
import { useTranslation } from '@/i18n';
import { formatRelativeTime } from '@/lib/format';
import { runBackupKey } from '@/hooks/use-backups';
import type { BackupTarget } from '@/types';
import { BackupHistory, VerifyBadge } from './backup-history';

interface BackupTargetCardProps {
  target:          BackupTarget;
  profileSlug:     string;
  isRunning?:      boolean;
  onRunNow:        (id: number) => void;
  onEdit:          (target: BackupTarget) => void;
  onToggleEnabled: (id: number, enabled: boolean) => void;
  /** DELETE /profiles/{slug}/backups/{id}; asks for confirmation first. */
  onDelete?:       (id: number) => void;
  isDeleting?:     boolean;
}

export function BackupTargetCard ({
  target,
  profileSlug,
  isRunning,
  onRunNow,
  onEdit,
  onToggleEnabled,
  onDelete,
  isDeleting,
}: BackupTargetCardProps) {
  const { t, locale } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  // POST .../run answers only when the backup has finished, and the job is
  // recorded only then, so a pending request is what "running" looks like.
  const runsInFlight = useMutationState({
    filters: {
      mutationKey: runBackupKey(profileSlug),
      status:      'pending',
      predicate:   (m) => m.state.variables === target.id,
    },
  });
  const runningNow = isRunning || runsInFlight.length > 0;

  const statusColor = target.last_backup_status === 'completed'
    ? 'bg-green-500/10 text-green-700 dark:text-green-400'
    : target.last_backup_status === 'failed'
      ? 'bg-red-500/10 text-red-700 dark:text-red-400'
      : 'bg-muted text-muted-foreground';

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-base">{target.name}</CardTitle>
          <Badge variant="outline">{t(`backups.types.${target.target_type}`)}</Badge>
          <Badge variant="secondary">{t(`backups.modes.${target.backup_mode}.label`)}</Badge>
          {target.encrypted && (
            <Badge variant="outline" className="gap-1" title={t('backups.encryptedHint')}>
              <Lock className="h-3 w-3" aria-hidden="true" /> {t('backups.encrypted')}
            </Badge>
          )}
          {target.last_liveness_ok === false && (
            <Badge variant="destructive" className="gap-1" title={target.last_liveness_error ?? undefined}>
              <AlertTriangle className="h-3 w-3" aria-hidden="true" /> {t('backups.unreachable')}
            </Badge>
          )}
          {target.overdue && (
            <Badge variant="destructive" className="gap-1" title={t('backups.overdueHint')}>
              <Clock className="h-3 w-3" aria-hidden="true" /> {t('backups.overdue')}
            </Badge>
          )}
        </div>
        <Switch
          checked={target.enabled}
          onCheckedChange={(checked) => onToggleEnabled(target.id, checked)}
          aria-label={t('backups.toggleEnabled', { name: target.name })}
        />
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="truncate font-mono text-sm text-muted-foreground" dir="ltr" title={target.target_path}>
          {target.target_path}
        </div>

        <div className="flex flex-wrap items-center gap-4 text-sm">
          <div className="flex items-center gap-1.5">
            <span className="text-muted-foreground">{t('backups.lastBackup')}:</span>
            <Badge className={statusColor}>
              {target.last_backup_status
                ? t(`backups.status.${target.last_backup_status}`)
                : t('backups.status.none')}
            </Badge>
            <span>{formatRelativeTime(target.last_backup_at, locale, t('backups.never'))}</span>
            {target.last_verify_status && (
              <VerifyBadge status={target.last_verify_status} message={target.last_verify_message} />
            )}
          </div>
          <div>
            <span className="text-muted-foreground">{t('backups.next')}:</span>{' '}
            {formatRelativeTime(target.next_scheduled_at, locale, t('backups.notScheduled'))}
          </div>
        </div>

        {target.last_verify_status === 'failed' && target.last_verify_message && (
          <p className="text-xs text-destructive" role="alert">
            {t('backups.verifyFailedDetail', { reason: target.last_verify_message })}
          </p>
        )}

        <div className="flex items-center gap-4 text-xs text-muted-foreground">
          <span>{t('backups.retentionShort', { count: target.retention_days })}</span>
          <span>{t('backups.keepLastShort', { count: target.keep_last })}</span>
          <span>{t('backups.frequencyShort', { count: target.frequency_hours })}</span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => onRunNow(target.id)}
            disabled={runningNow || !target.enabled}
          >
            {runningNow
              ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
              : <Play className="h-3 w-3" aria-hidden="true" />}
            {runningNow ? t('backups.running') : t('backups.runNow')}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onEdit(target)}>
            <Pencil className="h-3 w-3" aria-hidden="true" />
            {t('backups.edit')}
          </Button>
          {onDelete && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setConfirmDelete(true)}
              disabled={isDeleting || runningNow}
            >
              <Trash2 className="h-3 w-3 text-destructive" aria-hidden="true" />
              {t('common.delete')}
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setExpanded(!expanded)}
            className="ms-auto"
            aria-expanded={expanded}
          >
            {expanded ? <ChevronUp className="h-4 w-4" aria-hidden="true" /> : <ChevronDown className="h-4 w-4" aria-hidden="true" />}
            {t('backups.history')}
          </Button>
        </div>

        {expanded && (
          <BackupHistory profileSlug={profileSlug} targetId={target.id} />
        )}
      </CardContent>

      {onDelete && (
        <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
          <DialogContent className="sm:max-w-sm">
            <DialogHeader>
              <DialogTitle>{t('backups.deleteTitle')}</DialogTitle>
              <DialogDescription>{t('backups.confirmDelete', { name: target.name })}</DialogDescription>
            </DialogHeader>
            <DialogFooter className="gap-2 sm:gap-0">
              <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
                {t('common.cancel')}
              </Button>
              <Button
                variant="destructive"
                disabled={isDeleting}
                onClick={() => { onDelete(target.id); setConfirmDelete(false); }}
              >
                {t('common.delete')}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </Card>
  );
}
