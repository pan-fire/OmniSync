import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { proxy } from '@/proxy';
import { FORWARDED_CLIENT_HEADER, signClientAddress } from '@/lib/forwarded-client';

/** The header the proxy hands on to the backend, or null. */
async function forwardedClient (headers: Record<string, string>): Promise<string | null> {
  const res = await proxy(new NextRequest('http://localhost:3000/api/profiles', {
    headers: { host: 'localhost:3000', ...headers },
  }));
  return res.headers.get(`x-middleware-request-${FORWARDED_CLIENT_HEADER}`);
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.useRealTimers();
});

describe('signClientAddress', () => {
  it('matches the backend (backend/tests/test_forwarded_client.py pins the same value)', () => {
    expect(signClientAddress('server-token', '203.0.113.9', 1_800_000_000_999)).toBe(
      'v1;203.0.113.9;1800000000;66345dc8777db656349cb6644c757e91d42aa0132ba66c143aa41cff06bed7d2'
    );
  });
});

describe('the proxy vouches for the browser address', () => {
  it('signs the address it saw, with the current time and the API token', async () => {
    vi.stubEnv('OMNISYNC_API_TOKEN', 'server-token');
    vi.useFakeTimers({ now: 1_800_000_000_000, toFake: ['Date'] });
    expect(await forwardedClient({ 'x-forwarded-for': '203.0.113.9' }))
      .toBe(signClientAddress('server-token', '203.0.113.9', 1_800_000_000_000));
  });

  it('replaces whatever the browser sent in the header', async () => {
    vi.stubEnv('OMNISYNC_API_TOKEN', 'server-token');
    const forged = 'v1;198.51.100.1;1800000000;' + '0'.repeat(64);
    const value = await forwardedClient({ 'x-forwarded-for': '203.0.113.9', [FORWARDED_CLIENT_HEADER]: forged });
    expect(value).not.toBe(forged);
    expect(value?.split(';')[1]).toBe('203.0.113.9');
  });

  it('drops the browser header and sends none without a token or a usable address', async () => {
    vi.stubEnv('OMNISYNC_API_TOKEN', '');
    const forged = { 'x-forwarded-for': '203.0.113.9', [FORWARDED_CLIENT_HEADER]: 'v1;1.2.3.4;1;00' };
    expect(await forwardedClient(forged)).toBeNull();
    vi.stubEnv('OMNISYNC_API_TOKEN', 'server-token');
    expect(await forwardedClient({ 'x-forwarded-for': 'not an address', [FORWARDED_CLIENT_HEADER]: 'v1;1.2.3.4;1;00' }))
      .toBeNull();
  });
});
