import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider } from '@/i18n';
import { INITIAL_STATE, RemoteWizard, canAdvance, wizardReducer } from '@/components/config/wizard/remote-wizard';
import type { Provider } from '@/types';
import { stubBackend } from '../helpers/fake-backend';
import { fill } from '../helpers/fill';

const PROVIDERS: Provider[] = [
  {
    id:           'drive',
    display_name: 'Google Drive',
    icon:         'gdrive',
    auth_type:    'oauth',
    fields:       [
      { name: 'client_id', label: 'Client ID', field_type: 'text', required: true, help_text: '' },
      { name: 'client_secret', label: 'Client Secret', field_type: 'password', required: true, help_text: '' },
    ],
    default_name: 'gdrive',
    setup_guide:  'Create a Google Cloud project.',
  },
  {
    id:           's3',
    display_name: 'Amazon S3',
    icon:         's3',
    auth_type:    'key',
    fields:       [
      { name: 'access_key_id', label: 'Access Key ID', field_type: 'text', required: true, help_text: '' },
    ],
    default_name: 's3',
    setup_guide:  'Create an IAM user with S3 access.',
  },
];

const BASE = {
  'GET /wizard/providers':          PROVIDERS,
  'GET /wizard/oauth/redirect-uri': { redirect_uri: 'https://sync.example.com/api/wizard/oauth/callback' },
};

function renderWizard (onOpenChange = vi.fn(), locale: 'en' | 'fa' = 'en') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <I18nProvider initialLocale={locale}>
        <RemoteWizard open onOpenChange={onOpenChange} />
      </I18nProvider>
    </QueryClientProvider>
  );
  return onOpenChange;
}

