'use client';

import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Trash2, Wifi, ChevronDown, ChevronUp, FolderOpen, Loader2, Pencil, KeyRound, AlertTriangle,
} from 'lucide-react';
import { DirBrowser } from '@/components/shared/dir-browser';
import { RemoteWizard } from '@/components/config/wizard/remote-wizard';
import { useTranslation } from '@/i18n';
import { useRemoteDependencies, useTestRemote } from '@/hooks/use-remotes';
import { RemoteDeleteDialog } from './remote-delete-dialog';
import { RemoteEditDialog } from './remote-edit-dialog';
import { RemoteStorageBar } from './remote-storage-bar';
import { formatDateTime } from '@/lib/format';
import type { Remote, RemoteTestResult } from '@/types';

interface RemoteCardProps {
  remote: Remote;
}

export function RemoteCard ({ remote }: RemoteCardProps) {
  const { t, locale } = useTranslation();
  const testRemote = useTestRemote();
  const { data: deps } = useRemoteDependencies(remote.name);
  const [expanded, setExpanded] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [browseOpen, setBrowseOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [reconnectOpen, setReconnectOpen] = useState(false);
  const [testResult, setTestResult] = useState<RemoteTestResult | null>(null);

  const handleTest = () => {
    setTestResult(null);
    testRemote.mutate(remote.name, {
      onSuccess: (result) => setTestResult(result),
    });
  };

  // The provider refused the sign-in (last test, health check or sync):
  // offer the fix right on the card.
  const authFailed = testResult ? !!testResult.auth_error : !!remote.auth_error;
  const canReconnect = !!remote.reconnectable && !!remote.provider_id;

  return (
    <>
      <Card className={authFailed ? 'border-destructive' : undefined}>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-y-1 space-y-0 pb-2">
          <div className="flex items-center gap-2">
            <CardTitle className="text-base">{remote.name}</CardTitle>
            <Badge variant="outline">{remote.type}</Badge>
          </div>
          <div className="flex flex-wrap items-center gap-1">
            <Button
              size="sm"
              variant="ghost"
              onClick={handleTest}
              disabled={testRemote.isPending}
            >
              {testRemote.isPending
                ? <Loader2 className="h-3 w-3 animate-spin" />
                : <Wifi className="h-3 w-3" />}
              <span className="ms-1">{t('remotes.testConnection')}</span>
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setBrowseOpen(true)}>
              <FolderOpen className="h-3 w-3" aria-hidden="true" />
              <span className="ms-1">{t('browse.button')}</span>
            </Button>
            {remote.editable && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setEditOpen(true)}
                aria-label={t('remotes.edit.buttonNamed', { name: remote.name })}
              >
                <Pencil className="h-3 w-3" aria-hidden="true" />
                <span className="ms-1">{t('remotes.edit.button')}</span>
              </Button>
            )}
            {canReconnect && !authFailed && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setReconnectOpen(true)}
                aria-label={t('remotes.reconnect.buttonNamed', { name: remote.name })}
              >
                <KeyRound className="h-3 w-3" aria-hidden="true" />
                <span className="ms-1">{t('remotes.reconnect.button')}</span>
              </Button>
            )}
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setDeleteOpen(true)}
              aria-label={t('remotes.deleteNamed', { name: remote.name })}
            >
              <Trash2 className="h-3 w-3" aria-hidden="true" />
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {authFailed && (
            <div role="alert" className="flex flex-wrap items-center gap-2 rounded-md border border-destructive/50 bg-destructive/10 p-2 text-sm">
              <AlertTriangle className="text-destructive h-4 w-4 shrink-0" aria-hidden="true" />
              <span className="flex-1">
                {canReconnect ? t('remotes.reconnect.authFailed') : t('remotes.edit.authFailed')}
              </span>
              {canReconnect && (
                <Button size="sm" onClick={() => setReconnectOpen(true)}>
                  <KeyRound className="h-3 w-3" aria-hidden="true" />
                  {t('remotes.reconnect.button')}
                </Button>
              )}
              {!canReconnect && remote.editable && (
                <Button size="sm" onClick={() => setEditOpen(true)}>
                  <Pencil className="h-3 w-3" aria-hidden="true" />
                  {t('remotes.edit.credentials')}
                </Button>
              )}
            </div>
          )}

          {remote.last_verified && (
            <p className="text-xs text-muted-foreground">
              {t('remotes.lastVerified')}: {formatDateTime(remote.last_verified, locale)}
            </p>
          )}

          {testResult && (
            <div className="text-sm">
              {testResult.success
                ? (
                <span className="text-green-700 dark:text-green-400">
                  {t('remotes.testSuccess')}
                  {testResult.latency_ms != null && (
                    <span className="text-muted-foreground ms-2">
                      {t('remotes.latency')}: {testResult.latency_ms}ms
                    </span>
                  )}
                </span>
                  )
                : (
                <span className="text-red-700 dark:text-red-400">
                  {t('remotes.testFailed')}{testResult.error ? `: ${testResult.error}` : ''}
                </span>
                  )}
            </div>
          )}

          <RemoteStorageBar remoteName={remote.name} />

          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setExpanded(!expanded)}
              className="text-xs"
              aria-expanded={expanded}
            >
              {expanded ? <ChevronUp className="h-3 w-3 me-1" /> : <ChevronDown className="h-3 w-3 me-1" />}
              {t('remotes.usedBy')}
              {deps && (
                <Badge variant="secondary" className="ms-1 h-4 px-1.5 text-[10px]">
                  {deps.profiles.length + deps.backup_targets.length}
                </Badge>
              )}
            </Button>
          </div>

          {expanded && deps && (
            <div className="text-xs space-y-1 border-t pt-2">
              {deps.profiles.length === 0 && deps.backup_targets.length === 0 && (
                <p className="text-muted-foreground">{t('remotes.noProfiles')}</p>
              )}
              {deps.profiles.map((p) => (
                <div key={p.slug} className="flex items-center gap-2">
                  <Badge variant="secondary">{t('remotes.profile')}</Badge>
                  <span>{p.name}</span>
                </div>
              ))}
              {deps.backup_targets.map((bt) => (
                <div key={bt.target_id} className="flex items-center gap-2">
                  <Badge variant="outline">{t('remotes.backup')}</Badge>
                  <span>{bt.target_name} ({bt.profile_slug})</span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <RemoteDeleteDialog name={remote.name} open={deleteOpen} onOpenChange={setDeleteOpen} />
      <DirBrowser open={browseOpen} onOpenChange={setBrowseOpen} mode="remote" initialPath={`${remote.name}:`} />
      {remote.editable && (
        <RemoteEditDialog name={remote.name} open={editOpen} onOpenChange={setEditOpen} />
      )}
      {/* Mounted per reconnect: the wizard starts at this remote's sign-in. */}
      {reconnectOpen && remote.provider_id && (
        <RemoteWizard
          open
          onOpenChange={(o) => {
            setReconnectOpen(o);
            if (!o) setTestResult(null);
          }}
          reconnect={{ name: remote.name, providerId: remote.provider_id }}
        />
      )}
    </>
  );
}
