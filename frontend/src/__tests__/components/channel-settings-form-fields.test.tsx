import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider } from '@/i18n';
import { ChannelSettingsForm } from '@/components/notifications/channel-settings-form';
import type { ChannelConfig } from '@/types';
import { stubBackend } from '../helpers/fake-backend';

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

beforeAll(() => {
  // Radix Select uses pointer capture and scrollIntoView, which jsdom lacks.
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.releasePointerCapture ??= () => {};
  Element.prototype.scrollIntoView ??= () => {};
});

let sent: unknown[];

beforeEach(() => {
  sent = [];
  stubBackend({
    'PUT /notifications/config': (_url: URL, init?: globalThis.RequestInit) => {
      sent.push(JSON.parse(String(init?.body)));
      return { channels: {} };
    },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderForm (channelName: string, config: Partial<ChannelConfig> = {}, locale: 'en' | 'fa' = 'en') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <I18nProvider initialLocale={locale}>
        <ChannelSettingsForm channelName={channelName} config={{ enabled: false, min_severity: 'warning', ...config }} />
      </I18nProvider>
    </QueryClientProvider>
  );
}

/** The settings of `channel` in the last PUT. */
const lastSent = (channel: string) => (sent.at(-1) as { channels: Record<string, unknown> } | undefined)?.channels[channel];

describe('webhook headers', () => {
  it('adds, fills and removes header rows', async () => {
    const user = userEvent.setup();
    renderForm('webhook');
    await user.type(screen.getByLabelText('URL'), 'https://hooks.example.com/x');

    await user.click(screen.getByRole('button', { name: 'Add header' }));
    await user.click(screen.getByRole('button', { name: 'Add header' }));
    const names = screen.getAllByLabelText('Header name');
    const values = screen.getAllByLabelText('Header value (stored as a secret)');
    await user.type(names[0], 'X-Token');
    await user.type(values[0], 'abc');
    await user.type(names[1], 'X-Drop');

    // The second row has no value: refused before anything is sent.
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(screen.getByText('Enter a value for this header.')).toBeInTheDocument();
    expect(sent).toHaveLength(0);

    await user.click(screen.getAllByRole('button', { name: 'Remove header' })[1]);
    expect(screen.getAllByLabelText('Header name')).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(lastSent('webhook')).toEqual({
      webhook: { url: 'https://hooks.example.com/x', allow_http: false, headers: [{ name: 'X-Token', value: 'abc' }] },
    }));
  });

  it('offers no more than ten headers', async () => {
    const user = userEvent.setup();
    renderForm('webhook');
    for (let i = 0; i < 10; i += 1) {
      await user.click(screen.getByRole('button', { name: 'Add header' }));
    }
    expect(screen.getAllByLabelText('Header name')).toHaveLength(10);
    expect(screen.queryByRole('button', { name: 'Add header' })).not.toBeInTheDocument();
  });

  it('a refusal names the status, a failure without a message the generic save error', async () => {
    vi.unstubAllGlobals();
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response('{}', { status: 500 }))
      .mockRejectedValueOnce(new Error('')));
    const user = userEvent.setup();
    renderForm('webhook', { webhook: { url: 'https://hooks.example.com/x', allow_http: false, headers: [] } });
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Request failed (500)');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Failed to save notification settings'));
  });

  it('is labelled in Persian', () => {
    renderForm('webhook', {}, 'fa');
    expect(screen.queryByRole('button', { name: 'Add header' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'افزودن سرآیند' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'ذخیره' })).toBeInTheDocument();
  });
});

describe('ntfy settings', () => {
  it('starts at ntfy.sh and sends a new token, user name and password', async () => {
    const user = userEvent.setup();
    renderForm('ntfy');
    expect(screen.getByLabelText('Server')).toHaveValue('https://ntfy.sh');
    await user.type(screen.getByLabelText('Topic'), 'nas-alerts');
    await user.type(screen.getByLabelText('Access token'), 'tk_1');
    await user.type(screen.getByLabelText('Username'), ' anna ');
    await user.type(screen.getByLabelText('Password'), 'pw');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(lastSent('ntfy')).toEqual({
      ntfy: {
        server: 'https://ntfy.sh', topic: 'nas-alerts', allow_http: false, username: 'anna', token: 'tk_1', password: 'pw',
      },
    }));
  });

  // Typing a new secret after "Remove" replaces it; it must not also be cleared.
  it('removes a stored password unless a new one is typed', async () => {
    const user = userEvent.setup();
    renderForm('ntfy', {
      ntfy: { server: 'http://192.168.1.4', topic: 'nas', allow_http: true, username: 'u', token_set: true, password_set: true },
    });
    const removes = screen.getAllByRole('button', { name: 'Remove' });
    await user.click(removes[0]);
    await user.click(screen.getByRole('button', { name: 'Remove' }));
    expect(screen.queryByRole('button', { name: 'Remove' })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText('Access token'), 'new');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(lastSent('ntfy')).toEqual({
      ntfy: {
        server: 'http://192.168.1.4', topic: 'nas', allow_http: true, username: 'u', token: 'new', clear: ['password'],
      },
    }));
  });

  it('explains a bad topic', async () => {
    const user = userEvent.setup();
    renderForm('ntfy');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(screen.getByText('Use 1–64 letters, digits, - or _.')).toBeInTheDocument();
    expect(sent).toHaveLength(0);
  });
});

describe('email settings', () => {
  const email: ChannelConfig['email'] = {
    host: 'smtp.example.com', port: 587, security: 'starttls', username: 'nas', password_set: true, from_addr: 'nas@example.com', to: ['a@example.com'],
  };

  it('follows the usual port of the chosen encryption and warns about none', async () => {
    const user = userEvent.setup();
    renderForm('email', { email });

    await user.click(screen.getByRole('combobox', { name: 'Encryption' }));
    await user.click(await screen.findByRole('option', { name: 'TLS (port 465)' }));
    expect(screen.getByLabelText('Port')).toHaveValue('465');

    await user.click(screen.getByRole('combobox', { name: 'Encryption' }));
    await user.click(await screen.findByRole('option', { name: 'None (port 25)' }));
    expect(screen.getByLabelText('Port')).toHaveValue('25');
    expect(screen.getByText(/Without encryption/)).toBeInTheDocument();
  });

  it('keeps a port the user chose', async () => {
    const user = userEvent.setup();
    renderForm('email', { email });
    await user.clear(screen.getByLabelText('Port'));
    await user.type(screen.getByLabelText('Port'), '2525');
    await user.click(screen.getByRole('combobox', { name: 'Encryption' }));
    await user.click(await screen.findByRole('option', { name: 'TLS (port 465)' }));
    expect(screen.getByLabelText('Port')).toHaveValue('2525');
  });

  it('removes the stored password', async () => {
    const user = userEvent.setup();
    renderForm('email', { email });
    await user.type(screen.getByLabelText('Username'), '2');
    await user.click(screen.getByRole('button', { name: 'Remove' }));
    expect(screen.getByLabelText('Password')).toHaveAttribute('placeholder', 'optional');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(lastSent('email')).toEqual({
      email: {
        host:      'smtp.example.com',
        port:      587,
        security:  'starttls',
        username:  'nas2',
        from_addr: 'nas@example.com',
        to:        ['a@example.com'],
        clear:     ['password'],
      },
    }));
  });
});

describe('other channels', () => {
  it('have no settings form', () => {
    const { container } = renderForm('host_native');
    expect(container).toBeEmptyDOMElement();
  });
});
