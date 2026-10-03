import { randomBytes, scrypt, timingSafeEqual } from 'node:crypto';

// Password hashes for OMNISYNC_UI_PASSWORD_HASH:
//
//   scrypt:<log2 N>:<r>:<p>:<salt>:<key>      salt and key in base64url
//
// scrypt is in Node's crypto, so no native module is needed. The defaults
// (N=2^15, r=8, p=3: 32 MiB, roughly 0.1-0.3 s) follow OWASP's scrypt
// recommendations. The format avoids `$`, which docker compose would try to
// interpolate in .env and docker-compose.yml. scripts/hash-password.mjs
// writes the same format (a test checks the two agree).

export const HASH_PREFIX = 'scrypt';
export const DEFAULT_PARAMS = { logN: 15, r: 8, p: 3 } as const;
const SALT_BYTES = 16;
const KEY_BYTES = 32;

/** Bounds for hashes read from the environment, so a typo cannot exhaust memory. */
const LIMITS = { logN: [10, 20], r: [1, 32], p: [1, 16] } as const;

export type ScryptParams = { logN: number; r: number; p: number };

type ParsedHash = ScryptParams & { salt: Buffer; key: Buffer };

function deriveKey (password: string, salt: Buffer, { logN, r, p }: ScryptParams, length: number): Promise<Buffer> {
  const N = 2 ** logN;
  return new Promise((resolve, reject) => {
    // maxmem: the 128*N*r bytes scrypt needs, with headroom.
    scrypt(password.normalize('NFC'), salt, length, { N, r, p, maxmem: 256 * N * r }, (error, key) => {
      if (error) reject(error);
      else resolve(key);
    });
  });
}

function inRange (value: number, [min, max]: readonly [number, number]): boolean {
  return Number.isInteger(value) && value >= min && value <= max;
}

/** The parts of an encoded hash, or null when it is not one this module accepts. */
export function parseHash (encoded: string): ParsedHash | null {
  const parts = encoded.trim().split(':');
  if (parts.length !== 6 || parts[0] !== HASH_PREFIX) return null;
  const [logN, r, p] = parts.slice(1, 4).map((part) => (/^\d{1,3}$/.test(part) ? Number(part) : NaN));
  if (!inRange(logN, LIMITS.logN) || !inRange(r, LIMITS.r) || !inRange(p, LIMITS.p)) return null;
  if (!/^[A-Za-z0-9_-]+$/.test(parts[4]) || !/^[A-Za-z0-9_-]+$/.test(parts[5])) return null;
  const salt = Buffer.from(parts[4], 'base64url');
  const key = Buffer.from(parts[5], 'base64url');
  if (salt.length < SALT_BYTES || key.length < 16 || key.length > 64) return null;
  return { logN, r, p, salt, key };
}

export function isValidHash (encoded: string): boolean {
  return parseHash(encoded) !== null;
}

/** A new encoded hash of `password` with a random salt. */
export async function hashPassword (password: string, params: ScryptParams = DEFAULT_PARAMS): Promise<string> {
  const salt = randomBytes(SALT_BYTES);
  const key = await deriveKey(password, salt, params, KEY_BYTES);
  return [HASH_PREFIX, params.logN, params.r, params.p, salt.toString('base64url'), key.toString('base64url')].join(':');
}

/**
 * Whether `password` matches `encoded`. The comparison is constant-time; a
 * malformed hash never matches (it throws nothing, so a misconfiguration
 * fails closed).
 */
export async function verifyPassword (password: string, encoded: string): Promise<boolean> {
  const parsed = parseHash(encoded);
  if (!parsed) return false;
  const key = await deriveKey(password, parsed.salt, parsed, parsed.key.length);
  return timingSafeEqual(key, parsed.key);
}
