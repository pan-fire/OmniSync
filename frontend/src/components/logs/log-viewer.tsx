'use client';

import { useState } from 'react';
import { ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useTranslation } from '@/i18n';
import { cn } from '@/lib/utils';
import { formatDateTime } from '@/lib/format';
import type { LogEntry } from '@/types';

const LOG_LEVELS = ['ALL', 'DEBUG', 'INFO', 'WARNING', 'ERROR'] as const;
export type LogLevel = (typeof LOG_LEVELS)[number];

function getLevelVariant (level: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  switch (level.toUpperCase()) {
    case 'ERROR':
      return 'destructive';
    case 'WARNING':
      return 'default';
    case 'INFO':
      return 'secondary';
    case 'DEBUG':
      return 'outline';
    default:
      return 'outline';
  }
}

/** Filter logs by level. "ALL" returns all entries. */
export function filterLogsByLevel (logs: LogEntry[], level: string): LogEntry[] {
  if (level.toUpperCase() === 'ALL') return logs;
  return logs.filter((entry) => entry.level.toUpperCase() === level.toUpperCase());
}

/** Sort logs in reverse chronological order (newest first). */
export function sortLogsReverseChronological (logs: LogEntry[]): LogEntry[] {
  return [...logs].sort((a, b) => {
    const ta = new Date(a.timestamp).getTime();
    const tb = new Date(b.timestamp).getTime();
    return tb - ta;
  });
}

interface LogViewerProps {
  logs:           LogEntry[] | undefined;
  onRefresh:      () => void;
  isLoading?:     boolean;
  isError?:       boolean;
  isFetching?:    boolean;
  /** Controlled level filter; without it the viewer filters on its own. */
  level?:         LogLevel;
  onLevelChange?: (level: LogLevel) => void;
  /** Paging (0-based page, newest first); without onPageChange there are no page controls. */
  page?:          number;
  hasNext?:       boolean;
  onPageChange?:  (page: number) => void;
}

export function LogViewer ({
  logs, onRefresh, isLoading, isError, isFetching,
  level: controlledLevel, onLevelChange, page = 0, hasNext = false, onPageChange,
}: LogViewerProps) {
  const { t, locale } = useTranslation();
  const [ownLevel, setOwnLevel] = useState<LogLevel>('ALL');
  const level = controlledLevel ?? ownLevel;
  const setLevel = onLevelChange ?? setOwnLevel;

  const sorted = sortLogsReverseChronological(logs ?? []);
  const filtered = filterLogsByLevel(sorted, level);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <Tabs value={level} onValueChange={(v) => setLevel(v as LogLevel)}>
          <TabsList>
            {LOG_LEVELS.map((l) => (
              <TabsTrigger key={l} value={l}>
                {t(`logs.${l.toLowerCase()}`)}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <Button variant="outline" size="sm" onClick={onRefresh} disabled={isFetching}>
          <RefreshCw className={cn('me-2 h-4 w-4', isFetching && 'animate-spin')} aria-hidden="true" />
          {t('logs.refresh')}
        </Button>
      </div>

      <ScrollArea className="h-[600px] rounded-md border">
        {isError
          ? (
          <p className="p-4 text-destructive" role="alert">{t('logs.loadFailed')}</p>
            )
          : isLoading || !logs
            ? (
          <p className="p-4 text-muted-foreground" role="status">{t('common.loading')}</p>
              )
            : filtered.length === 0
              ? (
          <p className="text-muted-foreground p-4">{t('logs.noLogs')}</p>
                )
              : (
          <div className="space-y-1 p-4">
            {filtered.map((entry, i) => (
              <div
                key={`${entry.timestamp}-${i}`}
                className={cn(
                  'flex items-start gap-3 rounded-md px-3 py-2 text-sm',
                  'hover:bg-muted/50'
                )}
              >
                <time
                  className="text-muted-foreground shrink-0 font-mono text-xs"
                  dateTime={entry.timestamp}
                  title={entry.timestamp}
                >
                  {formatDateTime(entry.timestamp, locale)}
                </time>
                <Badge variant={getLevelVariant(entry.level)} className="shrink-0">
                  {entry.level}
                </Badge>
                {/* Backend messages are English and full of paths. */}
                <span className="break-all" dir="auto">{entry.message}</span>
              </div>
            ))}
          </div>
                )}
      </ScrollArea>

      {onPageChange && (
        <nav className="flex items-center justify-between gap-2" aria-label={t('logs.pagination')}>
          <Button variant="outline" size="sm" onClick={() => onPageChange(page - 1)} disabled={page === 0 || isFetching}>
            <ChevronLeft className="h-4 w-4 rtl:rotate-180" aria-hidden="true" />
            {t('logs.newer')}
          </Button>
          <span className="text-sm text-muted-foreground">{t('logs.page', { n: page + 1 })}</span>
          <Button variant="outline" size="sm" onClick={() => onPageChange(page + 1)} disabled={!hasNext || isFetching}>
            {t('logs.older')}
            <ChevronRight className="ms-1 h-4 w-4 rtl:rotate-180" aria-hidden="true" />
          </Button>
        </nav>
      )}
    </div>
  );
}
