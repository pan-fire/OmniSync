'use client';

import { useState, useMemo, useCallback, useRef, useEffect } from 'react';
import { AlertTriangle, ArrowUpDown, ChevronDown, ChevronRight, Filter, FolderTree, Loader2, Search } from 'lucide-react';
import { useVirtualizer } from '@tanstack/react-virtual';
import {
  TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import {
  Tooltip, TooltipContent, TooltipProvider, TooltipTrigger,
} from '@/components/ui/tooltip';
import { useTranslation } from '@/i18n';
import { formatBytes, formatDateTime } from '@/lib/format';
import { PathText } from '@/components/shared/path-text';
import { toast } from 'sonner';
import { BatchToolbar } from './batch-toolbar';
import { ConflictDialog } from './conflict-dialog';
import type { FileDiff, ChangeCategory, FileAction, SelectiveSyncItem, DiffSummary } from '@/types';

// --- Exported helpers for testing ---

export type SortKey = 'path' | 'category' | 'local_size' | 'local_mod_time';
export type SortDir = 'asc' | 'desc';

export function sortFiles (files: FileDiff[], key: SortKey, dir: SortDir): FileDiff[] {
  return [...files].sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    // nulls last
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    const cmp = typeof av === 'number' && typeof bv === 'number'
      ? av - bv
      : String(av).localeCompare(String(bv));
    return dir === 'asc' ? cmp : -cmp;
  });
}

export function filterFiles (files: FileDiff[], category: ChangeCategory | 'all'): FileDiff[] {
  if (category === 'all') return files;
  return files.filter((f) => f.category === category);
}

export function computeSummary (files: FileDiff[]): DiffSummary {
  const summary: DiffSummary = {
    local_only:      0,
    remote_only:     0,
    modified_local:  0,
    modified_remote: 0,
    modified_both:   0,
    manual:          0,
    total:           files.length,
  };
  for (const f of files) {
    if (f.manual_flag) summary.manual++;
    if (f.category in summary) {
      summary[f.category as keyof Omit<DiffSummary, 'manual' | 'total'>]++;
    }
  }
  return summary;
}

export function searchFiles (files: FileDiff[], query: string): FileDiff[] {
  if (!query) return files;
  const lower = query.toLowerCase();
  return files.filter((f) => f.path.toLowerCase().includes(lower));
}

// --- Directory tree helpers ---

export interface DirNode {
  type:     'dir';
  name:     string;
  path:     string;
  files:    FileDiff[];
  children: DirNode[];
}

export type FlatRow = { type: 'dir'; node: DirNode; depth: number } | { type: 'file'; file: FileDiff; depth: number };

export function buildDirectoryTree (files: FileDiff[]): DirNode {
  const root: DirNode = { type: 'dir', name: '', path: '', files: [], children: [] };
  const dirMap = new Map<string, DirNode>();
  dirMap.set('', root);

  for (const file of files) {
    const parts = file.path.split('/');
    parts.pop();
    let currentPath = '';
    let parent = root;

    for (const part of parts) {
      const nextPath = currentPath ? `${currentPath}/${part}` : part;
      let node = dirMap.get(nextPath);
      if (!node) {
        node = { type: 'dir', name: part, path: nextPath, files: [], children: [] };
        dirMap.set(nextPath, node);
        parent.children.push(node);
      }
      parent = node;
      currentPath = nextPath;
    }
    // Attach file under its leaf directory — fileName kept in file.path
    parent.files.push(file);
  }

  return root;
}

export function flattenTree (root: DirNode, expanded: Set<string>, depth = 0): FlatRow[] {
  const rows: FlatRow[] = [];

  // Sort children dirs first, then files
  const sortedChildren = [...root.children].sort((a, b) => a.name.localeCompare(b.name));
  const sortedFiles = [...root.files].sort((a, b) => a.path.localeCompare(b.path));

  for (const child of sortedChildren) {
    rows.push({ type: 'dir', node: child, depth });
    if (expanded.has(child.path)) {
      rows.push(...flattenTree(child, expanded, depth + 1));
    }
  }
  for (const file of sortedFiles) {
    rows.push({ type: 'file', file, depth });
  }
  return rows;
}

