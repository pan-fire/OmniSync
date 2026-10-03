import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api';
import { isLoginRequired, resetLoginRedirect } from '@/lib/auth/client';
import { AppSidebar } from '@/components/layout/app-sidebar';
import { I18nProvider } from '@/i18n';

vi.mock('next/navigation', () => ({ usePathname: () => '/' }));
vi.mock('next-themes', () => ({ useTheme: () => ({ theme: 'system', setTheme: vi.fn() }) }));

const originalLocation = window.location;
const assign = vi.fn();

function answer (status: number, body: unknown) {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify(body), { status }))));
}

beforeEach(() => {
  assign.mockReset();
  resetLoginRedirect();
  Object.defineProperty(window, 'location', {
    configurable: true,
    value:        { ...originalLocation, assign, pathname: '/jobs/7', search: '?tab=files', hash: '' },
  });
});

afterEach(() => {
  Object.defineProperty(window, 'location', { configurable: true, value: originalLocation });
  vi.unstubAllGlobals();
});

describe('session expiry in apiFetch', () => {
  it('sends the browser to the login page, returning to the current page', async () => {
    answer(401, { detail: 'Log in first.', code: 'login_required' });
    await expect(apiFetch('/profiles')).rejects.toMatchObject({ status: 401 });
    expect(assign).toHaveBeenCalledExactlyOnceWith('/login?next=%2Fjobs%2F7%3Ftab%3Dfiles');
    // Several failing queries redirect once.
    await expect(apiFetch('/jobs')).rejects.toMatchObject({ status: 401 });
    expect(assign).toHaveBeenCalledOnce();
  });

  it("leaves the backend's own 401 (wrong API token) alone", async () => {
    answer(401, { detail: 'Invalid API token.', code: 'token_invalid' });
    await expect(apiFetch('/profiles')).rejects.toMatchObject({ status: 401 });
    expect(assign).not.toHaveBeenCalled();
  });

  it('recognises only the login answer', () => {
    expect(isLoginRequired(401, { detail: 'Log in first.', code: 'login_required' })).toBe(true);
    expect(isLoginRequired(403, { detail: 'Log in first.', code: 'login_required' })).toBe(false);
    expect(isLoginRequired(401, { detail: 'Invalid API token.', code: 'token_invalid' })).toBe(false);
    expect(isLoginRequired(401, null)).toBe(false);
  });
});

describe('logout button', () => {
  function renderSidebar (showLogout: boolean) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={queryClient}>
        <I18nProvider>
          <AppSidebar isCollapsed={false} onToggle={() => {}} showLogout={showLogout} />
        </I18nProvider>
      </QueryClientProvider>
    );
  }

  it('is absent while the login is off', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    renderSidebar(false);
    expect(screen.queryByRole('button', { name: 'Log out' })).not.toBeInTheDocument();
  });

  it('ends the session and goes to the login page', async () => {
    const fetchMock = vi.fn((url: string) => Promise.resolve(
      url === '/auth/logout' ? new Response(null, { status: 204 }) : new Response('{}', { status: 200 })
    ));
    vi.stubGlobal('fetch', fetchMock);
    renderSidebar(true);
    await userEvent.click(screen.getByRole('button', { name: 'Log out' }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith('/login'));
    expect(fetchMock).toHaveBeenCalledWith('/auth/logout', expect.objectContaining({ method: 'POST' }));
  });
});
