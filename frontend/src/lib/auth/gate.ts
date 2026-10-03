import { randomBytes } from 'node:crypto';
import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import {
  AUTH_LOGIN_PATH,
  AUTH_LOGOUT_PATH,
  AUTH_SESSION_PATH,
  LOGIN_PATH,
  LOGIN_REQUIRED_CODE,
  MIN_SECRET_LENGTH,
  SECURE_SESSION_COOKIE,
  SESSION_COOKIE,
  type AuthSettings,
} from './config';
import { loginUrl, safeNextPath } from './next-path';
import { hashPassword, isValidHash, verifyPassword } from './password';
import {
  RevokedSessions,
  createSessionToken,
  deriveSessionKey,
  verifySessionToken,
  type Session,
} from './session';
import { LoginThrottle } from './throttle';
import { errorBody } from '@/lib/api-error';

// The login, run inside src/proxy.ts: session checks for every request it
// sees, and the UI server's own /auth/* endpoints. See config.ts for the
// design.

/** Paths reachable without a session (besides /login, /healthz and static files). */
const OAUTH_CALLBACK_PATH = '/api/wizard/oauth/callback';
/** A session's cookie is reissued (sliding expiry) once it is this old. */
const RENEW_AFTER_MS = 60 * 60 * 1000;
const MAX_LOGIN_BODY = 4096;
const MAX_PASSWORD_LENGTH = 1024;

type AuthState = {
  throttle:   LoginThrottle;
  revoked:    RevokedSessions;
  /** Per-start signing key for a plain OMNISYNC_UI_PASSWORD without a secret. */
  randomKey:  Buffer;
  /** OMNISYNC_UI_PASSWORD hashed once per start: [password, hash]. */
  plainHash?: [string, Promise<string>];
  warned:     Set<string>;
};

// Kept on globalThis so it is one per process even if the module were
// evaluated more than once.
const STATE_KEY = Symbol.for('omnisync.ui-auth');

function state (): AuthState {
  const holder = globalThis as unknown as Record<symbol, AuthState | undefined>;
  holder[STATE_KEY] ??= {
    throttle:  new LoginThrottle(),
    revoked:   new RevokedSessions(),
    randomKey: randomBytes(32),
    warned:    new Set(),
  };
  return holder[STATE_KEY];
}

/** Forget sessions, throttling and warnings (tests). */
export function resetAuthState (): void {
  delete (globalThis as unknown as Record<symbol, unknown>)[STATE_KEY];
}

function warnOnce (key: string, log: () => void): void {
  const { warned } = state();
  if (warned.has(key)) return;
  warned.add(key);
  log();
}

/** Logged once per start for settings that weaken or break the login. */
function checkSettings (settings: AuthSettings): void {
  const rawSecret = process.env.OMNISYNC_UI_SESSION_SECRET ?? '';
  if (rawSecret && rawSecret.length < MIN_SECRET_LENGTH) {
    warnOnce('short-secret', () => console.error(
      `[auth] OMNISYNC_UI_SESSION_SECRET is shorter than ${MIN_SECRET_LENGTH} characters and is ignored; use \`openssl rand -hex 32\`.`
    ));
  }
  if (settings.mode === 'hash' && process.env.OMNISYNC_UI_PASSWORD) {
    warnOnce('both', () => console.warn('[auth] Both OMNISYNC_UI_PASSWORD_HASH and OMNISYNC_UI_PASSWORD are set; the hash is used.'));
  }
  if (settings.mode === 'hash' && !isValidHash(settings.passwordHash)) {
    warnOnce('bad-hash', () => console.error(
      '[auth] OMNISYNC_UI_PASSWORD_HASH is not a hash made by scripts/hash-password.mjs; every login is refused.'
    ));
  }
  if (settings.mode === 'plain') {
    warnOnce('plain', () => console.warn(
      '[auth] OMNISYNC_UI_PASSWORD holds the password in plain text; prefer OMNISYNC_UI_PASSWORD_HASH (node scripts/hash-password.mjs).' +
      (settings.sessionSecret ? '' : ' Without OMNISYNC_UI_SESSION_SECRET, restarting the UI signs everyone out.')
    ));
    if (settings.password.length < 12) {
      warnOnce('short-password', () => console.warn('[auth] OMNISYNC_UI_PASSWORD is shorter than 12 characters.'));
    }
  }
}

