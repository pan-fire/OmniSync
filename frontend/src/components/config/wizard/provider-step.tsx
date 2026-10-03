'use client';

import type { Dispatch, ElementType } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useTranslation } from '@/i18n';
import { useProviders } from '@/hooks/use-wizard';
import {
  HardDrive,
  Cloud,
  Server,
  Database,
  Globe,
  Loader2,
  Lock,
  Network,
} from 'lucide-react';
import type { WizardState, WizardAction } from './remote-wizard';
import { isValidRemoteName, canAdvance } from './remote-wizard';
import type { Provider } from '@/types';

const PROVIDER_ICONS: Record<string, ElementType> = {
  gdrive:   Cloud,
  dropbox:  Cloud,
  onedrive: Cloud,
  s3:       Database,
  b2:       Database,
  sftp:     Server,
  ftp:      Globe,
  webdav:   Globe,
  smb:      Network,
  crypt:    Lock,
};

function getProviderIcon (icon: string) {
  return PROVIDER_ICONS[icon] ?? HardDrive;
}

interface ProviderStepProps {
  state:    WizardState;
  dispatch: Dispatch<WizardAction>;
}

export function ProviderStep ({ state, dispatch }: ProviderStepProps) {
  const { t } = useTranslation();
  const { data: providers, isLoading } = useProviders();

  const nameError =
    state.remoteName.length > 0 && !isValidRemoteName(state.remoteName);

  const handleSelectProvider = (provider: Provider) => {
    dispatch({
      type:        'SELECT_PROVIDER',
      providerId:  provider.id,
      authType:    provider.auth_type,
      defaultName: provider.default_name,
      defaults:    Object.fromEntries(
        provider.fields.filter((f) => f.default).map((f) => [f.name, f.default as string])
      ),
    });
  };

  return (
    <div className="space-y-6">
      <div>
        <h3 className="mb-3 text-sm font-medium">
          {t('wizard.providerStep.title')}
        </h3>
        {isLoading
          ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="text-muted-foreground h-6 w-6 animate-spin" />
          </div>
            )
          : (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {providers?.map((provider) => {
              const Icon = getProviderIcon(provider.icon);
              const isSelected = state.providerId === provider.id;
              return (
                <button
                  key={provider.id}
                  type="button"
                  onClick={() => handleSelectProvider(provider)}
                  className={`flex flex-col items-center gap-2 rounded-lg border p-4 transition-colors hover:bg-accent ${
                    isSelected
                      ? 'border-primary bg-accent'
                      : 'border-border'
                  }`}
                >
                  <Icon className="h-8 w-8" />
                  <span className="text-sm font-medium">
                    {provider.display_name}
                  </span>
                </button>
              );
            })}
          </div>
            )}
      </div>

      <div className="space-y-2">
        <Label htmlFor="remote-name">{t('wizard.providerStep.remoteName')}</Label>
        <Input
          id="remote-name"
          dir="ltr"
          value={state.remoteName}
          onChange={(e) => dispatch({ type: 'SET_NAME', name: e.target.value })}
          placeholder={t('wizard.providerStep.remoteNamePlaceholder')}
          aria-invalid={nameError}
        />
        {nameError && (
          <p className="text-destructive text-sm">
            {t('wizard.providerStep.nameError')}
          </p>
        )}
      </div>

      <div className="flex justify-end">
        <Button
          onClick={() => dispatch({ type: 'NEXT_STEP' })}
          disabled={!canAdvance(state)}
        >
          {t('wizard.next')}
        </Button>
      </div>
    </div>
  );
}
