'use client';

import { ArrowUp, ArrowDown, SkipForward, Hand, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { useTranslation } from '@/i18n';
import type { FileAction } from '@/types';

interface BatchToolbarProps {
  selectedCount: number;
  onAction:      (action: FileAction) => void;
  onClear:       () => void;
  disabled?:     boolean;
}

export function BatchToolbar ({ selectedCount, onAction, onClear, disabled }: BatchToolbarProps) {
  const { t } = useTranslation();

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-muted/30 p-2">
      <Badge variant="secondary">
        {t('granular.selectedCount', { n: selectedCount })}
      </Badge>
      <Button size="sm" variant="outline" onClick={() => onAction('push')} disabled={disabled}>
        <ArrowUp className="h-3.5 w-3.5" />
        {t('granular.pushSelected')}
      </Button>
      <Button size="sm" variant="outline" onClick={() => onAction('pull')} disabled={disabled}>
        <ArrowDown className="h-3.5 w-3.5" />
        {t('granular.pullSelected')}
      </Button>
      <Button size="sm" variant="outline" onClick={() => onAction('skip')} disabled={disabled}>
        <SkipForward className="h-3.5 w-3.5" />
        {t('granular.skipSelected')}
      </Button>
      <Button size="sm" variant="outline" onClick={() => onAction('manual')} disabled={disabled}>
        <Hand className="h-3.5 w-3.5" />
        {t('granular.markManualSelected')}
      </Button>
      <Button size="sm" variant="ghost" onClick={onClear}>
        <X className="me-1.5 h-3.5 w-3.5" />
        {t('granular.clearSelection')}
      </Button>
    </div>
  );
}
