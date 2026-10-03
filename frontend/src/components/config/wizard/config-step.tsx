'use client';

import { useState, useEffect, useCallback } from 'react';
import type { Dispatch } from 'react';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n';
import {
  useProviders,
  useStartAuthorize,
  useWizardSession,
  useCreateRemote,
  useReconnectRemote,
  useOAuthRedirectUri,
} from '@/hooks/use-wizard';
import { ExternalLink, Loader2, HelpCircle, ChevronDown, ChevronUp } from 'lucide-react';
import type { WizardState, WizardAction } from './remote-wizard';
import { canAdvance } from './remote-wizard';
import { ProviderFieldInput } from './provider-field-input';
import type { ProviderField } from '@/types';

// Sign-in failures the server reports with a code (WizardSession.error_code)
// get a translated explanation instead of its English message.
const WIZARD_ERROR_KEYS: Record<string, string> = {
  onedrive_drive_lookup_failed: 'wizard.configStep.onedriveDriveLookupFailed',
};

const REMOTES_GUIDE = 'https://github.com/pan-fire/OmniSync/blob/main/docs/gem/remotes.md';

/**
 * How to register your own OAuth app, per provider: the section of
 * docs/gem/remotes.md and the wizard.ownApp.* key of the short hint.
 */
export const OWN_APP_GUIDES: Record<string, { docs: string; hint: string }> = {
  drive:    { docs: `${REMOTES_GUIDE}#google-drive`, hint: 'wizard.ownApp.drive' },
  dropbox:  { docs: `${REMOTES_GUIDE}#dropbox`, hint: 'wizard.ownApp.dropbox' },
  onedrive: { docs: `${REMOTES_GUIDE}#onedrive`, hint: 'wizard.ownApp.onedrive' },
};

interface ConfigStepProps {
  state:    WizardState;
  dispatch: Dispatch<WizardAction>;
}

export function ConfigStep ({ state, dispatch }: ConfigStepProps) {
  const { data: providers } = useProviders();
  const createRemote = useCreateRemote();

  const provider = providers?.find((p) => p.id === state.providerId);

  if (!provider) return null;

  if (state.providerAuthType === 'oauth') {
    return <OAuthFlow state={state} dispatch={dispatch} provider={provider} />;
  }

  return <KeyBasedForm state={state} dispatch={dispatch} provider={provider} createRemote={createRemote} />;
}

// --- Key-based form ---

