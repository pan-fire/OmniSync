'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { RefreshCw, RotateCcw, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { PathText } from '@/components/shared/path-text';
import { useTranslation } from '@/i18n';
import { api } from '@/lib/api';
import { formatBytes, formatDateTime } from '@/lib/format';
import type { TrashActionResponse, TrashSide } from '@/types';

export function trashQueryKey (slug: string, side: TrashSide) {
  return ['profiles', slug, 'trash', side] as const;
}

type Pending = { action: 'delete' | 'overwrite', ids: string[] } | null;

/**
 * What the profile's syncs moved into .omnisync-trash on one side: restore
 * files to where they were (the next sync carries them to the other side)
 * or delete them for good.
 */
export function TrashPanel ({ slug, disabled }: { slug: string, disabled?: boolean }) {
  const { t, locale } = useTranslation();
  const queryClient = useQueryClient();
  const [side, setSide] = useState<TrashSide>('local');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState<Pending>(null);
  const trash = useQuery({
    queryKey: trashQueryKey(slug, side),
    queryFn:  () => api.getProfileTrash(slug, side),
  });

  const changeSide = (next: TrashSide) => {
    setSide(next);
    setSelected(new Set());
  };

  const report = (resp: TrashActionResponse, done: string) => {
    if (resp.done.length > 0) toast.success(t(done, { count: resp.done.length }));
    const newer = resp.failed.filter((f) => f.code === 'target_newer').map((f) => f.id);
    for (const f of resp.failed.filter((f) => f.code !== 'target_newer')) toast.error(`${f.id}: ${f.message}`);
    setSelected(new Set(newer));
    queryClient.invalidateQueries({ queryKey: ['profiles', slug, 'trash'] });
    return newer;
  };

  const restore = useMutation({
    mutationFn: ({ ids, overwrite }: { ids: string[], overwrite: boolean }) =>
      api.restoreFromTrash(slug, side, ids, overwrite),
    onSuccess: (resp) => {
      const newer = report(resp, 'trash.restored');
      if (newer.length > 0) setPending({ action: 'overwrite', ids: newer });
    },
    onError: (error: Error) => toast.error(error.message || t('common.error')),
  });
  const remove = useMutation({
    mutationFn: (ids: string[]) => api.deleteFromTrash(slug, side, ids),
    onSuccess:  (resp) => { report(resp, 'trash.deleted'); },
    onError:    (error: Error) => toast.error(error.message || t('common.error')),
  });

  const entries = trash.data?.entries ?? [];
  const allSelected = entries.length > 0 && entries.every((e) => selected.has(e.id));
  const toggle = (id: string) => setSelected((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const busy = restore.isPending || remove.isPending || !!disabled;
  const ids = [...selected];

  return (
    <Card>
      <CardHeader className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>{t('trash.title')}</CardTitle>
          <div className="flex gap-1" role="group" aria-label={t('trash.side')}>
            {(['local', 'remote'] as const).map((s) => (
              <Button
                key={s}
                size="sm"
                variant={side === s ? 'default' : 'outline'}
                aria-pressed={side === s}
                onClick={() => changeSide(s)}
              >
                {t(`trash.sides.${s}`)}
              </Button>
            ))}
            <Button
              size="icon"
              variant="ghost"
              onClick={() => trash.refetch()}
              disabled={trash.isFetching}
              aria-label={t('trash.refresh')}
            >
              <RefreshCw className={`h-4 w-4 ${trash.isFetching ? 'animate-spin' : ''}`} aria-hidden="true" />
            </Button>
          </div>
        </div>
        <p className="text-sm text-muted-foreground">{t('trash.explain')}</p>
      </CardHeader>
      <CardContent className="space-y-3">
        {trash.isLoading && <p className="text-muted-foreground" role="status">{t('common.loading')}</p>}
        {trash.isError && <p className="text-destructive" role="alert">{trash.error.message || t('common.error')}</p>}
        {trash.data && (
          <>
            <p className="text-sm" data-testid="trash-total">
              {t('trash.total', { count: trash.data.total_files, size: formatBytes(trash.data.total_bytes, locale) })}
              {trash.data.truncated && ` ${t('trash.truncated', { count: entries.length })}`}
            </p>
            {entries.length === 0
              ? <p className="text-sm text-muted-foreground">{t('trash.empty')}</p>
              : (
                <>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      onClick={() => restore.mutate({ ids, overwrite: false })}
                      disabled={busy || ids.length === 0}
                    >
                      <RotateCcw className="h-4 w-4" aria-hidden="true" />
                      {t('trash.restore', { count: ids.length })}
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => setPending({ action: 'delete', ids })}
                      disabled={busy || ids.length === 0}
                    >
                      <Trash2 className="h-4 w-4" aria-hidden="true" />
                      {t('trash.delete', { count: ids.length })}
                    </Button>
                  </div>
                  <div className="max-h-[28rem] overflow-auto rounded-md border">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-8">
                            <input
                              type="checkbox"
                              className="accent-primary"
                              checked={allSelected}
                              onChange={() => setSelected(allSelected ? new Set() : new Set(entries.map((e) => e.id)))}
                              aria-label={t('trash.selectAll')}
                            />
                          </TableHead>
                          <TableHead>{t('trash.path')}</TableHead>
                          <TableHead>{t('trash.trashedAt')}</TableHead>
                          <TableHead className="text-end">{t('trash.size')}</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {entries.map((e) => (
                          <TableRow key={e.id} data-state={selected.has(e.id) ? 'selected' : undefined}>
                            <TableCell>
                              <input
                                type="checkbox"
                                className="accent-primary"
                                checked={selected.has(e.id)}
                                onChange={() => toggle(e.id)}
                                aria-label={t('trash.select', { path: e.path })}
                              />
                            </TableCell>
                            <TableCell className="max-w-[24rem] truncate" title={e.id}><PathText>{e.path}</PathText></TableCell>
                            <TableCell className="whitespace-nowrap">
                              {formatDateTime(e.trashed_at ?? e.modified, locale)}
                            </TableCell>
                            <TableCell className="text-end tabular-nums">{formatBytes(e.size, locale)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                </>
                )}
          </>
        )}
      </CardContent>

      <Dialog open={pending !== null} onOpenChange={(open) => { if (!open) setPending(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {pending?.action === 'delete'
                ? t('trash.confirmDeleteTitle', { count: pending.ids.length })
                : t('trash.confirmOverwriteTitle', { count: pending?.ids.length ?? 0 })}
            </DialogTitle>
            <DialogDescription>
              {pending?.action === 'delete' ? t('trash.confirmDelete') : t('trash.confirmOverwrite')}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPending(null)}>{t('common.cancel')}</Button>
            <Button
              variant={pending?.action === 'delete' ? 'destructive' : 'default'}
              onClick={() => {
                if (!pending) return;
                if (pending.action === 'delete') remove.mutate(pending.ids);
                else restore.mutate({ ids: pending.ids, overwrite: true });
                setPending(null);
              }}
            >
              {pending?.action === 'delete' ? t('common.delete') : t('trash.replace')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
