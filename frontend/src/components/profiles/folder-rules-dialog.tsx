'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Folder, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { ScrollArea } from '@/components/ui/scroll-area';
import { PathText } from '@/components/shared/path-text';
import { useTranslation } from '@/i18n';
import { apiFetch } from '@/lib/api';
import {
  folderState, generateRules, parseRules, setChoice, type FolderChoices, type FolderState,
} from '@/lib/folder-rules';
import type { BrowseResponse, SyncMode } from '@/types';

interface FolderRulesDialogProps {
  open:         boolean;
  onOpenChange: (open: boolean) => void;
  /** The profile's local folder: the tree's root. */
  localDir:     string;
  /** The filter rules as typed (one per line). */
  rules:        string;
  syncMode:     SyncMode;
  /** The new rules text: kept non-folder rules, then the folder rules. */
  onApply:      (rules: string) => void;
}

interface Node {
  name:     string;
  path:     string; // relative to the root, '/'-separated
  children: Node[] | null; // null: not loaded yet
}

function TriStateBox ({ state, label, onToggle }: { state: FolderState, label: string, onToggle: () => void }) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={state === 'mixed' ? 'mixed' : state === 'on'}
      aria-label={label}
      onClick={onToggle}
      className="flex size-4 shrink-0 items-center justify-center rounded-[4px] border border-primary text-[10px] leading-none focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring data-[on=true]:bg-primary data-[on=true]:text-primary-foreground"
      data-on={state !== 'off'}
    >
      {state === 'on' ? '✓' : state === 'mixed' ? '–' : ''}
    </button>
  );
}

/**
 * "Choose folders": the local folder as a tree with checkboxes. Unchecking a
 * folder leaves it (and everything in it) out of the sync; a folder with
 * some subfolders checked and others not is shown as mixed. The result is
 * written as rclone filter rules into the profile's rules field, where it
 * can still be edited by hand.
 */
