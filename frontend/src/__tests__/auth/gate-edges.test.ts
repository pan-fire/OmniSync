import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { handleAuthEndpoint, isSecureRequest, resetAuthState } from '@/lib/auth/gate';
import { verifyPassword } from '@/lib/auth/password';
import type { AuthSettings } from '@/lib/auth/config';
import { resetServerLog } from '@/lib/server-log';

// verifyPassword is the real one unless a test makes it throw.
vi.mock('@/lib/auth/password', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/auth/password')>();
  return { ...real, verifyPassword: vi.fn(real.verifyPassword) };
});

const ORIGIN = 'http://127.0.0.1:3000';
const allowAll = () => true;

const PLAIN: AuthSettings = {
  mode:          'plain',
  passwordHash:  '',
  password:      'short',
  sessionSecret: '',
  sessionMs:     7 * 24 * 60 * 60 * 1000,
};

/** The structured lines the server logged (src/lib/server-log.ts). */
function logged (): Array<{ level: string; msg: string; fields: Record<string, unknown> }> {
  return vi.mocked(console.log).mock.calls.map(([line]) => JSON.parse(String(line)));
}

function loginRequest (body: string): NextRequest {
  return new NextRequest(`${ORIGIN}/auth/login`, {
    method:  'POST',
    headers: { host: '127.0.0.1:3000', origin: ORIGIN, 'content-type': 'application/json' },
    body,
  });
}

beforeEach(() => {
  resetAuthState();
  resetServerLog();
  vi.stubEnv('OMNISYNC_UI_SESSION_SECRET', '');
  vi.stubEnv('OMNISYNC_UI_PASSWORD', '');
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe('login settings warnings', () => {
  it('warns once each about a short secret, a short plain password and both password settings', async () => {
    vi.stubEnv('OMNISYNC_UI_SESSION_SECRET', 'too-short');
    const session = () => new NextRequest(`${ORIGIN}/auth/session`, { headers: { host: '127.0.0.1:3000' } });
    await handleAuthEndpoint(session(), PLAIN, null, allowAll);
    await handleAuthEndpoint(session(), PLAIN, null, allowAll);

    const msgs = logged().map((l) => l.msg);
    expect(msgs.filter((m) => m.includes('OMNISYNC_UI_SESSION_SECRET is shorter'))).toHaveLength(1);
    expect(msgs.filter((m) => m.includes('OMNISYNC_UI_PASSWORD is shorter than 12'))).toHaveLength(1);

    vi.stubEnv('OMNISYNC_UI_PASSWORD', 'also-set');
    await handleAuthEndpoint(session(), { ...PLAIN, mode: 'hash', passwordHash: 'not-a-hash' }, null, allowAll);
    expect(logged().some((l) => l.msg.includes('Both OMNISYNC_UI_PASSWORD_HASH and OMNISYNC_UI_PASSWORD are set'))).toBe(true);
  });

  it('refuses other methods on the session endpoint', async () => {
    const res = await handleAuthEndpoint(
      new NextRequest(`${ORIGIN}/auth/session`, { method: 'POST', headers: { host: '127.0.0.1:3000' } }),
      PLAIN, null, allowAll
    );
    expect(res.status).toBe(405);
    expect(res.headers.get('allow')).toBe('GET');
  });

  it('answers HEAD on the session endpoint', async () => {
    const res = await handleAuthEndpoint(
      new NextRequest(`${ORIGIN}/auth/session`, { method: 'HEAD', headers: { host: '127.0.0.1:3000' } }),
      PLAIN, null, allowAll
    );
    expect(res.status).toBe(200);
  });
});

describe('login edge cases', () => {
  it('refuses an empty or oversized body as a bad request', async () => {
    expect((await handleAuthEndpoint(loginRequest(''), PLAIN, null, allowAll)).status).toBe(400);
    const big = JSON.stringify({ password: 'x'.repeat(5000) });
    expect((await handleAuthEndpoint(loginRequest(big), PLAIN, null, allowAll)).status).toBe(400);
    expect((await handleAuthEndpoint(loginRequest('null'), PLAIN, null, allowAll)).status).toBe(400);
    expect((await handleAuthEndpoint(loginRequest('{not json'), PLAIN, null, allowAll)).status).toBe(400);
  });

  it('a body that cannot be read is a bad request', async () => {
    const request = loginRequest('{}');
    vi.spyOn(request, 'text').mockRejectedValue(new Error('aborted'));
    expect((await handleAuthEndpoint(request, PLAIN, null, allowAll)).status).toBe(400);
  });

  // A crashing check answers 500 and logs the error kind, never its text.
  it('answers 500 when the password check throws, and the attempt is not counted', async () => {
    vi.mocked(verifyPassword).mockRejectedValueOnce(new TypeError('secret detail'));
    const res = await handleAuthEndpoint(loginRequest(JSON.stringify({ password: 'short' })), PLAIN, null, allowAll);
    expect(res.status).toBe(500);
    expect(await res.json()).toMatchObject({ code: 'login_failed' });
    const line = logged().find((l) => l.fields.event === 'auth.login_error');
    expect(line?.fields.error).toBe('TypeError');
    expect(JSON.stringify(logged())).not.toContain('secret detail');

    // The next attempt is not throttled and succeeds.
    const ok = await handleAuthEndpoint(loginRequest(JSON.stringify({ password: 'short' })), PLAIN, null, allowAll);
    expect(ok.status).toBe(200);
  });

  it('logs the kind of a non-Error thrown by the check', async () => {
    vi.mocked(verifyPassword).mockRejectedValueOnce('boom');
    const res = await handleAuthEndpoint(loginRequest(JSON.stringify({ password: 'short' })), PLAIN, null, allowAll);
    expect(res.status).toBe(500);
    expect(logged().find((l) => l.fields.event === 'auth.login_error')?.fields.error).toBe('string');
  });
});

describe('isSecureRequest', () => {
  it('an https URL is secure whatever the headers say', () => {
    expect(isSecureRequest(new NextRequest('https://ui.example.com/'), () => false)).toBe(true);
  });

  it('without a Host header, checks the URL host against the allow-list', () => {
    const request = new NextRequest('http://ui.example.com/', { headers: { 'x-forwarded-proto': 'https' } });
    request.headers.delete('host');
    const seen: string[] = [];
    expect(isSecureRequest(request, (host) => { seen.push(host); return true; })).toBe(true);
    expect(seen).toEqual(['ui.example.com']);
  });
});
