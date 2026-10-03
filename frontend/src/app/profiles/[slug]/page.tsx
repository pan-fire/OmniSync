'use client';

import { useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { AlertTriangle, ArrowDown, ArrowLeft, ArrowUp, ArrowUpDown, Pause, Plus } from 'lucide-react';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { useTranslation } from '@/i18n';
import { stateBadgeVariant } from '@/components/sync/sync-status-card';
import { useProfile, useUpdateProfile, useDeleteProfile } from '@/hooks/use-profiles';
import {
  isActiveState,
  useStopProfileSync,
  useResumeProfileIntervals,
} from '@/hooks/use-profile-sync';
import { useBackupTargets, useDeleteBackupTarget, useRunBackup, useUpdateBackupTarget } from '@/hooks/use-backups';
import { ProfileForm, type ProfileFormSubmission } from '@/components/profiles/profile-form';
import { ProfileDeleteDialog } from '@/components/profiles/profile-delete-dialog';
import { BackupTargetCard } from '@/components/profiles/backup-target-card';
import { BackupTargetForm } from '@/components/profiles/backup-target-form';
import { SyncStatusCard } from '@/components/sync/sync-status-card';
import { ProfileDiffPanel } from '@/components/sync/profile-diff-panel';
import { useConfirmedSync } from '@/components/sync/sync-confirm-dialog';
import { LastErrorNotice } from '@/components/sync/last-error-notice';
import { ResyncAction } from '@/components/sync/resync-action';
import { SyncModeBadge } from '@/components/profiles/sync-mode-badge';
import { MirrorNotice } from '@/components/profiles/mirror-notice';
import { TestSyncButton } from '@/components/profiles/test-sync-button';
import { PageHeader } from '@/components/layout/page-header';
import { ProfileJobHistory } from '@/components/jobs/profile-job-history';
import { TrashPanel } from '@/components/profiles/trash-panel';
import { usePauseProfile } from '@/hooks/use-pause';
import { ApiError } from '@/types';
import type { ProfileUpdateRequest, SyncStatus, BackupTarget } from '@/types';
import { PathText } from '@/components/shared/path-text';

export default function ProfileDetailPage () {
  const { t } = useTranslation();
  const router = useRouter();
  const params = useParams<{ slug: string }>();
  const slug = params.slug;
  const searchParams = useSearchParams();
  const [tab, setTab] = useState(() => searchParams.get('tab') || 'overview');

  // Keep the tab in the address, so reload, back and shared links return
  // to it.
  const changeTab = (next: string) => {
    setTab(next);
    const params = new URLSearchParams(searchParams.toString());
    if (next === 'overview') params.delete('tab'); else params.set('tab', next);
    const query = params.toString();
    router.replace(`${window.location.pathname}${query ? `?${query}` : ''}`, { scroll: false });
  };

  const { data: profile, isError, isLoading, error, refetch, isFetching } = useProfile(slug);
  const updateProfile = useUpdateProfile(slug);
  const deleteProfile = useDeleteProfile();
  const confirmedSync = useConfirmedSync();
  const stopSync = useStopProfileSync(slug);
  const resumeIntervals = useResumeProfileIntervals(slug);
  const pauseProfile = usePauseProfile(slug);
  const backupTargetsQuery = useBackupTargets(slug);
  const backupTargets = backupTargetsQuery.data;
  const deleteBackup = useDeleteBackupTarget(slug);
  const runBackup = useRunBackup(slug);
  const updateBackup = useUpdateBackupTarget(slug);
  const [showEdit, setShowEdit] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [editingTarget, setEditingTarget] = useState<BackupTarget | null>(null);
  const [showTargetForm, setShowTargetForm] = useState(false);

  const active = isActiveState(profile?.state);

  const handleUpdate = (data: ProfileUpdateRequest) => {
    updateProfile.mutate(data as ProfileUpdateRequest, {
      onSuccess: (updated) => {
        setShowEdit(false);
        // A rename changes the slug: the old address would show "not found".
        if (updated.slug !== slug) router.replace(`/profiles/${encodeURIComponent(updated.slug)}`);
      },
    });
  };

  const handleDelete = () => {
    deleteProfile.mutate(slug, {
      onSuccess: () => {
        setShowDeleteConfirm(false);
        router.push('/profiles');
      },
    });
  };

  if (isLoading) {
    return <p className="text-muted-foreground p-4" role="status">{t('common.loading')}</p>;
  }

  if (isError || !profile) {
    // Only a 404 means the profile is gone; anything else (backend down,
    // timeout) is worth retrying.
    const notFound = !isError || (error instanceof ApiError && error.status === 404);
    return (
      <div className="space-y-4">
        <Button variant="ghost" asChild>
          <Link href="/profiles"><ArrowLeft className="h-4 w-4 rtl:rotate-180" aria-hidden="true" /> {t('profiles.backToList')}</Link>
        </Button>
        {notFound
          ? <p className="text-destructive" role="alert">{t('profiles.notFound')}</p>
          : (
            <div className="flex flex-wrap items-center gap-3" role="alert">
              <p className="text-destructive">
                {t('profiles.loadFailed')}
                {error?.message ? `: ${error.message}` : ''}
              </p>
              <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isFetching}>
                {t('common.retry')}
              </Button>
            </div>
            )}
      </div>
    );
  }

  // Build SyncStatus from profile data for reusing SyncStatusCard
  const syncStatus: SyncStatus = {
    state:               profile.state,
    last_sync:           profile.last_sync,
    current_job_id:      profile.current_job_id,
    files_processed:     profile.files_processed,
    errors:              profile.errors,
    pending_changes:     profile.pending_changes,
    intervals_paused:    profile.intervals_paused,
    paused_at:           profile.paused_at,
    last_error:          profile.last_error,
    resync_required:     profile.resync_required,
    user_paused:         profile.user_paused,
    progress:            profile.progress,
    outside_sync_window: profile.outside_sync_window,
    next_window_start:   profile.next_window_start,
    waiting_for_window:  profile.waiting_for_window,
  };
  const twoWay = profile.sync_mode === 'two_way';
  const syncDisabled = active || !profile.enabled || confirmedSync.isStarting;

  return (
    <div className="space-y-6">
      <PageHeader
        title={profile.name}
        beforeTitle={(
          <Button variant="ghost" size="icon" asChild>
            <Link href="/profiles" aria-label={t('profiles.backToList')}>
              <ArrowLeft className="h-4 w-4 rtl:rotate-180" aria-hidden="true" />
            </Link>
          </Button>
        )}
        afterTitle={(
          <>
            <Badge variant={stateBadgeVariant(profile.state)}>
              {t(`dashboard.state.${profile.state}`)}
            </Badge>
            <SyncModeBadge mode={profile.sync_mode} />
          </>
        )}
      />

      {twoWay && profile.resync_required && (
        <Alert variant="destructive" data-testid="resync-required">
          <AlertTriangle aria-hidden="true" />
          <AlertTitle>{t('twoWay.resyncRequiredTitle')}</AlertTitle>
          <AlertDescription>
            <p>{t('twoWay.resyncRequiredExplain')}</p>
            {profile.last_error && (
              <p className="break-words">
                <span className="font-medium">{t('twoWay.reason')}:</span> {profile.last_error}
              </p>
            )}
            <div className="mt-2">
              <ResyncAction slug={slug} name={profile.name} prominent disabled={active || !profile.enabled} />
            </div>
          </AlertDescription>
        </Alert>
      )}

      {/* Sync controls */}
      <div className="flex flex-wrap gap-2">
        {twoWay && (
          <Button
            onClick={() => confirmedSync.syncNow({ slug, name: profile.name }, profile.intervals_paused)}
            disabled={syncDisabled || profile.resync_required}
            title={t('syncMode.two_way.desc')}
          >
            <ArrowUpDown className="h-4 w-4" aria-hidden="true" />
            {t('twoWay.syncNow')}
          </Button>
        )}
        <Button
          variant={twoWay ? 'outline' : 'default'}
          onClick={() => confirmedSync.request('push', [{ slug, name: profile.name }])}
          disabled={syncDisabled}
        >
          <ArrowUp className="h-4 w-4" aria-hidden="true" />
          {t('dashboard.push')}
        </Button>
        <Button
          variant={twoWay ? 'outline' : 'default'}
          onClick={() => confirmedSync.request('pull', [{ slug, name: profile.name }])}
          disabled={syncDisabled}
        >
          <ArrowDown className="h-4 w-4" aria-hidden="true" />
          {t('dashboard.pull')}
        </Button>
        {twoWay && !profile.resync_required && (
          <ResyncAction slug={slug} name={profile.name} disabled={active || !profile.enabled} />
        )}
        {active && (
          <Button variant="destructive" onClick={() => stopSync.mutate()} disabled={stopSync.isPending}>
            {t('dashboard.stop')}
          </Button>
        )}
        {profile.enabled && !profile.user_paused && (
          <Button
            variant="outline"
            onClick={() => pauseProfile.mutate()}
            disabled={pauseProfile.isPending}
            title={t('pause.pauseHelp')}
          >
            <Pause className="h-4 w-4" aria-hidden="true" />
            {t('pause.pause')}
          </Button>
        )}
        {profile.intervals_paused && (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              onClick={() => resumeIntervals.mutate()}
              disabled={profile.pending_changes > 0 || resumeIntervals.isPending}
              aria-describedby={profile.pending_changes > 0 ? 'resume-blocked-reason' : undefined}
            >
              {t('intervalsPaused.resume')}
            </Button>
            {profile.pending_changes > 0 && (
              <p id="resume-blocked-reason" className="text-sm text-muted-foreground">
                {t('intervalsPaused.resumeBlocked', { count: profile.pending_changes })}
              </p>
            )}
          </div>
        )}
      </div>

      {!twoWay && <MirrorNotice profile={profile} />}

      {tab !== 'overview' && !profile.resync_required && <LastErrorNotice error={syncStatus.last_error} />}

      <Tabs value={tab} onValueChange={changeTab} className="space-y-4">
        <TabsList className="max-w-full overflow-x-auto">
          <TabsTrigger value="overview">{t('backups.overview')}</TabsTrigger>
          <TabsTrigger value="differences" className="gap-1.5">
            {t('granular.differences')}
            {profile.pending_changes > 0 && (
              <Badge variant="secondary" className="text-xs">{profile.pending_changes}</Badge>
            )}
            {profile.intervals_paused && (
              <span
                role="img"
                aria-label={t('intervalsPaused.paused')}
                title={t('intervalsPaused.paused')}
                className="h-2 w-2 rounded-full bg-amber-500 motion-safe:animate-pulse"
              />
            )}
          </TabsTrigger>
          <TabsTrigger value="backups">{t('backups.title')}</TabsTrigger>
          <TabsTrigger value="history">{t('profiles.history')}</TabsTrigger>
          <TabsTrigger value="trash">{t('trash.tab')}</TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <div className="grid gap-6 md:grid-cols-2">
            <SyncStatusCard status={syncStatus} isError={false} />
            <Card>
              <CardHeader>
                <CardTitle>{t('profiles.configuration')}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                  <span className="text-muted-foreground">{t('syncMode.label')}</span>
                  <span>{t(`syncMode.${profile.sync_mode}.badge`)}</span>
                  <span className="text-muted-foreground">{t('profiles.localDir')}</span>
                  <span className="truncate" title={profile.local_dir}><PathText>{profile.local_dir}</PathText></span>
                  <span className="text-muted-foreground">{t('profiles.remoteDir')}</span>
                  <span className="truncate" title={profile.remote_dir}><PathText>{profile.remote_dir}</PathText></span>
                  <span className="text-muted-foreground">{t('config.debounceSeconds')}</span>
                  <span>{profile.debounce_seconds}</span>
                  <span className="text-muted-foreground">{t('config.pullInterval')}</span>
                  <span>{profile.pull_interval_minutes}</span>
                  <span className="text-muted-foreground">{t('config.maxRetries')}</span>
                  <span>{profile.max_retries}</span>
                  {profile.bwlimit && (
                    <>
                      <span className="text-muted-foreground">{t('syncLimits.bwlimit')}</span>
                      <span dir="ltr" className="break-words">{profile.bwlimit}</span>
                    </>
                  )}
                  {profile.sync_window && (
                    <>
                      <span className="text-muted-foreground">{t('syncLimits.window')}</span>
                      <span dir="ltr">{profile.sync_window.start}–{profile.sync_window.end}</span>
                    </>
                  )}
                </div>
                <div className="mt-4 flex gap-2">
                  <Button size="sm" variant="outline" onClick={() => setShowEdit(true)}>
                    {t('profiles.edit')}
                  </Button>
                  <Button size="sm" variant="destructive" onClick={() => setShowDeleteConfirm(true)} disabled={deleteProfile.isPending}>
                    {t('common.delete')}
                  </Button>
                </div>
                <div className="mt-4 border-t pt-4">
                  <TestSyncButton slug={slug} localDir={profile.local_dir} remoteDir={profile.remote_dir} disabled={active} />
                </div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="backups">
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">{t('backups.title')}</h2>
              <Button size="sm" onClick={() => { setEditingTarget(null); setShowTargetForm(true); }}>
                <Plus className="h-4 w-4" aria-hidden="true" />
                {t('backups.addTarget')}
              </Button>
            </div>

            {backupTargetsQuery.isLoading && (
              <p className="text-muted-foreground" role="status">{t('common.loading')}</p>
            )}
            {backupTargetsQuery.isError && (
              <p className="text-destructive" role="alert">{backupTargetsQuery.error.message || t('common.error')}</p>
            )}
            {backupTargetsQuery.isSuccess && (!backupTargets || backupTargets.length === 0)
              ? (
              <Card>
                <CardContent className="py-8 text-center text-muted-foreground">
                  <p>{t('backups.empty')}</p>
                  <p className="text-sm mt-1">{t('backups.emptyHint')}</p>
                </CardContent>
              </Card>
                )
              : (
              <div className="space-y-4">
                {(backupTargets ?? []).map((target: BackupTarget) => (
                  <BackupTargetCard
                    key={target.id}
                    target={target}
                    profileSlug={slug}
                    isRunning={target.last_backup_status === 'running'}
                    onRunNow={(id) => runBackup.mutate(id)}
                    onEdit={(t) => { setEditingTarget(t); setShowTargetForm(true); }}
                    onToggleEnabled={(id, enabled) => updateBackup.mutate({ id, data: { enabled } })}
                    onDelete={(id) => deleteBackup.mutate(id)}
                    isDeleting={deleteBackup.isPending}
                  />
                ))}
              </div>
                )}
          </div>
        </TabsContent>

        <TabsContent value="history">
          <ProfileJobHistory slug={slug} />
        </TabsContent>

        <TabsContent value="trash">
          {tab === 'trash' && <TrashPanel slug={slug} disabled={active} />}
        </TabsContent>

        <TabsContent value="differences">
          <ProfileDiffPanel
            slug={slug}
            emptyExtra={profile.intervals_paused && (
              <Button
                variant="outline"
                size="sm"
                className="mt-3"
                onClick={() => resumeIntervals.mutate()}
                disabled={resumeIntervals.isPending}
              >
                {t('intervalsPaused.resume')}
              </Button>
            )}
          />
        </TabsContent>
      </Tabs>

      {confirmedSync.dialog}

      <Dialog open={showEdit} onOpenChange={setShowEdit}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('profiles.editTitle')}</DialogTitle>
            <DialogDescription className="sr-only">{t('profiles.editTitle')}</DialogDescription>
          </DialogHeader>
          <ProfileForm
            profile={profile}
            onSubmit={(data: ProfileFormSubmission) => handleUpdate(data.profile as ProfileUpdateRequest)}
            onCancel={() => setShowEdit(false)}
            isSaving={updateProfile.isPending}
          />
        </DialogContent>
      </Dialog>

      <BackupTargetForm
        key={`${editingTarget?.id ?? 'new'}-${showTargetForm}`}
        profileSlug={slug}
        target={editingTarget}
        open={showTargetForm}
        onOpenChange={setShowTargetForm}
      />

      <ProfileDeleteDialog
        profile={showDeleteConfirm ? profile : null}
        onCancel={() => setShowDeleteConfirm(false)}
        onConfirm={handleDelete}
        isPending={deleteProfile.isPending}
      />
    </div>
  );
}
