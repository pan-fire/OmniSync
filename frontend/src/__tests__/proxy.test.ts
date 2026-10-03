import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { backendTarget, isAllowedHost, proxy } from '@/proxy';

/** The request headers the proxy hands on to the rewrite. */
async function forwarded (request: NextRequest): Promise<Headers> {
  const response = await proxy(request);
  const out = new Headers();
  const names = response.headers.get('x-middleware-override-headers')?.split(',') ?? [];
  for (const name of names) {
    const value = response.headers.get(`x-middleware-request-${name}`);
    if (value !== null) out.set(name, value);
  }
  return out;
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe('backendTarget', () => {
  it('drops the /api prefix and keeps the query string', () => {
    vi.stubEnv('BACKEND_URL', 'http://backend:8000');
    expect(backendTarget('/api/profiles', '?limit=5').href)
      .toBe('http://backend:8000/profiles?limit=5');
  });

  it('defaults to the loopback backend', () => {
    vi.stubEnv('BACKEND_URL', '');
    expect(backendTarget('/api/health', '').href).toBe('http://127.0.0.1:8000/health');
  });

  it('keeps a base path and percent-encoded segments', () => {
    vi.stubEnv('BACKEND_URL', 'http://nas:9000/omnisync/');
    expect(backendTarget('/api/profiles/docs/manual-flags/a%20b/c.txt', '').href)
      .toBe('http://nas:9000/omnisync/profiles/docs/manual-flags/a%20b/c.txt');
  });

  it('maps /api itself to the backend root', () => {
    vi.stubEnv('BACKEND_URL', 'http://backend:8000');
    expect(backendTarget('/api', '').href).toBe('http://backend:8000/');
  });
});

describe('proxy', () => {
  it('rewrites to the backend read at request time and injects the token', async () => {
    vi.stubEnv('BACKEND_URL', 'http://backend:8000');
    vi.stubEnv('OMNISYNC_API_TOKEN', 'server-token');
    const res = await proxy(new NextRequest('http://127.0.0.1:3000/api/profiles?x=1', {
      headers: { authorization: 'Bearer from-the-browser' },
    }));
    expect(res.headers.get('x-middleware-rewrite')).toBe('http://backend:8000/profiles?x=1');
    expect(res.headers.get('x-middleware-request-authorization')).toBe('Bearer server-token');
  });

  it('drops a browser Authorization header when no token is configured', async () => {
    vi.stubEnv('OMNISYNC_API_TOKEN', '');
    const res = await proxy(new NextRequest('http://127.0.0.1:3000/api/profiles', {
      headers: { authorization: 'Bearer from-the-browser' },
    }));
    expect(res.headers.get('x-middleware-request-authorization')).toBeNull();
    expect(res.headers.get('x-middleware-override-headers')).not.toContain('authorization');
  });

  // X-Forwarded-* let the backend build the OAuth redirect to this UI
  describe('forwarded headers', () => {
    beforeEach(() => { vi.stubEnv('OMNISYNC_API_TOKEN', 'server-token'); });

    it('tells the backend where the browser came from, for the OAuth redirect', async () => {
      const headers = await forwarded(new NextRequest('http://localhost:3000/api/wizard/authorize', {
        method:  'POST',
        headers: { host: 'localhost:3000', origin: 'http://localhost:3000' },
      }));
      expect(headers.get('x-forwarded-host')).toBe('localhost:3000');
      expect(headers.get('x-forwarded-proto')).toBe('http');
      expect(headers.get('x-forwarded-prefix')).toBe('/api');
    });

    it('keeps what a reverse proxy in front of the UI already forwarded', async () => {
      vi.stubEnv('OMNISYNC_UI_ALLOWED_HOSTS', 'web');
      const headers = await forwarded(new NextRequest('http://web:3000/api/wizard/authorize', {
        method:  'POST',
        headers: {
          host:                'web:3000',
          origin:              'http://web:3000',
          'x-forwarded-host':  'sync.example.com',
          'x-forwarded-proto': 'https',
        },
      }));
      expect(headers.get('x-forwarded-host')).toBe('sync.example.com');
      expect(headers.get('x-forwarded-proto')).toBe('https');
    });
  });
});

type RequestOptions = { method?: string; headers?: Record<string, string>; body?: string };

/** A request to `url`, with the Host header taken from it unless given. */
function request (url: string, options: RequestOptions = {}): NextRequest {
  return new NextRequest(url, {
    ...options,
    headers: { host: new URL(url).host, ...options.headers },
  });
}

/** The proxy's answer: forwarded to the backend, or refused (status and detail). */
async function outcome (req: NextRequest): Promise<'forwarded' | { status: number; detail: string }> {
  const res = await proxy(req);
  if (res.headers.get('x-middleware-rewrite')) return 'forwarded';
  const body = await res.json() as { detail: string };
  return { status: res.status, detail: body.detail };
}

describe('isAllowedHost', () => {
  beforeEach(() => { vi.stubEnv('OMNISYNC_UI_ALLOWED_HOSTS', ''); });

  it.each([
    'localhost', 'localhost:3000', '127.0.0.1', '127.0.0.1:3000', '[::1]', '[::1]:3000', 'LOCALHOST:3000',
  ])('allows the loopback host %s on any port', (host) => {
    expect(isAllowedHost(host)).toBe(true);
  });

  it.each([
    'evil.example', 'evil.example:3000', 'localhost.evil.example', '127.0.0.1.evil.example',
    '192.168.1.10:3000', '', 'localhost:3000@evil.example', 'localhost:abc', '::1',
  ])('refuses %j by default', (host) => {
    expect(isAllowedHost(host)).toBe(false);
  });

  it('adds OMNISYNC_UI_ALLOWED_HOSTS, on any port unless the entry names one', () => {
    vi.stubEnv('OMNISYNC_UI_ALLOWED_HOSTS', ' Sync.Example.com , nas.lan:3000,,192.168.1.10');
    expect(isAllowedHost('sync.example.com')).toBe(true);
    expect(isAllowedHost('sync.example.com:8443')).toBe(true);
    expect(isAllowedHost('nas.lan:3000')).toBe(true);
    expect(isAllowedHost('nas.lan:3001')).toBe(false);
    expect(isAllowedHost('nas.lan')).toBe(false);
    expect(isAllowedHost('192.168.1.10:3000')).toBe(true);
    expect(isAllowedHost('other.example.com')).toBe(false);
  });

  it('treats * as a name, not a wildcard', () => {
    vi.stubEnv('OMNISYNC_UI_ALLOWED_HOSTS', '*');
    expect(isAllowedHost('evil.example')).toBe(false);
  });
});

describe('proxy guard', () => {
  beforeEach(() => {
    vi.stubEnv('OMNISYNC_API_TOKEN', 'server-token');
    vi.stubEnv('OMNISYNC_UI_ALLOWED_HOSTS', '');
  });

  describe('Host', () => {
    it('refuses a DNS-rebound host with 403 and never adds the token', async () => {
      const res = await proxy(request('http://evil.example:3000/api/profiles'));
      expect(res.status).toBe(403);
      expect(res.headers.get('x-middleware-rewrite')).toBeNull();
      expect(res.headers.get('x-middleware-request-authorization')).toBeNull();
      expect(await res.json()).toEqual({ detail: expect.stringContaining('OMNISYNC_UI_ALLOWED_HOSTS'), code: 'request_refused' });
    });

    it('checks the Host header, not X-Forwarded-Host', async () => {
      expect(await outcome(request('http://evil.example/api/profiles', {
        headers: { 'x-forwarded-host': 'localhost:3000' },
      }))).toMatchObject({ status: 403 });
    });

    it('forwards GETs for loopback and allow-listed hosts without an Origin', async () => {
      expect(await outcome(request('http://127.0.0.1:3000/api/profiles'))).toBe('forwarded');
      vi.stubEnv('OMNISYNC_UI_ALLOWED_HOSTS', 'sync.example.com');
      expect(await outcome(request('http://sync.example.com/api/profiles'))).toBe('forwarded');
    });
  });

  describe('Origin', () => {
    it.each(['POST', 'PUT', 'PATCH', 'DELETE'])('forwards a same-origin %s', async (method) => {
      expect(await outcome(request('http://localhost:3000/api/profiles/x', {
        method,
        headers: { origin: 'http://localhost:3000', 'content-type': 'application/json', 'content-length': '2' },
        body:    '{}',
      }))).toBe('forwarded');
    });

    it('refuses a body-less cross-site POST such as a form to /disable', async () => {
      expect(await outcome(request('http://localhost:3000/api/profiles/x/disable', {
        method:  'POST',
        headers: { origin: 'http://evil.example', 'content-length': '0' },
      }))).toEqual({ status: 403, detail: 'Cross-origin request refused.' });
    });

    it.each([
      ['another port', 'http://localhost:4000'],
      ['another loopback name', 'http://127.0.0.1:3000'],
      ['an opaque origin', 'null'],
      ['a non-web scheme', 'file://localhost:3000'],
      ['garbage', 'not a url'],
    ])('refuses an Origin on %s', async (_, origin) => {
      expect(await outcome(request('http://localhost:3000/api/sync/stop', {
        method:  'POST',
        headers: { origin },
      }))).toMatchObject({ status: 403 });
    });

    it('matches https origins behind a TLS-terminating reverse proxy', async () => {
      vi.stubEnv('OMNISYNC_UI_ALLOWED_HOSTS', 'sync.example.com');
      const stop = (headers: Record<string, string>) =>
        outcome(request('http://sync.example.com/api/sync/stop', { method: 'POST', headers }));
      expect(await stop({ origin: 'https://sync.example.com' })).toBe('forwarded');
      expect(await stop({ host: 'sync.example.com:443', origin: 'https://sync.example.com' })).toBe('forwarded');
      expect(await stop({ origin: 'https://sync.example.com:8443' })).toMatchObject({ status: 403 });
    });

    it.each(['same-origin', 'none'])('accepts a missing Origin with Sec-Fetch-Site: %s', async (site) => {
      expect(await outcome(request('http://localhost:3000/api/backups/1/run', {
        method:  'POST',
        headers: { 'sec-fetch-site': site },
      }))).toBe('forwarded');
    });

    it.each(['cross-site', 'same-site'])('refuses a missing Origin with Sec-Fetch-Site: %s', async (site) => {
      expect(await outcome(request('http://localhost:3000/api/backups/1/run', {
        method:  'POST',
        headers: { 'sec-fetch-site': site },
      }))).toMatchObject({ status: 403 });
    });

    it('refuses a state-changing request with neither Origin nor Sec-Fetch-Site', async () => {
      expect(await outcome(request('http://localhost:3000/api/backups/1/run', { method: 'POST' })))
        .toMatchObject({ status: 403 });
    });

    it.each(['GET', 'HEAD', 'OPTIONS'])('does not ask a %s for an Origin', async (method) => {
      expect(await outcome(request('http://localhost:3000/api/profiles', {
        method,
        headers: { origin: 'http://evil.example' },
      }))).toBe('forwarded');
    });
  });

  describe('Content-Type', () => {
    const sameOrigin = { origin: 'http://localhost:3000' };

    it.each([
      'text/plain',
      'application/x-www-form-urlencoded',
      'multipart/form-data; boundary=x',
      '',
    ])('refuses a body sent as %j', async (type) => {
      expect(await outcome(request('http://localhost:3000/api/profiles', {
        method:  'POST',
        headers: { ...sameOrigin, 'content-length': '2', ...(type && { 'content-type': type }) },
        body:    '{}',
      }))).toEqual({ status: 403, detail: 'Request bodies must be sent as application/json.' });
    });

    it('refuses a chunked body that is not JSON', async () => {
      expect(await outcome(request('http://localhost:3000/api/profiles', {
        method:  'POST',
        headers: { ...sameOrigin, 'transfer-encoding': 'chunked', 'content-type': 'text/plain' },
      }))).toMatchObject({ status: 403 });
    });

    it('accepts JSON with parameters, in any case', async () => {
      expect(await outcome(request('http://localhost:3000/api/profiles', {
        method:  'POST',
        headers: { ...sameOrigin, 'content-length': '2', 'content-type': 'Application/JSON; charset=utf-8' },
        body:    '{}',
      }))).toBe('forwarded');
    });

    it('does not ask a body-less request for a content type', async () => {
      expect(await outcome(request('http://localhost:3000/api/sync/stop', {
        method:  'POST',
        headers: { ...sameOrigin, 'content-length': '0' },
      }))).toBe('forwarded');
    });
  });
});
