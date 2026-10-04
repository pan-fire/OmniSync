'use client';

import { useState, useCallback, type ReactNode } from 'react';
import { AlertCircle, Hand, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { useTranslation } from '@/i18n';
import { useProfilePaginatedDiff } from '@/hooks/use-profile-paginated-diff';
import { useProfileSelectiveSync } from '@/hooks/use-profile-sync';
import { useClearProfileManualFlag, useProfileManualFlags } from '@/hooks/use-manual-flags';
import { FileBrowser } from './file-browser';
import type { DiffSummary, FileAction, FileError, SelectiveSyncItem } from '@/types';
import { PathText } from '@/components/shared/path-text';

const SINGLE_TOAST_KEYS: Record<FileAction, string> = {
  push:      'granular.toastPushed',
  pull:      'granular.toastPulled',
  skip:      'granular.toastSkipped',
  manual:    'granular.toastManual',
  keep_both: 'granular.toastKeepBoth',
};

const BATCH_TOAST_KEYS: Record<FileAction, string> = {
  push:      'granular.toastBatchPush',
  pull:      'granular.toastBatchPull',
  skip:      'granular.toastBatchSkip',
  manual:    'granular.toastBatchManual',
  keep_both: 'granular.toastBatchKeepBoth',
};

const ACTION_NAME_KEYS: Record<FileAction, string> = {
  push:      'granular.actionNames.push',
  pull:      'granular.actionNames.pull',
  skip:      'granular.actionNames.skip',
  manual:    'granular.actionNames.manual',
  keep_both: 'granular.actionNames.keep_both',
};

/** How many failed paths a toast names before it says "and N more". */
const MAX_NAMED_FAILURES = 3;

/** The failed files for a toast: the first few paths, then a count. */
export function failedFileList (errors: FileError[], t: (key: string, vars?: Record<string, string | number>) => string): string | undefined {
  if (errors.length === 0) return undefined;
  const named = errors.slice(0, MAX_NAMED_FAILURES).map(e => e.path).join(', ');
  const rest = errors.length - MAX_NAMED_FAILURES;
  return rest > 0 ? t('granular.failedFilesMore', { files: named, count: rest }) : named;
}

export const EMPTY_SUMMARY: DiffSummary = {
  local_only:      0,
  remote_only:     0,
  modified_local:  0,
  modified_remote: 0,
  modified_both:   0,
  manual:          0,
  total:           0,
};

interface ProfileDiffPanelProps {
  slug:        string;
  /** Extra content shown under the "no differences" message. */
  emptyExtra?: ReactNode;
}

/** The diff of one profile: loading, error and empty states, and the file browser. */
export function ProfileDiffPanel ({ slug, emptyExtra }: ProfileDiffPanelProps) {
  const { t } = useTranslation();
  const diff = useProfilePaginatedDiff(slug);
  const selectiveSync = useProfileSelectiveSync(slug);
  const clearManualFlag = useClearProfileManualFlag(slug);
  const unmark = useCallback((path: string) => clearManualFlag.mutate(path), [clearManualFlag]);
  const [pendingPaths, setPendingPaths] = useState<Set<string>>(new Set());

  const handleSync = useCallback((items: SelectiveSyncItem[]) => {
    if (items.length === 0) return;
    const paths = new Set(items.map(i => i.path));
    setPendingPaths(prev => new Set([...prev, ...paths]));
    const clearPending = () => setPendingPaths(prev => {
      const next = new Set(prev);
      for (const p of paths) next.delete(p);
      return next;
    });

    const action = items[0].action;
    const sameAction = items.every(i => i.action === action);
    const actionName = t(sameAction ? ACTION_NAME_KEYS[action] : 'granular.actionNames.mixed');

    selectiveSync.mutate(items, {
      onSuccess: (res) => {
        clearPending();
        const failedPaths = new Set(res.errors.map(e => e.path));
        // A failed file is still a difference: keep its row. If the backend
        // reported failures without naming every file, remove nothing and
        // let the refetch after the invalidation settle the table.
        if (failedPaths.size >= res.failed) {
          diff.removeFiles(new Set([...paths].filter(p => !failedPaths.has(p))));
        }

        if (res.failed > 0 && res.succeeded === 0) {
          toast.error(t('granular.toastActionFailed', {
            action: actionName,
            error:  res.errors[0]?.error ?? t('common.error'),
          }), { description: failedFileList(res.errors, t) });
        } else if (res.failed > 0) {
          toast.warning(t('granular.toastPartialFail', {
            action:    actionName,
            succeeded: res.succeeded,
            count:     res.total,
            failed:    res.failed,
          }), { description: failedFileList(res.errors, t) });
        } else if (items.length === 1) {
          toast.success(t(SINGLE_TOAST_KEYS[action], { path: items[0].path }));
        } else if (sameAction) {
          toast.success(t(BATCH_TOAST_KEYS[action], { count: res.succeeded }));
        } else {
          toast.success(t('granular.toastBatchMixed', { count: res.succeeded }));
        }
      },
      onError: (error: Error) => {
        clearPending();
        toast.error(t('granular.toastActionFailed', {
          action: actionName,
          error:  error.message || t('common.error'),
        }));
      },
    });
  }, [selectiveSync, diff, t]);

  if (diff.isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-foreground" role="status">
        <Loader2 className="me-2 h-5 w-5 animate-spin" aria-hidden="true" />
        {t('granular.loadingDiff')}
      </div>
    );
  }

  const errorBox = diff.error && (
    <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm" role="alert">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
      <div className="space-y-2">
        <p className="font-medium">{t('granular.diffFailed')}</p>
        <p className="break-words text-muted-foreground">{diff.error}</p>
        <Button size="sm" variant="outline" onClick={diff.reset}>{t('common.retry')}</Button>
      </div>
    </div>
  );

  if (diff.allFiles.length === 0 && !diff.hasMore) {
    return (
      <div className="space-y-3">
        {errorBox}
        {!diff.error && (
          <Card>
            <CardContent className="py-8 text-center text-muted-foreground">
              <p>{t('granular.noDifferences')}</p>
              {emptyExtra}
            </CardContent>
          </Card>
        )}
        <ManualFlagsList slug={slug} onUnmark={unmark} busy={clearManualFlag.isPending} />
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {errorBox}
      <FileBrowser
        files={diff.allFiles}
        summary={diff.summary ?? EMPTY_SUMMARY}
        onSync={handleSync}
        isSyncing={selectiveSync.isPending}
        pendingPaths={pendingPaths}
        onUnmarkManual={unmark}
      />
      {diff.hasMore && (
        <div className="flex justify-center">
          <Button variant="outline" size="sm" onClick={diff.loadMore} disabled={diff.isLoadingMore}>
            {diff.isLoadingMore
              ? <><Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> {t('common.loading')}</>
              : t('granular.loadMore')}
          </Button>
        </div>
      )}
      <ManualFlagsList slug={slug} onUnmark={unmark} busy={clearManualFlag.isPending} />
    </div>
  );
}

/**
 * Files flagged "manual" are skipped by every sync. List them with an
 * Unmark button so the flag can always be undone, even for files that no
 * longer appear in the diff.
 */
function ManualFlagsList ({ slug, onUnmark, busy }: { slug: string; onUnmark: (path: string) => void; busy: boolean }) {
  const { t } = useTranslation();
  const flags = useProfileManualFlags(slug);
  const paths = flags.data?.flags ?? [];
  if (paths.length === 0) return null;

  return (
    <details className="rounded-md border p-3 text-sm" data-testid="manual-flags">
      <summary className="flex cursor-pointer items-center gap-2 font-medium">
        <Hand className="h-4 w-4" aria-hidden="true" />
        {t('granular.manualFlagsTitle', { count: paths.length })}
      </summary>
      <p className="mt-2 text-xs text-muted-foreground">{t('granular.manualFlagsHint')}</p>
      <ul className="mt-2 space-y-1">
        {paths.map((path) => (
          <li key={path} className="flex items-center justify-between gap-2">
            <span className="truncate font-mono text-xs" title={path}><PathText>{path}</PathText></span>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 shrink-0 text-xs"
              disabled={busy}
              onClick={() => onUnmark(path)}
              aria-label={t('granular.unmarkFile', { path })}
            >
              {t('granular.unmarkManual')}
            </Button>
          </li>
        ))}
      </ul>
    </details>
  );
}
