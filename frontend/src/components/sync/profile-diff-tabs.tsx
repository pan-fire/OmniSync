'use client';

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { useTranslation } from '@/i18n';
import { ProfileDiffPanel } from './profile-diff-panel';
import type { ProfileSummary } from '@/types';

interface ProfileDiffTabsProps {
  profiles: ProfileSummary[];
}

export function ProfileDiffTabs ({ profiles }: ProfileDiffTabsProps) {
  const { t } = useTranslation();
  const withPending = profiles.filter(p => p.pending_changes > 0);

  if (withPending.length === 0) {
    return (
      <Card data-testid="profile-diff-empty">
        <CardContent className="py-6 text-center text-sm text-muted-foreground" role="status">
          {t('granular.nothingPending')}
        </CardContent>
      </Card>
    );
  }

  const defaultSlug = withPending[0].slug;

  // Radix unmounts inactive tab content, so only the visible profile's
  // diff is fetched.
  return (
    <Tabs defaultValue={defaultSlug} className="space-y-4" data-testid="profile-diff-tabs">
      <TabsList>
        {withPending.map(p => (
          <TabsTrigger key={p.slug} value={p.slug} className="gap-2">
            {p.name}
            <Badge variant="secondary" className="text-xs">
              {p.pending_changes}
            </Badge>
          </TabsTrigger>
        ))}
      </TabsList>
      {withPending.map(p => (
        <TabsContent key={p.slug} value={p.slug}>
          <ProfileDiffPanel slug={p.slug} />
        </TabsContent>
      ))}
    </Tabs>
  );
}
