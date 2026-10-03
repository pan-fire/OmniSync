'use client';

import { useState } from 'react';
import { RotateCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { useTranslation } from '@/i18n';
import { useResyncProfile } from '@/hooks/use-profile-sync';

interface ResyncActionProps {
  slug:       string;
  name:       string;
  /** A resync is required: a primary button instead of an outline one. */
  prominent?: boolean;
  disabled?:  boolean;
  size?:      'sm' | 'default';
}

/**
 * "Resync" of a two-way profile, behind a confirmation that explains it:
 * the union of both folders, nothing deleted, the older of two differing
 * versions goes to the trash. Nothing is sent until the user confirms.
 */
export function ResyncAction ({ slug, name, prominent, disabled, size = 'default' }: ResyncActionProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const resync = useResyncProfile(slug, name);

  return (
    <>
      <Button
        size={size}
        variant={prominent ? 'default' : 'outline'}
        onClick={() => setOpen(true)}
        disabled={disabled || resync.isPending}
      >
        <RotateCcw className="h-4 w-4" aria-hidden="true" />
        {t('twoWay.resync')}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t('twoWay.resyncTitle', { name })}</DialogTitle>
            <DialogDescription>{t('twoWay.resyncExplain')}</DialogDescription>
          </DialogHeader>
          <ul className="list-disc space-y-1 ps-5 text-sm text-muted-foreground">
            <li>{t('twoWay.resyncUnion')}</li>
            <li>{t('twoWay.resyncNewer')}</li>
            <li>{t('twoWay.resyncAfter')}</li>
          </ul>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button variant="outline" onClick={() => setOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              onClick={() => {
                resync.mutate();
                setOpen(false);
              }}
            >
              {t('twoWay.confirmResync')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
