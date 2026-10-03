import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nProvider } from '@/i18n';
import { RemoteWizard } from '@/components/config/wizard/remote-wizard';
import type { Provider, TestRemoteResult, WizardSession } from '@/types';

// OAuth sign-ins with the user's own app: OmniSync ships no client IDs, so
// the wizard asks for the app's client ID (and, for Google, its secret) up
// front, shows the redirect URI to register and the guide, and the
// provider's redirect to OmniSync's callback finishes the sign-in.

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
    setup_guide:  '',
  },
  {
    id:           'onedrive',
    display_name: 'OneDrive',
    icon:         'onedrive',
    auth_type:    'oauth',
    fields:       [
      { name: 'client_id', label: 'Client ID', field_type: 'text', required: true, help_text: '' },
      { name: 'client_secret', label: 'Client Secret', field_type: 'password', required: false, help_text: '' },
    ],
    default_name: 'onedrive',
    setup_guide:  '',
  },
];

const SID = 'session-own-app';
const REDIRECT = 'https://sync.example.com/api/wizard/oauth/callback';

function session (
  status: WizardSession['status'], error: string | null = null, errorCode: string | null = null
): WizardSession {
  return { session_id: SID, status, auth_url: 'https://accounts.google.com/auth', error, error_code: errorCode };
}

function reply (data: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(data) });
}

let fetchMock: ReturnType<typeof vi.fn>;
let sessionReply: WizardSession;
let authorizeReply: { status: number; body: unknown };

function setupBackend () {
  fetchMock.mockImplementation((url: string, options?: globalThis.RequestInit) => {
    if (url.includes('/wizard/providers')) return reply(PROVIDERS);
    if (url.includes('/wizard/oauth/redirect-uri')) return reply({ redirect_uri: REDIRECT });
    if (url.includes('/wizard/authorize') && options?.method === 'POST') {
      return reply(authorizeReply.body, authorizeReply.status);
    }
    if (url.includes('/wizard/sessions/')) return reply(sessionReply);
    if (url.includes('/wizard/create') && options?.method === 'POST') return reply({ detail: 'Remote created' });
    if (url.includes('/wizard/test') && options?.method === 'POST') {
      return reply({ success: true, error: null } satisfies TestRemoteResult);
    }
    return reply({});
  });
}

function calls (match: (url: string, init?: globalThis.RequestInit) => boolean) {
  return fetchMock.mock.calls.filter(([url, init]) => match(String(url), init as globalThis.RequestInit | undefined));
}

function bodyOf (url: string) {
  const [call] = calls((u, init) => u.includes(url) && init?.method === 'POST');
  return JSON.parse(String((call[1] as globalThis.RequestInit).body));
}

function wrapper () {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return function Wrapper ({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <I18nProvider>{children}</I18nProvider>
      </QueryClientProvider>
    );
  };
}

async function openProvider (user: ReturnType<typeof userEvent.setup>, name: string) {
  render(<RemoteWizard open={true} onOpenChange={() => {}} />, { wrapper: wrapper() });
  await waitFor(() => expect(screen.getByText(name)).toBeInTheDocument());
  await user.click(screen.getByText(name));
  await user.click(screen.getAllByRole('button', { name: /next/i })[0]);
  await waitFor(() => expect(screen.getByText(/start authorization/i)).toBeInTheDocument());
}

beforeEach(() => {
  fetchMock = vi.fn();
  global.fetch = fetchMock as typeof fetch;
  sessionReply = session('pending');
  authorizeReply = {
    status: 200,
    body:   { session_id: SID, auth_url: 'https://accounts.google.com/auth', redirect_uri: REDIRECT },
  };
  setupBackend();
});

describe('OAuth sign-in with your own app', () => {
  it('shows the redirect URI to register and the guide for the provider', async () => {
    const user = userEvent.setup();
    await openProvider(user, 'Google Drive');
    const hint = screen.getByTestId('own-app-hint');
    expect(hint).toHaveTextContent(/your own oauth app/i);
    expect(hint).toHaveTextContent(/google drive api/i);
    await waitFor(() => expect(screen.getByTestId('own-app-redirect-uri')).toHaveTextContent(REDIRECT));
    expect(screen.getByTestId('own-app-redirect-uri')).toHaveAttribute('dir', 'ltr');
    expect(screen.getByRole('link', { name: /step-by-step guide/i })).toHaveAttribute(
      'href', 'https://github.com/pan-fire/OmniSync/blob/main/docs/gem/remotes.md#google-drive'
    );
  });

  it('needs the client ID and, for Google, the client secret before the sign-in', async () => {
    const user = userEvent.setup();
    await openProvider(user, 'Google Drive');
    const start = screen.getByRole('button', { name: /start authorization/i });
    expect(start).toBeDisabled();
    await user.type(screen.getByLabelText(/client id/i), 'own.apps.googleusercontent.com');
    expect(start).toBeDisabled();
    await user.type(screen.getByLabelText(/client secret/i), 'own-secret');
    expect(start).toBeEnabled();
  });

  it('signs in with the own app and creates the remote once the callback completed', async () => {
    const user = userEvent.setup();
    await openProvider(user, 'OneDrive');
    expect(screen.getByRole('link', { name: /step-by-step guide/i })).toHaveAttribute(
      'href', expect.stringMatching(/remotes\.md#onedrive$/)
    );
    await user.type(screen.getByLabelText(/client id/i), '  app-guid  ');
    await user.click(screen.getByRole('button', { name: /start authorization/i }));
    await waitFor(() => expect(screen.getByText(/open authorization url/i)).toBeInTheDocument());
    expect(bodyOf('/wizard/authorize')).toEqual({ provider_id: 'onedrive', client_id: 'app-guid' });
    expect(screen.getByText(/waiting for authorization/i)).toBeInTheDocument();
    expect(screen.queryByTestId('own-app-hint')).not.toBeInTheDocument();
    expect(screen.queryByText(/paste/i)).not.toBeInTheDocument();

    sessionReply = session('completed');
    await waitFor(() => expect(calls((u, i) => u.includes('/wizard/create') && i?.method === 'POST')).toHaveLength(1), {
      timeout: 4000,
    });
    const create = bodyOf('/wizard/create');
    expect(create.session_id).toBe(SID);
    // The same app as the sign-in, so rclone refreshes the token with it.
    expect(create.params.client_id).toBe('app-guid');
    expect(create).not.toHaveProperty('token');
  });

  it('shows the server refusing a sign-in without the own app', async () => {
    const user = userEvent.setup();
    authorizeReply = {
      status: 422,
      body:   { detail: 'OneDrive needs your own OAuth app: register one.', code: 'oauth_client_id_required' },
    };
    await openProvider(user, 'OneDrive');
    await user.type(screen.getByLabelText(/client id/i), 'x');
    await user.click(screen.getByRole('button', { name: /start authorization/i }));
    await waitFor(() => expect(screen.getByText(/needs your own oauth app: register one/i)).toBeInTheDocument());
    expect(screen.queryByText(/open authorization url/i)).not.toBeInTheDocument();
  });

  it('explains a failed OneDrive drive lookup in the user language', async () => {
    const user = userEvent.setup();
    sessionReply = session('failed', 'Signed in, but...', 'onedrive_drive_lookup_failed');
    await openProvider(user, 'OneDrive');
    await user.type(screen.getByLabelText(/client id/i), 'app-guid');
    await user.click(screen.getByRole('button', { name: /start authorization/i }));
    await waitFor(() => expect(screen.getByText(/your onedrive could not be read/i)).toBeInTheDocument());
  });
});
