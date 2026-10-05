'use client';

import { useEffect, useState } from 'react';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { useTranslation } from '@/i18n';
import { useProfiles, useCreateProfile, useDeleteProfile, useToggleProfile } from '@/hooks/use-profiles';
import { ProfileCard } from '@/components/profiles/profile-card';
import { ProfileForm, type ProfileFormSubmission } from '@/components/profiles/profile-form';
import { ProfileDeleteDialog } from '@/components/profiles/profile-delete-dialog';
import { ProfileToggleDialog, type ProfileToggleRequest } from '@/components/profiles/profile-toggle-dialog';
import { PageHelp } from '@/components/layout/page-help';
import { PageHeader } from '@/components/layout/page-header';
import { useConfirmedSync } from '@/components/sync/sync-confirm-dialog';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import type { ProfileCreateRequest } from '@/types';

export default function ProfilesPage () {
  const { t } = useTranslation();
  const profiles = useProfiles();
  const createProfile = useCreateProfile();
  const deleteProfile = useDeleteProfile();
  const toggleProfile = useToggleProfile();
  const confirmedSync = useConfirmedSync();
  const [showCreate, setShowCreate] = useState(false);
  const [creatingInitialBackup, setCreatingInitialBackup] = useState(false);
  const [deletingSlug, setDeletingSlug] = useState<string | null>(null);
  // The switch only asks; the profile changes once the dialog is confirmed.
  const [toggling, setToggling] = useState<ProfileToggleRequest | null>(null);

  // The dashboard's "Create your first profile" links to ?create=1.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('create') === '1') setShowCreate(true);
  }, []);

  const handleCreate = async ({ profile, initialBackupTarget }: ProfileFormSubmission) => {
    try {
      const createdProfile = await createProfile.mutateAsync(profile as ProfileCreateRequest);

      if (initialBackupTarget) {
        setCreatingInitialBackup(true);
        try {
          await api.createBackupTarget(createdProfile.slug, initialBackupTarget);
          toast.success(t('backups.initialSetupSuccess'));
        } catch (error) {
          const message = error instanceof Error ? error.message : t('common.error');
          toast.error(`${t('backups.initialSetupFailed')}: ${message}`);
        } finally {
          setCreatingInitialBackup(false);
        }
      }

      setShowCreate(false);
    } catch {
      setCreatingInitialBackup(false);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('profiles.title')}
        actions={(
          <>
            <Button onClick={() => setShowCreate(true)}>
              <Plus className="h-4 w-4" /> {t('profiles.create')}
            </Button>
            <PageHelp pageKey="profiles" />
          </>
        )}
      />

      {profiles.isLoading && (
        <div className="grid gap-4 md:grid-cols-2" role="status" aria-label={t('common.loading')}>
          {[0, 1].map((i) => (
            <div key={i} className="h-40 animate-pulse rounded-xl border bg-muted/40" />
          ))}
        </div>
      )}

      {profiles.isError && (
        <div className="flex flex-wrap items-center gap-3" role="alert">
          <p className="text-destructive">
            {t('profiles.loadListFailed')}
            {profiles.error?.message ? `: ${profiles.error.message}` : ''}
          </p>
          <Button variant="outline" size="sm" onClick={() => profiles.refetch()} disabled={profiles.isFetching}>
            {t('common.retry')}
          </Button>
        </div>
      )}

      {profiles.data && profiles.data.length === 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <p className="mb-4">{t('profiles.empty')}</p>
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4" /> {t('profiles.createFirst')}
          </Button>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {profiles.data?.map((profile) => (
          <ProfileCard
            key={profile.slug}
            profile={profile}
            onToggle={(_slug, enabled) => setToggling({ profile, enabled })}
            onDelete={(slug) => setDeletingSlug(slug)}
            onSync={(p, direction) => confirmedSync.request(direction, [{ slug: p.slug, name: p.name }])}
            onSyncNow={(p) => confirmedSync.syncNow({ slug: p.slug, name: p.name }, p.intervals_paused)}
            isDeleting={deleteProfile.isPending}
          />
        ))}
      </div>

      {confirmedSync.dialog}

      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent className="flex h-[92vh] w-[95vw] max-w-[95vw] flex-col overflow-hidden md:h-[90vh] md:w-[92vw] md:max-w-[92vw] lg:h-[82vh] lg:w-[82vw] lg:max-w-[82vw]">
          <DialogHeader>
            <DialogTitle>{t('profiles.createTitle')}</DialogTitle>
            <DialogDescription className="sr-only">{t('profiles.createTitle')}</DialogDescription>
          </DialogHeader>
          <div className="min-h-0 overflow-y-auto pe-1">
            <ProfileForm
              onSubmit={handleCreate}
              onCancel={() => setShowCreate(false)}
              isSaving={createProfile.isPending || creatingInitialBackup}
            />
          </div>
        </DialogContent>
      </Dialog>

      <ProfileToggleDialog
        request={toggling}
        onCancel={() => setToggling(null)}
        isPending={toggleProfile.isPending}
        onConfirm={({ profile, enabled }) => {
          toggleProfile.mutate({ slug: profile.slug, enabled });
          setToggling(null);
        }}
      />

      <ProfileDeleteDialog
        profile={profiles.data?.find((p) => p.slug === deletingSlug) ?? null}
        onCancel={() => setDeletingSlug(null)}
        isPending={deleteProfile.isPending}
        onConfirm={() => {
          if (deletingSlug) {
            deleteProfile.mutate(deletingSlug, {
              onSuccess: () => setDeletingSlug(null),
            });
          }
        }}
      />
    </div>
  );
}
