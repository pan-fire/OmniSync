'use client';

import { Check, Cloud, Loader2 } from 'lucide-react';
import type { ReactNode } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useTranslation } from '@/i18n';
import type { Remote } from '@/types';

interface RemotePickerProps {
  remotes?:      Remote[];
  value:         string;
  onValueChange: (value: string) => void;
  placeholder:   string;
  emptyMessage:  string;
  /** Shown under emptyMessage when there are no remotes, e.g. a button that opens the wizard. */
  emptyAction?:  ReactNode;
  /** The remotes are still loading: do not claim there are none yet. */
  isLoading?:    boolean;
  /** Loading the remotes failed; onRetry adds a retry button. */
  isError?:      boolean;
  onRetry?:      () => void;
}

export function RemotePicker ({
  remotes,
  value,
  onValueChange,
  placeholder,
  emptyMessage,
  emptyAction,
  isLoading,
  isError,
  onRetry,
}: RemotePickerProps) {
  const { t } = useTranslation();
  if (isLoading && !remotes) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-dashed px-4 py-3 text-sm text-muted-foreground" role="status">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        {t('remotes.loading')}
      </div>
    );
  }
  if (isError && !remotes) {
    return (
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-dashed border-destructive/50 px-4 py-3 text-sm" role="alert">
        <p className="text-destructive">{t('remotes.loadFailed')}</p>
        {onRetry && (
          <Button type="button" variant="outline" size="sm" onClick={onRetry}>{t('common.retry')}</Button>
        )}
      </div>
    );
  }
  if (!remotes || remotes.length === 0) {
    return (
      <div className="space-y-2 rounded-xl border border-dashed px-4 py-3 text-sm text-muted-foreground">
        <p>{emptyMessage}</p>
        {emptyAction}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-2">
        {remotes.map((remote) => {
          const selected = value === remote.name;

          return (
            <button
              key={remote.name}
              type="button"
              onClick={() => onValueChange(remote.name)}
              className={cn(
                'flex w-full items-start justify-between rounded-xl border px-3 py-3 text-start transition-colors',
                selected
                  ? 'border-primary bg-primary/10 shadow-sm'
                  : 'border-border bg-card hover:bg-accent/40'
              )}
              aria-pressed={selected}
            >
              <div className="flex min-w-0 items-start gap-3">
                <span className={cn(
                  'mt-0.5 rounded-lg p-2',
                  selected ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'
                )}>
                  <Cloud className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{remote.name}</div>
                  <div className="mt-1 flex items-center gap-2">
                    <Badge variant="secondary" className="capitalize">{remote.type}</Badge>
                    {remote.last_verified && (
                      <span className="text-xs text-muted-foreground">{t('remotes.verified')}</span>
                    )}
                  </div>
                </div>
              </div>
              {selected && <Check className="mt-0.5 h-4 w-4 text-primary" />}
            </button>
          );
        })}
      </div>
      {!value && <p className="text-xs text-muted-foreground">{placeholder}</p>}
    </div>
  );
}
