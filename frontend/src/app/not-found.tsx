'use client';

import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n';

export default function NotFound () {
  const { t } = useTranslation();

  return (
    <div className="flex flex-col items-center justify-center gap-4 py-16 text-center">
      <h2 className="text-lg font-semibold">{t('errors.notFoundTitle')}</h2>
      <p className="text-sm text-muted-foreground">{t('errors.notFoundHint')}</p>
      <Button asChild variant="outline">
        <Link href="/">{t('errors.backToDashboard')}</Link>
      </Button>
    </div>
  );
}
