/**
 * The web UI's optional login: settings read from the server's environment.
 *
 * Design (see also "Exposing the web UI / HTTPS" in the README):
 *
 * - Off unless OMNISYNC_UI_PASSWORD_HASH (preferred) or OMNISYNC_UI_PASSWORD
 *   is set; off, the UI behaves exactly as before (loopback-only port).
 * - One password, no user names. The hash is scrypt from Node's own crypto
 *   (no native dependency), made with `node scripts/hash-password.mjs`,
 *   which reads the password from the terminal and never from argv.
 * - Sessions are stateless HMAC-signed cookies (HttpOnly, SameSite=Strict,
 *   Secure + __Host- prefix on HTTPS) that slide: each use within the
 *   lifetime extends them. They carry only an id and times, nothing secret,
 *   so they are signed, not encrypted. The signing key comes from
 *   OMNISYNC_UI_SESSION_SECRET when set, else from the password hash (high
 *   entropy, stable across restarts, and changing the password ends every
 *   session). Only with a plain OMNISYNC_UI_PASSWORD and no secret is the
 *   key random per start, so restarting the UI signs everyone out. The
 *   container is read-only, so no generated secret is written to disk.
 * - All of it runs in src/proxy.ts (one module in one process), which also
 *   holds the logout list and the login throttle in memory.
 *
 * Nothing here imports node:crypto, so the server layout can import it.
 */

export const SESSION_COOKIE = 'omnisync-session';
/** The cookie name on HTTPS: the prefix makes browsers insist on Secure, Path=/ and no Domain. */
export const SECURE_SESSION_COOKIE = `__Host-${SESSION_COOKIE}`;

export const LOGIN_PATH = '/login';
/** The UI server's own endpoints (not the backend's, which live under /api). */
export const AUTH_LOGIN_PATH = '/auth/login';
export const AUTH_LOGOUT_PATH = '/auth/logout';
export const AUTH_SESSION_PATH = '/auth/session';
/** Liveness for the container health check: answers 200 without a session. */
export const HEALTHZ_PATH = '/healthz';

/** The `code` of the 401 /api answers when the login is missing or expired. */
export const LOGIN_REQUIRED_CODE = 'login_required';

export const DEFAULT_SESSION_DAYS = 7;
const MAX_SESSION_DAYS = 365;
/** Shortest OMNISYNC_UI_SESSION_SECRET accepted (characters). */
export const MIN_SECRET_LENGTH = 32;

export type AuthMode = 'off' | 'hash' | 'plain';

export type AuthSettings = {
  mode:          AuthMode;
  /** OMNISYNC_UI_PASSWORD_HASH (mode 'hash'). */
  passwordHash:  string;
  /** OMNISYNC_UI_PASSWORD (mode 'plain'). */
  password:      string;
  /** OMNISYNC_UI_SESSION_SECRET, '' when unset or too short. */
  sessionSecret: string;
  sessionMs:     number;
};

/** Session lifetime in days from OMNISYNC_UI_SESSION_DAYS (fractions allowed). */
export function parseSessionDays (value: string | undefined): number {
  const days = Number(value);
  if (!value?.trim() || !Number.isFinite(days) || days <= 0) return DEFAULT_SESSION_DAYS;
  return Math.min(days, MAX_SESSION_DAYS);
}

/** The login settings, read from the environment on every call. */
export function authSettings (env: Record<string, string | undefined> = process.env): AuthSettings {
  const passwordHash = env.OMNISYNC_UI_PASSWORD_HASH?.trim() ?? '';
  const password = env.OMNISYNC_UI_PASSWORD ?? '';
  const secret = env.OMNISYNC_UI_SESSION_SECRET ?? '';
  let mode: AuthMode = 'off';
  if (passwordHash) mode = 'hash';
  else if (password) mode = 'plain';
  return {
    mode,
    passwordHash,
    password,
    sessionSecret: secret.length >= MIN_SECRET_LENGTH ? secret : '',
    sessionMs:     parseSessionDays(env.OMNISYNC_UI_SESSION_DAYS) * 24 * 60 * 60 * 1000,
  };
}

/** Whether the UI asks for a password (read by the layout for the logout button). */
export function isAuthEnabled (env: Record<string, string | undefined> = process.env): boolean {
  return authSettings(env).mode !== 'off';
}
