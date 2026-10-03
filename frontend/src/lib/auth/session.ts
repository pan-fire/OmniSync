import { createHmac, hkdfSync, randomBytes, timingSafeEqual } from 'node:crypto';

// Session tokens: "v1.<id>.<issued>.<expires>.<signature>", times in
// seconds since the epoch, signature = HMAC-SHA256 over everything before
// it, base64url. The id identifies the login (it survives renewals) so
// logging out can revoke it; nothing in the token is secret.

const VERSION = 'v1';
const HKDF_SALT = 'omnisync-ui-session-v1';
/** Clock skew tolerated for a token issued "in the future". */
const SKEW_SECONDS = 60;

export type Session = { id: string; issuedAt: number; expiresAt: number };

/** A 32-byte signing key from secret material (HKDF-SHA256). */
export function deriveSessionKey (material: string | Buffer, context = ''): Buffer {
  return Buffer.from(hkdfSync('sha256', material, HKDF_SALT, `session|${context}`, 32));
}

function sign (key: Buffer, payload: string): string {
  return createHmac('sha256', key).update(payload).digest('base64url');
}

export function newSessionId (): string {
  return randomBytes(16).toString('base64url');
}

/** A signed token for session `id`, valid for `lifetimeMs` from `now`. */
export function createSessionToken (key: Buffer, { id = newSessionId(), now = Date.now(), lifetimeMs }: {
  id?:        string;
  now?:       number;
  lifetimeMs: number;
}): { token: string; session: Session } {
  const issuedAt = Math.floor(now / 1000);
  const expiresAt = issuedAt + Math.max(1, Math.floor(lifetimeMs / 1000));
  const payload = `${VERSION}.${id}.${issuedAt}.${expiresAt}`;
  return { token: `${payload}.${sign(key, payload)}`, session: { id, issuedAt, expiresAt } };
}

/**
 * The session in `token`, or null when it is malformed, not signed with
 * `key`, expired, or longer-lived than `lifetimeMs` allows (after the
 * lifetime setting was lowered).
 */
export function verifySessionToken (key: Buffer, token: string | undefined, { now = Date.now(), lifetimeMs }: {
  now?:       number;
  lifetimeMs: number;
}): Session | null {
  if (!token || token.length > 256) return null;
  const parts = token.split('.');
  if (parts.length !== 5 || parts[0] !== VERSION) return null;
  const [, id, issued, expires, signature] = parts;
  if (!/^[A-Za-z0-9_-]{16,64}$/.test(id) || !/^\d{1,12}$/.test(issued) || !/^\d{1,12}$/.test(expires)) return null;

  const expected = Buffer.from(sign(key, parts.slice(0, 4).join('.')));
  const given = Buffer.from(signature);
  if (given.length !== expected.length || !timingSafeEqual(given, expected)) return null;

  const issuedAt = Number(issued);
  const expiresAt = Number(expires);
  const nowSeconds = Math.floor(now / 1000);
  if (expiresAt <= nowSeconds || issuedAt > nowSeconds + SKEW_SECONDS) return null;
  if (expiresAt - issuedAt > Math.ceil(lifetimeMs / 1000)) return null;
  return { id, issuedAt, expiresAt };
}

/**
 * Logged-out session ids, kept until their last token would have expired.
 * In memory: a restart forgets them (see the README; change the secret or
 * the password to end every session).
 */
export class RevokedSessions {
  private readonly until = new Map<string, number>();

  revoke (id: string, untilSeconds: number): void {
    this.until.set(id, untilSeconds);
  }

  isRevoked (id: string, now = Date.now()): boolean {
    const until = this.until.get(id);
    if (until === undefined) return false;
    if (until * 1000 > now) return true;
    this.until.delete(id);
    return false;
  }

  /** Forget ids whose tokens have all expired. */
  prune (now = Date.now()): void {
    for (const [id, until] of this.until) {
      if (until * 1000 <= now) this.until.delete(id);
    }
  }

  get size (): number {
    return this.until.size;
  }
}
