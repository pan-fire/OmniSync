'use client';

import { useState } from 'react';
import { Eye, EyeOff, FolderOpen } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { DirBrowser } from '@/components/shared/dir-browser';
import { useTranslation } from '@/i18n';
import { useRemotes } from '@/hooks/use-remotes';
import type { ProviderField } from '@/types';

const SELECT_CLASS = 'h-9 w-full rounded-md border border-input bg-background px-3 text-sm disabled:opacity-50';

interface ProviderFieldInputProps {
  field:        ProviderField;
  value:        string;
  onChange:     (value: string) => void;
  disabled?:    boolean;
  /** Overrides the placeholder (the edit form: "stored, leave empty to keep"). */
  placeholder?: string;
  /** The remote being set up or edited: not offered as a crypt target. */
  ownName?:     string;
}

/**
 * One provider setting in the wizard or the edit form: a text, password
 * (with a show button), select, or remote-and-folder field. Keys, URLs and
 * paths stay left to right also in Persian.
 */
export function ProviderFieldInput ({ field, value, onChange, disabled, placeholder, ownName }: ProviderFieldInputProps) {
  const id = `field-${field.name}`;
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>
        {field.label}
        {field.required && <span className="text-destructive ms-1">*</span>}
      </Label>
      {field.field_type === 'select'
        ? (
        <select
          id={id}
          dir="ltr"
          className={SELECT_CLASS}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
        >
          {!field.required && <option value="">{'—'}</option>}
          {(field.options ?? []).map((opt) => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
          )
        : field.field_type === 'remote_path'
          ? (
          <RemotePathInput id={id} value={value} onChange={onChange} disabled={disabled} ownName={ownName} />
            )
          : (
          <TextInput
            id={id}
            secret={field.field_type === 'password'}
            value={value}
            onChange={onChange}
            disabled={disabled}
            placeholder={placeholder ?? field.label}
          />
            )}
      {field.help_text && (
        <p className="text-muted-foreground text-xs">{field.help_text}</p>
      )}
    </div>
  );
}

function TextInput ({ id, secret, value, onChange, disabled, placeholder }: {
  id:          string;
  secret:      boolean;
  value:       string;
  onChange:    (value: string) => void;
  disabled?:   boolean;
  placeholder: string;
}) {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  return (
    <div className="relative" dir="ltr">
      <Input
        id={id}
        type={secret && !visible ? 'password' : 'text'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        autoComplete={secret ? 'new-password' : 'off'}
      />
      {secret && (
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          className="absolute top-1/2 end-2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          aria-label={t('wizard.configStep.togglePassword')}
        >
          {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      )}
    </div>
  );
}

/** "<remote>:<folder>": an existing remote from a list, the folder typed or browsed. */
function RemotePathInput ({ id, value, onChange, disabled, ownName }: {
  id:        string;
  value:     string;
  onChange:  (value: string) => void;
  disabled?: boolean;
  ownName?:  string;
}) {
  const { t } = useTranslation();
  const { data: remotes } = useRemotes();
  const [browseOpen, setBrowseOpen] = useState(false);
  const sep = value.indexOf(':');
  const remote = sep >= 0 ? value.slice(0, sep) : '';
  const folder = sep >= 0 ? value.slice(sep + 1) : value;
  const choices = (remotes ?? []).filter((r) => r.name !== ownName);

  return (
    <div className="space-y-2" dir="ltr">
      <div className="flex gap-2">
        <select
          id={id}
          className={`${SELECT_CLASS} sm:w-1/3`}
          value={remote}
          onChange={(e) => onChange(e.target.value ? `${e.target.value}:${folder}` : '')}
          disabled={disabled}
          aria-label={t('wizard.configStep.remotePathRemote')}
        >
          <option value="">{t('wizard.configStep.remotePathChoose')}</option>
          {choices.map((r) => (
            <option key={r.name} value={r.name}>{`${r.name} (${r.type})`}</option>
          ))}
        </select>
        <Input
          value={folder}
          onChange={(e) => onChange(`${remote}:${e.target.value}`)}
          placeholder={t('wizard.configStep.remotePathFolder')}
          aria-label={t('wizard.configStep.remotePathFolder')}
          disabled={disabled || !remote}
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-9"
          onClick={() => setBrowseOpen(true)}
          disabled={disabled || !remote}
        >
          <FolderOpen className="h-4 w-4" aria-hidden="true" />
          <span className="ms-1">{t('browse.button')}</span>
        </Button>
      </div>
      {choices.length === 0 && (
        <p className="text-muted-foreground text-xs">{t('wizard.configStep.remotePathNone')}</p>
      )}
      {remote && (
        <DirBrowser
          open={browseOpen}
          onOpenChange={setBrowseOpen}
          mode="remote"
          // The folder may not exist yet: start at the remote's top.
          initialPath={`${remote}:`}
          onSelect={(path) => onChange(path)}
        />
      )}
    </div>
  );
}
