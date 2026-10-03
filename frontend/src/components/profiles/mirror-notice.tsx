'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowRightLeft, Info, X } from 'lucide-react';
import { toast } from 'sonner';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { useTranslation } from '@/i18n';
import { useProfiles } from '@/hooks/use-profiles';
import { api } from '@/lib/api';
import type { Profile } from '@/types';
import { SwitchToTwoWayButton, SwitchToTwoWayHint } from './switch-to-two-way';
import { PathText } from '@/components/shared/path-text';

/** A mirror profile whose note the user has not hidden. */
export function showsMirrorNotice (profile: Pick<Profile, 'sync_mode' | 'mirror_notice_dismissed'>): boolean {
  return profile.sync_mode === 'mirror' && !profile.mirror_notice_dismissed;
}

/**
 * Hides (or shows again) a profile's mirror-mode note. The choice is stored
 * on the profile, so the TUI and every browser honour it.
 */
function useSetMirrorNotice () {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({ slug, dismissed }: { slug: string; dismissed: boolean }) =>
      api.updateProfile(slug, { mirror_notice_dismissed: dismissed }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
    },
    onError: (error: Error) => {
      toast.error(error.message || t('mirrorNotice.hideFailed'));
    },
  });
}

/**
 * The mirror-mode note of one profile (profile page): what mirror means, the
 * risk that the next push or pull overwrites or deletes the other side's
 * changes, and the switch to two-way. Once hidden, only the short switch
 * hint remains.
 */
export function MirrorNotice ({ profile }: { profile: Profile }) {
  const { t } = useTranslation();
  const setNotice = useSetMirrorNotice();

  if (profile.sync_mode !== 'mirror') return null;
  if (profile.mirror_notice_dismissed) {
    return <SwitchToTwoWayHint slug={profile.slug} name={profile.name} />;
  }

  return (
    <Alert data-testid="mirror-notice">
      <Info aria-hidden="true" />
      <AlertTitle>{t('mirrorNotice.title')}</AlertTitle>
      <AlertDescription>
        <p>{t('mirrorNotice.what')}</p>
        <p>{t('mirrorNotice.risk')}</p>
        <p>{t('mirrorNotice.twoWay')}</p>
        <div className="mt-2 flex flex-wrap gap-2" data-testid="switch-to-two-way">
          <SwitchToTwoWayButton slug={profile.slug} name={profile.name} variant="default" />
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setNotice.mutate({ slug: profile.slug, dismissed: true })}
            disabled={setNotice.isPending}
          >
            {t('mirrorNotice.hide')}
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}

/**
 * Switches every given mirror profile to two-way, one
 * PUT /profiles/{slug} {"sync_mode": "two_way"} per profile, and reports
 * the ones that failed.
 */
function useSwitchAllToTwoWay () {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: async (profiles: Profile[]) => {
      const failed: string[] = [];
      for (const p of profiles) {
        try {
          await api.updateProfile(p.slug, { sync_mode: 'two_way' });
        } catch {
          failed.push(p.name);
        }
      }
      return { switched: profiles.length - failed.length, failed };
    },
    onSuccess: ({ switched, failed }) => {
      queryClient.invalidateQueries({ queryKey: ['profiles'] });
      if (switched > 0) toast.success(t('mirrorNotice.switchAllDone', { count: switched }));
      if (failed.length > 0) toast.error(t('mirrorNotice.switchAllFailed', { names: failed.join(', ') }));
    },
  });
}

/**
 * Dashboard card for the mirror profiles whose note is shown: the risk in
 * one paragraph, a switch and a hide button per profile, and "Switch all
 * mirror profiles to two-way" behind one confirmation that lists every
 * mirror profile.
 */
export function MirrorProfilesNotice () {
  const { t } = useTranslation();
  const profiles = useProfiles();
  const setNotice = useSetMirrorNotice();
  const switchAll = useSwitchAllToTwoWay();
  const [confirming, setConfirming] = useState<Profile[] | null>(null);

  const mirrors = (profiles.data ?? []).filter((p) => p.sync_mode === 'mirror');
  const shown = mirrors.filter(showsMirrorNotice);
  if (shown.length === 0) return null;

  return (
    <Card data-testid="mirror-profiles-notice">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Info className="h-4 w-4" aria-hidden="true" />
          {t('mirrorNotice.dashboardTitle')}
        </CardTitle>
        <CardDescription className="space-y-1">
          <span className="block">{t('mirrorNotice.dashboardIntro')}</span>
          <span className="block">{t('mirrorNotice.twoWay')}</span>
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <ul className="space-y-2">
          {shown.map((p) => (
            <li key={p.slug} className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-2">
              <Link href={`/profiles/${p.slug}`} className="min-w-0 truncate font-medium hover:underline">
                {p.name}
              </Link>
              <div className="flex items-center gap-1">
                <SwitchToTwoWayButton slug={p.slug} name={p.name} />
                <Button
                  size="icon"
                  variant="ghost"
                  onClick={() => setNotice.mutate({ slug: p.slug, dismissed: true })}
                  disabled={setNotice.isPending}
                  aria-label={t('mirrorNotice.hideNamed', { name: p.name })}
                >
                  <X className="h-4 w-4" aria-hidden="true" />
                </Button>
              </div>
            </li>
          ))}
        </ul>
        <Button onClick={() => setConfirming(mirrors)} disabled={switchAll.isPending}>
          <ArrowRightLeft className="h-4 w-4" aria-hidden="true" />
          {t('mirrorNotice.switchAll')}
        </Button>
      </CardContent>
      <Dialog open={confirming !== null} onOpenChange={(open) => { if (!open) setConfirming(null); }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('mirrorNotice.switchAllTitle')}</DialogTitle>
            <DialogDescription>{t('syncMode.two_way.desc')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-2 text-sm">
            <p className="font-medium">{t('mirrorNotice.switchAllList')}</p>
            <ul className="max-h-60 space-y-1 overflow-y-auto" data-testid="switch-all-list">
              {(confirming ?? []).map((p) => (
                <li key={p.slug} className="break-words">
                  <span className="font-medium">{p.name}</span>
                  {' '}
                  <span className="text-muted-foreground">(<PathText>{p.local_dir}</PathText> ⇄ <PathText>{p.remote_dir}</PathText>)</span>
                  {!p.enabled && <span className="text-muted-foreground"> · {t('mirrorNotice.disabled')}</span>}
                </li>
              ))}
            </ul>
            <p className="text-muted-foreground">{t('twoWay.switchFirstRun')}</p>
          </div>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button variant="outline" onClick={() => setConfirming(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              onClick={() => {
                if (confirming) switchAll.mutate(confirming);
                setConfirming(null);
              }}
            >
              {t('mirrorNotice.confirmSwitchAll')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
