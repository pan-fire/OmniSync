'use client';

import { useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight, File, Folder, FolderOpen, Search } from 'lucide-react';
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
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { DirBrowser } from '@/components/shared/dir-browser';
import { useRestoreFiles, useSnapshotFiles } from '@/hooks/use-backups';
import { useTranslation } from '@/i18n';
import { formatBytes, formatDateTime } from '@/lib/format';
import type { Snapshot, SnapshotFileEntry } from '@/types';

/** Entries per page; the backend pages a folder or a search. */
export const SNAPSHOT_PAGE_SIZE = 100;

interface SnapshotBrowserProps {
  profileSlug:  string;
  targetId:     number;
  snapshot:     Snapshot;
  open:         boolean;
  onOpenChange: (open: boolean) => void;
}

type Destination = 'original' | 'folder';

/**
 * Browse one snapshot and restore chosen files and folders
 * (GET .../snapshots/{id}/files, POST .../restore-files).
 *
 * Only the selected files are written; the files they replace go to
 * .omnisync-trash/pre-restore/ in the destination. Restored into the
 * profile's folder, they are an ordinary local change: syncing is not
 * paused (backup_service.restore_files).
 */
export function SnapshotBrowser ({ profileSlug, targetId, snapshot, open, onOpenChange }: SnapshotBrowserProps) {
  const { t, locale } = useTranslation();
  const [path, setPath] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [destination, setDestination] = useState<Destination>('original');
  const [targetDir, setTargetDir] = useState('');
  const [browsing, setBrowsing] = useState(false);
  const restore = useRestoreFiles(profileSlug);

  // Search as the user types, a moment after the last key.
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput.trim());
      setOffset(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const files = useSnapshotFiles(
    profileSlug, targetId, snapshot.snapshot_id,
    { path, search: search || undefined, offset, limit: SNAPSHOT_PAGE_SIZE },
    open
  );
  const page = files.data;
  const entries = page?.entries ?? [];
  const crumbs = path ? path.split('/') : [];

  const openFolder = (folder: string) => {
    setPath(folder);
    setOffset(0);
    setSearchInput('');
    setSearch('');
  };

  const toggle = (entry: SnapshotFileEntry, checked: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(entry.path);
      else next.delete(entry.path);
      return next;
    });
  };

  const canRestore = selected.size > 0 && (destination === 'original' || targetDir.trim().startsWith('/'));

  const handleRestore = () => {
    restore.mutate(
      {
        id:   targetId,
        data: {
          snapshot_id: snapshot.snapshot_id,
          paths:       [...selected].sort(),
          target_dir:  destination === 'folder' ? targetDir.trim() : null,
        },
      },
      { onSuccess: (job) => { if (job.status === 'completed') onOpenChange(false); } }
    );
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{t('backups.browser.title')}</DialogTitle>
            <DialogDescription>
              {t('backups.browser.description', { date: formatDateTime(snapshot.created_at, locale) })}
            </DialogDescription>
          </DialogHeader>

          <div className="relative">
            <Search className="pointer-events-none absolute start-2.5 top-2.5 h-4 w-4 text-muted-foreground" aria-hidden="true" />
            <Input
              type="search"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder={t('backups.browser.search')}
              aria-label={t('backups.browser.search')}
              className="ps-8"
            />
          </div>

          {!search && (
            <nav aria-label={t('backups.browser.location')} className="flex flex-wrap items-center gap-1 text-sm" dir="ltr">
              <Button type="button" variant="link" size="sm" className="h-auto p-0" onClick={() => openFolder('')}>
                {t('backups.browser.root')}
              </Button>
              {crumbs.map((part, i) => (
                <span key={crumbs.slice(0, i + 1).join('/')} className="flex items-center gap-1">
                  <span className="text-muted-foreground">/</span>
                  <Button
                    type="button" variant="link" size="sm" className="h-auto p-0"
                    onClick={() => openFolder(crumbs.slice(0, i + 1).join('/'))}
                  >
                    {part}
                  </Button>
                </span>
              ))}
            </nav>
          )}

          <ScrollArea className="h-72 rounded-md border">
            {files.isLoading && (
              <p className="p-3 text-sm text-muted-foreground" role="status">{t('backups.browser.loading')}</p>
            )}
            {files.isError && (
              <p className="p-3 text-sm text-destructive" role="alert">
                {t('backups.browser.failed')}: {files.error.message}
              </p>
            )}
            {page && entries.length === 0 && (
              <p className="p-3 text-sm text-muted-foreground">
                {search ? t('backups.browser.noMatches') : t('backups.browser.empty')}
              </p>
            )}
            <ul className="divide-y">
              {entries.map((entry) => (
                <li key={entry.path} className="flex items-center gap-3 px-3 py-1.5 text-sm">
                  <Checkbox
                    checked={selected.has(entry.path)}
                    onCheckedChange={(v) => toggle(entry, v === true)}
                    aria-label={t('backups.browser.select', { name: entry.path })}
                  />
                  {entry.is_dir
                    ? <Folder className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                    : <File className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />}
                  {entry.is_dir
                    ? (
                      <button
                        type="button"
                        className="min-w-0 flex-1 truncate text-start hover:underline"
                        dir="ltr"
                        onClick={() => openFolder(entry.path)}
                        aria-label={t('backups.browser.open', { name: entry.path })}
                        title={entry.path}
                      >
                        {search ? entry.path : entry.name}/
                      </button>
                      )
                    : (
                      <span className="min-w-0 flex-1 truncate" dir="ltr" title={entry.path}>
                        {search ? entry.path : entry.name}
                      </span>
                      )}
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {entry.is_dir
                      ? t('backups.browser.files', { count: entry.file_count ?? 0 })
                      : entry.mod_time ? formatDateTime(entry.mod_time, locale) : ''}
                  </span>
                  <span className="w-20 shrink-0 text-end text-xs tabular-nums">
                    {entry.size != null ? formatBytes(entry.size, locale) : ''}
                  </span>
                </li>
              ))}
            </ul>
          </ScrollArea>

          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
            <span>
              {page && page.total > 0 && t('backups.browser.range', {
                from:  page.offset + 1,
                to:    Math.min(page.offset + entries.length, page.total),
                total: page.total,
              })}
              {selected.size > 0 && <> · {t('backups.browser.selected', { count: selected.size })}</>}
            </span>
            <div className="flex items-center gap-1">
              {selected.size > 0 && (
                <Button type="button" variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
                  {t('backups.browser.clear')}
                </Button>
              )}
              <Button
                type="button" variant="outline" size="icon" className="h-7 w-7"
                disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - SNAPSHOT_PAGE_SIZE))}
                aria-label={t('backups.browser.prev')}
              >
                <ChevronLeft className="h-4 w-4 rtl:rotate-180" aria-hidden="true" />
              </Button>
              <Button
                type="button" variant="outline" size="icon" className="h-7 w-7"
                disabled={!page || offset + SNAPSHOT_PAGE_SIZE >= page.total}
                onClick={() => setOffset(offset + SNAPSHOT_PAGE_SIZE)}
                aria-label={t('backups.browser.next')}
              >
                <ChevronRight className="h-4 w-4 rtl:rotate-180" aria-hidden="true" />
              </Button>
            </div>
          </div>

          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">{t('backups.browser.destination')}</legend>
            {(['original', 'folder'] as const).map((value) => (
              <label key={value} className="flex cursor-pointer items-start gap-3 rounded-md border p-2 hover:bg-muted/50">
                <input
                  type="radio"
                  name="restore_destination"
                  value={value}
                  checked={destination === value}
                  onChange={() => setDestination(value)}
                  className="mt-1 accent-primary"
                />
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium">
                    {t(value === 'original' ? 'backups.browser.toOriginal' : 'backups.browser.toFolder')}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {t(value === 'original' ? 'backups.browser.toOriginalDesc' : 'backups.browser.toFolderDesc')}
                  </div>
                </div>
              </label>
            ))}
            {destination === 'folder' && (
              <div className="flex gap-2">
                <Input
                  dir="ltr"
                  value={targetDir}
                  onChange={(e) => setTargetDir(e.target.value)}
                  placeholder={t('backups.browser.folderPlaceholder')}
                  aria-label={t('backups.browser.toFolder')}
                />
                <Button type="button" variant="outline" size="icon" onClick={() => setBrowsing(true)} aria-label={t('backups.browser.chooseFolder')}>
                  <FolderOpen className="h-4 w-4" aria-hidden="true" />
                </Button>
              </div>
            )}
            <p className="text-xs text-muted-foreground">{t('backups.browser.safety')}</p>
          </fieldset>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t('common.cancel')}
            </Button>
            <Button type="button" onClick={handleRestore} disabled={!canRestore || restore.isPending}>
              {restore.isPending ? t('backups.restoring') : t('backups.browser.restore', { count: selected.size })}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {destination === 'folder' && (
        <DirBrowser
          open={browsing}
          onOpenChange={setBrowsing}
          mode="local"
          initialPath={targetDir.trim() || '/'}
          onSelect={(picked) => {
            setTargetDir(picked);
            setBrowsing(false);
          }}
        />
      )}
    </>
  );
}
