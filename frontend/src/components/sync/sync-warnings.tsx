'use client';

import { AlertTriangle } from 'lucide-react';
import { PathText } from '@/components/shared/path-text';
import { useTranslation } from '@/i18n';
import { cn } from '@/lib/utils';
import type { SyncWarning } from '@/types';

/**
 * Local names a sync cannot carry as they are (preview, diff, job): one entry
 * per kind, with the paths the backend names (escaped for display, at most
 * 20) and how many more there are.
 */
export function SyncWarnings ({ warnings, className }: { warnings: SyncWarning[] | undefined; className?: string }) {
  const { t } = useTranslation();
  if (!warnings || warnings.length === 0) return null;
  return (
    <ul className={cn('space-y-2 text-sm', className)} data-testid="sync-warnings">
      {warnings.map((w) => (
        <li key={w.code} className="flex items-start gap-2">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-700 dark:text-amber-400" aria-hidden="true" />
          <div className="min-w-0 space-y-0.5">
            <p className="text-amber-700 dark:text-amber-400">{t(`syncWarnings.${w.code}`, { count: w.count })}</p>
            <ul className="break-all text-muted-foreground">
              {w.paths.map((p) => <li key={p}><PathText>{p}</PathText></li>)}
              {w.count > w.paths.length && <li>{t('syncWarnings.more', { count: w.count - w.paths.length })}</li>}
            </ul>
          </div>
        </li>
      ))}
    </ul>
  );
}
