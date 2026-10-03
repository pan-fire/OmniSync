'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useDeleteRemote, useRemoteDependencies } from '@/hooks/use-remotes';
import { useTranslation } from '@/i18n';
import { ApiError } from '@/types';

interface RemoteDeleteDialogProps {
  /** The remote to delete; null while the dialog is closed. */
  name:         string | null;
  open:         boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Confirms DELETE /remotes/{name}. The dependencies are loaded first; when
 * profiles or backup targets use the remote (or the server answers 409),
 * they are listed and only an explicit "Delete anyway" sends ?force=true.
 * A forced delete removes just the rclone configuration: the profiles and
 * targets stay as they are and fail until they point at another remote.
 */
export function RemoteDeleteDialog ({ name, open, onOpenChange }: RemoteDeleteDialogProps) {
  const { t } = useTranslation();
  const remote = name ?? '';
  const deps = useRemoteDependencies(remote, open);
  const deleteRemote = useDeleteRemote();
  // The server refused a plain delete with 409 (something uses the remote).
  const [conflict, setConflict] = useState(false);

  const profiles = deps.data?.profiles ?? [];
  const targets = deps.data?.backup_targets ?? [];
  const hasDeps = profiles.length > 0 || targets.length > 0;
  const needsForce = hasDeps || conflict;

  const close = () => {
    setConflict(false);
    onOpenChange(false);
  };

  const handleDelete = (force: boolean) => {
    if (!remote) return;
    deleteRemote.mutate(
      { name: remote, force },
      {
        onSuccess: close,
        onError:   (error) => {
          if (error instanceof ApiError && error.status === 409) {
            setConflict(true);
            deps.refetch();
          }
        },
      }
    );
  };

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? onOpenChange(true) : close())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('remotes.deleteTitle')}</DialogTitle>
          <DialogDescription>{t('remotes.deleteQuestion', { name: remote })}</DialogDescription>
        </DialogHeader>

        {deps.isLoading && (
          <p className="text-sm text-muted-foreground" role="status">{t('remotes.checkingDependencies')}</p>
        )}
        {deps.isError && !conflict && (
          <p className="text-sm text-muted-foreground">{t('remotes.dependenciesFailed')}</p>
        )}

        {needsForce && (
          <div className="space-y-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm" role="alert">
            {hasDeps
              ? (
              <>
                <p className="font-medium">{t('remotes.stillUsedBy')}</p>
                <ul className="list-disc space-y-1 ps-5">
                  {profiles.map((p) => (
                    <li key={`p-${p.slug}`}>{t('remotes.dependencyProfile', { name: p.name, slug: p.slug })}</li>
                  ))}
                  {targets.map((bt) => (
                    <li key={`b-${bt.target_id}`}>
                      {t('remotes.dependencyBackupTarget', { name: bt.target_name, profile: bt.profile_slug })}
                    </li>
                  ))}
                </ul>
              </>
                )
              : <p>{t('remotes.conflictUnlisted')}</p>}
            <p>{t('remotes.forceExplanation')}</p>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={close}>
            {t('common.cancel')}
          </Button>
          {needsForce
            ? (
            <Button variant="destructive" onClick={() => handleDelete(true)} disabled={deleteRemote.isPending}>
              {t('remotes.deleteAnyway')}
            </Button>
              )
            : (
            <Button
              variant="destructive"
              onClick={() => handleDelete(false)}
              disabled={deleteRemote.isPending || deps.isLoading}
            >
              {t('common.delete')}
            </Button>
              )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
