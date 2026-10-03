'use client';

import { useCallback, useState } from 'react';
import { useQueries } from '@tanstack/react-query';
import { AlertTriangle, ArrowDown, ArrowUp, ArrowUpDown, Loader2 } from 'lucide-react';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { useTranslation } from '@/i18n';
import { api } from '@/lib/api';
import { syncPreviewQueryKey, useStartSyncs, type SyncTarget } from '@/hooks/use-profile-sync';
import type { SyncDirection, SyncPreview, SyncPreviewCounts, TwoWayPreview } from '@/types';

/** What a push or pull would do, from the side-effect-free preview. */
export function previewFor (direction: 'push' | 'pull', preview: SyncPreview): SyncPreviewCounts {
  return direction === 'push' ? preview.push : preview.pull;
}

/** Whether a sync in `direction` would delete files, per its preview. */
function previewDeletes (direction: SyncDirection, preview: SyncPreview): boolean {
  if (direction === 'two_way') {
    return !!preview.two_way && (preview.two_way.local.deletes > 0 || preview.two_way.remote.deletes > 0);
  }
  return previewFor(direction, preview).deletes > 0;
}

const TEXT: Record<SyncDirection, {
  title:     string;
  titleMany: string;
  explain:   string;
  confirm:   string;
}> = {
  push: {
    title:     'syncConfirm.pushTitle',
    titleMany: 'syncConfirm.pushTitleMany',
    explain:   'syncConfirm.pushExplain',
    confirm:   'syncConfirm.confirmPush',
  },
  pull: {
    title:     'syncConfirm.pullTitle',
    titleMany: 'syncConfirm.pullTitleMany',
    explain:   'syncConfirm.pullExplain',
    confirm:   'syncConfirm.confirmPull',
  },
  two_way: {
    title:     'syncConfirm.twoWayTitle',
    titleMany: 'syncConfirm.twoWayTitleMany',
    explain:   'syncConfirm.twoWayExplain',
    confirm:   'syncConfirm.confirmTwoWay',
  },
};

const DIRECTION_ICON = { push: ArrowUp, pull: ArrowDown, two_way: ArrowUpDown };

/** One folder's side of a two-way preview. */
function TwoWaySide ({ label, counts, maxDelete }: { label: string; counts: SyncPreviewCounts; maxDelete: number | null }) {
  const { t } = useTranslation();
  return (
    <div>
      <p className="font-medium">{label}</p>
      <p className={counts.deletes > 0 ? 'font-medium text-destructive' : 'text-muted-foreground'}>
        {t('syncConfirm.twoWayDeletes', { count: counts.deletes })}
      </p>
      <p className="text-muted-foreground">{t('syncConfirm.replaces', { count: counts.replaces })}</p>
      <p className="text-muted-foreground">{t('syncConfirm.creates', { count: counts.creates })}</p>
      {counts.exceeds_max_delete && maxDelete !== null && (
        <p className="flex items-center gap-1 text-amber-700 dark:text-amber-400">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {t('syncConfirm.twoWayWillStop', { max: maxDelete })}
        </p>
      )}
    </div>
  );
}