export function FolderRulesDialog ({ open, onOpenChange, localDir, rules, syncMode, onApply }: FolderRulesDialogProps) {
  const { t } = useTranslation();
  const [root, setRoot] = useState<Node | null>(null);
  const [base, setBase] = useState('');
  const [expanded, setExpanded] = useState<Set<string>>(new Set(['']));
  const [loading, setLoading] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const parsed = useMemo(() => parseRules(rules.split('\n')), [rules]);
  const [choices, setChoices] = useState<FolderChoices>({});

  const load = useCallback(async (absolute: string): Promise<BrowseResponse> =>
    apiFetch<BrowseResponse>(`/browse/local?path=${encodeURIComponent(absolute)}`), []);

  const toNodes = (resp: BrowseResponse, rootPath: string): Node[] =>
    resp.entries.map((e) => ({
      name:     e.name,
      path:     e.path.slice(rootPath.length).replace(/^\/+/, ''),
      children: null,
    }));

  useEffect(() => {
    if (!open) return;
    setChoices(parsed.choices ?? {});
    setExpanded(new Set(['']));
    setRoot(null);
    setError(null);
    load(localDir)
      .then((resp) => {
        setBase(resp.current);
        setRoot({ name: resp.current, path: '', children: toNodes(resp, resp.current) });
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : t('common.error')));
    // Only when the dialog opens: the typed rules are read once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, localDir]);

  const replaceNode = (node: Node, path: string, children: Node[]): Node => {
    if (node.path === path) return { ...node, children };
    if (!node.children) return node;
    return {
      ...node,
      children: node.children.map((c) => (path === c.path || path.startsWith(`${c.path}/`) ? replaceNode(c, path, children) : c)),
    };
  };

  const expand = async (node: Node) => {
    const next = new Set(expanded);
    if (next.has(node.path)) {
      next.delete(node.path);
      setExpanded(next);
      return;
    }
    next.add(node.path);
    setExpanded(next);
    if (node.children !== null) return;
    setLoading((prev) => new Set(prev).add(node.path));
    try {
      const resp = await load(`${base}/${node.path}`);
      setRoot((r) => (r ? replaceNode(r, node.path, toNodes(resp, base)) : r));
    } catch (e) {
      setError(e instanceof Error ? e.message : t('common.error'));
    } finally {
      setLoading((prev) => {
        const s = new Set(prev);
        s.delete(node.path);
        return s;
      });
    }
  };

  const toggle = (path: string) => {
    const state = folderState(choices, path);
    setChoices(setChoice(choices, path, state === 'on' ? 'off' : 'on'));
  };

  const folderRules = generateRules(choices);
  const result = [...parsed.other, ...folderRules];

  const renderNode = (node: Node, level: number) => {
    const state = folderState(choices, node.path);
    const isOpen = expanded.has(node.path);
    const label = node.path === '' ? t('folderPicker.wholeFolder') : node.name;
    return (
      <li key={node.path || '/'} role="treeitem" aria-expanded={isOpen} aria-selected={state !== 'off'}>
        <div className="flex items-center gap-1.5 py-0.5" style={{ paddingInlineStart: `${level * 1.25}rem` }}>
          <button
            type="button"
            className="flex size-5 items-center justify-center rounded hover:bg-accent"
            onClick={() => expand(node)}
            aria-label={isOpen ? t('folderPicker.collapse', { name: label }) : t('folderPicker.expand', { name: label })}
          >
            {loading.has(node.path)
              ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
              : isOpen
                ? <ChevronDown className="h-3 w-3" aria-hidden="true" />
                : <ChevronRight className="h-3 w-3 rtl:rotate-180" aria-hidden="true" />}
          </button>
          <TriStateBox state={state} label={label} onToggle={() => toggle(node.path)} />
          <Folder className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <span className="truncate text-sm">{node.path === '' ? <PathText>{label}</PathText> : <PathText>{node.name}</PathText>}</span>
        </div>
        {isOpen && node.children && node.children.length > 0 && (
          <ul role="group">{node.children.map((c) => renderNode(c, level + 1))}</ul>
        )}
        {isOpen && node.children && node.children.length === 0 && node.path !== '' && (
          <p className="text-xs text-muted-foreground" style={{ paddingInlineStart: `${(level + 1) * 1.25 + 1.5}rem` }}>
            {t('folderPicker.noSubfolders')}
          </p>
        )}
      </li>
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('folderPicker.title')}</DialogTitle>
          <DialogDescription>{t('folderPicker.explain')}</DialogDescription>
        </DialogHeader>
        {parsed.choices === null && (
          <p className="text-xs text-amber-700 dark:text-amber-400" data-testid="folder-rules-replaced">
            {t('folderPicker.replacesRules')}
          </p>
        )}
        {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
        <ScrollArea className="h-64 rounded-md border p-2">
          {root
            ? <ul role="tree" aria-label={t('folderPicker.title')}>{renderNode(root, 0)}</ul>
            : !error && <p className="text-sm text-muted-foreground" role="status">{t('common.loading')}</p>}
        </ScrollArea>
        <div className="space-y-1">
          <p className="text-sm font-medium">{t('folderPicker.preview')}</p>
          <pre dir="ltr" className="max-h-32 overflow-auto rounded-md bg-muted p-2 text-xs" data-testid="folder-rules-preview">
            {result.length > 0 ? result.join('\n') : t('folderPicker.noRules')}
          </pre>
          {syncMode === 'two_way' && folderRules.length > 0 && (
            <p className="text-xs text-muted-foreground">{t('folderPicker.markerKept')}</p>
          )}
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>{t('common.cancel')}</Button>
          <Button
            type="button"
            onClick={() => {
              onApply(result.join('\n'));
              onOpenChange(false);
            }}
            disabled={!root}
          >
            {t('folderPicker.apply')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