function KeyBasedForm ({
  state,
  dispatch,
  provider,
  createRemote,
}: {
  state:        WizardState;
  dispatch:     Dispatch<WizardAction>;
  provider:     { fields: ProviderField[]; id: string; setup_guide: string };
  createRemote: ReturnType<typeof useCreateRemote>;
}) {
  const { t } = useTranslation();
  const [showGuide, setShowGuide] = useState(false);

  const requiredFieldNames = provider.fields
    .filter((f) => f.required)
    .map((f) => f.name);

  const handleNext = async () => {
    try {
      await createRemote.mutateAsync({
        name:        state.remoteName,
        provider_id: provider.id,
        params:      state.fields,
      });
      dispatch({ type: 'NEXT_STEP' });
    } catch (err) {
      dispatch({
        type:  'SET_ERROR',
        error: err instanceof Error ? err.message : String(err),
      });
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">{t('wizard.configStep.title')}</h3>
        {provider.setup_guide && (
          <button
            type="button"
            onClick={() => setShowGuide((v) => !v)}
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            aria-label={t('wizard.configStep.setupGuide')}
          >
            <HelpCircle className="h-4 w-4" />
            {showGuide ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          </button>
        )}
      </div>

      {showGuide && provider.setup_guide && (
        <div className="rounded-md border border-border bg-muted/50 p-3 text-xs text-muted-foreground whitespace-pre-line">
          {provider.setup_guide}
        </div>
      )}

      {provider.fields.map((field) => (
        <ProviderFieldInput
          key={field.name}
          field={field}
          value={state.fields[field.name] ?? ''}
          onChange={(value) => dispatch({ type: 'SET_FIELD', key: field.name, value })}
          ownName={state.remoteName}
        />
      ))}

      {state.error && (
        <p className="text-destructive text-sm">{state.error}</p>
      )}

      <div className="flex justify-end">
        <Button
          onClick={handleNext}
          disabled={!canAdvance(state, requiredFieldNames) || createRemote.isPending}
        >
          {createRemote.isPending
            ? (
            <Loader2 className="h-4 w-4 animate-spin" />
              )
            : null}
          {t('wizard.next')}
        </Button>
      </div>
    </div>
  );
}

// --- OAuth flow ---

/**
 * The provider's settings with the app credentials trimmed (and dropped
 * when blank), as both the sign-in and the new remote get them: the token
 * rclone stores must belong to the client rclone.conf names.
 */
function appParams (fields: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(fields)) {
    const v = key === 'client_id' || key === 'client_secret' ? value.trim() : value;
    if (v !== '') out[key] = v;
  }
  return out;
}

/**
 * What the user needs before the sign-in: their own OAuth app (OmniSync
 * ships none), the redirect URI to register with it, and the guide.
 */
function OwnAppHint ({ provider, reconnecting }: {
  provider:     { id: string; display_name?: string };
  reconnecting: boolean;
}) {
  const { t } = useTranslation();
  const { data } = useOAuthRedirectUri();
  const guide = OWN_APP_GUIDES[provider.id];
  // The server's answer honours OMNISYNC_OAUTH_REDIRECT_URI; until it is
  // known, the web UI's own callback address.
  const redirectUri = data?.redirect_uri ??
    (typeof window === 'undefined' ? '' : `${window.location.origin}/api/wizard/oauth/callback`);
  return (
    <div className="space-y-2 rounded-md border border-border bg-muted/50 p-3 text-sm" data-testid="own-app-hint">
      <p className="font-medium">{t('wizard.ownApp.title')}</p>
      <p className="text-muted-foreground text-xs">
        {t('wizard.ownApp.intro', { provider: provider.display_name ?? provider.id })}
      </p>
      {guide && <p className="text-muted-foreground text-xs">{t(guide.hint)}</p>}
      <div className="space-y-1">
        <p className="text-muted-foreground text-xs">{t('wizard.ownApp.redirectLabel')}</p>
        <code dir="ltr" className="block break-all rounded bg-background px-2 py-1 text-xs" data-testid="own-app-redirect-uri">
          {redirectUri}
        </code>
      </div>
      {guide && (
        <a
          href={guide.docs}
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary inline-flex items-center gap-1 text-xs underline"
        >
          {t('wizard.ownApp.docs')}
          <ExternalLink className="h-3 w-3" aria-hidden="true" />
        </a>
      )}
      {reconnecting && <p className="text-muted-foreground text-xs">{t('wizard.ownApp.reconnectNote')}</p>}
    </div>
  );
}

function OAuthFlow ({
  state,
  dispatch,
  provider,
}: {
  state:    WizardState;
  dispatch: Dispatch<WizardAction>;
  provider: { fields: ProviderField[]; id: string; setup_guide: string; display_name?: string };
}) {
  const { t } = useTranslation();
  const startAuthorize = useStartAuthorize();
  const createRemote = useCreateRemote();
  const { data: session } = useWizardSession(state.sessionId);
  const reconnectRemote = useReconnectRemote();
  const reconnecting = state.mode === 'reconnect';
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [showGuide, setShowGuide] = useState(false);

  // A new remote needs the app's client ID (and secret where the provider
  // requires one); a reconnect may keep the remote's stored app.
  const missingApp = !reconnecting && provider.fields.some(
    (f) => f.required && !(state.fields[f.name] ?? '').trim()
  );

  const handleStartAuth = useCallback(async () => {
    try {
      dispatch({ type: 'SET_ERROR', error: null });
      const app = appParams(state.fields);
      const result = await startAuthorize.mutateAsync({
        provider_id:   provider.id,
        client_id:     app.client_id,
        client_secret: app.client_secret,
        remote_name:   reconnecting ? state.remoteName : undefined,
      });
      dispatch({ type: 'SET_SESSION', sessionId: result.session_id });
      setAuthUrl(result.auth_url);
    } catch (err) {
      dispatch({
        type:  'SET_ERROR',
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }, [dispatch, startAuthorize, provider.id, state.fields, reconnecting, state.remoteName]);

  // Create the remote (or, reconnecting, store the new token in it) once
  // OAuth completes. The token never reaches the browser: the server reads
  // it from the completed wizard session.
  useEffect(() => {
    const saving = createRemote.isPending || reconnectRemote.isPending;
    if (session?.status === 'completed' && state.sessionId && !saving) {
      const save = reconnecting
        ? reconnectRemote.mutateAsync({ name: state.remoteName, sessionId: state.sessionId })
        : createRemote.mutateAsync({
          name:        state.remoteName,
          provider_id: provider.id,
          params:      appParams(state.fields),
          session_id:  state.sessionId,
        });
      save
        .then(() => dispatch({ type: 'NEXT_STEP' }))
        .catch((err) =>
          dispatch({
            type:  'SET_ERROR',
            error: err instanceof Error ? err.message : String(err),
          })
        );
    }
    if (session?.status === 'failed') {
      const key = session.error_code ? WIZARD_ERROR_KEYS[session.error_code] : undefined;
      dispatch({
        type:  'SET_ERROR',
        error: key ? t(key) : session.error ?? t('wizard.configStep.authFailed'),
      });
    }
  }, [session?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const isPending = state.sessionId !== null && session?.status === 'pending';
  const isCreating = session?.status === 'completed' && (createRemote.isPending || reconnectRemote.isPending);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">{t('wizard.configStep.oauthTitle')}</h3>
        {provider.setup_guide && (
          <button
            type="button"
            onClick={() => setShowGuide((v) => !v)}
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            aria-label={t('wizard.configStep.setupGuide')}
          >
            <HelpCircle className="h-4 w-4" />
            {showGuide ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          </button>
        )}
      </div>

      {showGuide && provider.setup_guide && (
        <div className="rounded-md border border-border bg-muted/50 p-3 text-xs text-muted-foreground whitespace-pre-line">
          {provider.setup_guide}
        </div>
      )}

      {state.mode === 'reconnect' && (
        <p className="text-muted-foreground text-sm">
          {t('wizard.reconnect.intro', { name: state.remoteName })}
        </p>
      )}

      {!state.sessionId && <OwnAppHint provider={provider} reconnecting={reconnecting} />}

      {provider.fields.map((field) => (
        <ProviderFieldInput
          key={field.name}
          field={reconnecting ? { ...field, required: false } : field}
          value={state.fields[field.name] ?? ''}
          onChange={(value) => dispatch({ type: 'SET_FIELD', key: field.name, value })}
          disabled={!!state.sessionId}
          placeholder={state.mode === 'reconnect' ? t('wizard.reconnect.keepApp') : undefined}
        />
      ))}

      {!state.sessionId && (
        <Button
          onClick={handleStartAuth}
          disabled={startAuthorize.isPending || missingApp}
        >
          {startAuthorize.isPending && (
            <Loader2 className="h-4 w-4 animate-spin" />
          )}
          {t('wizard.configStep.startAuth')}
        </Button>
      )}

      {authUrl && (
        <div className="rounded-md border p-3 space-y-2">
          <p className="text-sm">{t('wizard.configStep.authInstructions')}</p>
          <a
            href={authUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary inline-flex items-center gap-1 text-sm underline"
          >
            {t('wizard.configStep.openAuthUrl')}
            <ExternalLink className="h-3 w-3" />
          </a>
        </div>
      )}

      {isPending && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t('wizard.configStep.waitingForAuth')}
        </div>
      )}

      {isCreating && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          {reconnecting ? t('wizard.reconnect.saving') : t('wizard.configStep.creatingRemote')}
        </div>
      )}

      {state.error && (
        <div className="space-y-2">
          <p className="text-destructive text-sm">{state.error}</p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              dispatch({ type: 'SET_ERROR', error: null });
              // Empty string indicates no active OAuth session while satisfying reducer action type.
              dispatch({ type: 'SET_SESSION', sessionId: '' });
              setAuthUrl(null);
            }}
          >
            {t('wizard.retry')}
          </Button>
        </div>
      )}
    </div>
  );
}
