'use client';

import { useState } from 'react';
import { Loader2, Upload } from 'lucide-react';
import { toast } from 'sonner';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { isValidRemoteName } from '@/components/config/wizard/remote-wizard';
import { useTranslation } from '@/i18n';
import { useImportRemotes, usePreviewImport } from '@/hooks/use-remotes';
import type { ImportCandidate } from '@/types';

// The server's limit (schemas.MAX_IMPORT_CONFIG_LENGTH); the request body may be 1 MB.
export const MAX_IMPORT_LENGTH = 512 * 1024;

interface RemoteImportDialogProps {
  open:          boolean;
  onOpenChange:  (open: boolean) => void;
  /** The remotes that exist now (the server checks again on import). */
  existingNames: string[];
}

interface Choice {
  selected: boolean;
  name:     string;
}

/** A name for a remote whose own name is taken: name-imported, name-imported-2, ... */
function freeName (name: string, taken: Set<string>): string {
  if (!taken.has(name)) return name;
  for (let i = 1; ; i++) {
    const candidate = i === 1 ? `${name}-imported` : `${name}-imported-${i}`;
    if (!taken.has(candidate)) return candidate;
  }
}

/**
 * Import remotes from an existing rclone.conf: paste it or pick the file,
 * see which remotes it holds and which names clash, then import the chosen
 * ones (a clashing one under another name). The file's values are sent to
 * the server only; it never returns them.
 */
