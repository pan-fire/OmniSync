'use client';

import { useEffect, useCallback, useState } from 'react';
import type { Dispatch } from 'react';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n';
import { api } from '@/lib/api';
import { CheckCircle, XCircle, Loader2 } from 'lucide-react';
import type { TestRemoteResult } from '@/types';
import type { WizardState, WizardAction } from './remote-wizard';

interface TestStepProps {
  state:    WizardState;
  dispatch: Dispatch<WizardAction>;
}

export function TestStep ({ state, dispatch }: TestStepProps) {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<TestRemoteResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runTest = useCallback(async () => {
    setLoading(true);
    setResult(null);
    setError(null);
    try {
      const res = await api.testRemote(state.remoteName);
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : '');
    } finally {
      setLoading(false);
    }
  }, [state.remoteName]);

  // Run test once on mount
  useEffect(() => {
    runTest();
  }, [runTest]);

  const success = result?.success === true;
  const failed = (result !== null && !result.success) || error !== null;
  const failMessage = error || result?.error || t('wizard.testStep.unknownError');

  // Auto-advance on success
  useEffect(() => {
    if (success) {
      dispatch({ type: 'NEXT_STEP' });
    }
  }, [success, dispatch]);

  return (
    <div className="flex flex-col items-center justify-center space-y-4 py-8">
      {loading && (
        <>
          <Loader2 className="text-muted-foreground h-12 w-12 animate-spin" />
          <p className="text-muted-foreground text-sm">
            {t('wizard.testStep.testing')}
          </p>
        </>
      )}

      {!loading && success && (
        <>
          <CheckCircle className="h-12 w-12 text-green-500" />
          <div className="text-center">
            <p className="font-medium">{t('wizard.testStep.success')}</p>
            <p className="text-muted-foreground text-sm">
              {state.remoteName}
            </p>
          </div>
        </>
      )}

      {!loading && failed && (
        <>
          <XCircle className="text-destructive h-12 w-12" />
          <div className="text-center space-y-2">
            <p className="font-medium">{t('wizard.testStep.failed')}</p>
            <p className="text-destructive text-sm">{failMessage}</p>
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={() => dispatch({ type: 'PREV_STEP' })}
            >
              {t('wizard.back')}
            </Button>
            <Button onClick={runTest}>
              {t('wizard.retry')}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
