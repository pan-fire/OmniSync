'use client';

import { useState, useCallback, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useTranslation } from '@/i18n';
import { apiFetch } from '@/lib/api';
import type { BrowseResponse } from '@/types';
import { Folder, FolderUp, Loader2, ChevronRight } from 'lucide-react';
import { PathText } from '@/components/shared/path-text';

type BrowseMode = 'local' | 'remote';

interface DirBrowserProps {
  open:         boolean;
  onOpenChange: (open: boolean) => void;
  mode:         BrowseMode;
  initialPath:  string;
  /** Without it the browser only looks around: no Select button. */
  onSelect?:    (path: string) => void;
}

export function DirBrowser ({
  open,
  onOpenChange,
  mode,
  initialPath,
  onSelect,
}: DirBrowserProps) {
  const { t } = useTranslation();
  const [data, setData] = useState<BrowseResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const browse = useCallback(async (path: string) => {
    setLoading(true);
    setError(null);
    try {
      const endpoint = mode === 'local' ? '/browse/local' : '/browse/remote';
      const result = await apiFetch<BrowseResponse>(
        `${endpoint}?path=${encodeURIComponent(path)}`
      );
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : '');
    } finally {
      setLoading(false);
    }
  }, [mode]);

  // Trigger initial browse when dialog opens
  useEffect(() => {
    if (open) {
      setData(null);
      setError(null);
      const startPath = initialPath || (mode === 'local' ? '~' : '');
      if (startPath) {
        browse(startPath);
      }
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSelect = () => {
    if (data?.current && onSelect) {
      onSelect(data.current);
      onOpenChange(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>
            {mode === 'local'
              ? t('browse.localTitle')
              : t('browse.remoteTitle')}
          </DialogTitle>
        </DialogHeader>

        {data && (
          <div className="rounded-md bg-muted px-3 py-2 text-sm font-mono truncate" dir="ltr">
            {data.current}
          </div>
        )}

        {error !== null && (
          <div className="py-2 text-sm text-destructive" role="alert">{error || t('common.error')}</div>
        )}

        {loading && (
          <div className="flex items-center justify-center py-8" role="status" aria-label={t('common.loading')}>
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-hidden="true" />
          </div>
        )}

        {!loading && data && (
          <ScrollArea className="h-64 rounded-md border">
            <div className="p-2 space-y-0.5">
              {data.parent && (
                <button
                  type="button"
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent text-muted-foreground"
                  onClick={() => browse(data.parent!)}
                  aria-label={t('browse.parent')}
                >
                  <FolderUp className="h-4 w-4" aria-hidden="true" />
                  ..
                </button>
              )}

              {data.entries.map((entry) => (
                <button
                  key={entry.path}
                  type="button"
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent"
                  onClick={() => browse(entry.path)}
                >
                  <Folder className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                  <span className="flex-1 truncate text-start"><PathText>{entry.name}</PathText></span>
                  <ChevronRight className="h-3 w-3 text-muted-foreground rtl:rotate-180" aria-hidden="true" />
                </button>
              ))}

              {data.entries.length === 0 && !data.parent && (
                <div className="text-sm text-muted-foreground py-4 text-center">
                  {t('browse.empty')}
                </div>
              )}
            </div>
          </ScrollArea>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {onSelect ? t('common.cancel') : t('browse.close')}
          </Button>
          {onSelect && (
            <Button onClick={handleSelect} disabled={!data?.current}>
              {t('browse.select')}
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
