import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nProvider } from '@/i18n';
import { RemoteWizard } from '@/components/config/wizard/remote-wizard';
import type { Provider, TestRemoteResult, WizardSession } from '@/types';

// --- Mock data ---

const MOCK_PROVIDERS: Provider[] = [
  {
    id:           'drive',
    display_name: 'Google Drive',
    icon:         'gdrive',
    auth_type:    'oauth',
    fields:       [
      { name: 'client_id', label: 'Client ID', field_type: 'text', required: false, help_text: 'Optional.' },
      { name: 'client_secret', label: 'Client Secret', field_type: 'password', required: false, help_text: 'Optional.' },
    ],
    default_name: 'gdrive',
    setup_guide:  'Create a Google Cloud project and enable the Drive API.',
  },
  {
    id:           's3',
    display_name: 'Amazon S3',
    icon:         's3',
    auth_type:    'key',
    fields:       [
      { name: 'access_key_id', label: 'Access Key ID', field_type: 'text', required: true, help_text: 'AWS IAM key.' },
      { name: 'secret_access_key', label: 'Secret Access Key', field_type: 'password', required: true, help_text: 'Secret key.' },
    ],
    default_name: 's3',
    setup_guide:  'Create an IAM user with S3 access.',
  },
  {
    id:           'sftp',
    display_name: 'SFTP',
    icon:         'sftp',
    auth_type:    'key',
    fields:       [
      { name: 'host', label: 'Host', field_type: 'text', required: true, help_text: 'Hostname.' },
      { name: 'user', label: 'Username', field_type: 'text', required: true, help_text: 'SSH user.' },
      { name: 'pass', label: 'Password', field_type: 'password', required: false, help_text: 'SSH password.' },
    ],
    default_name: 'sftp',
    setup_guide:  'Ensure SSH access is configured on the remote host.',
  },
];

// --- Mock fetch ---

let fetchMock: ReturnType<typeof vi.fn>;

function mockFetchResponse (data: unknown, ok = true, status = 200) {
  return Promise.resolve({
    ok,
    status,
    json: () => Promise.resolve(data),
  });
}

beforeEach(() => {
  fetchMock = vi.fn();
  global.fetch = fetchMock as typeof fetch;
});

function createWrapper () {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries:   { retry: false },
      mutations: { retry: false },
    },
  });
  return function Wrapper ({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <I18nProvider>{children}</I18nProvider>
      </QueryClientProvider>
    );
  };
}

// --- Tests ---

// Req 3.1: Wizard renders provider grid on open
describe('Wizard renders provider grid on open', () => {
  it('shows provider cards when wizard is opened', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes('/wizard/providers')) {
        return mockFetchResponse(MOCK_PROVIDERS);
      }
      return mockFetchResponse({});
    });

    render(<RemoteWizard open={true} onOpenChange={() => {}} />, {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(screen.getByText('Google Drive')).toBeInTheDocument();
    });
    expect(screen.getByText('Amazon S3')).toBeInTheDocument();
    expect(screen.getByText('SFTP')).toBeInTheDocument();
  });
});

// Req 3.3: Password fields render as masked inputs
describe('Password fields render as masked inputs', () => {
  it('renders password fields with type=password on config step', async () => {
    const user = userEvent.setup();

    fetchMock.mockImplementation((url: string) => {
      if (url.includes('/wizard/providers')) {
        return mockFetchResponse(MOCK_PROVIDERS);
      }
      return mockFetchResponse({});
    });

    render(<RemoteWizard open={true} onOpenChange={() => {}} />, {
      wrapper: createWrapper(),
    });

    // Wait for providers to load
    await waitFor(() => {
      expect(screen.getByText('Amazon S3')).toBeInTheDocument();
    });

    // Select S3 provider (key-based, has password field)
    await user.click(screen.getByText('Amazon S3'));

    // Name should be auto-filled, click Next
    await waitFor(() => {
      const nextButtons = screen.getAllByRole('button', { name: /next/i });
      expect(nextButtons.length).toBeGreaterThan(0);
    });
    const nextBtn = screen.getAllByRole('button', { name: /next/i })[0];
    await user.click(nextBtn);

    // Now on config step — check password field
    await waitFor(() => {
      expect(screen.getByText('Secret Access Key')).toBeInTheDocument();
    });

    const secretInput = screen.getByLabelText(/Secret Access Key/i) as HTMLInputElement;
    expect(secretInput.type).toBe('password');
  });
});