/**
 * The key session cookies are signed with: from OMNISYNC_UI_SESSION_SECRET
 * (bound to the password, so changing it ends all sessions), else from the
 * password hash (its random salt and key make it a strong secret), else
 * random per start.
 */
export function sessionKey (settings: AuthSettings): Buffer {
  const credential = settings.mode === 'hash' ? settings.passwordHash : settings.password;
  if (settings.sessionSecret) return deriveSessionKey(settings.sessionSecret, `${settings.mode}|${credential}`);
  if (settings.mode === 'hash') return deriveSessionKey(settings.passwordHash, 'hash');
  return state().randomKey;
}

function hostOf (request: NextRequest): string {
  return request.headers.get('host') ?? request.nextUrl.host;
}

/**
 * Whether the browser reached this UI over HTTPS, directly or through a
 * TLS-terminating proxy (X-Forwarded-Proto, believed for allow-listed hosts
 * only). A wrong "yes" only makes the browser drop the cookie.
 */
export function isSecureRequest (request: NextRequest, isAllowedHost: (host: string) => boolean): boolean {
  if (request.nextUrl.protocol === 'https:') return true;
  const proto = request.headers.get('x-forwarded-proto')?.split(',')[0].trim().toLowerCase();
  return proto === 'https' && isAllowedHost(hostOf(request));
}

/**
 * The client address for throttling and logs: the last X-Forwarded-For
 * entry (the one a reverse proxy appends), else 'unknown'. Without a
 * reverse proxy the browser can set it, which the global throttle covers.
 */
export function clientAddress (request: NextRequest): string {
  const entries = request.headers.get('x-forwarded-for')?.split(',') ?? [];
  const last = entries[entries.length - 1]?.trim() ?? '';
  // Only characters of IPv4/IPv6 addresses: the value goes into the log.
  return /^[0-9A-Fa-f.:[\]%a-z]{1,64}$/.test(last) ? last : 'unknown';
}

/** The valid session the request carries, if any. */
export function readSession (request: NextRequest, settings: AuthSettings): Session | null {
  const key = sessionKey(settings);
  for (const name of [SECURE_SESSION_COOKIE, SESSION_COOKIE]) {
    const session = verifySessionToken(key, request.cookies.get(name)?.value, { lifetimeMs: settings.sessionMs });
    if (session && !state().revoked.isRevoked(session.id)) return session;
  }
  return null;
}

/** Set the session cookie for `token` (and drop the other name's cookie). */
function setSessionCookie (response: NextResponse, token: string, settings: AuthSettings, secure: boolean): void {
  const name = secure ? SECURE_SESSION_COOKIE : SESSION_COOKIE;
  response.cookies.set(name, token, {
    httpOnly: true,
    secure,
    sameSite: 'strict',
    path:     '/',
    maxAge:   Math.floor(settings.sessionMs / 1000),
  });
  const other = secure ? SESSION_COOKIE : SECURE_SESSION_COOKIE;
  response.cookies.set(other, '', { httpOnly: true, secure: other === SECURE_SESSION_COOKIE, sameSite: 'strict', path: '/', maxAge: 0 });
}

function clearSessionCookies (response: NextResponse): void {
  for (const name of [SECURE_SESSION_COOKIE, SESSION_COOKIE]) {
    response.cookies.set(name, '', { httpOnly: true, secure: name === SECURE_SESSION_COOKIE, sameSite: 'strict', path: '/', maxAge: 0 });
  }
}

/** Whether `pathname` (with `method`) is reachable without a session. */
export function isExemptPath (pathname: string, method: string): boolean {
  if (pathname.startsWith('/_next/') || pathname.startsWith('/__nextjs')) return true;
  // The OAuth provider redirects the browser here from its own site, so a
  // SameSite=Strict cookie is not sent. The backend accepts only the
  // unguessable state of a wizard session a logged-in user just opened.
  return pathname === OAUTH_CALLBACK_PATH && (method === 'GET' || method === 'HEAD');
}

/**
 * A redirect to a path on this site. Next.js wants an absolute URL here
 * and sends it relative (it strips its own origin), so the browser stays
 * on the host and scheme it used, also behind a reverse proxy.
 */
