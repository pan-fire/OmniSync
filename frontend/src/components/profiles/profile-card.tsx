'use client';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import { useTranslation } from '@/i18n';
import { stateBadgeVariant } from '@/components/sync/sync-status-card';
import { isActiveState } from '@/hooks/use-profile-sync';
import { LastErrorNotice } from '@/components/sync/last-error-notice';
import { ResyncAction } from '@/components/sync/resync-action';
import { SyncModeBadge } from './sync-mode-badge';
import { ArrowUp, ArrowDown, ArrowUpDown, Settings, Trash2 } from 'lucide-react';
import Link from 'next/link';
import { formatDateTime } from '@/lib/format';
import type { ProfileStatus, SyncDirection } from '@/types';
import { PathText } from '@/components/shared/path-text';

interface ProfileCardProps {
  profile:     ProfileStatus;
  onToggle:    (slug: string, enabled: boolean) => void;
  onDelete:    (slug: string) => void;
  /** Opens the push/pull confirmation for this profile. */
  onSync:      (profile: ProfileStatus, direction: Exclude<SyncDirection, 'two_way'>) => void;
  /** "Sync now" of a two-way profile (confirmed first when it is paused). */
  onSyncNow:   (profile: ProfileStatus) => void;
  isDeleting?: boolean;
}

export function ProfileCard ({ profile, onToggle, onDelete, onSync, onSyncNow, isDeleting }: ProfileCardProps) {
  const { t, locale } = useTranslation();
  const active = isActiveState(profile.state);
  const twoWay = profile.sync_mode === 'two_way';

  return (
    // Disabled: a dashed outline and a "Disabled" badge rather than fading
    // the whole card, which pushed its muted text below AA contrast.
    <Card className={!profile.enabled ? 'border-dashed bg-muted/40 shadow-none' : undefined} data-disabled={!profile.enabled || undefined}>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <CardTitle className="text-base font-semibold">
            <Link href={`/profiles/${profile.slug}`} className="hover:underline">
              {profile.name}
            </Link>
          </CardTitle>
          <Badge variant={stateBadgeVariant(profile.state)}>
            {t(`dashboard.state.${profile.state}`)}
          </Badge>
          <SyncModeBadge mode={profile.sync_mode} />
          {!profile.enabled && (
            <Badge variant="outline">{t('profiles.disabled')}</Badge>
          )}
          {twoWay && profile.resync_required && (
            <Badge variant="destructive">{t('twoWay.resyncRequiredBadge')}</Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Switch
            checked={profile.enabled}
            onCheckedChange={(checked) => onToggle(profile.slug, checked)}
            aria-label={t('profiles.toggleEnabledNamed', { name: profile.name })}
          />
          <Button variant="ghost" size="icon" asChild>
            <Link href={`/profiles/${profile.slug}`} aria-label={t('profiles.openSettings', { name: profile.name })}>
              <Settings className="h-4 w-4" aria-hidden="true" />
            </Link>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => onDelete(profile.slug)}
            disabled={isDeleting}
            aria-label={t('profiles.deleteNamed', { name: profile.name })}
          >
            <Trash2 className="h-4 w-4 text-destructive" aria-hidden="true" />
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
          <span className="text-muted-foreground">{t('profiles.localDir')}</span>
          <span className="truncate" title={profile.local_dir}><PathText>{profile.local_dir}</PathText></span>
          <span className="text-muted-foreground">{t('profiles.remoteDir')}</span>
          <span className="truncate" title={profile.remote_dir}><PathText>{profile.remote_dir}</PathText></span>
          {profile.last_sync && (
            <>
              <span className="text-muted-foreground">{t('dashboard.lastSync')}</span>
              <span>{formatDateTime(profile.last_sync, locale)}</span>
            </>
          )}
          {profile.pending_changes > 0 && (
            <>
              <span className="text-muted-foreground">{t('profiles.pendingChanges')}</span>
              <span>{profile.pending_changes}</span>
            </>
          )}
        </div>
        <LastErrorNotice error={profile.last_error} compact className="mt-2" />
        {profile.enabled && (
          <div className="mt-3 flex flex-wrap gap-2">
            {twoWay && profile.resync_required && (
              <ResyncAction slug={profile.slug} name={profile.name} prominent size="sm" disabled={active} />
            )}
            {twoWay && !profile.resync_required && (
              <Button size="sm" onClick={() => onSyncNow(profile)} disabled={active}>
                <ArrowUpDown className="h-3 w-3" aria-hidden="true" /> {t('twoWay.syncNow')}
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={() => onSync(profile, 'push')}
              disabled={active}
            >
              <ArrowUp className="h-3 w-3" aria-hidden="true" /> {t('dashboard.push')}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => onSync(profile, 'pull')}
              disabled={active}
            >
              <ArrowDown className="h-3 w-3" aria-hidden="true" /> {t('dashboard.pull')}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
