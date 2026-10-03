'use client';

import { Loader2 } from 'lucide-react';
import { useTranslation } from '@/i18n';

export default function Loading () {
  const { t } = useTranslation();

  return (
    <div className="flex items-center justify-center py-16 text-muted-foreground" role="status">
      <Loader2 className="me-2 h-5 w-5 animate-spin" aria-hidden="true" />
      {t('common.loading')}
    </div>
  );
}