function redirectTo (request: NextRequest, path: string): NextResponse {
  const response = NextResponse.redirect(new URL(path, request.url), 307);
  response.headers.set('cache-control', 'no-store');
  return response;
}

function loginRequired (): NextResponse {
  return NextResponse.json(
    errorBody(LOGIN_REQUIRED_CODE, 'Log in to the OmniSync web UI first.'),
    { status: 401, headers: { 'cache-control': 'no-store' } }
  );
}

export type GateResult =
  /** Answer with this response; do not forward the request. */
  | { response: NextResponse }
  /** Continue; call `finish` on the response that is sent (reissues a sliding session cookie). */
  | { finish: (response: NextResponse) => NextResponse };

const passThrough = { finish: (response: NextResponse) => response };

/**
 * The login check for a page or /api request (not /auth/*): the request
 * continues, or is answered with a redirect to /login (pages) or 401 (/api).
 */
export function gate (request: NextRequest, settings: AuthSettings, isAllowedHost: (host: string) => boolean): GateResult {
  const { pathname, search } = request.nextUrl;
  if (settings.mode === 'off') {
    // Nothing to log in to.
    if (pathname === LOGIN_PATH) return { response: redirectTo(request, '/') };
    return passThrough;
  }
  checkSettings(settings);
  if (isExemptPath(pathname, request.method)) return passThrough;

  const session = readSession(request, settings);
  if (pathname === LOGIN_PATH) {
    // Already logged in (the cookie came along, so this was a same-site
    // navigation): go where the login would have led.
    if (session && (request.method === 'GET' || request.method === 'HEAD')) {
      const next = safeNextPath(request.nextUrl.searchParams.get('next'));
      return { response: redirectTo(request, next) };
    }
    return passThrough;
  }
  if (!session) {
    const isApi = pathname === '/api' || pathname.startsWith('/api/');
    if (isApi || (request.method !== 'GET' && request.method !== 'HEAD')) return { response: loginRequired() };
    return { response: redirectTo(request, loginUrl(`${pathname}${search}`)) };
  }

  if (Date.now() - session.issuedAt * 1000 < Math.min(RENEW_AFTER_MS, settings.sessionMs / 2)) return passThrough;
  const secure = isSecureRequest(request, isAllowedHost);
  return {
    finish: (response) => {
      const { token } = createSessionToken(sessionKey(settings), { id: session.id, lifetimeMs: settings.sessionMs });
      setSessionCookie(response, token, settings, secure);
      return response;
    },
  };
}

async function passwordMatches (password: string, settings: AuthSettings): Promise<boolean> {
  if (settings.mode === 'hash') return verifyPassword(password, settings.passwordHash);
  const auth = state();
  if (!auth.plainHash || auth.plainHash[0] !== settings.password) {
    auth.plainHash = [settings.password, hashPassword(settings.password)];
  }
  return verifyPassword(password, await auth.plainHash[1]);
}

function throttled (waitMs: number): NextResponse {
  const seconds = Math.max(1, Math.ceil(waitMs / 1000));
  return NextResponse.json(
    errorBody('login_throttled', 'Too many attempts. Try again later.', { retry_after: seconds }),
    { status: 429, headers: { 'retry-after': String(seconds), 'cache-control': 'no-store' } }
  );
}

async function readPassword (request: NextRequest): Promise<string | null> {
  // JSON only (also when the body length is unknown): HTML forms cannot send it.
  const mediaType = request.headers.get('content-type')?.split(';')[0].trim().toLowerCase();
  if (mediaType !== 'application/json') return null;
  const text = await request.text().catch(() => '');
  if (!text || text.length > MAX_LOGIN_BODY) return null;
  try {
    const body: unknown = JSON.parse(text);
    const password = (body as { password?: unknown } | null)?.password;
    return typeof password === 'string' && password.length > 0 && password.length <= MAX_PASSWORD_LENGTH ? password : null;
  } catch {
    return null;
  }
}