async function choose (user: ReturnType<typeof userEvent.setup>, provider: string) {
  await user.click(await screen.findByText(provider));
  await user.click(screen.getByRole('button', { name: 'Next' }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('wizard state', () => {
  it('a config step needs every required field, filled with more than spaces', () => {
    const state = { ...INITIAL_STATE, step: 'config' as const, fields: { a: '  ' } };
    expect(canAdvance(state)).toBe(true);
    expect(canAdvance(state, ['a'])).toBe(false);
    expect(canAdvance(state, ['b'])).toBe(false);
    expect(canAdvance({ ...state, fields: { a: 'x' } }, ['a'])).toBe(true);
  });

  it('the test step may always advance, the last step never', () => {
    expect(canAdvance({ ...INITIAL_STATE, step: 'test' })).toBe(true);
    expect(canAdvance({ ...INITIAL_STATE, step: 'complete' })).toBe(false);
  });

  it('stays put at either end and resets', () => {
    expect(wizardReducer(INITIAL_STATE, { type: 'PREV_STEP' }).step).toBe('provider');
    const last = { ...INITIAL_STATE, step: 'complete' as const };
    expect(wizardReducer(last, { type: 'NEXT_STEP' }).step).toBe('complete');
    const named = wizardReducer(INITIAL_STATE, { type: 'SET_NAME', name: 'nas' });
    expect(wizardReducer(named, { type: 'RESET' })).toEqual(INITIAL_STATE);
  });
});

describe('RemoteWizard, key-based provider', () => {
  it('shows the setup guide on request and the server\'s refusal to create the remote', async () => {
    const { requests } = stubBackend(BASE);
    const user = userEvent.setup();
    renderWizard();
    await choose(user, 'Amazon S3');

    expect(screen.queryByText('Create an IAM user with S3 access.')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Where do I find these?' }));
    expect(screen.getByText('Create an IAM user with S3 access.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Where do I find these?' }));
    expect(screen.queryByText('Create an IAM user with S3 access.')).not.toBeInTheDocument();

    const next = screen.getByRole('button', { name: 'Next' });
    expect(next).toBeDisabled();
    await fill(user, screen.getByLabelText(/Access Key ID/), 'AKIA1');
    await user.click(next);
    expect(await screen.findByText('no route for POST /wizard/create')).toBeInTheDocument();
    expect(requests).toContain('POST /wizard/create');
  });

  it('Back returns to the provider step', async () => {
    stubBackend(BASE);
    const user = userEvent.setup();
    renderWizard();
    await choose(user, 'Amazon S3');
    await user.click(screen.getByRole('button', { name: 'Back' }));
    expect(await screen.findByText('Google Drive')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
  });

  it('creates and tests the remote, and Done closes the wizard', async () => {
    stubBackend({
      ...BASE,
      'POST /wizard/create': { detail: 'created' },
      'POST /wizard/test':   { success: true, error: null },
    });
    const user = userEvent.setup();
    const onOpenChange = renderWizard();
    await choose(user, 'Amazon S3');
    await fill(user, screen.getByLabelText(/Access Key ID/), 'AKIA1');
    await user.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('Remote configured successfully!')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Done' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});

describe('RemoteWizard, OAuth provider', () => {
  const OAUTH = {
    ...BASE,
    'POST /wizard/authorize':     { session_id: 's1', auth_url: 'https://accounts.example.com/auth' },
    'DELETE /wizard/sessions/s1': {},
  };

  async function signIn (user: ReturnType<typeof userEvent.setup>) {
    await choose(user, 'Google Drive');
    await fill(user, screen.getByLabelText(/Client ID/), 'app-id');
    await fill(user, screen.getByLabelText(/Client Secret/), 'app-secret');
    await user.click(screen.getByRole('button', { name: 'Start authorization' }));
  }

  it('reports a failed save after the sign-in, and Retry starts over', async () => {
    stubBackend({ ...OAUTH, 'GET /wizard/sessions/s1': { session_id: 's1', status: 'completed', auth_url: null, error: null } });
    const user = userEvent.setup();
    renderWizard();
    await signIn(user);

    expect(await screen.findByText('no route for POST /wizard/create')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(screen.queryByText('no route for POST /wizard/create')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start authorization' })).toBeInTheDocument();
  });

  it('shows the provider guide on request', async () => {
    stubBackend(OAUTH);
    const user = userEvent.setup();
    renderWizard();
    await choose(user, 'Google Drive');
    await user.click(screen.getByRole('button', { name: 'Where do I find these?' }));
    expect(screen.getByText('Create a Google Cloud project.')).toBeInTheDocument();
  });

  // Closing the dialog during a sign-in must not leave the session open on the server.
  it('closing with Escape cancels the pending sign-in', async () => {
    const { requests } = stubBackend({ ...OAUTH, 'GET /wizard/sessions/s1': { session_id: 's1', status: 'pending', auth_url: null, error: null } });
    const user = userEvent.setup();
    const onOpenChange = renderWizard();
    await signIn(user);
    expect(await screen.findByText('Waiting for authorization...')).toBeInTheDocument();

    await user.keyboard('{Escape}');
    expect(onOpenChange).toHaveBeenCalledWith(false);
    await waitFor(() => expect(requests).toContain('DELETE /wizard/sessions/s1'));
  });

  // The session may be gone already; a failed cancel must not stop the close.
  it('closes even when cancelling the sign-in fails', async () => {
    const { 'DELETE /wizard/sessions/s1': _gone, ...routes } = OAUTH;
    const { requests } = stubBackend({ ...routes, 'GET /wizard/sessions/s1': { session_id: 's1', status: 'pending', auth_url: null, error: null } });
    const user = userEvent.setup();
    const onOpenChange = renderWizard();
    await signIn(user);
    await screen.findByText('Waiting for authorization...');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    await waitFor(() => expect(requests).toContain('DELETE /wizard/sessions/s1'));
  });

  it('is titled in Persian', async () => {
    stubBackend(BASE);
    renderWizard(vi.fn(), 'fa');
    expect(await screen.findByRole('dialog', { name: 'راهنمای راه‌اندازی ریموت' })).toBeInTheDocument();
  });
});
