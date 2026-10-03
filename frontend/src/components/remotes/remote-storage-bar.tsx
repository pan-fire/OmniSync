'use client';

import { useRemoteStorageInfo } from '@/hooks/use-remotes';
import { useTranslation } from '@/i18n';
import { formatBytes } from '@/lib/format';

interface RemoteStorageBarProps {
  remoteName: string;
  enabled?:   boolean;
}

export function RemoteStorageBar ({ remoteName, enabled = true }: RemoteStorageBarProps) {
  const { t, locale } = useTranslation();
  const { data, isLoading } = useRemoteStorageInfo(remoteName, enabled);

  if (isLoading) {
    return <div className="h-2 rounded-full bg-muted animate-pulse" />;
  }

  if (!data || !data.supported || data.total_bytes == null || data.used_bytes == null) {
    return (
      <span className="text-xs text-muted-foreground">{t('remotes.storageUnavailable')}</span>
    );
  }

  const pct = Math.min((data.used_bytes / data.total_bytes) * 100, 100);
  const color = pct > 90 ? 'bg-red-500' : pct > 75 ? 'bg-yellow-500' : 'bg-primary';

  return (
    <div className="space-y-1">
      <div className="h-2 rounded-full bg-muted overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <p className="text-xs text-muted-foreground">
        {formatBytes(data.used_bytes, locale)} {t('remotes.usedOf')} {formatBytes(data.total_bytes, locale)}
      </p>
    </div>
  );
}