async function login (request: NextRequest, settings: AuthSettings, isAllowedHost: (host: string) => boolean): Promise<NextResponse> {
  if (settings.mode === 'hash' && !isValidHash(settings.passwordHash)) {
    return NextResponse.json(
      errorBody('login_misconfigured', 'The login is not set up correctly; see the web UI server log.'),
      { status: 503 }
    );
  }
  const password = await readPassword(request);
  if (password === null) {
    return NextResponse.json(errorBody('invalid_login_request', 'Send {"password": "..."} as JSON.'), { status: 400 });
  }

  const { throttle } = state();
  const client = clientAddress(request);
  const wait = throttle.check(client);
  if (wait > 0) {
    console.warn(`[auth] Login attempt from ${client} refused: throttled for ${Math.ceil(wait / 1000)} s`);
    return throttled(wait);
  }

  let matches: boolean;
  try {
    matches = await passwordMatches(password, settings);
  } catch (error) {
    throttle.release();
    console.error('[auth] Password check failed:', error instanceof Error ? error.message : error);
    return NextResponse.json(errorBody('login_failed', 'Login failed.'), { status: 500 });
  }

  if (!matches) {
    const lockMs = throttle.failure(client);
    console.warn(`[auth] Failed login from ${client}`);
    return NextResponse.json(
      errorBody('invalid_password', 'Wrong password.', {
        retry_after: lockMs > 0 ? Math.ceil(lockMs / 1000) : undefined,
      }),
      { status: 401, headers: { 'cache-control': 'no-store' } }
    );
  }

  throttle.success(client);
  console.info(`[auth] Login from ${client}`);
  const { token } = createSessionToken(sessionKey(settings), { lifetimeMs: settings.sessionMs });
  const response = NextResponse.json({ ok: true }, { headers: { 'cache-control': 'no-store' } });
  setSessionCookie(response, token, settings, isSecureRequest(request, isAllowedHost));
  return response;
}

function logout (request: NextRequest, settings: AuthSettings): NextResponse {
  if (settings.mode !== 'off') {
    const session = readSession(request, settings);
    if (session) {
      // Renewed copies of this session's token may expire up to a full
      // lifetime from now.
      const auth = state();
      auth.revoked.prune();
      auth.revoked.revoke(session.id, Math.floor((Date.now() + settings.sessionMs) / 1000));
      console.info(`[auth] Logout from ${clientAddress(request)}`);
    }
  }
  const response = new NextResponse(null, { status: 204, headers: { 'cache-control': 'no-store' } });
  clearSessionCookies(response);
  return response;
}

/** Whether `pathname` is one of the UI server's own /auth/* endpoints. */
export function isAuthEndpoint (pathname: string): boolean {
  return pathname === AUTH_LOGIN_PATH || pathname === AUTH_LOGOUT_PATH || pathname === AUTH_SESSION_PATH;
}

/**
 * Answer /auth/login (POST {password}), /auth/logout (POST) and
 * /auth/session (GET). `refused` is the proxy's Host/Origin/JSON check,
 * which the login itself must pass too.
 */
export async function handleAuthEndpoint (
  request: NextRequest,
  settings: AuthSettings,
  refused: string | null,
  isAllowedHost: (host: string) => boolean
): Promise<NextResponse> {
  const { pathname } = request.nextUrl;
  if (refused) return NextResponse.json(errorBody('request_refused', refused), { status: 403 });
  if (settings.mode !== 'off') checkSettings(settings);

  if (pathname === AUTH_SESSION_PATH) {
    if (request.method !== 'GET' && request.method !== 'HEAD') return methodNotAllowed('GET');
    const enabled = settings.mode !== 'off';
    return NextResponse.json(
      { enabled, authenticated: !enabled || readSession(request, settings) !== null },
      { headers: { 'cache-control': 'no-store' } }
    );
  }
  if (request.method !== 'POST') return methodNotAllowed('POST');
  if (pathname === AUTH_LOGOUT_PATH) return logout(request, settings);
  if (settings.mode === 'off') {
    return NextResponse.json(
      errorBody('login_disabled', 'The web UI has no login (OMNISYNC_UI_PASSWORD_HASH is not set).'),
      { status: 404 }
    );
  }
  return login(request, settings, isAllowedHost);
}

function methodNotAllowed (allow: string): NextResponse {
  return NextResponse.json(errorBody('method_not_allowed', 'Method not allowed.'), { status: 405, headers: { allow } });
}
