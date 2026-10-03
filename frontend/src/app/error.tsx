'use client';

import { useEffect } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n';

export default function AppError ({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const { t } = useTranslation();

  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex flex-col items-center justify-center gap-4 py-16 text-center" role="alert">
      <AlertTriangle className="h-10 w-10 text-destructive" aria-hidden="true" />
      <div className="space-y-1">
        <h2 className="text-lg font-semibold">{t('errors.pageTitle')}</h2>
        <p className="max-w-xl break-words text-sm text-muted-foreground">
          {error.message || t('common.error')}
        </p>
      </div>
      <Button onClick={reset}>{t('common.retry')}</Button>
    </div>
  );
}