// Req 4.3: OAuth step shows auth URL as clickable link
describe('OAuth step shows auth URL as clickable link', () => {
  it('displays auth URL link after starting authorization', async () => {
    const user = userEvent.setup();

    fetchMock.mockImplementation((url: string, options?: globalThis.RequestInit) => {
      if (url.includes('/wizard/providers')) {
        return mockFetchResponse(MOCK_PROVIDERS);
      }
      if (url.includes('/wizard/authorize') && options?.method === 'POST') {
        return mockFetchResponse({
          session_id: 'test-session-123',
          auth_url:   'https://accounts.google.com/o/oauth2/auth?client_id=test',
        });
      }
      if (url.includes('/wizard/sessions/')) {
        return mockFetchResponse({
          session_id: 'test-session-123',
          status:     'pending',
          auth_url:   'https://accounts.google.com/o/oauth2/auth?client_id=test',
          error:      null,
        } satisfies WizardSession);
      }
      return mockFetchResponse({});
    });

    render(<RemoteWizard open={true} onOpenChange={() => {}} />, {
      wrapper: createWrapper(),
    });

    // Wait for providers, select Google Drive (OAuth)
    await waitFor(() => {
      expect(screen.getByText('Google Drive')).toBeInTheDocument();
    });
    await user.click(screen.getByText('Google Drive'));

    // Click Next to go to config step
    const nextBtn = screen.getAllByRole('button', { name: /next/i })[0];
    await user.click(nextBtn);

    // Click "Start authorization"
    await waitFor(() => {
      expect(screen.getByText(/start authorization/i)).toBeInTheDocument();
    });
    await user.click(screen.getByText(/start authorization/i));

    // Auth URL should appear as a clickable link
    await waitFor(() => {
      const link = screen.getByText(/open authorization url/i);
      expect(link).toBeInTheDocument();
      expect(link.closest('a')).toHaveAttribute('href', 'https://accounts.google.com/o/oauth2/auth?client_id=test');
      expect(link.closest('a')).toHaveAttribute('target', '_blank');
    });
  });
});

// Req 4.6: Auto-advance on OAuth completion
describe('Auto-advance on OAuth completion', () => {
  it('advances to test step when OAuth session completes', async () => {
    const user = userEvent.setup();
    let sessionCallCount = 0;

    fetchMock.mockImplementation((url: string, options?: globalThis.RequestInit) => {
      if (url.includes('/wizard/providers')) {
        return mockFetchResponse(MOCK_PROVIDERS);
      }
      if (url.includes('/wizard/authorize') && options?.method === 'POST') {
        return mockFetchResponse({
          session_id: 'session-456',
          auth_url:   'https://accounts.google.com/auth',
        });
      }
      if (url.includes('/wizard/sessions/')) {
        sessionCallCount++;
        // First call: pending, subsequent: completed
        if (sessionCallCount <= 1) {
          return mockFetchResponse({
            session_id: 'session-456',
            status:     'pending',
            auth_url:   'https://accounts.google.com/auth',
            error:      null,
          } satisfies WizardSession);
        }
        return mockFetchResponse({
          session_id: 'session-456',
          status:     'completed',
          auth_url:   'https://accounts.google.com/auth',
          error:      null,
        } satisfies WizardSession);
      }
      if (url.includes('/wizard/create') && options?.method === 'POST') {
        return mockFetchResponse({ detail: 'Remote created' });
      }
      if (url.includes('/wizard/test') && options?.method === 'POST') {
        return mockFetchResponse({ success: true, error: null } satisfies TestRemoteResult);
      }
      return mockFetchResponse({});
    });

    render(<RemoteWizard open={true} onOpenChange={() => {}} />, {
      wrapper: createWrapper(),
    });

    // Select Google Drive
    await waitFor(() => {
      expect(screen.getByText('Google Drive')).toBeInTheDocument();
    });
    await user.click(screen.getByText('Google Drive'));

    // Go to config step
    const nextBtn = screen.getAllByRole('button', { name: /next/i })[0];
    await user.click(nextBtn);

    // Start auth
    await waitFor(() => {
      expect(screen.getByText(/start authorization/i)).toBeInTheDocument();
    });
    await user.click(screen.getByText(/start authorization/i));

    // Should auto-advance past test step to complete step (test auto-advances on success)
    await waitFor(
      () => {
        expect(screen.getByText(/configured successfully/i)).toBeInTheDocument();
      },
      { timeout: 5000 }
    );

    // The remote is created from the completed session: the browser sends
    // the session id and never handles the OAuth token.
    const createCall = fetchMock.mock.calls.find(([url]) => String(url).includes('/wizard/create'));
    expect(createCall).toBeDefined();
    const body = JSON.parse(String((createCall![1] as globalThis.RequestInit).body));
    expect(body).toMatchObject({ name: 'gdrive', provider_id: 'drive', session_id: 'session-456' });
    expect(body).not.toHaveProperty('token');
  });
});

// Req 8.1: Remote name input on provider step
describe('Remote name input on provider step', () => {
  it('shows a remote name input field on the provider step', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes('/wizard/providers')) {
        return mockFetchResponse(MOCK_PROVIDERS);
      }
      return mockFetchResponse({});
    });

    render(<RemoteWizard open={true} onOpenChange={() => {}} />, {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(screen.getByText('Google Drive')).toBeInTheDocument();
    });

    const nameInput = screen.getByLabelText(/remote name/i);
    expect(nameInput).toBeInTheDocument();
    expect(nameInput).toBeInstanceOf(HTMLInputElement);
  });
});

