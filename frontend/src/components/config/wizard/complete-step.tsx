'use client';

import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n';
import { useQueryClient } from '@tanstack/react-query';
import { CheckCircle } from 'lucide-react';
import type { WizardState } from './remote-wizard';

interface CompleteStepProps {
  state:  WizardState;
  onDone: () => void;
}

export function CompleteStep ({ state, onDone }: CompleteStepProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const handleDone = () => {
    queryClient.invalidateQueries({ queryKey: ['remotes'] });
    onDone();
  };

  return (
    <div className="flex flex-col items-center justify-center space-y-4 py-8">
      <CheckCircle className="h-16 w-16 text-green-500" />
      <div className="text-center space-y-1">
        <p className="text-lg font-medium">
          {state.mode === 'reconnect' ? t('wizard.reconnect.done') : t('wizard.completeStep.title')}
        </p>
        <p className="text-muted-foreground text-sm">
          {`${state.remoteName} (${state.providerId ?? ''})`}
        </p>
      </div>
      <Button onClick={handleDone}>{t('wizard.done')}</Button>
    </div>
  );
}