/** What the next two-way sync would do to each folder (POST /sync/preview, `two_way`). */
export function TwoWayPreviewDetails ({ preview, maxDelete }: { preview: TwoWayPreview; maxDelete: number | null }) {
  const { t } = useTranslation();
  return (
    <div className="mt-1 space-y-2" data-testid="two-way-preview">
      {preview.resync_required && (
        <p className="flex items-center gap-1 font-medium text-destructive">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {t('syncConfirm.twoWayResyncRequired')}
        </p>
      )}
      {preview.resync && !preview.resync_required && (
        <p className="text-muted-foreground">{t('syncConfirm.twoWayResync')}</p>
      )}
      <div className="grid gap-2 sm:grid-cols-2">
        <TwoWaySide label={t('syncConfirm.localFolder')} counts={preview.local} maxDelete={maxDelete} />
        <TwoWaySide label={t('syncConfirm.remoteFolder')} counts={preview.remote} maxDelete={maxDelete} />
      </div>
      <p className={preview.conflicts > 0 ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'}>
        {t('syncConfirm.twoWayConflicts', { count: preview.conflicts })}
      </p>
      <p className="text-muted-foreground">
        {maxDelete === null
          ? t('syncConfirm.noLimit')
          : t('syncConfirm.twoWayLimit', { max: maxDelete })}
      </p>
    </div>
  );
}

interface SyncRequest {
  direction: SyncDirection;
  targets:   SyncTarget[];
}

interface SyncConfirmDialogProps {
  request:   SyncRequest | null;
  onConfirm: () => void;
  onCancel:  () => void;
}

export function SyncConfirmDialog ({ request, onConfirm, onCancel }: SyncConfirmDialogProps) {
  const { t } = useTranslation();
  const open = request !== null;
  const direction = request?.direction ?? 'push';
  const targets = request?.targets ?? [];

  // POST /sync/preview only counts: unlike /sync/check it does not change the
  // pending count or pause the profile, so opening this dialog is harmless.
  const previews = useQueries({
    queries: targets.map((p) => ({
      queryKey:  syncPreviewQueryKey(p.slug),
      queryFn:   () => api.previewProfileSync(p.slug),
      enabled:   open,
      staleTime: 0,
      retry:     false,
    })),
  });

  const stillChecking = previews.some((c) => c.isLoading);
  const twoWay = direction === 'two_way';
  // A confirmed sync is sent with force, so without a preview the user must
  // say explicitly that they accept not knowing what it changes. A two-way
  // answer without its `two_way` part counts as no preview too.
  const previewFailed = previews.some((c) =>
    c.isError || !!c.data?.error || (twoWay && !!c.data && (!c.data.two_way || !!c.data.two_way.error)));
  const deletes = previews.some((c) => !!c.data && !c.data.error && previewDeletes(direction, c.data));
  // Remembered per request, so the next dialog starts unticked.
  const [acknowledgedFor, setAcknowledgedFor] = useState<SyncRequest | null>(null);
  const acknowledged = request !== null && acknowledgedFor === request;
  const single = targets.length === 1;
  // `targets` is empty while the dialog fades out after closing.
  const text = TEXT[direction];
  const title = targets.length === 0
    ? ''
    : single
      ? t(text.title, { name: targets[0].name })
      : t(text.titleMany, { count: targets.length });
  const DirectionIcon = DIRECTION_ICON[direction];

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onCancel(); }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <DirectionIcon className="h-5 w-5" aria-hidden="true" />
            {title}
          </DialogTitle>
          <DialogDescription>
            {t(text.explain)}
          </DialogDescription>
        </DialogHeader>

        <ul className="max-h-64 space-y-2 overflow-y-auto" data-testid="sync-confirm-profiles">
          {targets.map((p, i) => {
            const query = previews[i];
            const data = query?.data;
            const counts = data && direction !== 'two_way' ? previewFor(direction, data) : null;
            return (
              <li key={p.slug} className="rounded-md border p-3 text-sm">
                <div className="font-medium">{p.name}</div>
                {query?.isLoading && (
                  <p className="mt-1 flex items-center gap-2 text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                    {t('syncConfirm.checking')}
                  </p>
                )}
                {query?.isError && (
                  <p className="mt-1 text-destructive">
                    {t('syncConfirm.checkFailed')}: {query.error.message}
                  </p>
                )}
                {data?.error && (
                  <p className="mt-1 text-destructive">
                    {t('syncConfirm.checkFailed')}: {data.error}
                  </p>
                )}
                {twoWay && data && !data.error && (
                  data.two_way?.error
                    ? (
                      <p className="mt-1 text-destructive">
                        {t('syncConfirm.checkFailed')}: {data.two_way.error}
                      </p>
                      )
                    : data.two_way
                      ? <TwoWayPreviewDetails preview={data.two_way} maxDelete={data.max_delete} />
                      : <p className="mt-1 text-muted-foreground">{t('syncConfirm.twoWayUnavailable')}</p>
                )}
                {data && counts && !data.error && (
                  <div className="mt-1 space-y-0.5">
                    {data.sync_mode === 'two_way' && (
                      <p className="text-muted-foreground">{t('syncConfirm.overrideNote')}</p>
                    )}
                    <p className={counts.deletes > 0 ? 'font-medium text-destructive' : 'text-muted-foreground'}>
                      {t(direction === 'push' ? 'syncConfirm.deletesRemote' : 'syncConfirm.deletesLocal', { count: counts.deletes })}
                    </p>
                    <p className="text-muted-foreground">
                      {t('syncConfirm.replaces', { count: counts.replaces })}
                    </p>
                    {data.excluded > 0 && (
                      <p className="text-muted-foreground">
                        {t('syncConfirm.excluded', { count: data.excluded })}
                      </p>
                    )}
                    <p className="text-muted-foreground">
                      {data.max_delete === null
                        ? t('syncConfirm.noLimit')
                        : t('syncConfirm.limit', { max: data.max_delete })}
                    </p>
                    {counts.exceeds_max_delete && data.max_delete !== null && (
                      <p className="flex items-center gap-1 text-amber-700 dark:text-amber-400">
                        <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                        {t('syncConfirm.willStop', { max: data.max_delete })}
                      </p>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>

        <div className="space-y-1 rounded-md bg-muted/50 p-3 text-xs text-muted-foreground">
          <p>{t(twoWay ? 'syncConfirm.twoWayTrashNote' : 'syncConfirm.trashNote')}</p>
          <p>{t(twoWay ? 'syncConfirm.twoWayLimitExplain' : 'syncConfirm.limitExplain')}</p>
        </div>

        {previewFailed && !stillChecking && (
          <div className="space-y-3 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm" role="alert">
            <p className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
              {t('syncConfirm.noPreviewWarning')}
            </p>
            <div className="flex items-center gap-2">
              <Checkbox
                id="sync-confirm-no-preview"
                checked={acknowledged}
                onCheckedChange={(v) => setAcknowledgedFor(v === true ? request : null)}
              />
              <Label htmlFor="sync-confirm-no-preview" className="text-sm font-normal">
                {t('syncConfirm.confirmWithoutPreview')}
              </Label>
            </div>
          </div>
        )}

        <DialogFooter className="gap-2 sm:gap-0">
          <Button variant="outline" onClick={onCancel}>
            {t('common.cancel')}
          </Button>
          <Button
            variant={deletes || previewFailed ? 'destructive' : 'default'}
            onClick={onConfirm}
            disabled={stillChecking || (previewFailed && !acknowledged)}
          >
            {t(text.confirm)}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Push/pull (and bulk two-way syncs) behind a confirmation. `request` opens
 * the dialog; nothing is sent to /sync/start until the user confirms. The
 * confirmed sync is sent with force=true: any diff pauses a profile that has
 * differences, and a sync the user explicitly confirmed is how they resolve
 * them. The backend still runs every safety check (sync marker, empty
 * source, delete limit, trash).
 *
 * `syncNow` is "Sync now" of one two-way profile: it starts right away
 * (without force) unless the profile is paused, which needs the
 * confirmation (and then force) like a push or pull.
 */
export function useConfirmedSync () {
  const [pending, setPending] = useState<SyncRequest | null>(null);
  const startSyncs = useStartSyncs();

  const request = useCallback((direction: SyncDirection, targets: SyncTarget[]) => {
    if (targets.length === 0) return;
    setPending({ direction, targets });
  }, []);

  const syncNow = useCallback((target: SyncTarget, paused: boolean) => {
    if (paused) {
      setPending({ direction: 'two_way', targets: [target] });
    } else {
      startSyncs.mutate({ direction: 'two_way', targets: [target] });
    }
  }, [startSyncs]);

  const dialog = (
    <SyncConfirmDialog
      request={pending}
      onCancel={() => setPending(null)}
      onConfirm={() => {
        if (pending) startSyncs.mutate({ ...pending, force: true });
        setPending(null);
      }}
    />
  );

  return { request, syncNow, dialog, isStarting: startSyncs.isPending };
}
