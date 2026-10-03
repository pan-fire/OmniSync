'use client';

import { useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useNotificationHistory } from '@/hooks/use-notifications';
import { useTranslation } from '@/i18n';
import { formatDateTime, formatRelativeTime } from '@/lib/format';
import type { NotificationSeverity } from '@/types';

const SEVERITY_COLORS: Record<NotificationSeverity, string> = {
  error:   'bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30',
  warning: 'bg-amber-500/15 text-amber-800 dark:text-amber-400 border-amber-500/30',
  info:    'bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30',
  debug:   'bg-zinc-500/15 text-zinc-700 dark:text-zinc-400 border-zinc-500/30',
};

const PAGE_SIZE = 20;

export function NotificationHistory () {
  const { t, locale } = useTranslation();
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const { data, isLoading, isError } = useNotificationHistory(PAGE_SIZE, offset);

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const hasMore = offset + PAGE_SIZE < total;

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <Button variant="ghost" size="sm" className="flex items-center gap-2 text-muted-foreground">
          {open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          {t('notifications.history')}
          {total > 0 && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
              {total}
            </Badge>
          )}
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ScrollArea className="mt-3 max-h-80 rounded-md border border-sidebar-border bg-muted/30 p-3">
          {isLoading && (
            <p className="text-xs text-muted-foreground">{t('common.loading')}</p>
          )}
          {isError && (
            <p className="text-xs text-destructive" role="alert">{t('notifications.historyFailed')}</p>
          )}
          {!isLoading && !isError && items.length === 0 && (
            <p className="text-xs text-muted-foreground">{t('notifications.noHistory')}</p>
          )}
          <div className="space-y-2">
            {items.map((entry) => (
              <div key={entry.id} className="flex items-start gap-2 text-xs">
                <Badge
                  variant="outline"
                  className={`shrink-0 text-[10px] px-1.5 py-0 ${SEVERITY_COLORS[entry.severity] ?? SEVERITY_COLORS.debug}`}
                >
                  {t(`notifications.severity.${entry.severity}`)}
                </Badge>
                <div className="min-w-0 flex-1">
                  <span className="font-medium">{entry.title}</span>
                  {entry.channels_delivered.length > 0 && (
                    <span className="ms-2 text-muted-foreground">
                      {t('notifications.via', { channels: entry.channels_delivered.map((c) => t(`notifications.channels.${c}`)).join(', ') })}
                    </span>
                  )}
                </div>
                <time
                  className="shrink-0 text-muted-foreground"
                  dateTime={entry.timestamp}
                  title={formatDateTime(entry.timestamp, locale)}
                >
                  {formatRelativeTime(entry.timestamp, locale)}
                </time>
              </div>
            ))}
          </div>
          {hasMore && (
            <Button
              variant="ghost"
              size="sm"
              className="mt-2 w-full text-xs"
              onClick={() => setOffset((prev) => prev + PAGE_SIZE)}
            >
              {t('notifications.loadMore')}
            </Button>
          )}
        </ScrollArea>
      </CollapsibleContent>
    </Collapsible>
  );
}
