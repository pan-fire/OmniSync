'use client';

import { useState } from 'react';
import { ArrowRightLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { useTranslation } from '@/i18n';
import { useUpdateProfile } from '@/hooks/use-profiles';

/**
 * The one-click switch of a mirror profile to two-way
 * (PUT /profiles/{slug} {"sync_mode": "two_way"}), behind a confirmation
 * that says the next sync is a resync that deletes nothing.
 */
export function SwitchToTwoWayButton ({ slug, name, variant = 'outline' }: {
  slug:     string;
  name:     string;
  variant?: 'outline' | 'default';
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const updateProfile = useUpdateProfile(slug);

  return (
    <>
      <Button size="sm" variant={variant} onClick={() => setOpen(true)} disabled={updateProfile.isPending}>
        <ArrowRightLeft className="h-4 w-4" aria-hidden="true" />
        {t('twoWay.switch')}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t('twoWay.switchTitle', { name })}</DialogTitle>
            <DialogDescription>{t('syncMode.two_way.desc')}</DialogDescription>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">{t('twoWay.switchFirstRun')}</p>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button variant="outline" onClick={() => setOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              onClick={() => {
                updateProfile.mutate({ sync_mode: 'two_way' });
                setOpen(false);
              }}
            >
              {t('twoWay.confirmSwitch')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

/**
 * A short hint for a mirror profile whose mirror-mode note was hidden:
 * two-way keeps changes from both sides, with the switch next to it.
 */
export function SwitchToTwoWayHint ({ slug, name }: { slug: string; name: string }) {
  const { t } = useTranslation();

  return (
    <div
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-dashed p-3 text-sm"
      data-testid="switch-to-two-way"
    >
      <p className="text-muted-foreground">{t('twoWay.switchHint')}</p>
      <SwitchToTwoWayButton slug={slug} name={name} />
    </div>
  );
}
