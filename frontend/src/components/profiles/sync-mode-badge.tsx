'use client';

import { ArrowRightLeft, Copy } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { useTranslation } from '@/i18n';
import type { SyncMode } from '@/types';

/** The profile's sync mode: two-way, or mirror (one-way push/pull). */
export function SyncModeBadge ({ mode }: { mode: SyncMode }) {
  const { t } = useTranslation();
  const Icon = mode === 'two_way' ? ArrowRightLeft : Copy;
  return (
    <Badge
      variant="outline"
      title={t(`syncMode.${mode}.desc`)}
      data-testid="sync-mode-badge"
    >
      <Icon aria-hidden="true" />
      {t(`syncMode.${mode}.badge`)}
    </Badge>
  );
}
