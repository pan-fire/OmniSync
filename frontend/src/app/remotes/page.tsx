'use client';

import { useState } from 'react';
import { FileUp, Wand2 } from 'lucide-react';
import { useTranslation } from '@/i18n';
import { useRemotes } from '@/hooks/use-remotes';
import { RemoteCard } from '@/components/remotes/remote-card';
import { RemoteWizard } from '@/components/config/wizard/remote-wizard';
import { RemoteImportDialog } from '@/components/remotes/remote-import-dialog';
import { PageHelp } from '@/components/layout/page-help';
import { PageHeader } from '@/components/layout/page-header';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';

export default function RemotesPage () {
  const { t } = useTranslation();
  const { data: remotes, isLoading, isError, refetch } = useRemotes();
  const [wizardOpen, setWizardOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  if (isLoading) {
    return <p className="text-muted-foreground p-4">{t('common.loading')}</p>;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('remotes.title')}
        actions={(
          <>
            {/* Remotes are created through the wizard (POST /wizard/create). */}
            <Button type="button" onClick={() => setWizardOpen(true)}>
              <Wand2 className="h-4 w-4" aria-hidden="true" />
              {t('wizard.setupWizard')}
            </Button>
            <Button type="button" variant="outline" onClick={() => setImportOpen(true)}>
              <FileUp className="h-4 w-4" aria-hidden="true" />
              {t('remotes.import.button')}
            </Button>
            <PageHelp pageKey="remotes" />
          </>
        )}
      />

      {isError && (
        <div className="flex items-center gap-3" role="alert">
          <p className="text-destructive">{t('remotes.loadFailed')}</p>
          <Button variant="outline" size="sm" onClick={() => refetch()}>{t('common.retry')}</Button>
        </div>
      )}

      {/* Remote cards grid */}
      {isError
        ? null
        : (!remotes || remotes.length === 0)
            ? (
        <Card>
          <CardContent className="py-8 text-center text-muted-foreground">
            <p>{t('remotes.noRemotes')}</p>
            <p className="text-sm mt-1">{t('remotes.noRemotesHint')}</p>
          </CardContent>
        </Card>
              )
            : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {remotes.map((remote) => (
            <RemoteCard key={remote.name} remote={remote} />
          ))}
        </div>
              )}

      <RemoteWizard open={wizardOpen} onOpenChange={setWizardOpen} />
      <RemoteImportDialog
        open={importOpen}
        onOpenChange={setImportOpen}
        existingNames={(remotes ?? []).map((r) => r.name)}
      />
    </div>
  );
}
