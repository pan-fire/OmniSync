'use client';

import { useState } from 'react';
import type { FormEvent } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { DirBrowser } from '@/components/shared/dir-browser';
import { RemotePicker } from '@/components/shared/remote-picker';
import { useRemotes } from '@/hooks/use-remotes';
import { useTranslation } from '@/i18n';
import { FolderOpen, FolderTree, Shield, Wand2 } from 'lucide-react';
import { RemoteWizard } from '@/components/config/wizard/remote-wizard';
import { TestSyncButton } from './test-sync-button';
import { FolderRulesDialog } from './folder-rules-dialog';
import { SyncLimitsFields, initialSyncLimits, syncLimitsErrors, syncLimitsPayload } from './sync-limits-fields';
import {
  BACKUP_MODES, BACKUP_TARGET_TYPES, backupBrowseStart, backupTargetPathError, isRemoteTargetType, remoteOfPath, withRemote,
} from './backup-options';
import { validateProfileForm } from '@/lib/profile-validation';
import type {
  BackupMode,
  BackupTargetCreateRequest,
  BackupTargetType,
  Profile,
  ProfileCreateRequest,
  ProfileUpdateRequest,
  SyncMode,
} from '@/types';

/** The sync modes in display order: two-way first, as the recommended default. */
export const SYNC_MODES: SyncMode[] = ['two_way', 'mirror'];

export interface ProfileFormSubmission {
  profile:              ProfileCreateRequest | ProfileUpdateRequest;
  initialBackupTarget?: BackupTargetCreateRequest | null;
}

interface ProfileFormProps {
  profile?:  Profile;
  onSubmit:  (data: ProfileFormSubmission) => void;
  onCancel:  () => void;
  isSaving?: boolean;
}

