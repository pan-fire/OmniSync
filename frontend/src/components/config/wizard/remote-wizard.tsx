'use client';

import { useReducer, useCallback } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n';
import { useCancelSession } from '@/hooks/use-wizard';
import type { AuthType } from '@/types';
import { ProviderStep } from './provider-step';
import { ConfigStep } from './config-step';
import { TestStep } from './test-step';
import { CompleteStep } from './complete-step';

// --- Exported types and constants for the wizard state machine ---

export type WizardStep = 'provider' | 'config' | 'test' | 'complete';

export const STEP_ORDER: WizardStep[] = ['provider', 'config', 'test', 'complete'];

/**
 * 'create' sets up a new remote. 'reconnect' signs an existing OAuth remote
 * in again: it starts at the sign-in, and only the remote's token changes.
 */
export type WizardMode = 'create' | 'reconnect';

/** Reconnect skips choosing a provider and name: the remote has both. */
export const RECONNECT_STEP_ORDER: WizardStep[] = ['config', 'test', 'complete'];

export function stepsFor (mode: WizardMode): WizardStep[] {
  return mode === 'reconnect' ? RECONNECT_STEP_ORDER : STEP_ORDER;
}

export interface WizardState {
  step:             WizardStep;
  mode:             WizardMode;
  remoteName:       string;
  providerId:       string | null;
  providerAuthType: AuthType | null;
  fields:           Record<string, string>;
  sessionId:        string | null;
  error:            string | null;
}

export type WizardAction =
  | { type: 'SELECT_PROVIDER'; providerId: string; authType: AuthType; defaultName: string; defaults?: Record<string, string> }
  | { type: 'START_RECONNECT'; providerId: string; remoteName: string }
  | { type: 'SET_NAME'; name: string }
  | { type: 'SET_FIELD'; key: string; value: string }
  | { type: 'NEXT_STEP' }
  | { type: 'PREV_STEP' }
  | { type: 'SET_SESSION'; sessionId: string }
  | { type: 'SET_ERROR'; error: string | null }
  | { type: 'RESET' };

export const INITIAL_STATE: WizardState = {
  step:             'provider',
  mode:             'create',
  remoteName:       '',
  providerId:       null,
  providerAuthType: null,
  fields:           {},
  sessionId:        null,
  error:            null,
};

// --- Exported helper functions ---

export function isValidRemoteName (name: string): boolean {
  return /^[a-zA-Z0-9_-]+$/.test(name);
}

function nextStep (current: WizardStep, mode: WizardMode = 'create'): WizardStep {
  const order = stepsFor(mode);
  const idx = order.indexOf(current);
  if (idx < 0 || idx >= order.length - 1) return current;
  return order[idx + 1];
}

function prevStep (current: WizardStep, mode: WizardMode = 'create'): WizardStep {
  const order = stepsFor(mode);
  const idx = order.indexOf(current);
  if (idx <= 0) return current;
  return order[idx - 1];
}

export function canAdvance (state: WizardState, requiredFieldNames?: string[]): boolean {
  switch (state.step) {
    case 'provider':
      return state.providerId !== null && isValidRemoteName(state.remoteName);
    case 'config': {
      if (!requiredFieldNames || requiredFieldNames.length === 0) return true;
      return requiredFieldNames.every(
        (name) => {
          const val = Object.hasOwn(state.fields, name) ? state.fields[name] : '';
          return val.trim().length > 0;
        }
      );
    }
    case 'test':
      return true;
    case 'complete':
      return false;
    default:
      return false;
  }
}

