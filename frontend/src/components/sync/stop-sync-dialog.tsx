'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { useTranslation } from '@/i18n';
import type { SyncTarget } from '@/hooks/use-profile-sync';

type StopSyncDialogProps = {
  /** The profiles whose running sync would be stopped; null keeps the dialog closed. */
  profiles:  SyncTarget[] | null;
  onCancel:  () => void;
  /** Called with the profiles the dialog named. */
  onConfirm: (profiles: SyncTarget[]) => void;
  isPending: boolean;
};

/**
 * Confirms stopping running syncs (POST /profiles/{slug}/sync/stop for each
 * named profile). The stop cancels the running rclone and records the job
 * as stopped; the watcher and schedule stay, so automatic syncing goes on,
 * which the dialog says. Nothing is sent until the user confirms.
 */
export function StopSyncDialog ({ profiles, onCancel, onConfirm, isPending }: StopSyncDialogProps) {
  const { t } = useTranslation();
  // Keep showing the last profiles while the dialog animates closed.
  const [shown, setShown] = useState(profiles);
  if (profiles && profiles !== shown) setShown(profiles);
  const count = shown?.length ?? 0;

  return (
    <Dialog open={profiles !== null} onOpenChange={(open) => { if (!open) onCancel(); }}>
      <DialogContent className="sm:max-w-md">
        {shown && (
          <>
            <DialogHeader>
              <DialogTitle className="break-words pe-6">
                {count === 1
                  ? t('stopConfirm.title', { name: shown[0].name })
                  : t('stopConfirm.titleMany', { count })}
              </DialogTitle>
              <DialogDescription>{t('stopConfirm.explain', { count })}</DialogDescription>
            </DialogHeader>
            {count > 1 && (
              <ul className="list-disc space-y-1 ps-5 text-sm" data-testid="stop-sync-profiles">
                {shown.map((p) => <li key={p.slug} className="break-words">{p.name}</li>)}
              </ul>
            )}
            <p className="text-sm text-muted-foreground">{t('stopConfirm.keepsSyncing', { count })}</p>
            <DialogFooter>
              <Button variant="outline" onClick={onCancel}>
                {t('common.cancel')}
              </Button>
              <Button variant="destructive" disabled={isPending} onClick={() => onConfirm(shown)}>
                {t('stopConfirm.confirm', { count })}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
