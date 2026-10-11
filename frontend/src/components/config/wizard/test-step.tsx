'use client';

import { useEffect, useState } from 'react';
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

interface Outcome {
  attempt:    number;
  remoteName: string;
  result:     TestRemoteResult | null;
  error:      string | null;
}

export function TestStep ({ state, dispatch }: TestStepProps) {
  const { t } = useTranslation();
  // Each attempt (the first on mount, then one per Retry) gets its own
  // number; only the answer to the current attempt, for the current remote,
  // is shown.
  const [attempt, setAttempt] = useState(0);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  useEffect(() => {
    let ignore = false;
    api.testRemote(state.remoteName)
      .then((result) => { if (!ignore) setOutcome({ attempt, remoteName: state.remoteName, result, error: null }); })
      .catch((err: unknown) => {
        if (!ignore) setOutcome({ attempt, remoteName: state.remoteName, result: null, error: err instanceof Error ? err.message : '' });
      });
    return () => { ignore = true; };
  }, [state.remoteName, attempt]);

  const current = outcome?.attempt === attempt && outcome.remoteName === state.remoteName ? outcome : null;
  const loading = current === null;
  const result = current?.result ?? null;
  const error = current?.error ?? null;
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
            <Button onClick={() => setAttempt((a) => a + 1)}>
              {t('wizard.retry')}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