function countTreeFiles (node: DirNode): number {
  let count = node.files.length;
  for (const child of node.children) count += countTreeFiles(child);
  return count;
}

function collectTreeFiles (node: DirNode): FileDiff[] {
  const result: FileDiff[] = [...node.files];
  for (const child of node.children) result.push(...collectTreeFiles(child));
  return result;
}

// The -700 text keeps AA contrast on the pale tint in light mode; the
// -400 shades are for the dark background.
const CATEGORY_COLORS: Record<ChangeCategory, string> = {
  local_only:      'bg-blue-500/15 text-blue-700 dark:text-blue-400',
  remote_only:     'bg-purple-500/15 text-purple-700 dark:text-purple-400',
  modified_local:  'bg-green-500/15 text-green-700 dark:text-green-400',
  modified_remote: 'bg-yellow-500/15 text-yellow-800 dark:text-yellow-400',
  modified_both:   'bg-red-500/15 text-red-700 dark:text-red-400',
};

/**
 * Whether `action` can apply to `file`: there is nothing to push for a
 * file that exists only on the remote, nothing to pull for one that exists
 * only locally (the backend rejects both).
 */
export function canApplyAction (file: Pick<FileDiff, 'category'>, action: FileAction): boolean {
  if (action === 'push') return file.category !== 'remote_only';
  if (action === 'pull') return file.category !== 'local_only';
  if (action === 'keep_both') return file.category !== 'local_only' && file.category !== 'remote_only';
  return true;
}

type SummaryBadgeProps = {
  label:      string;
  tip:        string;
  className?: string;
  variant?:   'secondary' | 'outline';
};

/** A count with an explanation: a tooltip on hover or focus, and sr-only text for screen readers. */
function SummaryBadge ({ label, tip, className, variant }: SummaryBadgeProps) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant={variant} className={className} tabIndex={0}>
          {label}
          <span className="sr-only">: {tip}</span>
        </Badge>
      </TooltipTrigger>
      <TooltipContent aria-hidden="true"><p>{tip}</p></TooltipContent>
    </Tooltip>
  );
}

// --- Component ---

interface FileBrowserProps {
  files:           FileDiff[];
  summary:         DiffSummary;
  onSync:          (items: SelectiveSyncItem[]) => void;
  isSyncing?:      boolean;
  pendingPaths?:   ReadonlySet<string>;
  /** Undo "Mark manual" for a file. */
  onUnmarkManual?: (path: string) => void;
}

const ROW_HEIGHT = 40;
const OVERSCAN = 10;
const MAX_HEIGHT = 600;

// Stable default so the memoised callbacks below do not change every render.
const NO_PENDING: ReadonlySet<string> = new Set();

