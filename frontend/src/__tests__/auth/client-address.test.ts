import { describe, expect, it } from 'vitest';
import { NextRequest } from 'next/server';
import { clientAddress } from '@/lib/auth/gate';

function withForwardedFor (value?: string): NextRequest {
  const headers: Record<string, string> = value === undefined ? {} : { 'x-forwarded-for': value };
  return new NextRequest('http://127.0.0.1:3000/', { headers });
}

describe('clientAddress', () => {
  it('takes the last X-Forwarded-For entry (the one the proxy appended)', () => {
    expect(clientAddress(withForwardedFor('203.0.113.9, 192.0.2.7'))).toBe('192.0.2.7');
  });

  it.each([
    '192.0.2.7',
    '2001:DB8::1',
    '2001:db8::a:b:c:d',
    '[2001:db8::1]',
    'fe80::1%eth0',
  ])('keeps the address %s', (address) => {
    expect(clientAddress(withForwardedFor(address))).toBe(address);
  });

  it.each([
    'evil\tINFO forged log line',
    'ZONE:G',
    'host name',
    '<script>',
    'a'.repeat(65),
  ])('replaces %j, which is no address, with "unknown"', (value) => {
    expect(clientAddress(withForwardedFor(value))).toBe('unknown');
  });

  it('is "unknown" without the header', () => {
    expect(clientAddress(withForwardedFor())).toBe('unknown');
  });
});
