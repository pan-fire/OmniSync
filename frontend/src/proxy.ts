import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { HEALTHZ_PATH, SECURE_SESSION_COOKIE, SESSION_COOKIE, authSettings } from '@/lib/auth/config';
import { clientAddress, gate, handleAuthEndpoint, isAuthEndpoint } from '@/lib/auth/gate';
import { errorBody } from '@/lib/api-error';
import { logServerEvent } from '@/lib/server-log';

// Forwards every /api request to the backend with the API token added:
// /api/profiles?x=1 -> ${BACKEND_URL}/profiles?x=1.
//
// Both settings are read from this server's environment on every request,
// so one built image works for any deployment (a next.config.ts rewrite
// would fix BACKEND_URL at build time). The token (OMNISYNC_API_TOKEN) never
// reaches the browser, and any Authorization header the browser sends is
// replaced. How long a proxied request may take is set by
// experimental.proxyTimeout in next.config.ts.
//
// Because the token is added for whoever reaches this server, requests are
// checked first (see guard): the Host must be allow-listed (against DNS
// rebinding), and state-changing requests must come from this UI's own
// origin and, when they carry a body, be JSON (against cross-site forms and
// fetches). The pages themselves contain no backend data (they load it
// through /api in the browser), so only /api is guarded.
//
// With the optional login (OMNISYNC_UI_PASSWORD_HASH, see src/lib/auth),
// this proxy also sees every page: without a valid session cookie pages
// redirect to /login and /api answers 401. It answers the UI server's own
// /auth/* endpoints and /healthz itself.
export const DEFAULT_BACKEND_URL = 'http://127.0.0.1:8000';

/** Host names the UI is always reachable under, on any port. */
const DEFAULT_ALLOWED_HOSTS = ['localhost', '127.0.0.1', '[::1]'];

/** Methods that must not change state; they skip the origin checks. */
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

/** The backend URL for an /api path (still percent-encoded) and query string. */
export function backendTarget (pathname: string, search: string): URL {
  const target = new URL(process.env.BACKEND_URL || DEFAULT_BACKEND_URL);
  const rest = pathname.replace(/^\/api(?=\/|$)/, '') || '/';
  target.pathname = target.pathname.replace(/\/+$/, '') + rest;
  target.search = search;
  return target;
}

/**
 * Split a Host header value (or an allow-list entry) into its lower-cased
 * host name (IPv6 literals keep their brackets) and port ('' when absent).
 * Returns null for anything that is not a plain host[:port].
 */
function parseHost (value: string): { hostname: string; port: string } | null {
  const match = /^(\[[0-9a-f:.]+\]|[a-z0-9_.-]+)(?::(\d{1,5}))?$/.exec(value.trim().toLowerCase());
  return match ? { hostname: match[1], port: match[2] ?? '' } : null;
}

/** Whether a Host header names this UI: loopback, or OMNISYNC_UI_ALLOWED_HOSTS. */
export function isAllowedHost (host: string): boolean {
  const parsed = parseHost(host);
  if (!parsed) return false;
  const extra = (process.env.OMNISYNC_UI_ALLOWED_HOSTS ?? '').split(',').filter((entry) => entry.trim());
  return [...DEFAULT_ALLOWED_HOSTS, ...extra].some((entry) => {
    const allowed = parseHost(entry);
    // An entry without a port allows every port.
    return !!allowed && allowed.hostname === parsed.hostname &&
      (!allowed.port || allowed.port === parsed.port);
  });
}

/** Whether `origin` (an Origin header) is the site `host` (a Host header) names. */
function isSameOrigin (origin: string, host: string): boolean {
  try {
    const source = new URL(origin);
    // Parse the Host with the Origin's scheme so default ports compare equal
    // (https://name and name:443). The scheme itself is not compared: behind
    // a TLS-terminating reverse proxy this server sees plain http.
    return (source.protocol === 'http:' || source.protocol === 'https:') &&
      source.host === new URL(`${source.protocol}//${host}`).host;
  } catch {
    return false;
  }
}

/** The refusals of guard(), with the reason the log gives for each. */
export const REFUSED_HOST = 'Host not allowed. Add it to OMNISYNC_UI_ALLOWED_HOSTS if this is intended.';
export const REFUSED_ORIGIN = 'Cross-origin request refused.';
export const REFUSED_CONTENT_TYPE = 'Request bodies must be sent as application/json.';
const REFUSAL_REASONS: Record<string, string> = {
  [REFUSED_HOST]:         'host',
  [REFUSED_ORIGIN]:       'origin',
  [REFUSED_CONTENT_TYPE]: 'content_type',
};

