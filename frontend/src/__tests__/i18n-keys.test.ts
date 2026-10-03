import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import en from '@/i18n/locales/en.json';
import de from '@/i18n/locales/de.json';
import fa from '@/i18n/locales/fa.json';

// Every literal key passed to t() must exist in all three locales, either
// as a plain key or as a plural pair (key_one/key_other).

const SRC = path.resolve(__dirname, '..');

function sourceFiles (dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return name === '__tests__' ? [] : sourceFiles(full);
    return /\.(ts|tsx)$/.test(name) ? [full] : [];
  });
}

function has (locale: Record<string, unknown>, key: string): boolean {
  const lookup = (k: string) => k.split('.').reduce<unknown>(
    (node, part) => (node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined),
    locale
  );
  return typeof lookup(key) === 'string' || typeof lookup(`${key}_other`) === 'string';
}

const keys = new Set<string>();
for (const file of sourceFiles(SRC)) {
  const text = readFileSync(file, 'utf8');
  for (const m of text.matchAll(/\bt\(\s*'([a-zA-Z0-9_.]+)'/g)) keys.add(m[1]);
}

describe('i18n keys used in code', () => {
  it('finds keys to check', () => {
    expect(keys.size).toBeGreaterThan(100);
  });

  for (const [name, locale] of Object.entries({ en, de, fa })) {
    it(`all exist in ${name}.json`, () => {
      const missing = [...keys].filter((k) => !has(locale as Record<string, unknown>, k));
      expect(missing).toEqual([]);
    });
  }
});
