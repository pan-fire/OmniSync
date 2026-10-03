import { describe, expect, it } from 'vitest';
import { RevokedSessions, createSessionToken, deriveSessionKey, verifySessionToken } from '@/lib/auth/session';

const DAY = 24 * 60 * 60 * 1000;
const key = deriveSessionKey('a secret of at least thirty-two characters');
const lifetimeMs = 7 * DAY;
const now = Date.UTC(2026, 9, 1);

describe('session tokens', () => {
  it('verifies a fresh token and returns its session', () => {
    const { token, session } = createSessionToken(key, { now, lifetimeMs });
    expect(token.split('.')).toHaveLength(5);
    expect(verifySessionToken(key, token, { now, lifetimeMs })).toEqual(session);
    expect(session.expiresAt - session.issuedAt).toBe(7 * 24 * 60 * 60);
  });

  it('keeps the id when renewed', () => {
    const first = createSessionToken(key, { now, lifetimeMs });
    const renewed = createSessionToken(key, { id: first.session.id, now: now + DAY, lifetimeMs });
    expect(verifySessionToken(key, renewed.token, { now: now + DAY, lifetimeMs })?.id).toBe(first.session.id);
  });

  it('expires', () => {
    const { token } = createSessionToken(key, { now, lifetimeMs });
    expect(verifySessionToken(key, token, { now: now + lifetimeMs - 1000, lifetimeMs })).not.toBeNull();
    expect(verifySessionToken(key, token, { now: now + lifetimeMs, lifetimeMs })).toBeNull();
  });

  it('refuses tokens longer-lived than the current setting allows', () => {
    const { token } = createSessionToken(key, { now, lifetimeMs: 30 * DAY });
    expect(verifySessionToken(key, token, { now, lifetimeMs })).toBeNull();
  });

  it('refuses tokens issued in the future', () => {
    const { token } = createSessionToken(key, { now: now + 10 * 60 * 1000, lifetimeMs });
    expect(verifySessionToken(key, token, { now, lifetimeMs })).toBeNull();
  });

  it('refuses a token signed with another key', () => {
    const { token } = createSessionToken(deriveSessionKey('another secret, also long enough!!'), { now, lifetimeMs });
    expect(verifySessionToken(key, token, { now, lifetimeMs })).toBeNull();
  });

  it('binds the key to its context', () => {
    expect(deriveSessionKey('s', 'a').equals(deriveSessionKey('s', 'b'))).toBe(false);
    expect(deriveSessionKey('s', 'a').equals(deriveSessionKey('s', 'a'))).toBe(true);
  });

  it('refuses every tampered part', () => {
    const { token } = createSessionToken(key, { now, lifetimeMs });
    const [version, id, issued, expires, signature] = token.split('.');
    const tampered = [
      [version, id, issued, String(Number(expires) + 3600), signature],
      [version, id, String(Number(issued) - 1), expires, signature],
      [version, `${id.slice(0, -1)}${id.endsWith('A') ? 'B' : 'A'}`, issued, expires, signature],
      [version, id, issued, expires, `${signature.slice(0, -1)}${signature.endsWith('A') ? 'B' : 'A'}`],
      ['v2', id, issued, expires, signature],
      [version, id, issued, expires],
      [version, id, issued, expires, ''],
    ].map((parts) => parts.join('.'));
    for (const value of tampered) {
      expect(verifySessionToken(key, value, { now, lifetimeMs }), value).toBeNull();
    }
  });

  it.each([undefined, '', 'garbage', 'a.b.c.d.e', 'x'.repeat(1000)])('refuses %j', (value) => {
    expect(verifySessionToken(key, value, { now, lifetimeMs })).toBeNull();
  });
});

describe('RevokedSessions', () => {
  it('revokes until the given time, then forgets', () => {
    const revoked = new RevokedSessions();
    revoked.revoke('abc', Math.floor(now / 1000) + 60);
    expect(revoked.isRevoked('abc', now)).toBe(true);
    expect(revoked.isRevoked('other', now)).toBe(false);
    revoked.prune(now + 61_000);
    expect(revoked.size).toBe(0);
    expect(revoked.isRevoked('abc', now + 61_000)).toBe(false);
  });
});
