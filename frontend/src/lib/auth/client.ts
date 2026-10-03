import { AUTH_LOGOUT_PATH, LOGIN_PATH, LOGIN_REQUIRED_CODE } from './config';
import { loginUrl } from './next-path';
import { parseApiError } from '@/lib/api-error';

// Browser side of the optional login (the checks run in src/proxy.ts).

/** Whether an /api answer means the UI session is missing or has expired. */
export function isLoginRequired (status: number, body: unknown): boolean {
  return status === 401 && parseApiError(status, body).code === LOGIN_REQUIRED_CODE;
}

let redirecting = false;

/** Send the browser to the login page, which returns here afterwards. Once only. */
export function redirectToLogin (): void {
  if (redirecting || typeof window === 'undefined') return;
  if (window.location.pathname === LOGIN_PATH) return;
  redirecting = true;
  window.location.assign(loginUrl(`${window.location.pathname}${window.location.search}${window.location.hash}`));
}

/** For tests. */
export function resetLoginRedirect (): void {
  redirecting = false;
}

/** End the session on the server and go to the login page. */
export async function logout (): Promise<void> {
  try {
    await fetch(AUTH_LOGOUT_PATH, { method: 'POST', credentials: 'same-origin' });
  } finally {
    // A full load: nothing of the old session stays in memory.
    window.location.assign(LOGIN_PATH);
  }
}
