import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import { ChannelCard } from '@/components/notifications/channel-card';
import { emailErrors, ntfyErrors, urlError, webhookErrors } from '@/components/notifications/channel-settings-form';
import type { ChannelConfig } from '@/types';

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

type Call = { method: string; url: string; body?: unknown };
let calls: Call[];
let putResponse: { status: number; body: unknown };

function json (status: number, body: unknown) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
}

beforeEach(() => {
  calls = [];
  putResponse = { status: 200, body: { channels: {} } };
  global.fetch = vi.fn((url: string, init?: globalThis.RequestInit) => {
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    const path = url.replace(/^\/api/, '').split('?')[0];
    calls.push({ method, url: path, body });
    if (method === 'PUT') return json(putResponse.status, putResponse.body);
    return json(200, {});
  }) as unknown as typeof fetch;
  vi.mocked(toast.success).mockClear();
});

function renderCard (channelName: string, config: ChannelConfig) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><I18nProvider>{children}</I18nProvider></QueryClientProvider>
  );
  return render(
    <ChannelCard
      channelName={channelName}
      config={config}
      status={{ available: false, missing_dependencies: [] }}
      onToggle={() => {}}
      onSeverityChange={() => {}}
    />,
    { wrapper: Wrapper }
  );
}

const putBody = () => calls.find((c) => c.method === 'PUT')?.body as { channels: Record<string, Record<string, unknown>> };

describe('validation', () => {
  it('requires https unless plain http is allowed', () => {
    expect(urlError('https://hooks.example.com/x', false)).toBeNull();
    expect(urlError('http://192.168.1.2/x', false)).toBe('httpNotAllowed');
    expect(urlError('http://192.168.1.2/x', true)).toBeNull();
    expect(urlError('ftp://example.com', true)).toBe('urlScheme');
    expect(urlError('https://user:pw@example.com', true)).toBe('urlCredentials');
    expect(urlError('', true)).toBe('urlRequired');
  });

  it('checks headers', () => {
    expect(webhookErrors('https://a.b', false, [{ name: 'Host', value: 'x', valueSet: false }])).toEqual({ 'header-0': 'headerName' });
    expect(webhookErrors('https://a.b', false, [{ name: 'X-A', value: '', valueSet: false }])).toEqual({ 'header-0': 'headerValue' });
    expect(webhookErrors('https://a.b', false, [{ name: 'X-A', value: '', valueSet: true }])).toEqual({});
    expect(webhookErrors('https://a.b', false, [
      { name: 'X-A', value: '1', valueSet: false }, { name: 'x-a', value: '2', valueSet: false },
    ])).toEqual({ 'header-1': 'headerDuplicate' });
  });

  it('checks ntfy and email fields', () => {
    expect(ntfyErrors('https://ntfy.sh', 'a/b', false)).toEqual({ topic: 'topic' });
    expect(emailErrors('smtp.example.com', '587', 'a@b.c', 'd@e.f, g@h.i')).toEqual({});
    expect(emailErrors('', '0', 'nope', '')).toEqual({ host: 'host', port: 'port', from_addr: 'from', to: 'to' });
  });
});