export function ProfileForm ({ profile, onSubmit, onCancel, isSaving }: ProfileFormProps) {
  const { t } = useTranslation();
  const isEdit = !!profile;
  const remotesQuery = useRemotes();
  const remotes = remotesQuery.data;

  const [name, setName] = useState(profile?.name ?? '');
  const [localDir, setLocalDir] = useState(profile?.local_dir ?? '');
  const [remoteDir, setRemoteDir] = useState(profile?.remote_dir ?? '');
  const [debounceSeconds, setDebounceSeconds] = useState(String(profile?.debounce_seconds ?? 5));
  const [pullInterval, setPullInterval] = useState(String(profile?.pull_interval_minutes ?? 5));
  const [rcloneFilter, setRcloneFilter] = useState((profile?.rclone_filter ?? []).join('\n'));
  const [rcloneArgs, setRcloneArgs] = useState((profile?.rclone_args ?? []).join('\n'));
  const [maxRetries, setMaxRetries] = useState(String(profile?.max_retries ?? 3));
  const [syncLimits, setSyncLimits] = useState(() => initialSyncLimits(profile));
  const [folderPickerOpen, setFolderPickerOpen] = useState(false);
  // New profiles sync both ways; an existing profile keeps its mode.
  const [syncMode, setSyncMode] = useState<SyncMode>(profile?.sync_mode ?? 'two_way');
  const modeChanged = !!profile && syncMode !== profile.sync_mode;
  const [createInitialBackup, setCreateInitialBackup] = useState(false);
  const [backupName, setBackupName] = useState(() => t('backups.form.defaultName'));
  const [backupTargetType, setBackupTargetType] = useState<BackupTargetType>('local');
  const [backupTargetPath, setBackupTargetPath] = useState('');
  const [backupRemoteName, setBackupRemoteName] = useState('');
  const [backupRetentionDays, setBackupRetentionDays] = useState('30');
  const [backupFrequencyHours, setBackupFrequencyHours] = useState('24');
  const [backupMode, setBackupMode] = useState<BackupMode>('archive');
  const [backupEnabled, setBackupEnabled] = useState(true);

  const [browseLocalOpen, setBrowseLocalOpen] = useState(false);
  const [browseRemoteOpen, setBrowseRemoteOpen] = useState(false);
  const [browseBackupOpen, setBrowseBackupOpen] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  // Errors are shown after the first submit attempt and then update live.
  const [submitted, setSubmitted] = useState(false);

  const withinRange = (value: string, max: number) => /^\d+$/.test(value.trim()) && Number(value) >= 1 && Number(value) <= max;
  // "Same remote" backs up to the remote typed in the remote directory above.
  const profileRemote = remoteOfPath(remoteDir);
  const backupPathError = backupTargetPathError(backupTargetType, backupTargetPath, backupRemoteName);
  const backupBrowse = backupBrowseStart(
    backupTargetType, backupTargetPath, backupTargetType === 'custom_remote' ? backupRemoteName : profileRemote
  );
  const backupPathExample = `${(backupTargetType === 'custom_remote' ? backupRemoteName : profileRemote) || 'gdrive'}:Backups/my-profile`;
  const allErrors: Record<string, string> = {
    ...validateProfileForm({
      name,
      local_dir:             localDir,
      remote_dir:            remoteDir,
      debounce_seconds:      debounceSeconds,
      pull_interval_minutes: pullInterval,
      max_retries:           maxRetries,
    }),
    ...syncLimitsErrors(syncLimits, rcloneArgs),
    ...(!isEdit && createInitialBackup
      ? {
          ...(!backupName.trim() && { backup_name: 'backups.validation.nameRequired' }),
          ...(backupPathError && { backup_path: backupPathError }),
          ...(backupTargetType === 'custom_remote' && !backupRemoteName && { backup_remote: 'backups.validation.remoteRequired' }),
          ...(!withinRange(backupRetentionDays, 365) && { backup_retention: 'backups.validation.retentionRange' }),
          ...(!withinRange(backupFrequencyHours, 8760) && { backup_frequency: 'backups.validation.frequencyRange' }),
        }
      : {}),
  };
  const errors = submitted ? allErrors : {};

  // Props that tie an input to its error message.
  const invalidProps = (field: string, id: string) => (errors[field]
    ? { 'aria-invalid': true as const, 'aria-describedby': `${id}-error` }
    : {});
  const errorText = (field: string, id: string) => errors[field] && (
    <p id={`${id}-error`} className="text-xs text-destructive">{t(errors[field])}</p>
  );

  const FIELD_IDS: Record<string, string> = {
    name:                  'profile-name',
    local_dir:             'profile-local-dir',
    remote_dir:            'profile-remote-dir',
    debounce_seconds:      'profile-debounce',
    pull_interval_minutes: 'profile-pull-interval',
    max_retries:           'profile-max-retries',
    bwlimit:               'profile-bwlimit',
    sync_window:           'profile-window-start',
    backup_name:           'initial-backup-name',
    backup_path:           'initial-backup-path',
    backup_retention:      'initial-backup-retention',
    backup_frequency:      'initial-backup-frequency',
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    const invalid = Object.keys(allErrors);
    if (invalid.length > 0) {
      const firstId = invalid.map((f) => FIELD_IDS[f]).find(Boolean);
      if (firstId) document.getElementById(firstId)?.focus();
      return;
    }
    const profileData = {
      name,
      local_dir:             localDir,
      remote_dir:            remoteDir,
      debounce_seconds:      Number(debounceSeconds),
      pull_interval_minutes: Number(pullInterval),
      rclone_filter:         rcloneFilter.split('\n').map((l) => l.trim()).filter(Boolean),
      rclone_args:           rcloneArgs.split('\n').map((l) => l.trim()).filter(Boolean),
      max_retries:           Number(maxRetries),
      ...syncLimitsPayload(syncLimits),
      // An unchanged mode is not sent: switching modes resets the two-way state.
      ...(!isEdit || modeChanged ? { sync_mode: syncMode } : {}),
    };

    const initialBackupTarget = !isEdit && createInitialBackup
      ? {
          name:            backupName,
          target_path:     backupTargetPath.trim(),
          target_type:     backupTargetType,
          remote_name:     backupTargetType === 'custom_remote' ? backupRemoteName : null,
          retention_days:  Number(backupRetentionDays),
          frequency_hours: Number(backupFrequencyHours),
          backup_mode:     backupMode,
          enabled:         backupEnabled,
        }
      : null;

    onSubmit({ profile: profileData, initialBackupTarget });
  };

  const handleRemoteSelect = (remoteName: string) => {
    const suffix = remoteDir.includes(':') ? remoteDir.split(':').slice(1).join(':') : '';
    setRemoteDir(`${remoteName}:${suffix}`);
  };

  return (
    <>
    <form onSubmit={handleSubmit} className="space-y-5" noValidate>
      <div className="space-y-2">
        <Label htmlFor="profile-name">{t('profiles.name')}</Label>
        <Input id="profile-name" value={name} onChange={(e) => setName(e.target.value)} required {...invalidProps('name', 'profile-name')} />
        {errorText('name', 'profile-name')}
      </div>

      <div className="space-y-2">
        <Label htmlFor="profile-local-dir">{t('profiles.localDir')}</Label>
        <div className="flex gap-2">
          <Input
            id="profile-local-dir"
            dir="ltr"
            value={localDir}
            onChange={(e) => setLocalDir(e.target.value)}
            required
            placeholder="/home/you/Sync"
            className="flex-1"
            {...invalidProps('local_dir', 'profile-local-dir')}
          />
          <Button type="button" variant="outline" size="icon" onClick={() => setBrowseLocalOpen(true)} aria-label={t('profiles.browseLocal')}>
            <FolderOpen className="h-4 w-4" />
          </Button>
        </div>
        {errorText('local_dir', 'profile-local-dir') || <p className="text-xs text-muted-foreground">{t('config.localDirHelp')}</p>}
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <Label>{t('profiles.remote')}</Label>
          <Badge variant="outline">{t('profiles.remotesConfigured', { count: (remotes ?? []).length })}</Badge>
        </div>
        <RemotePicker
          remotes={remotes}
          isLoading={remotesQuery.isLoading}
          isError={remotesQuery.isError}
          onRetry={() => remotesQuery.refetch()}
          value={remoteDir.split(':')[0] || ''}
          onValueChange={handleRemoteSelect}
          placeholder={t('profiles.selectRemote')}
          emptyMessage={t('remotes.noRemotesHint')}
          emptyAction={(
            <Button type="button" variant="outline" size="sm" onClick={() => setWizardOpen(true)}>
              <Wand2 className="h-4 w-4" aria-hidden="true" />
              {t('wizard.setupWizard')}
            </Button>
          )}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="profile-remote-dir">{t('profiles.remoteDir')}</Label>
        <div className="flex gap-2">
          <Input
            id="profile-remote-dir"
            dir="ltr"
            value={remoteDir}
            onChange={(e) => setRemoteDir(e.target.value)}
            required
            placeholder="gdrive:Backup"
            className="flex-1"
            {...invalidProps('remote_dir', 'profile-remote-dir')}
          />
          <Button type="button" variant="outline" size="icon" onClick={() => setBrowseRemoteOpen(true)} aria-label={t('profiles.browseRemote')}>
            <FolderOpen className="h-4 w-4" />
          </Button>
        </div>
        {errorText('remote_dir', 'profile-remote-dir') || <p className="text-xs text-muted-foreground">{t('config.remoteDirHelp')}</p>}
      </div>

      <div className="space-y-1">
        {/* Unchanged folders of a saved profile are tested through the profile. */}
        <TestSyncButton
          slug={isEdit && localDir === profile.local_dir && remoteDir === profile.remote_dir ? profile.slug : undefined}
          localDir={localDir}
          remoteDir={remoteDir}
        />
        <p className="text-xs text-muted-foreground">{t('config.testSyncHint')}</p>
      </div>

      <div className="space-y-2">
        <span className="text-sm font-medium" id="profile-sync-mode-label">{t('syncMode.label')}</span>
        <div className="space-y-2" role="radiogroup" aria-labelledby="profile-sync-mode-label">
          {SYNC_MODES.map((value) => (
            <label
              key={value}
              className={`flex items-start gap-3 rounded-xl border p-3 transition-colors ${
                syncMode === value
                  ? 'border-primary bg-primary/10'
                  : 'hover:bg-muted/50'
              }`}
            >
              <input
                type="radio"
                name="profile_sync_mode"
                value={value}
                checked={syncMode === value}
                onChange={() => setSyncMode(value)}
                className="mt-1 accent-primary"
                aria-describedby={`profile-sync-mode-${value}-desc`}
              />
              <div>
                <div className="text-sm font-medium">{t(`syncMode.${value}.label`)}</div>
                <div id={`profile-sync-mode-${value}-desc`} className="text-xs text-muted-foreground">
                  {t(`syncMode.${value}.desc`)}
                </div>
              </div>
            </label>
          ))}
        </div>
        {modeChanged && (
          <p className="text-xs text-amber-700 dark:text-amber-400" data-testid="sync-mode-change-note">
            {t(syncMode === 'two_way' ? 'syncMode.switchToTwoWayNote' : 'syncMode.switchToMirrorNote')}
          </p>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <div className="space-y-2">
          <Label htmlFor="profile-debounce">{t('config.debounceSeconds')}</Label>
          <Input
            id="profile-debounce"
            type="number"
            min="1"
            step="1"
            required
            value={debounceSeconds}
            onChange={(e) => setDebounceSeconds(e.target.value)}
            {...invalidProps('debounce_seconds', 'profile-debounce')}
          />
          {errorText('debounce_seconds', 'profile-debounce')}
        </div>
        <div className="space-y-2">
          <Label htmlFor="profile-pull-interval">{t('config.pullInterval')}</Label>
          <Input
            id="profile-pull-interval"
            type="number"
            min="1"
            step="1"
            required
            value={pullInterval}
            onChange={(e) => setPullInterval(e.target.value)}
            {...invalidProps('pull_interval_minutes', 'profile-pull-interval')}
          />
          {errorText('pull_interval_minutes', 'profile-pull-interval')}
        </div>
        <div className="space-y-2">
          <Label htmlFor="profile-max-retries">{t('config.maxRetries')}</Label>
          <Input
            id="profile-max-retries"
            type="number"
            min="1"
            step="1"
            required
            value={maxRetries}
            onChange={(e) => setMaxRetries(e.target.value)}
            {...invalidProps('max_retries', 'profile-max-retries')}
          />
          {errorText('max_retries', 'profile-max-retries')}
        </div>
      </div>

      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Label htmlFor="profile-rclone-filter">{t('config.rcloneFilter')}</Label>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setFolderPickerOpen(true)}
            disabled={!localDir.trim().startsWith('/')}
          >
            <FolderTree className="h-4 w-4" aria-hidden="true" />
            {t('folderPicker.open')}
          </Button>
        </div>
        <textarea
          id="profile-rclone-filter"
          dir="ltr"
          className="flex min-h-[60px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          value={rcloneFilter}
          onChange={(e) => setRcloneFilter(e.target.value)}
          placeholder={t('config.rcloneFilterHint')}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="profile-rclone-args">{t('config.rcloneArgs')}</Label>
        <textarea
          id="profile-rclone-args"
          dir="ltr"
          className="flex min-h-[60px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          value={rcloneArgs}
          onChange={(e) => setRcloneArgs(e.target.value)}
          placeholder={t('config.rcloneArgsHint')}
        />
      </div>

      <SyncLimitsFields value={syncLimits} onChange={setSyncLimits} errors={errors} />

      {!isEdit && (
        <Card className="border-dashed">
          <CardHeader className="space-y-3 pb-4">
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-1">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Shield className="h-4 w-4" />
                  {t('backups.form.initialTitle')}
                </CardTitle>
                <p className="text-sm text-muted-foreground">
                  {t('backups.form.initialHint')}
                </p>
              </div>
              <Switch
                checked={createInitialBackup}
                onCheckedChange={setCreateInitialBackup}
                aria-label={t('backups.form.initialTitle')}
              />
            </div>
          </CardHeader>
          {createInitialBackup && (
            <CardContent className="space-y-4 pt-0">
              <div className="space-y-2">
                <Label htmlFor="initial-backup-name">{t('backups.form.name')}</Label>
                <Input
                  id="initial-backup-name"
                  value={backupName}
                  onChange={(e) => setBackupName(e.target.value)}
                  placeholder={t('backups.form.defaultName')}
                  required={createInitialBackup}
                  {...invalidProps('backup_name', 'initial-backup-name')}
                />
                {errorText('backup_name', 'initial-backup-name')}
              </div>

              <div className="space-y-2">
                <span className="text-sm font-medium" id="initial-backup-type-label">{t('backups.form.targetType')}</span>
                <div className="grid gap-2 sm:grid-cols-3" role="radiogroup" aria-labelledby="initial-backup-type-label">
                  {BACKUP_TARGET_TYPES.map((value) => (
                    <label
                      key={value}
                      className={`rounded-xl border px-3 py-2 text-sm transition-colors focus-within:ring-2 focus-within:ring-ring ${
                        backupTargetType === value
                          ? 'border-primary bg-primary/10'
                          : 'hover:bg-muted/50'
                      }`}
                    >
                      <input
                        type="radio"
                        name="initial_backup_target_type"
                        value={value}
                        checked={backupTargetType === value}
                        onChange={() => {
                          setBackupTargetType(value as BackupTargetType);
                          const remote = value === 'custom_remote' ? backupRemoteName : value === 'remote' ? profileRemote : null;
                          // Start a remote path with its remote; never rewrite what the user typed.
                          if (remote && !backupTargetPath.trim()) setBackupTargetPath(`${remote}:`);
                        }}
                        className="sr-only"
                      />
                      {t(`backups.types.${value}`)}
                    </label>
                  ))}
                </div>
              </div>

              {backupTargetType === 'custom_remote' && (
                <div className="space-y-2">
                  <Label>{t('backups.form.remote')}</Label>
                  <RemotePicker
                    remotes={remotes}
                    isLoading={remotesQuery.isLoading}
                    isError={remotesQuery.isError}
                    onRetry={() => remotesQuery.refetch()}
                    value={backupRemoteName}
                    onValueChange={(remote) => {
                      setBackupRemoteName(remote);
                      setBackupTargetPath(withRemote(backupTargetPath, remote));
                    }}
                    placeholder={t('profiles.selectRemote')}
                    emptyMessage={t('remotes.noRemotesHint')}
                  />
                  {errorText('backup_remote', 'initial-backup-remote')}
                </div>
              )}

              <div className="space-y-2">
                <Label htmlFor="initial-backup-path">{t('backups.form.targetPath')}</Label>
                <div className="flex gap-2">
                  <Input
                    id="initial-backup-path"
                    dir="ltr"
                    value={backupTargetPath}
                    onChange={(e) => setBackupTargetPath(e.target.value)}
                    placeholder={isRemoteTargetType(backupTargetType) ? backupPathExample : '/backups/my-profile'}
                    required={createInitialBackup}
                    className="flex-1"
                    {...invalidProps('backup_path', 'initial-backup-path')}
                  />
                  {backupBrowse && (
                    <Button type="button" variant="outline" size="icon" onClick={() => setBrowseBackupOpen(true)} aria-label={t('backups.form.browse')}>
                      <FolderOpen className="h-4 w-4" />
                    </Button>
                  )}
                </div>
                {errorText('backup_path', 'initial-backup-path')}
                {backupTargetType === 'remote' && profileRemote && (
                  <p className="text-xs text-muted-foreground">{t('backups.form.sameRemoteHint', { remote: profileRemote })}</p>
                )}
                {isRemoteTargetType(backupTargetType) && (
                  <p className="text-xs text-muted-foreground">{t('backups.form.remotePathHint', { example: backupPathExample })}</p>
                )}
              </div>

              <div className="space-y-2">
                <span className="text-sm font-medium" id="initial-backup-mode-label">{t('backups.form.mode')}</span>
                <div className="space-y-2" role="radiogroup" aria-labelledby="initial-backup-mode-label">
                  {BACKUP_MODES.map((value) => (
                    <label
                      key={value}
                      className={`flex items-start gap-3 rounded-xl border p-3 transition-colors ${
                        backupMode === value
                          ? 'border-primary bg-primary/10'
                          : 'hover:bg-muted/50'
                      }`}
                    >
                      <input
                        type="radio"
                        name="initial_backup_mode"
                        value={value}
                        checked={backupMode === value}
                        onChange={() => setBackupMode(value as BackupMode)}
                        className="mt-1 accent-primary"
                      />
                      <div>
                        <div className="text-sm font-medium">{t(`backups.modes.${value}.label`)}</div>
                        <div className="text-xs text-muted-foreground">{t(`backups.modes.${value}.desc`)}</div>
                      </div>
                    </label>
                  ))}
                </div>
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="initial-backup-retention">{t('backups.form.retentionDays')}</Label>
                  <Input
                    id="initial-backup-retention"
                    type="number"
                    min="1"
                    max="365"
                    step="1"
                    value={backupRetentionDays}
                    onChange={(e) => setBackupRetentionDays(e.target.value)}
                    {...invalidProps('backup_retention', 'initial-backup-retention')}
                  />
                  {errorText('backup_retention', 'initial-backup-retention')}
                </div>
                <div className="space-y-2">
                  <Label htmlFor="initial-backup-frequency">{t('backups.form.frequencyHours')}</Label>
                  <Input
                    id="initial-backup-frequency"
                    type="number"
                    min="1"
                    max="8760"
                    step="1"
                    value={backupFrequencyHours}
                    onChange={(e) => setBackupFrequencyHours(e.target.value)}
                    {...invalidProps('backup_frequency', 'initial-backup-frequency')}
                  />
                  {errorText('backup_frequency', 'initial-backup-frequency')}
                </div>
              </div>

              <div className="flex items-center justify-between rounded-xl border px-3 py-3">
                <div>
                  <div className="text-sm font-medium" id="initial-backup-enabled-label">{t('backups.form.scheduleTitle')}</div>
                  <div className="text-xs text-muted-foreground">{t('backups.form.scheduleHint')}</div>
                </div>
                <Switch
                  checked={backupEnabled}
                  onCheckedChange={setBackupEnabled}
                  aria-labelledby="initial-backup-enabled-label"
                />
              </div>
            </CardContent>
          )}
        </Card>
      )}

      <div className="flex gap-2">
        <Button type="submit" disabled={isSaving}>
          {isEdit ? t('common.save') : t('profiles.create')}
        </Button>
        <Button type="button" variant="outline" onClick={onCancel}>
          {t('common.cancel')}
        </Button>
      </div>

      <DirBrowser
        open={browseLocalOpen}
        onOpenChange={setBrowseLocalOpen}
        mode="local"
        initialPath={localDir || '/'}
        onSelect={setLocalDir}
      />
      <DirBrowser
        open={browseRemoteOpen}
        onOpenChange={setBrowseRemoteOpen}
        mode="remote"
        initialPath={remoteDir || ''}
        onSelect={setRemoteDir}
      />
      <FolderRulesDialog
        open={folderPickerOpen}
        onOpenChange={setFolderPickerOpen}
        localDir={localDir.trim()}
        rules={rcloneFilter}
        syncMode={syncMode}
        onApply={setRcloneFilter}
      />
      {backupBrowse && (
        <DirBrowser
          open={browseBackupOpen}
          onOpenChange={setBrowseBackupOpen}
          mode={backupBrowse.mode}
          initialPath={backupBrowse.initialPath}
          onSelect={setBackupTargetPath}
        />
      )}
    </form>
    {/* Outside the form: React events bubble through portals, so a submit
        inside the wizard would otherwise submit this form too. */}
    <RemoteWizard open={wizardOpen} onOpenChange={setWizardOpen} />
    </>
  );
}
