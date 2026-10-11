'use client';

import { useEffect, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useTranslation } from '@/i18n';
import { useOpenCount } from '@/hooks/use-open-count';
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

function fetchDir (mode: BrowseMode, path: string): Promise<BrowseResponse> {
  const endpoint = mode === 'local' ? '/browse/local' : '/browse/remote';
  return apiFetch<BrowseResponse>(`${endpoint}?path=${encodeURIComponent(path)}`);
}

export function DirBrowser ({
  open,
  onOpenChange,
  mode,
  initialPath,
  onSelect,
}: DirBrowserProps) {
  const { t } = useTranslation();
  // Each opening starts over at the initial path, with nothing shown from
  // the last visit.
  const openCount = useOpenCount(open);

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
        <DirBrowserBody
          key={openCount}
          onOpenChange={onOpenChange}
          mode={mode}
          initialPath={initialPath}
          onSelect={onSelect}
        />
      </DialogContent>
    </Dialog>
  );
}

function DirBrowserBody ({ onOpenChange, mode, initialPath, onSelect }: Omit<DirBrowserProps, 'open'>) {
  const { t } = useTranslation();
  // Where the dialog opened, read once: a later change of the caller's field
  // (Select itself makes one) must not browse again while the dialog closes.
  const [start] = useState(() => ({ mode, path: initialPath || (mode === 'local' ? '~' : '') }));
  const [data, setData] = useState<BrowseResponse | null>(null);
  const [loading, setLoading] = useState(start.path !== '');
  const [error, setError] = useState<string | null>(null);

  const browse = async (path: string) => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchDir(mode, path));
    } catch (e) {
      setError(e instanceof Error ? e.message : '');
    } finally {
      setLoading(false);
    }
  };

  // The first listing, once the dialog opens (this body mounts).
  useEffect(() => {
    if (!start.path) return;
    let ignore = false;
    fetchDir(start.mode, start.path)
      .then((result) => { if (!ignore) setData(result); })
      .catch((e: unknown) => { if (!ignore) setError(e instanceof Error ? e.message : ''); })
      .finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; };
  }, [start]);

  const handleSelect = () => {
    if (data?.current && onSelect) {
      onSelect(data.current);
      onOpenChange(false);
    }
  };

  return (
    <>
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
    </>
  );
}