export function FileBrowser ({ files, summary, onSync, isSyncing, pendingPaths = NO_PENDING, onUnmarkManual }: FileBrowserProps) {
  const { t, locale } = useTranslation();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sortKey, setSortKey] = useState<SortKey>('path');
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [categoryFilter, setCategoryFilter] = useState<ChangeCategory | 'all'>('all');
  // Conflicting files waiting for a decision in the conflict dialog.
  const [conflictQueue, setConflictQueue] = useState<FileDiff[]>([]);
  const [searchInput, setSearchInput] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [groupByDir, setGroupByDir] = useState(false);
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());
  const scrollRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // Debounced search
  const handleSearchChange = useCallback((value: string) => {
    setSearchInput(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setSearchQuery(value), 300);
  }, []);

  useEffect(() => () => { if (debounceRef.current) clearTimeout(debounceRef.current); }, []);

  // Filter pipeline: category → search → sort
  const filtered = useMemo(() => {
    const catFiltered = filterFiles(files, categoryFilter);
    return searchFiles(catFiltered, searchQuery);
  }, [files, categoryFilter, searchQuery]);
  const sorted = useMemo(() => sortFiles(filtered, sortKey, sortDir), [filtered, sortKey, sortDir]);

  // Directory tree mode
  const tree = useMemo(() => groupByDir ? buildDirectoryTree(sorted) : null, [sorted, groupByDir]);
  const flatRows = useMemo(() => tree ? flattenTree(tree, expandedDirs) : null, [tree, expandedDirs]);

  // Items for virtualizer: use flat rows in dir mode, sorted files otherwise
  const rowCount = flatRows ? flatRows.length : sorted.length;

  // The React Compiler skips this component because of useVirtualizer; the
  // callbacks above are memoised by hand, so that is fine.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count:            rowCount,
    getScrollElement: () => scrollRef.current,
    estimateSize:     () => ROW_HEIGHT,
    overscan:         OVERSCAN,
  });

  // Reset scroll on sort/filter/search/group change
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 });
  }, [sortKey, sortDir, categoryFilter, searchQuery, groupByDir]);

  const allSelected = sorted.length > 0 && sorted.every((f) => !pendingPaths.has(f.path) && selected.has(f.path));

  const toggleSort = useCallback((key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(key); setSortDir('asc'); }
  }, [sortKey]);

  const toggleSelect = useCallback((path: string) => {
    if (pendingPaths.has(path)) return;
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path); else next.add(path);
      return next;
    });
  }, [pendingPaths]);

  const toggleAll = useCallback(() => {
    if (allSelected) setSelected(new Set());
    else setSelected(new Set(sorted.filter((f) => !pendingPaths.has(f.path)).map((f) => f.path)));
  }, [allSelected, sorted, pendingPaths]);

  /**
   * Apply `action` to `targets`, except that conflicting files are never
   * pushed or pulled blindly: they go through the conflict dialog one by
   * one. Skip and manual are safe for conflicts and apply directly.
   */
  const applyAction = useCallback((allTargets: FileDiff[], action: FileAction) => {
    const targets = allTargets.filter((f) => canApplyAction(f, action));
    const inapplicable = allTargets.length - targets.length;
    if (inapplicable > 0) toast.warning(t('granular.skippedInapplicable', { count: inapplicable }));
    const overwrites = action === 'push' || action === 'pull' || action === 'keep_both';
    const conflicts = overwrites ? targets.filter((f) => f.is_conflict) : [];
    const direct = overwrites ? targets.filter((f) => !f.is_conflict) : targets;
    if (direct.length > 0) onSync(direct.map((f) => ({ path: f.path, action })));
    if (conflicts.length > 0) setConflictQueue(conflicts);
  }, [onSync, t]);

  const handleRowAction = useCallback((file: FileDiff, action: FileAction) => {
    applyAction([file], action);
  }, [applyAction]);

  const handleBatch = useCallback((action: FileAction) => {
    const byPath = new Map(files.map((f) => [f.path, f]));
    const targets = Array.from(selected)
      .map((path) => byPath.get(path))
      .filter((f): f is FileDiff => !!f);
    applyAction(targets, action);
    setSelected(new Set());
  }, [files, selected, applyAction]);

  const handleConflictResolve = useCallback((action: FileAction, applyToRemaining: boolean) => {
    const [current, ...rest] = conflictQueue;
    if (!current) return;
    if (applyToRemaining) {
      onSync(conflictQueue.map((f) => ({ path: f.path, action })));
      setConflictQueue([]);
      return;
    }
    onSync([{ path: current.path, action }]);
    setConflictQueue(rest);
  }, [conflictQueue, onSync]);

  const toggleDir = useCallback((dirPath: string) => {
    setExpandedDirs(prev => {
      const next = new Set(prev);
      if (next.has(dirPath)) next.delete(dirPath); else next.add(dirPath);
      return next;
    });
  }, []);

  const handleDirBatch = useCallback((node: DirNode, action: FileAction) => {
    applyAction(collectTreeFiles(node).filter((f) => !pendingPaths.has(f.path)), action);
  }, [applyAction, pendingPaths]);

  const isSearchActive = searchQuery.length > 0;

  return (
    <div className="space-y-3">
      {/* Summary bar */}
      <TooltipProvider>
      <div className="flex flex-wrap gap-2 text-xs" data-testid="diff-summary">
        <SummaryBadge
          variant="secondary"
          label={isSearchActive
            ? t('granular.totalFiltered', { shown: filtered.length, count: summary.total })
            : t('granular.total', { count: summary.total })}
          tip={t('granular.tipTotal')}
        />
        <SummaryBadge
          className={CATEGORY_COLORS.local_only}
          label={t('granular.localOnly', { count: summary.local_only })}
          tip={t('granular.tipLocalOnly')}
        />
        <SummaryBadge
          className={CATEGORY_COLORS.remote_only}
          label={t('granular.remoteOnly', { count: summary.remote_only })}
          tip={t('granular.tipRemoteOnly')}
        />
        <SummaryBadge
          className={CATEGORY_COLORS.modified_local}
          label={t('granular.modifiedLocal', { count: summary.modified_local })}
          tip={t('granular.tipModifiedLocal')}
        />
        <SummaryBadge
          className={CATEGORY_COLORS.modified_remote}
          label={t('granular.modifiedRemote', { count: summary.modified_remote })}
          tip={t('granular.tipModifiedRemote')}
        />
        <SummaryBadge
          className={CATEGORY_COLORS.modified_both}
          label={t('granular.modifiedBoth', { count: summary.modified_both })}
          tip={t('granular.tipModifiedBoth')}
        />
        {summary.manual > 0 && (
          <Badge variant="outline">{t('granular.manualCount', { count: summary.manual })}</Badge>
        )}
      </div>
      </TooltipProvider>

      {/* Filter + Search + Group */}
      <div className="flex items-center gap-2 flex-wrap">
        <Filter className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        <Select value={categoryFilter} onValueChange={(v) => setCategoryFilter(v as ChangeCategory | 'all')}>
          <SelectTrigger className="w-48 h-8 text-xs" aria-label={t('granular.filterCategory')}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t('granular.filterAll')}</SelectItem>
            <SelectItem value="local_only">{t('granular.catLocalOnly')}</SelectItem>
            <SelectItem value="remote_only">{t('granular.catRemoteOnly')}</SelectItem>
            <SelectItem value="modified_local">{t('granular.catModifiedLocal')}</SelectItem>
            <SelectItem value="modified_remote">{t('granular.catModifiedRemote')}</SelectItem>
            <SelectItem value="modified_both">{t('granular.catModifiedBoth')}</SelectItem>
          </SelectContent>
        </Select>
        <div className="relative">
          <Search className="absolute start-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            type="search"
            placeholder={t('granular.searchPlaceholder')}
            aria-label={t('granular.searchPlaceholder')}
            value={searchInput}
            onChange={(e) => handleSearchChange(e.target.value)}
            className="h-8 w-56 ps-7 text-xs"
            data-testid="file-search-input"
          />
        </div>
        <Button
          variant={groupByDir ? 'default' : 'outline'}
          size="sm"
          className="h-8 text-xs"
          onClick={() => setGroupByDir(v => !v)}
          aria-pressed={groupByDir}
          data-testid="group-by-dir-toggle"
        >
          <FolderTree className="h-3.5 w-3.5" />
          {t('granular.groupByDir')}
        </Button>
      </div>

      {/* Batch toolbar */}
      {selected.size > 0 && (
        <BatchToolbar
          selectedCount={selected.size}
          onAction={handleBatch}
          onClear={() => setSelected(new Set())}
          disabled={isSyncing || pendingPaths.size > 0}
        />
      )}

      {/* Virtualized Table */}
      <div className="rounded-md border overflow-hidden">
        <div
          ref={scrollRef}
          className="overflow-auto"
          style={{ maxHeight: MAX_HEIGHT }}
          data-testid="virtual-scroll-container"
        >
          <table className="w-full text-sm" style={{ tableLayout: 'fixed' }}>
            <colgroup>
              <col style={{ width: '40px' }} />
              <col />
              <col style={{ width: '120px' }} />
              <col style={{ width: '100px' }} />
              <col style={{ width: '100px' }} />
              <col style={{ width: '160px' }} />
              <col style={{ width: '160px' }} />
              <col style={{ width: '80px' }} />
            </colgroup>
            <TableHeader className="sticky top-0 z-10 bg-background">
              <TableRow>
                <TableHead>
                  <Checkbox checked={allSelected} onCheckedChange={toggleAll} aria-label={t('granular.selectAll')} />
                </TableHead>
                <TableHead>
                  <Button variant="ghost" size="sm" className="h-6 px-1 text-xs" onClick={() => toggleSort('path')}>
                    {t('granular.filePath')} <ArrowUpDown className="ms-1 h-3 w-3" />
                  </Button>
                </TableHead>
                <TableHead>
                  <Button variant="ghost" size="sm" className="h-6 px-1 text-xs" onClick={() => toggleSort('category')}>
                    {t('granular.category')} <ArrowUpDown className="ms-1 h-3 w-3" />
                  </Button>
                </TableHead>
                <TableHead>
                  <Button variant="ghost" size="sm" className="h-6 px-1 text-xs" onClick={() => toggleSort('local_size')}>
                    {t('granular.localSize')} <ArrowUpDown className="ms-1 h-3 w-3" />
                  </Button>
                </TableHead>
                <TableHead>{t('granular.remoteSize')}</TableHead>
                <TableHead>
                  <Button variant="ghost" size="sm" className="h-6 px-1 text-xs" onClick={() => toggleSort('local_mod_time')}>
                    {t('granular.localTime')} <ArrowUpDown className="ms-1 h-3 w-3" />
                  </Button>
                </TableHead>
                <TableHead>{t('granular.remoteTime')}</TableHead>
                <TableHead>{t('granular.actions')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
          {rowCount === 0
            ? (
              <TableRow>
                <TableCell colSpan={8} className="text-center text-muted-foreground py-8">
                  {t('granular.allResolved')}
                </TableCell>
              </TableRow>
              )
            : (<>
              {virtualizer.getVirtualItems()[0]?.start > 0 && (
                <tr><td colSpan={8} style={{ height: virtualizer.getVirtualItems()[0].start, padding: 0 }} /></tr>
              )}
              {virtualizer.getVirtualItems().map((virtualRow) => {
                const row = flatRows ? flatRows[virtualRow.index] : null;
                const file = row ? (row.type === 'file' ? row.file : null) : sorted[virtualRow.index];

                // Directory header row
                if (row?.type === 'dir') {
                  const node = row.node;
                  const isExpanded = expandedDirs.has(node.path);
                  const fileCount = countTreeFiles(node);
                  return (
                        <TableRow
                          key={`dir-${node.path}`}
                          data-index={virtualRow.index}
                          ref={virtualizer.measureElement}
                          className="bg-muted/30"
                          style={{ height: ROW_HEIGHT }}
                        >
                          <TableCell colSpan={7} style={{ paddingInlineStart: `${row.depth * 16}px` }}>
                            <button
                              type="button"
                              className="flex items-center gap-1 text-xs font-medium hover:underline"
                              onClick={() => toggleDir(node.path)}
                              aria-expanded={isExpanded}
                              data-testid={`dir-toggle-${node.path}`}
                            >
                              {isExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5 rtl:rotate-180" />}
                              <PathText>{node.name}/</PathText>
                              <Badge variant="secondary" className="ms-1 text-[10px]">{fileCount}</Badge>
                            </button>
                          </TableCell>
                          <TableCell>
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-6 px-2 text-xs"
                                  aria-label={t('granular.actionForDir', { path: node.path })}
                                >
                                  {t('granular.action')}
                                </Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end">
                                <DropdownMenuItem onClick={() => handleDirBatch(node, 'push')}>{t('granular.pushAll')}</DropdownMenuItem>
                                <DropdownMenuItem onClick={() => handleDirBatch(node, 'pull')}>{t('granular.pullAll')}</DropdownMenuItem>
                                <DropdownMenuItem onClick={() => handleDirBatch(node, 'skip')}>{t('granular.skipAll')}</DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          </TableCell>
                        </TableRow>
                  );
                }

                // File row (flat or tree mode)
                if (!file) return null;
                const isPending = pendingPaths.has(file.path);
                const indentStyle = row ? { paddingInlineStart: `${row.depth * 16}px` } : undefined;

                return (
                      <TableRow
                        key={file.path}
                        data-index={virtualRow.index}
                        ref={virtualizer.measureElement}
                        data-state={selected.has(file.path) ? 'selected' : undefined}
                        className={isPending ? 'opacity-50 pointer-events-none' : undefined}
                        style={{ height: ROW_HEIGHT }}
                      >
                        <TableCell>
                          <Checkbox
                            checked={selected.has(file.path)}
                            onCheckedChange={() => toggleSelect(file.path)}
                            disabled={isPending}
                            aria-label={t('granular.selectFile', { path: file.path })}
                          />
                        </TableCell>
                        <TableCell className="font-mono text-xs truncate" title={file.path} style={indentStyle}>
                          {file.is_conflict && <AlertTriangle className="inline me-1 h-3.5 w-3.5 text-red-600 dark:text-red-400" aria-label={t('granular.conflict')} />}
                          <PathText>{groupByDir ? file.path.split('/').pop() : file.path}</PathText>
                        </TableCell>
                        <TableCell>
                          <Badge className={CATEGORY_COLORS[file.category] + ' text-xs'}>
                            {t(`granular.cat${capitalize(file.category)}`)}
                          </Badge>
                          {file.manual_flag && (
                            <Badge variant="outline" className="ms-1 text-xs">{t('granular.manual')}</Badge>
                          )}
                        </TableCell>
                        <TableCell className="text-xs">{formatBytes(file.local_size, locale, t('granular.na'))}</TableCell>
                        <TableCell className="text-xs">{formatBytes(file.remote_size, locale, t('granular.na'))}</TableCell>
                        <TableCell className="text-xs">{formatDateTime(file.local_mod_time, locale, t('granular.na'))}</TableCell>
                        <TableCell className="text-xs">{formatDateTime(file.remote_mod_time, locale, t('granular.na'))}</TableCell>
                        <TableCell>
                          {isPending
                            ? (
                            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" data-testid="row-spinner" />
                              )
                            : (
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button variant="ghost" size="sm" className="h-6 px-2 text-xs">
                                {t('granular.action')}
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <RowActionItem file={file} action="push" label={t('granular.push')} reason={t('granular.pushUnavailable')} onSelect={handleRowAction} />
                              <RowActionItem file={file} action="pull" label={t('granular.pull')} reason={t('granular.pullUnavailable')} onSelect={handleRowAction} />
                              <DropdownMenuItem onClick={() => handleRowAction(file, 'skip')}>{t('granular.skip')}</DropdownMenuItem>
                              {file.manual_flag && onUnmarkManual
                                ? <DropdownMenuItem onClick={() => onUnmarkManual(file.path)}>{t('granular.unmarkManual')}</DropdownMenuItem>
                                : <DropdownMenuItem onClick={() => handleRowAction(file, 'manual')}>{t('granular.markManual')}</DropdownMenuItem>}
                            </DropdownMenuContent>
                          </DropdownMenu>
                              )}
                        </TableCell>
                      </TableRow>
                );
              })}
              {(() => {
                const items = virtualizer.getVirtualItems();
                const lastItem = items[items.length - 1];
                const paddingBottom = lastItem ? virtualizer.getTotalSize() - lastItem.end : 0;
                return paddingBottom > 0 ? <tr><td colSpan={8} style={{ height: paddingBottom, padding: 0 }} /></tr> : null;
              })()}
            </>)}
            </TableBody>
          </table>
        </div>
      </div>

      {/* Conflict dialog */}
      <ConflictDialog
        file={conflictQueue[0] ?? null}
        remaining={Math.max(0, conflictQueue.length - 1)}
        onResolve={handleConflictResolve}
        onClose={() => setConflictQueue([])}
      />
    </div>
  );
}

type RowActionItemProps = {
  file:     FileDiff;
  action:   'push' | 'pull';
  label:    string;
  /** Shown under the label when the action cannot apply to this file. */
  reason:   string;
  onSelect: (file: FileDiff, action: FileAction) => void;
};

function RowActionItem ({ file, action, label, reason, onSelect }: RowActionItemProps) {
  const applicable = canApplyAction(file, action);
  return (
    <DropdownMenuItem
      disabled={!applicable}
      onClick={() => onSelect(file, action)}
      className={applicable ? undefined : 'flex-col items-start gap-0'}
    >
      {label}
      {!applicable && <span className="text-xs text-muted-foreground">{reason}</span>}
    </DropdownMenuItem>
  );
}

function capitalize (s: string): string {
  return s.replace(/(^|_)([a-z])/g, (_, __, c) => c.toUpperCase());
}