// Req 8.4: Default name suggested on provider selection
describe('Default name suggested on provider selection', () => {
  it('auto-fills default name when a provider is selected', async () => {
    const user = userEvent.setup();

    fetchMock.mockImplementation((url: string) => {
      if (url.includes('/wizard/providers')) {
        return mockFetchResponse(MOCK_PROVIDERS);
      }
      return mockFetchResponse({});
    });

    render(<RemoteWizard open={true} onOpenChange={() => {}} />, {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(screen.getByText('Amazon S3')).toBeInTheDocument();
    });

    const nameInput = screen.getByLabelText(/remote name/i) as HTMLInputElement;
    expect(nameInput.value).toBe('');

    // Select S3
    await user.click(screen.getByText('Amazon S3'));

    // Name should be auto-filled with "s3"
    expect(nameInput.value).toBe('s3');
  });
});

// Req 6.2: Remotes list refreshed on wizard completion
describe('Remotes list refreshed on wizard completion', () => {
  it('invalidates remotes query when Done is clicked', async () => {
    const user = userEvent.setup();
    let sessionCallCount = 0;

    const queryClient = new QueryClient({
      defaultOptions: {
        queries:   { retry: false },
        mutations: { retry: false },
      },
    });
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

    fetchMock.mockImplementation((url: string, options?: globalThis.RequestInit) => {
      if (url.includes('/wizard/providers')) {
        return mockFetchResponse(MOCK_PROVIDERS);
      }
      if (url.includes('/wizard/authorize') && options?.method === 'POST') {
        return mockFetchResponse({
          session_id: 'session-done',
          auth_url:   'https://example.com/auth',
        });
      }
      if (url.includes('/wizard/sessions/')) {
        sessionCallCount++;
        if (sessionCallCount <= 1) {
          return mockFetchResponse({
            session_id: 'session-done',
            status:     'pending',
            auth_url:   'https://example.com/auth',
            error:      null,
          } satisfies WizardSession);
        }
        return mockFetchResponse({
          session_id: 'session-done',
          status:     'completed',
          auth_url:   'https://example.com/auth',
          error:      null,
        } satisfies WizardSession);
      }
      if (url.includes('/wizard/create') && options?.method === 'POST') {
        return mockFetchResponse({ detail: 'Remote created' });
      }
      if (url.includes('/wizard/test') && options?.method === 'POST') {
        return mockFetchResponse({ success: true, error: null } satisfies TestRemoteResult);
      }
      return mockFetchResponse({});
    });

    render(
      <QueryClientProvider client={queryClient}>
        <I18nProvider>
          <RemoteWizard open={true} onOpenChange={() => {}} />
        </I18nProvider>
      </QueryClientProvider>
    );

    // Navigate through wizard: select provider → auth → complete
    await waitFor(() => {
      expect(screen.getByText('Google Drive')).toBeInTheDocument();
    });
    await user.click(screen.getByText('Google Drive'));
    const nextBtn = screen.getAllByRole('button', { name: /next/i })[0];
    await user.click(nextBtn);

    await waitFor(() => {
      expect(screen.getByText(/start authorization/i)).toBeInTheDocument();
    });
    await user.click(screen.getByText(/start authorization/i));

    // Wait for complete step
    await waitFor(
      () => {
        expect(screen.getByText(/configured successfully/i)).toBeInTheDocument();
      },
      { timeout: 5000 }
    );

    // Click Done
    await user.click(screen.getByText(/done/i));

    // Verify remotes query was invalidated
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['remotes'] })
    );
  });
});

// Cancelling during OAuth stops the server-side authorisation session.
describe('Cancelling the wizard during OAuth', () => {
  it('deletes the pending wizard session', async () => {
    const user = userEvent.setup();

    fetchMock.mockImplementation((url: string, options?: globalThis.RequestInit) => {
      if (url.includes('/wizard/providers')) return mockFetchResponse(MOCK_PROVIDERS);
      if (url.includes('/wizard/authorize') && options?.method === 'POST') {
        return mockFetchResponse({ session_id: 'session-cancel', auth_url: 'https://example.com/auth' });
      }
      if (url.includes('/wizard/sessions/')) {
        return mockFetchResponse({
          session_id: 'session-cancel',
          status:     'pending',
          auth_url:   'https://example.com/auth',
          error:      null,
        } satisfies WizardSession);
      }
      return mockFetchResponse({});
    });

    render(<RemoteWizard open={true} onOpenChange={() => {}} />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText('Google Drive')).toBeInTheDocument());
    await user.click(screen.getByText('Google Drive'));
    await user.click(screen.getAllByRole('button', { name: /next/i })[0]);
    await waitFor(() => expect(screen.getByText(/start authorization/i)).toBeInTheDocument());
    await user.click(screen.getByText(/start authorization/i));
    await waitFor(() => expect(screen.getByText(/open authorization url/i)).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => {
      const deleted = fetchMock.mock.calls.some(
        ([url, init]) => String(url) === '/api/wizard/sessions/session-cancel' && (init as globalThis.RequestInit)?.method === 'DELETE'
      );
      expect(deleted).toBe(true);
    });
  });
});
