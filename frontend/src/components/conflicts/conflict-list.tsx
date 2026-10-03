'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { useTranslation } from '@/i18n';
import { formatDateTime } from '@/lib/format';
import type { Conflict, ConflictResolution } from '@/types';
import { PathText } from '@/components/shared/path-text';

interface ConflictAction {
  resolution: ConflictResolution;
  label:      string;
  explain:    string;
  confirm:    string;
}

/** The actions offered per conflict, in display order, with their i18n keys. */
export const CONFLICT_ACTIONS: ConflictAction[] = [
  { resolution: 'keep_local', label: 'conflicts.keepLocal', explain: 'conflicts.keepLocalExplain', confirm: 'conflicts.confirmKeepLocal' },
  { resolution: 'keep_remote', label: 'conflicts.keepRemote', explain: 'conflicts.keepRemoteExplain', confirm: 'conflicts.confirmKeepRemote' },
  { resolution: 'keep_both', label: 'conflicts.keepBoth', explain: 'conflicts.keepBothExplain', confirm: 'conflicts.confirmKeepBoth' },
  { resolution: 'dismiss', label: 'conflicts.dismiss', explain: 'conflicts.dismissExplain', confirm: 'conflicts.confirmDismiss' },
];

/**
 * A conflict a two-way sync found: both versions already exist on both
 * sides. keep_local / keep_remote keep only that version under file_path
 * (the other copy goes to the trash); keep_both closes the record and leaves
 * both files (dismiss would do the same, so it is not offered twice).
 */
export const TWO_WAY_CONFLICT_ACTIONS: ConflictAction[] = [
  { resolution: 'keep_local', label: 'conflicts.keepOnlyLocal', explain: 'conflicts.keepOnlyLocalExplain', confirm: 'conflicts.confirmKeepOnlyLocal' },
  { resolution: 'keep_remote', label: 'conflicts.keepOnlyRemote', explain: 'conflicts.keepOnlyRemoteExplain', confirm: 'conflicts.confirmKeepOnlyRemote' },
  { resolution: 'keep_both', label: 'conflicts.keepBoth', explain: 'conflicts.twoWayKeepBothExplain', confirm: 'conflicts.confirmKeepBoth' },
];

/** Both versions were already kept by a two-way sync (local_kept_as / remote_kept_as). */
export function isTwoWayConflict (conflict: Conflict): boolean {
  return !!(conflict.local_kept_as || conflict.remote_kept_as);
}

export function conflictActions (conflict: Conflict): ConflictAction[] {
  return isTwoWayConflict(conflict) ? TWO_WAY_CONFLICT_ACTIONS : CONFLICT_ACTIONS;
}

interface ConflictListProps {
  conflicts:  Conflict[] | undefined;
  isLoading?: boolean;
  isError?:   boolean;
  onRetry?:   () => void;
  /**
   * Called after the user confirmed an action. keep_local / keep_remote /
   * keep_both change the files on the server (the replaced version goes to
   * .omnisync-trash); dismiss only closes the record.
   */
  onResolve:  (id: number, resolution: ConflictResolution) => void;
  /** The conflict whose action is running, if any. */
  busyId?:    number | null;
}

interface PendingAction {
  conflict:   Conflict;
  resolution: ConflictResolution;
}

export function ConflictList ({ conflicts, isLoading, isError, onRetry, onResolve, busyId }: ConflictListProps) {
  const { t, locale } = useTranslation();
  const [pending, setPending] = useState<PendingAction | null>(null);

  if (isError) {
    return (
      <div className="flex flex-col items-start gap-3" role="alert">
        <p className="text-destructive">{t('conflicts.loadFailed')}</p>
        {onRetry && <Button variant="outline" size="sm" onClick={onRetry}>{t('common.retry')}</Button>}
      </div>
    );
  }

  if (isLoading || !conflicts) {
    return <p className="text-muted-foreground" role="status">{t('common.loading')}</p>;
  }

  const unresolved = conflicts.filter((c) => !c.resolved);

  if (unresolved.length === 0) {
    return (
      <p className="text-muted-foreground">{t('conflicts.noConflicts')}</p>
    );
  }

  const pendingAction = pending
    ? conflictActions(pending.conflict).find((a) => a.resolution === pending.resolution)
    : undefined;

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">{t('conflicts.explain')}</p>
      {unresolved.map((conflict) => (
        <Card key={conflict.id}>
          <CardHeader className="pb-3">
            <CardTitle className="break-all text-base font-medium">
              <PathText>{conflict.file_path}</PathText>
            </CardTitle>
            {conflict.profile_slug && (
              <p className="text-sm text-muted-foreground">
                {t('conflicts.profile')}:{' '}
                <Link href={`/profiles/${conflict.profile_slug}`} className="text-primary hover:underline">
                  {conflict.profile_name ?? conflict.profile_slug}
                </Link>
              </p>
            )}
          </CardHeader>
          <CardContent className="space-y-3">
            {isTwoWayConflict(conflict) && (
              <p className="break-all text-sm" data-testid="two-way-kept">
                {t('conflicts.twoWayKept', {
                  local:  conflict.local_kept_as ?? conflict.file_path,
                  remote: conflict.remote_kept_as ?? conflict.file_path,
                })}
              </p>
            )}
            <div className="flex flex-wrap gap-4 text-sm">
              <div>
                <span className="text-muted-foreground">{t('conflicts.localModified')}:</span>{' '}
                <Badge variant="outline">
                  {formatDateTime(conflict.local_modified, locale)}
                </Badge>
              </div>
              <div>
                <span className="text-muted-foreground">{t('conflicts.remoteModified')}:</span>{' '}
                <Badge variant="outline">
                  {formatDateTime(conflict.remote_modified, locale)}
                </Badge>
              </div>
            </div>
            <div
              className="flex flex-wrap gap-2"
              role="group"
              aria-label={t('conflicts.actionsFor', { path: conflict.file_path })}
            >
              {conflictActions(conflict).map((action) => (
                <Button
                  key={action.resolution}
                  size="sm"
                  variant={action.resolution === 'dismiss' ? 'ghost' : 'outline'}
                  onClick={() => setPending({ conflict, resolution: action.resolution })}
                  disabled={busyId === conflict.id}
                  title={t(action.explain, { path: conflict.file_path })}
                >
                  {t(action.label)}
                </Button>
              ))}
            </div>
          </CardContent>
        </Card>
      ))}

      <Dialog open={pending !== null} onOpenChange={(open) => { if (!open) setPending(null); }}>
        <DialogContent className="sm:max-w-md">
          {pending && pendingAction && (
            <>
              <DialogHeader>
                <DialogTitle className="break-all">
                  {t(pendingAction.confirm, { path: pending.conflict.file_path })}
                </DialogTitle>
                <DialogDescription>{t(pendingAction.explain, { path: pending.conflict.file_path })}</DialogDescription>
              </DialogHeader>
              {pending.conflict.profile_name && (
                <p className="text-sm text-muted-foreground">
                  {t('conflicts.profile')}: {pending.conflict.profile_name}
                </p>
              )}
              <DialogFooter className="gap-2 sm:gap-0">
                <Button variant="outline" onClick={() => setPending(null)}>
                  {t('common.cancel')}
                </Button>
                <Button
                  onClick={() => {
                    onResolve(pending.conflict.id, pending.resolution);
                    setPending(null);
                  }}
                >
                  {t(pendingAction.label)}
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
