import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { LoginForm } from '@/components/auth/login-form';
import { I18nProvider, type Locale } from '@/i18n';

const originalLocation = window.location;
const assign = vi.fn();
const replace = vi.fn();

type Answer = { status: number; body?: unknown };

/** fetch answering /auth/session with `session` and /auth/login with the queued answers. */
function mockFetch (session: Answer, logins: Answer[] = []) {
  const fetchMock = vi.fn((url: string) => {
    const answer = url === '/auth/session' ? session : (logins.shift() ?? { status: 500 });
    return Promise.resolve(new Response(answer.body === undefined ? null : JSON.stringify(answer.body), {
      status:  answer.status,
      headers: { 'content-type': 'application/json' },
    }));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderForm (next?: string, locale: Locale = 'en') {
  return render(
    <I18nProvider initialLocale={locale}>
      <LoginForm next={next} />
    </I18nProvider>
  );
}

beforeEach(() => {
  assign.mockReset();
  replace.mockReset();
  Object.defineProperty(window, 'location', {
    configurable: true,
    value:        { ...originalLocation, assign, replace, pathname: '/login', search: '', hash: '' },
  });
});

afterEach(() => {
  Object.defineProperty(window, 'location', { configurable: true, value: originalLocation });
  vi.unstubAllGlobals();
});

const LOGGED_OUT = { status: 200, body: { enabled: true, authenticated: false } };

describe('LoginForm', () => {
  it('sets the page title like the other routes', () => {
    mockFetch(LOGGED_OUT);
    const { unmount } = renderForm();
    expect(document.title).toBe('Log in · OmniSync');
    unmount();
    expect(document.title).toBe('OmniSync');
    renderForm(undefined, 'de');
    expect(document.title).toBe('Anmelden · OmniSync');
  });

  it('is an accessible form with a labelled password field', async () => {
    mockFetch(LOGGED_OUT);
    renderForm();
    expect(screen.getByRole('heading', { level: 1, name: 'Log in to OmniSync' })).toBeInTheDocument();
    expect(screen.getByRole('form', { name: 'Log in to OmniSync' })).toBeInTheDocument();
    const field = screen.getByLabelText('Password');
    expect(field).toHaveAttribute('type', 'password');
    expect(field).toHaveAttribute('autocomplete', 'current-password');
    expect(field).toHaveFocus();
    expect(screen.getByRole('button', { name: 'Log in' })).toBeDisabled();
  });

  it('posts the password as JSON and goes to the page it came for', async () => {
    const fetchMock = mockFetch(LOGGED_OUT, [{ status: 200, body: { ok: true } }]);
    renderForm('/jobs?x=1');
    await userEvent.type(screen.getByLabelText('Password'), 'my secret password');
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith('/jobs?x=1'));
    const [, init] = fetchMock.mock.calls.find(([url]) => url === '/auth/login') as unknown as [string, globalThis.RequestInit];
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ password: 'my secret password' });
  });

  it('never follows a next that leaves the site', async () => {
    mockFetch(LOGGED_OUT, [{ status: 200, body: { ok: true } }]);
    renderForm('//evil.example/x');
    await userEvent.type(screen.getByLabelText('Password'), 'my secret password{Enter}');
    await waitFor(() => expect(assign).toHaveBeenCalledWith('/'));
  });

  it('says the password is wrong, clears it and marks the field invalid', async () => {
    mockFetch(LOGGED_OUT, [{ status: 401, body: { detail: 'Wrong password.', code: 'invalid_password' } }]);
    renderForm();
    const field = screen.getByLabelText('Password');
    await userEvent.type(field, 'guess{Enter}');
    expect(await screen.findByRole('alert')).toHaveTextContent('Wrong password. Please try again.');
    expect(field).toHaveValue('');
    expect(field).toHaveAttribute('aria-invalid', 'true');
    expect(field).toHaveAccessibleDescription('Wrong password. Please try again.');
    expect(assign).not.toHaveBeenCalled();
  });

  it('shows how long to wait when throttled and keeps the button disabled', async () => {
    mockFetch(LOGGED_OUT, [{ status: 429, body: { detail: 'Too many attempts. Try again later.', code: 'login_throttled', details: { retry_after: 30 } } }]);
    renderForm();
    await userEvent.type(screen.getByLabelText('Password'), 'guess{Enter}');
    expect(await screen.findByRole('alert')).toHaveTextContent(/Too many failed attempts\. Try again in (30|29) seconds\./);
    expect(screen.getByRole('button', { name: 'Log in' })).toBeDisabled();
  });

  it('explains a misconfigured login', async () => {
    mockFetch(LOGGED_OUT, [{ status: 503, body: { detail: 'The login is not set up correctly.', code: 'login_misconfigured' } }]);
    renderForm();
    await userEvent.type(screen.getByLabelText('Password'), 'anything{Enter}');
    expect(await screen.findByRole('alert')).toHaveTextContent('not set up correctly');
  });

  it('moves on when the browser is logged in already', async () => {
    mockFetch({ status: 200, body: { enabled: true, authenticated: true } });
    renderForm('/remotes');
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/remotes'));
  });

  it('is translated and right-to-left in Persian', async () => {
    mockFetch(LOGGED_OUT);
    renderForm(undefined, 'fa');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('ورود به OmniSync');
    expect(screen.getByLabelText('گذرواژه')).toBeInTheDocument();
    await waitFor(() => expect(document.documentElement).toHaveAttribute('dir', 'rtl'));
  });
});
