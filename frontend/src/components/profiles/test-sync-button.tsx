'use client';

import { useMutation } from '@tanstack/react-query';
import { CheckCircle2, FlaskConical, Loader2, XCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n';
import { api } from '@/lib/api';
import type { TestSyncResult } from '@/types';

/** The steps POST .../test-sync reports, in order (services/rclone/probe.py test_sync). */
const KNOWN_STEPS = ['local_write', 'remote_upload', 'remote_verify', 'remote_cleanup', 'local_cleanup'];

interface TestSyncButtonProps {
  /** Test the saved profile's folders (POST /profiles/{slug}/config/test-sync). */
  slug?:     string;
  /** Otherwise test these folders (POST /config/test-sync). */
  localDir:  string;
  remoteDir: string;
  disabled?: boolean;
  size?:     'sm' | 'default';
}

/**
 * Writes a dummy file locally, uploads it, checks it arrived and removes
 * it again: shows whether the two folders can sync before anything real
 * is synced. The result stays next to the button until the folders change.
 */
export function TestSyncButton ({ slug, localDir, remoteDir, disabled, size = 'sm' }: TestSyncButtonProps) {
  const { t } = useTranslation();
  const test = useMutation<TestSyncResult, Error, { slug?: string; localDir: string; remoteDir: string }>({
    mutationFn: (v) => (v.slug ? api.testProfileSync(v.slug) : api.testSync(v.localDir, v.remoteDir)),
  });

  // A result belongs to the folders it tested; after an edit it is stale.
  const tested = test.variables;
  const current = !!tested && tested.slug === slug && tested.localDir === localDir && tested.remoteDir === remoteDir;
  const result = current ? test.data : undefined;
  const requestError = current ? test.error : null;
  const missing = !slug && (!localDir.trim() || !remoteDir.trim());

  return (
    <div className="space-y-2" data-testid="test-sync">
      <Button
        type="button"
        variant="outline"
        size={size}
        disabled={disabled || missing || test.isPending}
        onClick={() => test.mutate({ slug, localDir, remoteDir })}
      >
        {test.isPending
          ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          : <FlaskConical className="h-4 w-4" aria-hidden="true" />}
        {test.isPending ? t('config.testSyncRunning') : t('config.testSync')}
      </Button>

      <div aria-live="polite">
        {result?.success && (
          <p className="flex items-center gap-1.5 text-sm text-green-700 dark:text-green-400" role="status">
            <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden="true" />
            {t('config.testSyncSuccess')}
          </p>
        )}
        {(result && !result.success) || requestError
          ? (
          <div className="space-y-1 text-sm" role="alert">
            <p className="flex items-center gap-1.5 font-medium text-destructive">
              <XCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
              {t('config.testSyncFailed')}
            </p>
            <p className="break-words text-muted-foreground">{result?.error ?? requestError?.message}</p>
            {result && result.steps.length > 0 && (
              <ul className="space-y-0.5 text-xs">
                {result.steps.map((s) => (
                  <li key={s.step} className="flex items-center gap-1.5">
                    {s.ok
                      ? <CheckCircle2 className="h-3.5 w-3.5 text-green-600" aria-hidden="true" />
                      : <XCircle className="h-3.5 w-3.5 text-destructive" aria-hidden="true" />}
                    <span>{KNOWN_STEPS.includes(s.step) ? t(`config.testSyncSteps.${s.step}`) : s.step}</span>
                    <span className="sr-only">{s.ok ? t('config.testSyncStepOk') : t('config.testSyncStepFailed')}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
            )
          : null}
      </div>
    </div>
  );
}
