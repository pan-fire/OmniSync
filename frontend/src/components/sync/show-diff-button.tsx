'use client';

import { GitCompare, X } from 'lucide-react';
import { useIsFetching, useQueryClient, type Query } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n';

/** Every profile's diff query: ['profiles', slug, 'diff']. */
function isDiffQuery (query: Query): boolean {
  const key = query.queryKey;
  return key[0] === 'profiles' && key[2] === 'diff';
}

interface ShowDiffButtonProps {
  shown:    boolean;
  disabled: boolean;
  onShow:   () => void;
  onHide:   () => void;
}

/**
 * Show Diff opens the diff tabs. Once they are open, the button refreshes
 * them instead: the tables stay visible and the icon spins while the new
 * diff loads. A separate button hides them.
 */
export function ShowDiffButton ({ shown, disabled, onShow, onHide }: ShowDiffButtonProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const refreshing = useIsFetching({ predicate: isDiffQuery }) > 0;

  const refresh = () => {
    queryClient.invalidateQueries({ predicate: isDiffQuery });
    // The tab list comes from the aggregate status: a profile with new
    // differences gets its tab.
    queryClient.invalidateQueries({ queryKey: ['sync', 'status'] });
  };

  return (
    <>
      <Button
        variant="outline"
        onClick={shown ? refresh : onShow}
        disabled={disabled}
        aria-busy={shown && refreshing}
      >
        <GitCompare className={`h-4 w-4 ${shown && refreshing ? 'animate-spin' : ''}`} aria-hidden="true" />
        {shown ? t('granular.refreshDiff') : t('granular.showDiff')}
        {shown && refreshing && <span className="sr-only">{t('granular.refreshingDiff')}</span>}
      </Button>
      {shown && (
        <Button variant="ghost" onClick={onHide}>
          <X className="me-2 h-4 w-4" aria-hidden="true" />
          {t('granular.hideDiff')}
        </Button>
      )}
    </>
  );
}
