'use client';

import { useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { useTranslation } from '@/i18n';
import type { FileDiff, FileAction } from '@/types';

interface ConflictDialogProps {
  file:       FileDiff | null;
  /** Conflicts still queued after this one (batch and folder actions). */
  remaining?: number;
  onResolve:  (action: FileAction, applyToRemaining: boolean) => void;
  /** Cancel: this and any remaining queued conflicts are left untouched. */
  onClose:    () => void;
}

export function ConflictDialog ({ file, remaining = 0, onResolve, onClose }: ConflictDialogProps) {
  const { t } = useTranslation();
  const [applyToRemaining, setApplyToRemaining] = useState(false);

  const resolve = (action: FileAction) => {
    onResolve(action, applyToRemaining);
    setApplyToRemaining(false);
  };

  return (
    <Dialog
      open={file !== null}
      onOpenChange={(open) => {
        if (!open) {
          setApplyToRemaining(false);
          onClose();
        }
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-red-600 dark:text-red-500" aria-hidden="true" />
            {t('granular.conflictTitle')}
          </DialogTitle>
          <DialogDescription>
            {file ? t('granular.conflictDesc', { path: file.path }) : ''}
          </DialogDescription>
        </DialogHeader>
        {remaining > 0 && (
          <p className="text-sm text-muted-foreground">
            {t('granular.conflictsRemaining', { count: remaining })}
          </p>
        )}
        <div className="space-y-2">
          <Button className="w-full justify-start" variant="outline" onClick={() => resolve('push')}>
            {t('granular.keepLocal')}
          </Button>
          <Button className="w-full justify-start" variant="outline" onClick={() => resolve('pull')}>
            {t('granular.keepRemote')}
          </Button>
          <Button className="w-full justify-start" variant="outline" onClick={() => resolve('keep_both')}>
            {t('granular.keepBoth')}
          </Button>
          <Button className="w-full justify-start" variant="outline" onClick={() => resolve('manual')}>
            {t('granular.markManual')}
          </Button>
        </div>
        {remaining > 0 && (
          <div className="flex items-center gap-2">
            <Checkbox
              id="conflict-apply-remaining"
              checked={applyToRemaining}
              onCheckedChange={(v) => setApplyToRemaining(v === true)}
            />
            <Label htmlFor="conflict-apply-remaining" className="text-sm font-normal">
              {t('granular.applyToRemaining', { count: remaining })}
            </Label>
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={() => { setApplyToRemaining(false); onClose(); }}>
            {remaining > 0 ? t('granular.skipConflicts') : t('common.cancel')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
