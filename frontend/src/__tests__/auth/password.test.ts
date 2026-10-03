import { describe, expect, it } from 'vitest';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { hashPassword, isValidHash, parseHash, verifyPassword } from '@/lib/auth/password';

// Cheap parameters keep the suite fast; the format is the same.
const FAST = { logN: 10, r: 8, p: 1 };
const SCRIPT = path.resolve(__dirname, '../../../scripts/hash-password.mjs');

describe('password hashing', () => {
  it('verifies the password it hashed, and nothing else', async () => {
    const hash = await hashPassword('correct horse battery', FAST);
    expect(hash).toMatch(/^scrypt:10:8:1:[\w-]{22}:[\w-]{43}$/);
    expect(await verifyPassword('correct horse battery', hash)).toBe(true);
    expect(await verifyPassword('correct horse batterY', hash)).toBe(false);
    expect(await verifyPassword('', hash)).toBe(false);
  });

  it('salts every hash', async () => {
    const [a, b] = await Promise.all([hashPassword('same password!', FAST), hashPassword('same password!', FAST)]);
    expect(a).not.toBe(b);
  });

  it('uses the strong defaults and contains no $ (compose would interpolate it)', async () => {
    const hash = await hashPassword('default parameters');
    expect(hash.startsWith('scrypt:15:8:3:')).toBe(true);
    expect(hash).not.toContain('$');
  });

  it('treats composed and decomposed Unicode alike', async () => {
    const hash = await hashPassword('Grüße aus Köln', FAST);
    expect(await verifyPassword('Grüße aus Köln', hash)).toBe(true);
  });

  it.each([
    '',
    'not a hash',
    '$scrypt$ln=15,r=8,p=1$abc$def',
    'scrypt:15:8:3:c2FsdA:a2V5', // salt too short
    'scrypt:9:8:1:AAAAAAAAAAAAAAAAAAAAAA:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', // N too small
    'scrypt:24:8:1:AAAAAAAAAAAAAAAAAAAAAA:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', // would need 2 GiB
    'scrypt:15:8:1:AAAA+AAAAAAAAAAAAAAAAA:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
    'bcrypt:15:8:1:AAAAAAAAAAAAAAAAAAAAAA:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
  ])('refuses the malformed hash %j (fails closed)', async (hash) => {
    expect(isValidHash(hash)).toBe(false);
    expect(await verifyPassword('anything', hash)).toBe(false);
  });

  it('accepts surrounding whitespace from an .env file', async () => {
    const hash = await hashPassword('trailing newline', FAST);
    expect(parseHash(`${hash}\n`)).not.toBeNull();
    expect(await verifyPassword('trailing newline', ` ${hash} `)).toBe(true);
  });
});

describe('scripts/hash-password.mjs', () => {
  it('reads the password from stdin and prints a hash the server accepts', async () => {
    const run = spawnSync(process.execPath, [SCRIPT], { input: 'a long enough password\n', encoding: 'utf8' });
    expect(run.status).toBe(0);
    const hash = run.stdout.trim();
    expect(hash.startsWith('scrypt:15:8:3:')).toBe(true);
    expect(run.stdout).not.toContain('a long enough password');
    expect(await verifyPassword('a long enough password', hash)).toBe(true);
  }, 30_000);

  it('refuses a short password', () => {
    const run = spawnSync(process.execPath, [SCRIPT], { input: 'short', encoding: 'utf8' });
    expect(run.status).toBe(1);
    expect(run.stdout).toBe('');
    expect(run.stderr).toContain('at least 12');
  });
});
