'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { PathText } from '@/components/shared/path-text';
import { useTranslation } from '@/i18n';
import type { Profile } from '@/types';

type ProfileDeleteDialogProps = {
  /** The profile to delete; null keeps the dialog closed. */
  profile:   Pick<Profile, 'name' | 'local_dir' | 'remote_dir'> | null;
  onCancel:  () => void;
  onConfirm: () => void;
  isPending: boolean;
};

/**
 * Confirms deleting a profile. DELETE /profiles/{slug} removes the profile
 * and its job history, conflicts, manual flags, backup targets and two-way
 * state from OmniSync only: no file in either folder is touched, which the
 * dialog says plainly.
 */
export function ProfileDeleteDialog ({ profile, onCancel, onConfirm, isPending }: ProfileDeleteDialogProps) {
  const { t } = useTranslation();
  // Keep showing the last profile while the dialog animates closed.
  const [shown, setShown] = useState(profile);
  if (profile && profile !== shown) setShown(profile);

  return (
    <Dialog open={profile !== null} onOpenChange={(open) => { if (!open) onCancel(); }}>
      <DialogContent className="sm:max-w-md">
        {shown && (
          <>
            <DialogHeader>
              <DialogTitle className="break-words pe-6">{t('profiles.deleteTitle', { name: shown.name })}</DialogTitle>
              <DialogDescription>{t('profiles.deleteRemoves')}</DialogDescription>
            </DialogHeader>
            <div className="space-y-2 rounded-md border bg-muted/40 p-3 text-sm" data-testid="profile-delete-keeps">
              <p className="font-medium">{t('profiles.deleteKeepsFiles')}</p>
              <ul className="list-disc space-y-1 ps-5 text-muted-foreground">
                <li className="break-all"><PathText>{shown.local_dir}</PathText></li>
                <li className="break-all"><PathText>{shown.remote_dir}</PathText></li>
              </ul>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={onCancel}>
                {t('common.cancel')}
              </Button>
              <Button variant="destructive" disabled={isPending} onClick={onConfirm}>
                {t('profiles.deleteConfirm')}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
