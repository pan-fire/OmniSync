'use client';

import type { FormEvent } from 'react';
import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { AlertTriangle, FolderOpen, Lock } from 'lucide-react';
import { DirBrowser } from '@/components/shared/dir-browser';
import { RemotePicker } from '@/components/shared/remote-picker';
import { useRemotes } from '@/hooks/use-remotes';
import { useProfile } from '@/hooks/use-profiles';
import { useCreateBackupTarget, useUpdateBackupTarget } from '@/hooks/use-backups';
import { useTranslation } from '@/i18n';
import {
  BACKUP_MODES, BACKUP_TARGET_TYPES, backupBrowseStart, backupTargetPathError, isRemoteTargetType, remoteOfPath, withRemote,
} from './backup-options';
import type { BackupTarget, BackupTargetType, BackupMode, BackupTargetUpdateRequest } from '@/types';

/** The backend's minimum passphrase length (PASSPHRASE_MIN_LENGTH in backend/api/schemas.py). */
export const PASSPHRASE_MIN_LENGTH = 8;

interface BackupTargetFormProps {
  profileSlug:  string;
  target?:      BackupTarget | null;
  open:         boolean;
  onOpenChange: (open: boolean) => void;
}

export function BackupTargetForm ({
  profileSlug,
  target,
  open,
  onOpenChange,
}: BackupTargetFormProps) {
  const { t } = useTranslation();
  const isEdit = !!target;
  const remotesQuery = useRemotes();
  const remotes = remotesQuery.data;
  const createMutation = useCreateBackupTarget(profileSlug);
  const updateMutation = useUpdateBackupTarget(profileSlug);

  const [name, setName]                     = useState(target?.name ?? '');
  const [targetType, setTargetType]         = useState<BackupTargetType>(target?.target_type ?? 'local');
  const [targetPath, setTargetPath]         = useState(target?.target_path ?? '');
  const [remoteName, setRemoteName]         = useState(target?.remote_name ?? '');
  const [retentionDays, setRetentionDays]   = useState(target?.retention_days ?? 30);
  const [keepLast, setKeepLast]             = useState(target?.keep_last ?? 3);
  const [frequencyHours, setFrequencyHours] = useState(target?.frequency_hours ?? 24);
  const [backupMode, setBackupMode]         = useState<BackupMode>(target?.backup_mode ?? 'archive');
  const [enabled, setEnabled]               = useState(target?.enabled ?? true);
  const [verify, setVerify]                 = useState(target?.verify_after_backup ?? true);
  // New target: encrypt or not. Existing target: change (set, replace or
  // remove) the passphrase, which the backend allows only for an empty location.
  const [encrypt, setEncrypt]               = useState(false);
  const [passphrase, setPassphrase]         = useState('');
  const [passphrase2, setPassphrase2]       = useState('');
  const [browsing, setBrowsing]             = useState(false);
  // The path error shows after the first submit and then updates live.
  const [submitted, setSubmitted]           = useState(false);

  // "Same remote" means the remote this profile syncs with.
  const { data: profile } = useProfile(profileSlug);
  const profileRemote = profile ? remoteOfPath(profile.remote_dir) : null;

  const isPending = createMutation.isPending || updateMutation.isPending;
  const isRemoteType = isRemoteTargetType(targetType);
  const pathError = backupTargetPathError(targetType, targetPath, remoteName);
  const shownPathError = submitted ? pathError : null;
  // Removing the encryption of an existing target needs no passphrase.
  const passphraseError = !encrypt || (isEdit && !passphrase && !passphrase2)
    ? null
    : passphrase.length < PASSPHRASE_MIN_LENGTH
      ? 'backups.validation.passphraseShort'
      : passphrase !== passphrase2 ? 'backups.validation.passphraseMismatch' : null;
  const shownPassphraseError = submitted ? passphraseError : null;
  const exampleRemote = (targetType === 'custom_remote' ? remoteName : profileRemote) || 'gdrive';
  const pathExample = `${exampleRemote}:Backups/${profileSlug}`;
  const browseStart = backupBrowseStart(
    targetType, targetPath, targetType === 'custom_remote' ? remoteName : profileRemote
  );

  const handleTypeChange = (value: BackupTargetType) => {
    setTargetType(value);
    const remote = value === 'custom_remote' ? remoteName : value === 'remote' ? profileRemote : null;
    // Start a remote path with its remote; never rewrite what the user typed.
    if (remote && !targetPath.trim()) setTargetPath(`${remote}:`);
  };

  const handleCustomRemote = (remote: string) => {
    setRemoteName(remote);
    setTargetPath(withRemote(targetPath, remote));
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (pathError) {
      document.getElementById('bt-path')?.focus();
      return;
    }
    if (passphraseError) {
      document.getElementById('bt-passphrase')?.focus();
      return;
    }

    const data: BackupTargetUpdateRequest & { name: string; target_path: string; target_type: BackupTargetType } = {
      name,
      target_path:         targetPath.trim(),
      target_type:         targetType,
      remote_name:         targetType === 'custom_remote' ? remoteName : null,
      retention_days:      retentionDays,
      keep_last:           keepLast,
      frequency_hours:     frequencyHours,
      backup_mode:         backupMode,
      enabled,
      verify_after_backup: verify,
    };
    if (encrypt) data.encryption_passphrase = passphrase || null;

    const options = { onSuccess: () => onOpenChange(false) };

    if (isEdit && target) {
      updateMutation.mutate({ id: target.id, data }, options);
    } else {
      createMutation.mutate(data, options);
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{isEdit ? t('backups.form.editTitle') : t('backups.form.newTitle')}</DialogTitle>
            <DialogDescription className="sr-only">{isEdit ? t('backups.form.editTitle') : t('backups.form.newTitle')}</DialogDescription>
          </DialogHeader>

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Name */}
            <div className="space-y-2">
              <Label htmlFor="bt-name">{t('backups.form.name')}</Label>
              <Input
                id="bt-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t('backups.form.namePlaceholder')}
                required
              />
            </div>

            {/* Target Type */}
            <div className="space-y-2">
              <span className="text-sm font-medium" id="bt-type-label">{t('backups.form.targetType')}</span>
              <div className="flex gap-2" role="radiogroup" aria-labelledby="bt-type-label">
                {BACKUP_TARGET_TYPES.map((value) => (
                  <label
                    key={value}
                    className={`flex-1 cursor-pointer rounded-md border px-3 py-2 text-center text-sm transition-colors focus-within:ring-2 focus-within:ring-ring ${
                      targetType === value
                        ? 'border-primary bg-primary/10'
                        : 'hover:bg-muted/50'
                    }`}
                  >
                    <input
                      type="radio"
                      name="target_type"
                      value={value}
                      checked={targetType === value}
                      onChange={() => handleTypeChange(value as BackupTargetType)}
                      className="sr-only"
                    />
                    {t(`backups.types.${value}`)}
                  </label>
                ))}
              </div>
            </div>

            {/* Custom Remote selector */}
            {targetType === 'custom_remote' && (
              <div className="space-y-2">
                <Label>{t('backups.form.remote')}</Label>
                <RemotePicker
                  remotes={remotes}
                  isLoading={remotesQuery.isLoading}
                  isError={remotesQuery.isError}
                  onRetry={() => remotesQuery.refetch()}
                  value={remoteName}
                  onValueChange={handleCustomRemote}
                  placeholder={t('profiles.selectRemote')}
                  emptyMessage={t('backups.form.noRemotes')}
                />
                <input
                  value={remoteName}
                  onChange={() => undefined}
                  required
                  readOnly
                  aria-hidden="true"
                  tabIndex={-1}
                  className="sr-only"
                />
              </div>
            )}

            {/* Target Path */}
            <div className="space-y-2">
              <Label htmlFor="bt-path">{t('backups.form.targetPath')}</Label>
              <div className="flex gap-2">
                <Input
                  id="bt-path"
                  dir="ltr"
                  value={targetPath}
                  onChange={(e) => setTargetPath(e.target.value)}
                  placeholder={isRemoteType ? pathExample : '/backups/myprofile'}
                  required
                  className="flex-1"
                  aria-describedby="bt-path-help"
                  aria-invalid={shownPathError ? true : undefined}
                />
                {browseStart && (
                  <Button type="button" variant="outline" size="icon" onClick={() => setBrowsing(true)} aria-label={t('backups.form.browse')}>
                    <FolderOpen className="h-4 w-4" />
                  </Button>
                )}
              </div>
              <div id="bt-path-help" className="space-y-1">
                {shownPathError && <p className="text-xs text-destructive">{t(shownPathError)}</p>}
                {targetType === 'remote' && profileRemote && (
                  <p className="text-xs text-muted-foreground">{t('backups.form.sameRemoteHint', { remote: profileRemote })}</p>
                )}
                {isRemoteType && (
                  <p className="text-xs text-muted-foreground">{t('backups.form.remotePathHint', { example: pathExample })}</p>
                )}
              </div>
            </div>

            {/* Backup Mode */}
            <div className="space-y-2">
              <span className="text-sm font-medium" id="bt-mode-label">{t('backups.form.mode')}</span>
              <div className="space-y-2" role="radiogroup" aria-labelledby="bt-mode-label">
                {BACKUP_MODES.map((value) => (
                  <label
                    key={value}
                    className={`flex cursor-pointer items-center gap-3 rounded-md border p-3 transition-colors ${
                      backupMode === value
                        ? 'border-primary bg-primary/10'
                        : 'hover:bg-muted/50'
                    }`}
                  >
                    <input
                      type="radio"
                      name="backup_mode"
                      value={value}
                      checked={backupMode === value}
                      onChange={() => setBackupMode(value as BackupMode)}
                      className="accent-primary"
                    />
                    <div>
                      <div className="text-sm font-medium">{t(`backups.modes.${value}.label`)}</div>
                      <div className="text-xs text-muted-foreground">{t(`backups.modes.${value}.desc`)}</div>
                    </div>
                  </label>
                ))}
              </div>
            </div>

            {/* Retention & Frequency */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="bt-retention">{t('backups.form.retentionDays')}</Label>
                <Input
                  id="bt-retention"
                  type="number"
                  min={1}
                  max={365}
                  required
                  value={retentionDays}
                  onChange={(e) => setRetentionDays(Number(e.target.value))}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="bt-frequency">{t('backups.form.frequencyHours')}</Label>
                <Input
                  id="bt-frequency"
                  type="number"
                  min={1}
                  max={8760}
                  required
                  value={frequencyHours}
                  onChange={(e) => setFrequencyHours(Number(e.target.value))}
                />
              </div>
            </div>

            {/* Minimum number of snapshots retention keeps */}
            <div className="space-y-2">
              <Label htmlFor="bt-keep-last">{t('backups.form.keepLast')}</Label>
              <Input
                id="bt-keep-last"
                type="number"
                min={1}
                max={1000}
                required
                value={keepLast}
                onChange={(e) => setKeepLast(Number(e.target.value))}
                aria-describedby="bt-keep-last-help"
              />
              <p id="bt-keep-last-help" className="text-xs text-muted-foreground">{t('backups.form.keepLastHint')}</p>
            </div>

            {/* Verification */}
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <Label htmlFor="bt-verify">{t('backups.form.verify')}</Label>
                <Switch id="bt-verify" checked={verify} onCheckedChange={setVerify} aria-describedby="bt-verify-help" />
              </div>
              <p id="bt-verify-help" className="text-xs text-muted-foreground">{t('backups.form.verifyHint')}</p>
            </div>

            {/* Encryption */}
            <div className="space-y-2 rounded-md border p-3">
              {isEdit && (
                <p className="flex items-center gap-2 text-sm">
                  <Lock className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                  {target?.encrypted ? t('backups.form.encryptedStatus') : t('backups.form.notEncryptedStatus')}
                </p>
              )}
              <div className="flex items-center justify-between gap-2">
                <Label htmlFor="bt-encrypt">{isEdit ? t('backups.form.changeEncryption') : t('backups.form.encrypt')}</Label>
                <Switch id="bt-encrypt" checked={encrypt} onCheckedChange={setEncrypt} aria-describedby="bt-encrypt-help" />
              </div>
              <p id="bt-encrypt-help" className="text-xs text-muted-foreground">
                {isEdit ? t('backups.form.changeEncryptionHint') : t('backups.form.encryptHint')}
              </p>
              {encrypt && (
                <>
                  <div className="space-y-2">
                    <Label htmlFor="bt-passphrase">{t('backups.form.passphrase')}</Label>
                    <Input
                      id="bt-passphrase"
                      type="password"
                      autoComplete="new-password"
                      value={passphrase}
                      onChange={(e) => setPassphrase(e.target.value)}
                      required={!isEdit}
                      aria-invalid={shownPassphraseError ? true : undefined}
                      aria-describedby="bt-passphrase-error"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="bt-passphrase2">{t('backups.form.passphraseConfirm')}</Label>
                    <Input
                      id="bt-passphrase2"
                      type="password"
                      autoComplete="new-password"
                      value={passphrase2}
                      onChange={(e) => setPassphrase2(e.target.value)}
                      required={!isEdit}
                      aria-invalid={shownPassphraseError ? true : undefined}
                      aria-describedby="bt-passphrase-error"
                    />
                  </div>
                  <p id="bt-passphrase-error" className="text-xs text-destructive">
                    {shownPassphraseError ? t(shownPassphraseError) : null}
                  </p>
                  <p className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs" role="alert">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
                    {t('backups.form.passphraseWarning')}
                  </p>
                </>
              )}
            </div>

            {/* Enabled */}
            <div className="flex items-center justify-between">
              <Label htmlFor="bt-enabled">{t('backups.form.enabled')}</Label>
              <Switch id="bt-enabled" checked={enabled} onCheckedChange={setEnabled} />
            </div>

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                {t('common.cancel')}
              </Button>
              <Button type="submit" disabled={isPending}>
                {isPending ? t('backups.form.saving') : isEdit ? t('common.save') : t('backups.form.create')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {browseStart && (
        <DirBrowser
          open={browsing}
          onOpenChange={setBrowsing}
          mode={browseStart.mode}
          initialPath={browseStart.initialPath}
          onSelect={(path) => {
            setTargetPath(path);
            setBrowsing(false);
          }}
        />
      )}
    </>
  );
}
