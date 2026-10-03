import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { proxy } from '@/proxy';
import { hashPassword } from '@/lib/auth/password';
import { createSessionToken } from '@/lib/auth/session';
import { authSettings } from '@/lib/auth/config';
import { resetAuthState, sessionKey } from '@/lib/auth/gate';
import { FREE_FAILURES } from '@/lib/auth/throttle';

const PASSWORD = 'correct horse battery staple';
let HASH = '';

beforeAll(async () => {
  HASH = await hashPassword(PASSWORD, { logN: 10, r: 8, p: 1 });
});

beforeEach(() => {
  resetAuthState();
  vi.stubEnv('OMNISYNC_API_TOKEN', 'server-token');
  vi.stubEnv('BACKEND_URL', 'http://backend:8000');
  vi.stubEnv('OMNISYNC_UI_ALLOWED_HOSTS', '');
  vi.stubEnv('OMNISYNC_UI_PASSWORD_HASH', '');
  vi.stubEnv('OMNISYNC_UI_PASSWORD', '');
  vi.stubEnv('OMNISYNC_UI_SESSION_SECRET', '');
  vi.stubEnv('OMNISYNC_UI_SESSION_DAYS', '');
  vi.spyOn(console, 'info').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const ORIGIN = 'http://127.0.0.1:3000';

function req (path: string, init: { method?: string; headers?: Record<string, string>; body?: string } = {}): NextRequest {
  return new NextRequest(`${ORIGIN}${path}`, { ...init, headers: { host: '127.0.0.1:3000', ...init.headers } });
}

function loginRequest (password: string, headers: Record<string, string> = {}): NextRequest {
  return req('/auth/login', {
    method:  'POST',
    headers: { origin: ORIGIN, 'content-type': 'application/json', ...headers },
    body:    JSON.stringify({ password }),
  });
}

/** The name=value of the session cookie a response sets ('' when it clears it). */
function sessionCookie (res: Response): { name: string; value: string; header: string } | null {
  const header = res.headers.getSetCookie().find((c) => /^(__Host-)?omnisync-session=[^;]+/.test(c));
  if (!header) return null;
  const [pair] = header.split(';');
  const [name, value] = pair.split('=');
  return { name, value, header };
}

/** Path and query a redirect leads to, checking it stays on this site. */
function target (res: Response): string | null {
  const location = res.headers.get('location');
  if (location === null) return null;
  const url = new URL(location, ORIGIN);
  expect(url.port).toBe('3000');
  return `${url.pathname}${url.search}`;
}

async function logIn (): Promise<string> {
  vi.stubEnv('OMNISYNC_UI_PASSWORD_HASH', HASH);
  const res = await proxy(loginRequest(PASSWORD));
  expect(res.status).toBe(200);
  const cookie = sessionCookie(res);
  expect(cookie).not.toBeNull();
  return `${cookie!.name}=${cookie!.value}`;
}

describe('login off (default)', () => {
  it('forwards /api and serves pages as before', async () => {
    const api = await proxy(req('/api/profiles'));
    expect(api.headers.get('x-middleware-rewrite')).toBe('http://backend:8000/profiles');
    const page = await proxy(req('/profiles'));
    expect(page.headers.get('x-middleware-next')).toBe('1');
  });

  it('sends /login to the dashboard and refuses /auth/login', async () => {
    const page = await proxy(req('/login'));
    expect(page.status).toBe(307);
    expect(target(page)).toBe('/');
    expect((await proxy(loginRequest('x'))).status).toBe(404);
  });

  it('reports the login as off', async () => {
    const res = await proxy(req('/auth/session'));
    expect(await res.json()).toEqual({ enabled: false, authenticated: true });
  });
});

describe('login on', () => {
  beforeEach(() => { vi.stubEnv('OMNISYNC_UI_PASSWORD_HASH', HASH); });

  it('answers /api without a session with 401 and never forwards it', async () => {
    const res = await proxy(req('/api/profiles'));
    expect(res.status).toBe(401);
    expect(res.headers.get('x-middleware-rewrite')).toBeNull();
    expect(await res.json()).toEqual({ detail: expect.any(String), code: 'login_required' });
  });

  it('redirects pages to /login with the page to return to', async () => {
    const res = await proxy(req('/jobs/3?tab=files'));
    expect(res.status).toBe(307);
    expect(target(res)).toBe('/login?next=%2Fjobs%2F3%3Ftab%3Dfiles');
    const root = await proxy(req('/'));
    expect(target(root)).toBe('/login');
  });

  it('answers a non-GET page request with 401', async () => {
    const res = await proxy(req('/profiles', { method: 'POST', headers: { origin: ORIGIN } }));
    expect(res.status).toBe(401);
  });

  it.each([
    ['/login', 'GET'],
    ['/healthz', 'GET'],
    ['/api/wizard/oauth/callback?state=s&code=c', 'GET'],
    ['/_next/static/chunks/app.js', 'GET'],
  ])('lets %s through without a session', async (path, method) => {
    const res = await proxy(req(path, { method }));
    expect(res.status).toBe(200);
    expect(res.headers.get('location')).toBeNull();
  });

  it('forwards the OAuth callback to the backend, which checks its state', async () => {
    const res = await proxy(req('/api/wizard/oauth/callback?state=s&code=c'));
    expect(res.headers.get('x-middleware-rewrite')).toBe('http://backend:8000/wizard/oauth/callback?state=s&code=c');
  });

  it('does not exempt other methods on the callback path', async () => {
    const res = await proxy(req('/api/wizard/oauth/callback', { method: 'POST', headers: { origin: ORIGIN } }));
    expect(res.status).toBe(401);
  });

  it('logs in, forwards /api with the session, and logs out', async () => {
    const cookie = await logIn();

    const api = await proxy(req('/api/profiles', { headers: { cookie: `${cookie}; omnisync-locale=de` } }));
    expect(api.headers.get('x-middleware-rewrite')).toBe('http://backend:8000/profiles');
    expect(api.headers.get('x-middleware-request-authorization')).toBe('Bearer server-token');
    // The session cookie stays on this server.
    expect(api.headers.get('x-middleware-request-cookie')).toBe('omnisync-locale=de');

    const page = await proxy(req('/profiles', { headers: { cookie } }));
    expect(page.headers.get('x-middleware-next')).toBe('1');

    const session = await proxy(req('/auth/session', { headers: { cookie } }));
    expect(await session.json()).toEqual({ enabled: true, authenticated: true });

    const out = await proxy(req('/auth/logout', { method: 'POST', headers: { cookie, origin: ORIGIN } }));
    expect(out.status).toBe(204);
    expect(out.headers.getSetCookie().some((c) => c.startsWith('omnisync-session=;') && /Max-Age=0/i.test(c))).toBe(true);

    // The old cookie no longer works.
    expect((await proxy(req('/api/profiles', { headers: { cookie } }))).status).toBe(401);
  });

  it('sets an HttpOnly, SameSite=Strict cookie with the configured lifetime', async () => {
    vi.stubEnv('OMNISYNC_UI_SESSION_DAYS', '2');
    const res = await proxy(loginRequest(PASSWORD));
    const { name, header } = sessionCookie(res)!;
    expect(name).toBe('omnisync-session');
    expect(header).toMatch(/HttpOnly/i);
    expect(header).toMatch(/SameSite=Strict/i);
    expect(header).toMatch(/Path=\//);
    expect(header).toMatch(/Max-Age=172800/);
    expect(header).not.toMatch(/Secure/i);
  });

  it('uses a Secure __Host- cookie behind an HTTPS proxy on an allowed host', async () => {
    vi.stubEnv('OMNISYNC_UI_ALLOWED_HOSTS', 'sync.example.com');
    const res = await proxy(new NextRequest('http://sync.example.com/auth/login', {
      method:  'POST',
      headers: {
        host:                'sync.example.com',
        origin:              'https://sync.example.com',
        'x-forwarded-proto': 'https',
        'content-type':      'application/json',
      },
      body: JSON.stringify({ password: PASSWORD }),
    }));
    expect(res.status).toBe(200);
    const { name, header } = sessionCookie(res)!;
    expect(name).toBe('__Host-omnisync-session');
    expect(header).toMatch(/Secure/i);
  });

  it('refuses a wrong password with a generic message and logs no password', async () => {
    const res = await proxy(loginRequest('wrong password'));
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ detail: 'Wrong password.', code: 'invalid_password' });
    expect(sessionCookie(res)).toBeNull();
    const logged = vi.mocked(console.warn).mock.calls.flat().join(' ');
    expect(logged).toContain('Failed login');
    expect(logged).not.toContain('wrong password');
  });

  it('throttles repeated failures with 429 and Retry-After', async () => {
    for (let i = 0; i < FREE_FAILURES; i++) {
      expect((await proxy(loginRequest('nope', { 'x-forwarded-for': '192.0.2.7' }))).status).toBe(401);
    }
    const locked = await proxy(loginRequest('nope', { 'x-forwarded-for': '192.0.2.7' }));
    expect(locked.status).toBe(401);
    expect((await locked.json()).details.retry_after).toBe(1);
    // Even the right password waits.
    const res = await proxy(loginRequest(PASSWORD, { 'x-forwarded-for': '192.0.2.7' }));
    expect(res.status).toBe(429);
    expect(res.headers.get('retry-after')).toBe('1');
    expect(await res.json()).toMatchObject({ code: 'login_throttled', details: { retry_after: 1 } });
    // Another client is not affected.
    expect((await proxy(loginRequest(PASSWORD, { 'x-forwarded-for': '192.0.2.8' }))).status).toBe(200);
  });

  it('runs the Origin, Host and JSON checks on the login too', async () => {
    expect((await proxy(loginRequest(PASSWORD, { origin: 'https://evil.example' }))).status).toBe(403);
    expect((await proxy(new NextRequest('http://evil.example/auth/login', {
      method:  'POST',
      headers: { host: 'evil.example', origin: 'http://evil.example', 'content-type': 'application/json' },
      body:    JSON.stringify({ password: PASSWORD }),
    }))).status).toBe(403);
    expect((await proxy(req('/auth/login', {
      method:  'POST',
      headers: { origin: ORIGIN, 'content-type': 'application/x-www-form-urlencoded', 'content-length': '40' },
      body:    `password=${encodeURIComponent(PASSWORD)}`,
    }))).status).toBe(403);
  });

  it('refuses a bad login body and other methods', async () => {
    const empty = req('/auth/login', { method: 'POST', headers: { origin: ORIGIN, 'content-type': 'application/json' }, body: '{}' });
    expect((await proxy(empty)).status).toBe(400);
    expect((await proxy(req('/auth/login'))).status).toBe(405);
    // Not JSON, with no length the origin check could see: still no login.
    const form = req('/auth/login', { method: 'POST', headers: { origin: ORIGIN, 'content-type': 'text/plain' }, body: JSON.stringify({ password: PASSWORD }) });
    const res = await proxy(form);
    expect(res.status).toBe(400);
    expect(sessionCookie(res)).toBeNull();
  });

  it('refuses tampered, foreign and expired cookies', async () => {
    const cookie = await logIn();
    const tampered = `${cookie.slice(0, -2)}xx`;
    expect((await proxy(req('/api/profiles', { headers: { cookie: tampered } }))).status).toBe(401);

    const settings = authSettings();
    const { token } = createSessionToken(sessionKey(settings), { now: Date.now() - 8 * 24 * 3600 * 1000, lifetimeMs: settings.sessionMs });
    expect((await proxy(req('/api/profiles', { headers: { cookie: `omnisync-session=${token}` } }))).status).toBe(401);

    // A new password ends existing sessions.
    vi.stubEnv('OMNISYNC_UI_PASSWORD_HASH', await hashPassword('another password!', { logN: 10, r: 8, p: 1 }));
    expect((await proxy(req('/api/profiles', { headers: { cookie } }))).status).toBe(401);
  });

  it('renews an older session cookie (sliding expiry), keeping it valid', async () => {
    const settings = authSettings();
    const { token } = createSessionToken(sessionKey(settings), { now: Date.now() - 2 * 3600 * 1000, lifetimeMs: settings.sessionMs });
    const res = await proxy(req('/api/profiles', { headers: { cookie: `omnisync-session=${token}` } }));
    expect(res.headers.get('x-middleware-rewrite')).toBe('http://backend:8000/profiles');
    const renewed = sessionCookie(res);
    expect(renewed).not.toBeNull();
    expect(renewed!.value).not.toBe(token);
    expect(renewed!.value.split('.')[1]).toBe(token.split('.')[1]);

    // A fresh one is not reissued on every request.
    const cookie = await logIn();
    expect(sessionCookie(await proxy(req('/api/profiles', { headers: { cookie } })))).toBeNull();
  });

  it('sends a logged-in browser from /login to the page it came for', async () => {
    const cookie = await logIn();
    const res = await proxy(req('/login?next=%2Fjobs', { headers: { cookie } }));
    expect(res.status).toBe(307);
    expect(target(res)).toBe('/jobs');
    const evil = await proxy(req('/login?next=%2F%2Fevil.example', { headers: { cookie } }));
    expect(target(evil)).toBe('/');
  });

  it('fails closed on a malformed hash', async () => {
    vi.stubEnv('OMNISYNC_UI_PASSWORD_HASH', 'not-a-hash');
    expect((await proxy(req('/api/profiles'))).status).toBe(401);
    expect((await proxy(loginRequest(PASSWORD))).status).toBe(503);
    expect(vi.mocked(console.error)).toHaveBeenCalledWith(expect.stringContaining('OMNISYNC_UI_PASSWORD_HASH'));
  });
});

describe('plain OMNISYNC_UI_PASSWORD', () => {
  it('works, warns that a hash is better, and keeps sessions for this start', async () => {
    vi.stubEnv('OMNISYNC_UI_PASSWORD', PASSWORD);
    const res = await proxy(loginRequest(PASSWORD));
    expect(res.status).toBe(200);
    expect(vi.mocked(console.warn)).toHaveBeenCalledWith(expect.stringContaining('OMNISYNC_UI_PASSWORD_HASH'));
    const { name, value } = sessionCookie(res)!;
    expect((await proxy(req('/api/profiles', { headers: { cookie: `${name}=${value}` } }))).status).toBe(200);
    // A restart (new per-start key) ends it.
    resetAuthState();
    expect((await proxy(req('/api/profiles', { headers: { cookie: `${name}=${value}` } }))).status).toBe(401);
  });

  it('keeps sessions across restarts with OMNISYNC_UI_SESSION_SECRET', async () => {
    vi.stubEnv('OMNISYNC_UI_PASSWORD', PASSWORD);
    vi.stubEnv('OMNISYNC_UI_SESSION_SECRET', 'x'.repeat(40));
    const { name, value } = sessionCookie(await proxy(loginRequest(PASSWORD)))!;
    resetAuthState();
    expect((await proxy(req('/api/profiles', { headers: { cookie: `${name}=${value}` } }))).status).toBe(200);
  });
});

describe('/healthz', () => {
  it('answers ok without a session or the backend', async () => {
    vi.stubEnv('OMNISYNC_UI_PASSWORD_HASH', HASH);
    const res = await proxy(req('/healthz'));
    expect(res.status).toBe(200);
    expect(await res.text()).toBe('ok');
  });
});