/** Why the request must not reach the backend, or null when it may. */
export function guard (request: NextRequest): string | null {
  // The Host header the browser sent. X-Forwarded-Host is not used here:
  // any client can set it.
  const host = request.headers.get('host') ?? request.nextUrl.host;
  if (!isAllowedHost(host)) {
    return REFUSED_HOST;
  }
  if (SAFE_METHODS.has(request.method)) return null;

  // Browsers send Origin with every other method; Sec-Fetch-Site vouches
  // for a request without one.
  const origin = request.headers.get('origin');
  if (origin !== null) {
    if (!isSameOrigin(origin, host)) return REFUSED_ORIGIN;
  } else {
    const site = request.headers.get('sec-fetch-site');
    if (site !== 'same-origin' && site !== 'none') return REFUSED_ORIGIN;
  }

  // HTML forms can only send urlencoded, multipart or text/plain bodies.
  const length = request.headers.get('content-length');
  const hasBody = request.headers.has('transfer-encoding') || (length !== null && length.trim() !== '0');
  const mediaType = request.headers.get('content-type')?.split(';')[0].trim().toLowerCase();
  if (hasBody && mediaType !== 'application/json') {
    return REFUSED_CONTENT_TYPE;
  }
  return null;
}

/** Log a refused request: the reason and where it came from, no query string, no cookies. */
function logRefusal (request: NextRequest, refused: string): void {
  logServerEvent('WARNING', 'proxy.refused', refused, {
    reason: REFUSAL_REASONS[refused] ?? 'other',
    method: request.method,
    path:   request.nextUrl.pathname,
    host:   request.headers.get('host') ?? undefined,
    origin: request.headers.get('origin') ?? undefined,
    client: clientAddress(request),
  });
}

/** Drop the UI's session cookies from a Cookie header bound for the backend. */
function removeSessionCookies (headers: Headers): void {
  const cookie = headers.get('cookie');
  if (cookie === null) return;
  const kept = cookie.split(/;\s*/).filter((pair) => {
    const name = pair.split('=')[0].trim();
    return name !== SESSION_COOKIE && name !== SECURE_SESSION_COOKIE;
  });
  if (kept.length > 0) headers.set('cookie', kept.join('; '));
  else headers.delete('cookie');
}

export async function proxy (request: NextRequest): Promise<NextResponse> {
  const { pathname, search } = request.nextUrl;
  // For the container health check: no session, no backend.
  if (pathname === HEALTHZ_PATH) {
    return new NextResponse('ok', { headers: { 'content-type': 'text/plain', 'cache-control': 'no-store' } });
  }
  const settings = authSettings();
  if (isAuthEndpoint(pathname)) {
    const refused = guard(request);
    if (refused) logRefusal(request, refused);
    return handleAuthEndpoint(request, settings, refused, isAllowedHost);
  }

  const isApi = pathname === '/api' || pathname.startsWith('/api/');
  if (isApi) {
    const refused = guard(request);
    if (refused) {
      logRefusal(request, refused);
      return NextResponse.json(errorBody('request_refused', refused), { status: 403 });
    }
  }
  const checked = gate(request, settings, isAllowedHost);
  if ('response' in checked) return checked.response;
  if (!isApi) return checked.finish(NextResponse.next());

  const token = process.env.OMNISYNC_API_TOKEN;
  const headers = new Headers(request.headers);
  headers.delete('authorization');
  // The session cookie is for this server only, not the backend.
  removeSessionCookies(headers);
  if (token) {
    headers.set('authorization', `Bearer ${token}`);
  }
  // Tell the backend where the browser came from: the rewrite sends the
  // request with the backend's own Host, and the OAuth redirect URI must
  // point at this UI (<proto>://<host>/api/wizard/oauth/callback). Values a
  // reverse proxy in front of this server already set are kept.
  if (!headers.has('x-forwarded-host')) {
    headers.set('x-forwarded-host', request.headers.get('host') ?? request.nextUrl.host);
  }
  if (!headers.has('x-forwarded-proto')) {
    headers.set('x-forwarded-proto', request.nextUrl.protocol.replace(/:$/, ''));
  }
  headers.set('x-forwarded-prefix', '/api');
  return checked.finish(NextResponse.rewrite(backendTarget(pathname, search), { request: { headers } }));
}

export const config = {
  // Everything but the build's static files and the public assets (the
  // service worker, the icon): /api always, pages and /auth for the login.
  matcher: '/((?!_next/static|_next/image|favicon.ico|sw.js).*)',
};