describe('webhook form', () => {
  const config: ChannelConfig = {
    enabled:      true,
    min_severity: 'error',
    webhook:      { url: 'https://hooks.example.com/x', allow_http: false, headers: [{ name: 'Authorization', value_set: true }] },
  };

  it('masks the stored header and keeps it when left empty', async () => {
    const user = userEvent.setup();
    renderCard('webhook', config);
    await user.click(screen.getByRole('button', { name: 'Configure' }));
    const value = screen.getByLabelText('Header value (stored as a secret)');
    expect(value).toHaveValue('');
    expect(value).toHaveAttribute('type', 'password');
    expect(value).toHaveAttribute('placeholder', '•••• set — leave empty to keep');

    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(putBody()).toBeDefined());
    expect(putBody()).toEqual({
      channels: {
        webhook: {
          webhook: {
            url: 'https://hooks.example.com/x', allow_http: false, headers: [{ name: 'Authorization', value: '' }],
          }
        }
      }
    });
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Channel settings saved'));
  });

  it('refuses http without the opt-in before sending', async () => {
    const user = userEvent.setup();
    renderCard('webhook', { ...config, webhook: { url: '', allow_http: false, headers: [] } });
    await user.click(screen.getByRole('button', { name: 'Configure' }));
    await user.type(screen.getByLabelText('URL'), 'http://192.168.1.5:8123/api/webhook/x');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(screen.getByText(/needs “Allow plain http”/)).toBeInTheDocument();
    expect(screen.getByLabelText('URL')).toHaveAttribute('aria-invalid', 'true');
    expect(putBody()).toBeUndefined();

    await user.click(screen.getByRole('checkbox', { name: /Allow plain http/ }));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(putBody()?.channels.webhook).toEqual({
      webhook: {
        url: 'http://192.168.1.5:8123/api/webhook/x', allow_http: true, headers: [],
      }
    }));
  });

  it("shows the server's reason when it refuses the settings", async () => {
    putResponse = { status: 400, body: { detail: 'Invalid webhook URL: link-local and metadata addresses are not allowed' } };
    const user = userEvent.setup();
    renderCard('webhook', config);
    await user.click(screen.getByRole('button', { name: 'Configure' }));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(/link-local and metadata addresses are not allowed/)).toBeInTheDocument();
  });
});

describe('ntfy form', () => {
  it('sends a new token, never the stored one', async () => {
    const user = userEvent.setup();
    renderCard('ntfy', {
      enabled:      false,
      min_severity: 'warning',
      ntfy:         { server: 'https://ntfy.sh', topic: 'nas', allow_http: false, username: '', token_set: true, password_set: false },
    });
    await user.click(screen.getByRole('button', { name: 'Configure' }));
    expect(screen.getByLabelText('Access token')).toHaveAttribute('placeholder', '•••• set — leave empty to keep');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(putBody()?.channels.ntfy).toEqual({
      ntfy: {
        server: 'https://ntfy.sh', topic: 'nas', allow_http: false, username: '',
      }
    }));
  });

  it('can remove a stored token', async () => {
    const user = userEvent.setup();
    renderCard('ntfy', {
      enabled:      false,
      min_severity: 'warning',
      ntfy:         { server: 'https://ntfy.sh', topic: 'nas', allow_http: false, username: '', token_set: true, password_set: false },
    });
    await user.click(screen.getByRole('button', { name: 'Configure' }));
    await user.click(screen.getByRole('button', { name: 'Remove' }));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect((putBody()?.channels.ntfy as { ntfy: { clear: string[] } }).ntfy.clear).toEqual(['token']));
  });
});

describe('email form', () => {
  it('sends the settings with the recipients split', async () => {
    const user = userEvent.setup();
    renderCard('email', { enabled: false, min_severity: 'warning' });
    await user.click(screen.getByRole('button', { name: 'Configure' }));
    await user.type(screen.getByLabelText('SMTP server'), 'smtp.example.com');
    await user.type(screen.getByLabelText('Password'), 'geheim');
    await user.type(screen.getByLabelText('From'), 'nas@example.com');
    await user.type(screen.getByLabelText('To'), 'a@example.com, b@example.com');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(putBody()?.channels.email).toEqual({
      email: {
        host:      'smtp.example.com',
        port:      587,
        security:  'starttls',
        username:  '',
        password:  'geheim',
        from_addr: 'nas@example.com',
        to:        ['a@example.com', 'b@example.com'],
      }
    }));
  });

  it('explains invalid fields', async () => {
    const user = userEvent.setup();
    renderCard('email', { enabled: false, min_severity: 'warning' });
    await user.click(screen.getByRole('button', { name: 'Configure' }));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(screen.getByText('Enter a host name or IP address.')).toBeInTheDocument();
    expect(screen.getByText('Enter one mail address.')).toBeInTheDocument();
    expect(screen.getByText('Enter at least one valid mail address.')).toBeInTheDocument();
    expect(putBody()).toBeUndefined();
  });
});

describe('channels without settings', () => {
  it('has no configure button', () => {
    renderCard('host_native', { enabled: false, min_severity: 'warning' });
    expect(screen.queryByRole('button', { name: 'Configure' })).not.toBeInTheDocument();
  });
});
