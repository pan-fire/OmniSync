'use client';

import { AlertCircle } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTranslation } from '@/i18n';

interface LastErrorNoticeProps {
  error:      string | null | undefined;
  className?: string;
  /** Compact single-block text for cards and list rows. */
  compact?:   boolean;
}

/**
 * Why a profile's last sync failed or was refused (the status `last_error`),
 * e.g. an unmounted folder or a missing .omnisync-check marker.
 */
export function LastErrorNotice ({ error, className, compact }: LastErrorNoticeProps) {
  const { t } = useTranslation();
  if (!error) return null;

  if (compact) {
    return (
      <p className={cn('break-words text-xs text-destructive', className)} data-testid="last-error">
        <span className="font-medium">{t('dashboard.lastError')}:</span> {error}
      </p>
    );
  }

  return (
    <div
      className={cn('flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm', className)}
      role="alert"
      data-testid="last-error"
    >
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
      <div className="min-w-0">
        <p className="font-medium">{t('dashboard.lastError')}</p>
        <p className="break-words text-muted-foreground">{error}</p>
      </div>
    </div>
  );
}
