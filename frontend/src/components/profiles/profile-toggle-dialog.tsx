'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { useTranslation } from '@/i18n';
import { isActiveState } from '@/hooks/use-profile-sync';
import type { ProfileStatus } from '@/types';

export interface ProfileToggleRequest {
  profile: Pick<ProfileStatus, 'slug' | 'name' | 'state' | 'user_paused'>;
  /** The state the switch asks for: true enables, false disables. */
  enabled: boolean;
}

type ProfileToggleDialogProps = {
  /** The profile and the state it would get; null keeps the dialog closed. */
  request:   ProfileToggleRequest | null;
  onCancel:  () => void;
  /** Called with the request the dialog showed. */
  onConfirm: (request: ProfileToggleRequest) => void;
  isPending: boolean;
};

/**
 * Confirms enabling or disabling a profile. POST /profiles/{slug}/disable
 * stops its engine: the watcher and schedule end, and a running sync is
 * cancelled. POST /profiles/{slug}/enable starts the engine again, so its
 * automatic syncs resume; a pause of the user's (user_paused, the only
 * pause a disabled profile reports) still holds. The dialog says which.
 */
export function ProfileToggleDialog ({ request, onCancel, onConfirm, isPending }: ProfileToggleDialogProps) {
  const { t } = useTranslation();
  // Keep showing the last request while the dialog animates closed.
  const [shown, setShown] = useState(request);
  if (request && request !== shown) setShown(request);

  const profile = shown?.profile;

  return (
    <Dialog open={request !== null} onOpenChange={(open) => { if (!open) onCancel(); }}>
      <DialogContent className="sm:max-w-md">
        {shown && profile && (
          <>
            <DialogHeader>
              <DialogTitle className="break-words pe-6">
                {shown.enabled
                  ? t('profiles.enableTitle', { name: profile.name })
                  : t('profiles.disableTitle', { name: profile.name })}
              </DialogTitle>
              <DialogDescription>
                {shown.enabled
                  ? t('profiles.enableExplain')
                  : t('profiles.disableExplain')}
              </DialogDescription>
            </DialogHeader>
            {!shown.enabled && isActiveState(profile.state) && (
              <p className="text-sm font-medium text-destructive">{t('profiles.disableStopsRunning')}</p>
            )}
            {shown.enabled && profile.user_paused && (
              <p className="text-sm text-muted-foreground">{t('profiles.enableStaysPaused')}</p>
            )}
            <p className="text-sm text-muted-foreground">{t('profiles.toggleKeeps')}</p>
            <DialogFooter>
              <Button variant="outline" onClick={onCancel}>
                {t('common.cancel')}
              </Button>
              <Button
                variant={shown.enabled ? 'default' : 'destructive'}
                disabled={isPending}
                onClick={() => onConfirm(shown)}
              >
                {shown.enabled ? t('profiles.enableConfirm') : t('profiles.disableConfirm')}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
