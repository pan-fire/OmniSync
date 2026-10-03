'use client';

import { useState } from 'react';
import { Trash2, Wand2 } from 'lucide-react';
import { RemoteWizard } from '@/components/config/wizard/remote-wizard';
import { RemoteDeleteDialog } from '@/components/remotes/remote-delete-dialog';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useTranslation } from '@/i18n';
import { formatDateTime } from '@/lib/format';
import type { Remote } from '@/types';

interface RemoteManagerProps {
  remotes: Remote[];
}

export function RemoteManager ({ remotes }: RemoteManagerProps) {
  const { t, locale } = useTranslation();
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('config.remotes')}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {remotes.length === 0
          ? (
          <p className="text-muted-foreground text-sm">{t('config.noRemotes')}</p>
            )
          : (
          <div className="space-y-2">
            {remotes.map((remote) => (
              <div
                key={remote.name}
                className="flex items-center justify-between rounded-md border p-3"
              >
                <div>
                  <span className="font-medium">{remote.name}</span>
                  <span className="text-muted-foreground ms-2 text-sm">{remote.type}</span>
                  {remote.last_verified && (
                    <span className="text-muted-foreground ms-2 text-xs">
                      {t('config.lastVerified')}: {formatDateTime(remote.last_verified, locale)}
                    </span>
                  )}
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setDeleteTarget(remote.name)}
                  aria-label={`${t('config.deleteRemote')} ${remote.name}`}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            ))}
          </div>
            )}

        {/* Remotes are created through the wizard (POST /wizard/create). */}
        <Button type="button" variant="outline" onClick={() => setWizardOpen(true)}>
          <Wand2 className="h-4 w-4" aria-hidden="true" />
          {t('wizard.setupWizard')}
        </Button>

        <RemoteWizard open={wizardOpen} onOpenChange={setWizardOpen} />

        {/* Loads what uses the remote; force only after an explicit "Delete anyway". */}
        <RemoteDeleteDialog
          name={deleteTarget}
          open={deleteTarget !== null}
          onOpenChange={(open) => !open && setDeleteTarget(null)}
        />
      </CardContent>
    </Card>
  );
}
