'use client';

import { useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { useRestore, useRestorePreview } from '@/hooks/use-backups';
import { useTranslation } from '@/i18n';
import { formatDateTime } from '@/lib/format';
import type { Snapshot, RestorePreviewSide, RestoreScope } from '@/types';

interface RestoreDialogProps {
  profileSlug:  string;
  targetId:     number;
  /**
   * What a restore does depends on the snapshot kind (backup_service/restore.py): a
   * full snapshot (archive, or mirror with a manifest) makes the folder
   * equal to the tree right after that backup; a legacy mirror version
   * copies files back and deletes nothing. Replaced and removed files go to
   * .omnisync-trash/pre-restore/<time>/ inside the destination.
   */
  snapshot:     Snapshot;
  open:         boolean;
  onOpenChange: (open: boolean) => void;
}

const SCOPES: RestoreScope[] = ['local_only', 'remote_only', 'both'];

const SCOPE_KEYS: Record<RestoreScope, { label: string; desc: string }> = {
  local_only:  { label: 'backups.scopeLocal', desc: 'backups.scopeLocalDesc' },
  remote_only: { label: 'backups.scopeRemote', desc: 'backups.scopeRemoteDesc' },
  both:        { label: 'backups.scopeBoth', desc: 'backups.scopeBothDesc' },
};

/** One side of the restore preview: the counts, with example paths as tooltips. */
function PreviewSide ({ side }: { side: RestorePreviewSide }) {
  const { t } = useTranslation();
  const parts: Array<[string, number, string[]]> = [
    ['added', side.added, side.added_examples],
    ['replaced', side.replaced, side.replaced_examples],
    ['removed', side.removed, side.removed_examples],
  ];
  return (
    <li>
      <span className="font-medium">{t(`backups.preview.${side.side}`)}</span>{' '}
      <span className="font-mono text-xs text-muted-foreground" dir="ltr">{side.path}</span>
      <div className="flex flex-wrap gap-x-3 text-xs">
        {parts.map(([kind, count, examples]) => (
          <span
            key={kind}
            className={kind === 'removed' && count > 0 ? 'text-destructive' : undefined}
            title={examples.length ? t('backups.preview.examples', { paths: examples.join(', ') }) : undefined}
          >
            {t(`backups.preview.${kind}`, { count })}
          </span>
        ))}
        <span className="text-muted-foreground">{t('backups.preview.unchanged', { count: side.unchanged })}</span>
      </div>
    </li>
  );
}

export function RestoreDialog ({
  profileSlug,
  targetId,
  snapshot,
  open,
  onOpenChange,
}: RestoreDialogProps) {
  const { t, locale } = useTranslation();
  // Default to the narrowest scope; restoring overwrites the chosen side(s).
  const [scope, setScope] = useState<RestoreScope>('local_only');
  const [understood, setUnderstood] = useState(false);
  const restore = useRestore(profileSlug);
  // What the restore would change, for the chosen scope (changes nothing).
  const preview = useRestorePreview(profileSlug, targetId, snapshot.snapshot_id, scope, open);
  const sides = preview.data?.sides ?? [];

  const handleRestore = () => {
    restore.mutate(
      { id: targetId, data: { snapshot_id: snapshot.snapshot_id, restore_scope: scope } },
      { onSuccess: () => onOpenChange(false) }
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('backups.restoreTitle')}</DialogTitle>
          <DialogDescription>
            {t(snapshot.kind === 'legacy' ? 'backups.restoreDescriptionLegacy' : 'backups.restoreDescription', {
              date: formatDateTime(snapshot.created_at, locale),
            })}
          </DialogDescription>
        </DialogHeader>

        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">{t('backups.restoreScope')}</legend>
          <div className="space-y-2">
            {SCOPES.map((s) => (
              <label
                key={s}
                className="flex cursor-pointer items-center gap-3 rounded-md border p-3 transition-colors hover:bg-muted/50"
              >
                <input
                  type="radio"
                  name="restore_scope"
                  value={s}
                  checked={scope === s}
                  onChange={() => { setScope(s); setUnderstood(false); }}
                  className="accent-primary"
                />
                <div>
                  <div className="text-sm font-medium">{t(SCOPE_KEYS[s].label)}</div>
                  <div className="text-xs text-muted-foreground">{t(SCOPE_KEYS[s].desc)}</div>
                </div>
              </label>
            ))}
          </div>
        </fieldset>

        <section className="space-y-2 rounded-md border p-3 text-sm" aria-labelledby="restore-preview-title" aria-live="polite">
          <h3 id="restore-preview-title" className="font-medium">{t('backups.preview.title')}</h3>
          {preview.isLoading && <p className="text-xs text-muted-foreground" role="status">{t('backups.preview.loading')}</p>}
          {preview.isError && (
            <p className="text-xs text-destructive">{t('backups.preview.failed', { error: preview.error.message })}</p>
          )}
          {sides.length > 0 && (
            <ul className="space-y-1.5">
              {sides.map((side) => <PreviewSide key={side.side} side={side} />)}
            </ul>
          )}
          {preview.data && <p className="text-xs text-muted-foreground">{t('backups.preview.basis')}</p>}
        </section>

        <div className="space-y-3 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm" role="alert">
          <p className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
            {t(`backups.restoreWarning.${snapshot.kind === 'legacy' ? 'legacy' : 'full'}.${scope}`)}
          </p>
          <p>{t('backups.restoreSafetyCopies')}</p>
          {scope !== 'both' && <p>{t('backups.restorePausesSync')}</p>}
          <div className="flex items-center gap-2">
            <Checkbox
              id="restore-understood"
              checked={understood}
              onCheckedChange={(v) => setUnderstood(v === true)}
            />
            <Label htmlFor="restore-understood" className="text-sm font-normal">
              {t('backups.restoreConfirmOverwrite')}
            </Label>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="destructive"
            onClick={handleRestore}
            disabled={!understood || restore.isPending}
          >
            {restore.isPending ? t('backups.restoring') : t('backups.restore')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
