'use client';

import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { ProviderFieldInput } from '@/components/config/wizard/provider-field-input';
import { useTranslation } from '@/i18n';
import { useProviders } from '@/hooks/use-wizard';
import { useRemoteConfig, useUpdateRemote } from '@/hooks/use-remotes';
import type { Provider, RemoteConfig, UpdateRemoteRequest } from '@/types';

interface RemoteEditDialogProps {
  name:         string;
  open:         boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Edit an existing remote's settings in place (PUT /remotes/{name}).
 * Secrets are never shown: an empty secret field keeps the stored one, a
 * new value replaces it, and "Remove" deletes it.
 */
export function RemoteEditDialog ({ name, open, onOpenChange }: RemoteEditDialogProps) {
  const { t } = useTranslation();
  const config = useRemoteConfig(name, open);
  const { data: providers } = useProviders();
  const provider = providers?.find((p) => p.id === config.data?.provider_id);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[600px]">
        <DialogHeader>
          <DialogTitle>{t('remotes.edit.title', { name })}</DialogTitle>
          <DialogDescription>{t('remotes.edit.description')}</DialogDescription>
        </DialogHeader>
        {config.isError && (
          <p role="alert" className="text-destructive text-sm">
            {config.error instanceof Error && config.error.message ? config.error.message : t('remotes.edit.loadFailed')}
          </p>
        )}
        {(config.isLoading || (config.data && !provider)) && !config.isError && (
          <div className="flex justify-center py-8">
            <Loader2 className="text-muted-foreground h-6 w-6 animate-spin" />
          </div>
        )}
        {config.data && provider && (
          // Keyed by the loaded config: the form starts from it.
          <EditForm
            key={`${config.dataUpdatedAt}`}
            name={name}
            config={config.data}
            provider={provider}
            onDone={() => onOpenChange(false)}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function EditForm ({ name, config, provider, onDone }: {
  name:     string;
  config:   RemoteConfig;
  provider: Provider;
  onDone:   () => void;
}) {
  const { t } = useTranslation();
  const update = useUpdateRemote();
  const stored = Object.fromEntries(config.fields.map((f) => [f.name, f]));
  const [values, setValues] = useState<Record<string, string>>(
    () => Object.fromEntries(config.fields.map((f) => [f.name, f.secret ? '' : f.value]))
  );
  const [clear, setClear] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);

  const missing = provider.fields.some((f) => f.required && !(
    (values[f.name] ?? '').trim() || (stored[f.name]?.secret && stored[f.name]?.is_set && !clear[f.name])
  ));

  const handleSave = async () => {
    setError(null);
    const data: UpdateRemoteRequest = { params: {}, clear: [] };
    for (const f of provider.fields) {
      const value = values[f.name] ?? '';
      if (stored[f.name]?.secret) {
        if (clear[f.name]) data.clear!.push(f.name);
        else if (value) data.params[f.name] = value;
      } else if (value !== (stored[f.name]?.value ?? '')) {
        data.params[f.name] = value;
      }
    }
    try {
      await update.mutateAsync({ name, data });
      toast.success(t('remotes.edit.saved', { name }));
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="space-y-4">
      {provider.id === 'crypt' && (
        <p className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3 text-sm">
          {t('remotes.edit.cryptWarning')}
        </p>
      )}
      <div className="max-h-[60vh] space-y-4 overflow-y-auto pe-1">
        {provider.fields.map((field) => {
          const info = stored[field.name];
          const secretSet = !!info?.secret && info.is_set;
          return (
            <div key={field.name} className="space-y-1">
              <ProviderFieldInput
                field={field}
                value={values[field.name] ?? ''}
                onChange={(value) => setValues((prev) => ({ ...prev, [field.name]: value }))}
                disabled={!!clear[field.name]}
                placeholder={secretSet ? t('remotes.edit.secretKept') : undefined}
                ownName={name}
              />
              {secretSet && !field.required && (
                <div className="flex items-center gap-2">
                  <Checkbox
                    id={`clear-${field.name}`}
                    checked={!!clear[field.name]}
                    onCheckedChange={(checked) => {
                      setClear((prev) => ({ ...prev, [field.name]: checked === true }));
                      if (checked === true) setValues((prev) => ({ ...prev, [field.name]: '' }));
                    }}
                  />
                  <Label htmlFor={`clear-${field.name}`} className="text-xs font-normal">
                    {t('remotes.edit.removeSecret', { field: field.label })}
                  </Label>
                </div>
              )}
            </div>
          );
        })}
      </div>
      {config.other_keys.length > 0 && (
        <p className="text-muted-foreground text-xs">
          {t('remotes.edit.otherKeys', { keys: config.other_keys.join(', ') })}
        </p>
      )}
      {error && <p role="alert" className="text-destructive text-sm">{error}</p>}
      <DialogFooter>
        <Button variant="outline" onClick={onDone}>{t('common.cancel')}</Button>
        <Button onClick={handleSave} disabled={missing || update.isPending}>
          {update.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          {t('remotes.edit.save')}
        </Button>
      </DialogFooter>
    </div>
  );
}
