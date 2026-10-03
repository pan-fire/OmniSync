'use client';

import { useState } from 'react';
import { useConflicts, useResolveConflict } from '@/hooks/use-conflicts';
import { useProfiles } from '@/hooks/use-profiles';
import { ConflictList } from '@/components/conflicts/conflict-list';
import { PageHelp } from '@/components/layout/page-help';
import { PageHeader } from '@/components/layout/page-header';
import { Label } from '@/components/ui/label';
import { useTranslation } from '@/i18n';

export default function ConflictsPage () {
  const { t } = useTranslation();
  // '' shows the conflicts of every profile.
  const [profile, setProfile] = useState('');
  const profiles = useProfiles();
  const conflicts = useConflicts(profile || undefined);
  const resolveConflict = useResolveConflict();

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('conflicts.title')}
        actions={<PageHelp pageKey="conflicts" />}
      />
      {(profiles.data?.length ?? 0) > 1 && (
        <div className="flex items-center gap-2">
          <Label htmlFor="conflicts-profile">{t('conflicts.profileFilter')}</Label>
          <select
            id="conflicts-profile"
            className="h-9 rounded-md border border-input bg-background px-3 text-sm"
            value={profile}
            onChange={(e) => setProfile(e.target.value)}
          >
            <option value="">{t('conflicts.allProfiles')}</option>
            {profiles.data?.map((p) => (
              <option key={p.slug} value={p.slug}>{p.name}</option>
            ))}
          </select>
        </div>
      )}
      <ConflictList
        conflicts={conflicts.data}
        isLoading={conflicts.isLoading}
        isError={conflicts.isError}
        onRetry={() => conflicts.refetch()}
        onResolve={(id, resolution) => resolveConflict.mutate({ id, resolution })}
        busyId={resolveConflict.isPending ? resolveConflict.variables?.id : null}
      />
    </div>
  );
}