export function RemoteImportDialog ({ open, onOpenChange, existingNames }: RemoteImportDialogProps) {
  const { t } = useTranslation();
  const preview = usePreviewImport();
  const importRemotes = useImportRemotes();
  const [content, setContent] = useState('');
  const [candidates, setCandidates] = useState<ImportCandidate[] | null>(null);
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [error, setError] = useState<string | null>(null);

  const existing = new Set(existingNames);

  const reset = () => {
    setContent('');
    setCandidates(null);
    setChoices({});
    setError(null);
  };

  const close = (o: boolean) => {
    if (!o) reset();
    onOpenChange(o);
  };

  const handleFile = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > MAX_IMPORT_LENGTH) {
      setError(t('remotes.import.tooLarge'));
      return;
    }
    setError(null);
    setContent(await file.text());
    setCandidates(null);
  };

  const handlePreview = async () => {
    setError(null);
    if (content.length > MAX_IMPORT_LENGTH) {
      setError(t('remotes.import.tooLarge'));
      return;
    }
    try {
      const result = await preview.mutateAsync(content);
      if (result.errors.length > 0) {
        setError(result.errors.join(' '));
        setCandidates(null);
        return;
      }
      const taken = new Set(existing);
      const next: Record<string, Choice> = {};
      for (const c of result.remotes) {
        const name = freeName(c.name, taken);
        if (c.problems.length === 0) taken.add(name);
        next[c.name] = { selected: c.problems.length === 0 && !c.exists, name };
      }
      setChoices(next);
      setCandidates(result.remotes);
      if (result.remotes.length === 0) setError(t('remotes.import.empty'));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const selected = (candidates ?? []).filter((c) => choices[c.name]?.selected);
  const targetCounts = new Map<string, number>();
  for (const c of selected) {
    const n = choices[c.name].name;
    targetCounts.set(n, (targetCounts.get(n) ?? 0) + 1);
  }
  const nameProblem = (c: ImportCandidate): string | null => {
    const n = choices[c.name]?.name ?? '';
    // As the server checks it: not starting with '-', and not configparser's DEFAULT.
    if (!isValidRemoteName(n) || n.startsWith('-') || n === 'DEFAULT') return t('wizard.providerStep.nameError');
    if (existing.has(n)) return t('remotes.import.nameTaken');
    if ((targetCounts.get(n) ?? 0) > 1) return t('remotes.import.nameTwice');
    return null;
  };
  const canImport = selected.length > 0 && selected.every((c) => nameProblem(c) === null);

  const handleImport = async () => {
    setError(null);
    try {
      const result = await importRemotes.mutateAsync({
        content,
        remotes: selected.map((c) => ({ source: c.name, name: choices[c.name].name })),
      });
      toast.success(t('remotes.import.done', { count: result.imported.length }));
      close(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const setChoice = (name: string, change: Partial<Choice>) =>
    setChoices((prev) => ({ ...prev, [name]: { ...prev[name], ...change } }));

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-[680px]">
        <DialogHeader>
          <DialogTitle>{t('remotes.import.title')}</DialogTitle>
          <DialogDescription>{t('remotes.import.description')}</DialogDescription>
        </DialogHeader>

        {candidates === null
          ? (
          <div className="space-y-3">
            <div className="space-y-1">
              <Label htmlFor="import-file">{t('remotes.import.file')}</Label>
              <Input
                id="import-file"
                type="file"
                accept=".conf,.txt,text/plain"
                onChange={(e) => handleFile(e.target.files?.[0])}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="import-content">{t('remotes.import.paste')}</Label>
              <textarea
                id="import-content"
                dir="ltr"
                spellCheck={false}
                autoComplete="off"
                className="flex min-h-[180px] w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs"
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder={'[gdrive]\ntype = drive\n...'}
              />
              <p className="text-muted-foreground text-xs">{t('remotes.import.privacy')}</p>
            </div>
          </div>
            )
          : (
          <ul className="max-h-[50vh] space-y-2 overflow-y-auto pe-1" aria-label={t('remotes.import.found')}>
            {candidates.map((c) => {
              const choice = choices[c.name];
              const blocked = c.problems.length > 0;
              const problem = choice?.selected ? nameProblem(c) : null;
              return (
                <li key={c.name} className="rounded-md border p-3 space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <Checkbox
                      id={`import-${c.name}`}
                      checked={!!choice?.selected}
                      disabled={blocked}
                      onCheckedChange={(checked) => setChoice(c.name, { selected: checked })}
                      aria-label={t('remotes.import.select', { name: c.name })}
                    />
                    <span dir="ltr" className="font-medium">{c.name}</span>
                    <Badge variant="outline">{c.type || '?'}</Badge>
                    {c.exists && !blocked && <Badge variant="secondary">{t('remotes.import.exists')}</Badge>}
                    {blocked && <Badge variant="destructive">{t('remotes.import.refused')}</Badge>}
                  </div>
                  {blocked && (
                    <p className="text-destructive text-xs">{c.problems.join('; ')}</p>
                  )}
                  {!blocked && choice?.selected && (
                    <div className="space-y-1">
                      <Label htmlFor={`import-name-${c.name}`} className="text-xs">{t('remotes.import.importAs')}</Label>
                      <Input
                        id={`import-name-${c.name}`}
                        dir="ltr"
                        value={choice.name}
                        onChange={(e) => setChoice(c.name, { name: e.target.value })}
                        aria-invalid={problem !== null}
                      />
                      {problem && <p className="text-destructive text-xs">{problem}</p>}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
            )}

        {error && <p role="alert" className="text-destructive text-sm">{error}</p>}

        <DialogFooter>
          {candidates === null
            ? (
            <>
              <Button variant="outline" onClick={() => close(false)}>{t('common.cancel')}</Button>
              <Button onClick={handlePreview} disabled={!content.trim() || preview.isPending}>
                {preview.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                {t('remotes.import.check')}
              </Button>
            </>
              )
            : (
            <>
              <Button variant="outline" onClick={() => { setCandidates(null); setError(null); }}>
                {t('wizard.back')}
              </Button>
              <Button onClick={handleImport} disabled={!canImport || importRemotes.isPending}>
                {importRemotes.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                {t('remotes.import.submit', { count: selected.length })}
              </Button>
            </>
              )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