export function wizardReducer (state: WizardState, action: WizardAction): WizardState {
  switch (action.type) {
    case 'SELECT_PROVIDER':
      return {
        ...state,
        providerId:       action.providerId,
        providerAuthType: action.authType,
        remoteName:       state.remoteName || action.defaultName,
        // Select fields start at their default value.
        fields:           { ...(action.defaults ?? {}) },
        error:            null,
      };
    case 'START_RECONNECT':
      return {
        ...INITIAL_STATE,
        step:             'config',
        mode:             'reconnect',
        providerId:       action.providerId,
        providerAuthType: 'oauth',
        remoteName:       action.remoteName,
      };
    case 'SET_NAME':
      return { ...state, remoteName: action.name };
    case 'SET_FIELD':
      return {
        ...state,
        fields: { ...state.fields, [action.key]: action.value },
      };
    case 'NEXT_STEP':
      return { ...state, step: nextStep(state.step, state.mode), error: null };
    case 'PREV_STEP':
      return { ...state, step: prevStep(state.step, state.mode), error: null };
    case 'SET_SESSION':
      return { ...state, sessionId: action.sessionId };
    case 'SET_ERROR':
      return { ...state, error: action.error };
    case 'RESET':
      return { ...INITIAL_STATE };
    default:
      return state;
  }
}

function initialState (reconnect: { name: string; providerId: string } | null): WizardState {
  return reconnect
    ? wizardReducer(INITIAL_STATE, { type: 'START_RECONNECT', providerId: reconnect.providerId, remoteName: reconnect.name })
    : INITIAL_STATE;
}

// --- Component ---

interface RemoteWizardProps {
  open:         boolean;
  onOpenChange: (open: boolean) => void;
  /** Sign this existing OAuth remote in again instead of creating one. */
  reconnect?:   { name: string; providerId: string } | null;
}

export function RemoteWizard ({ open, onOpenChange, reconnect }: RemoteWizardProps) {
  const { t } = useTranslation();
  // In reconnect mode the wizard starts at the sign-in for that remote. The
  // caller mounts it per reconnect, so the initial state is read once.
  const [state, dispatch] = useReducer(wizardReducer, reconnect ?? null, initialState);
  const cancelSession = useCancelSession();

  const handleCancel = useCallback(() => {
    // Stop a pending OAuth authorisation on the server; it is gone already
    // once the remote was created, so errors are ignored.
    if (state.sessionId && state.step !== 'complete') {
      cancelSession.mutate(state.sessionId, { onError: () => {} });
    }
    dispatch({ type: 'RESET' });
    onOpenChange(false);
  }, [onOpenChange, state.sessionId, state.step, cancelSession]);

  const handleDone = useCallback(() => {
    dispatch({ type: 'RESET' });
    onOpenChange(false);
  }, [onOpenChange]);

  const steps = stepsFor(state.mode);
  const stepIndex = steps.indexOf(state.step);
  const totalSteps = steps.length;

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) handleCancel(); }}>
      <DialogContent className="sm:max-w-[600px]">
        <DialogHeader>
          <DialogTitle>
            {state.mode === 'reconnect'
              ? t('wizard.reconnect.title', { name: state.remoteName })
              : t('wizard.title')}
          </DialogTitle>
          <DialogDescription className="sr-only">
            {t('wizard.description')}
          </DialogDescription>
          <div className="text-muted-foreground flex items-center gap-2 text-sm">
            <span>
              {`${stepIndex + 1} / ${totalSteps}`}
            </span>
            <div className="flex gap-1">
              {steps.map((s, i) => (
                <div
                  key={s}
                  className={`h-1.5 w-6 rounded-full ${
                    i <= stepIndex ? 'bg-primary' : 'bg-muted'
                  }`}
                />
              ))}
            </div>
          </div>
        </DialogHeader>

        <div className="min-h-[300px] py-4">
          {state.step === 'provider' && (
            <ProviderStep state={state} dispatch={dispatch} />
          )}
          {state.step === 'config' && (
            <ConfigStep state={state} dispatch={dispatch} />
          )}
          {state.step === 'test' && (
            <TestStep state={state} dispatch={dispatch} />
          )}
          {state.step === 'complete' && (
            <CompleteStep state={state} onDone={handleDone} />
          )}
        </div>

        <div className="flex justify-between">
          <Button variant="outline" onClick={handleCancel}>
            {t('common.cancel')}
          </Button>
          {state.step !== steps[0] && state.step !== 'complete' && (
            <Button variant="ghost" onClick={() => dispatch({ type: 'PREV_STEP' })}>
              {t('wizard.back')}
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
