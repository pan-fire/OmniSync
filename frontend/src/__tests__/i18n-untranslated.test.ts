import { describe, it, expect } from 'vitest';
import en from '@/i18n/locales/en.json';
import de from '@/i18n/locales/de.json';
import fa from '@/i18n/locales/fa.json';
import { translate } from '@/i18n';

// A de/fa value equal to the English one is almost always a string that
// was added in English and never translated. The few that legitimately
// stay the same (brand and OS names, units, loanwords German uses as is)
// are listed here, so a new untranslated string fails CI instead of
// reaching users.

type Allow = { keys: string[], patterns: RegExp[] };

// Same in every locale: product name, an em dash placeholder, OS names.
const SHARED: Allow = {
  keys:     ['nav.version', 'granular.na', 'notifications.channels.ntfy'],
  patterns: [/^notifications\.hostOs\.(linux|macos|windows|android)$/],
};

const ALLOW: Record<'de' | 'fa', Allow> = {
  de: {
    keys: [
      // English loanwords German UIs use unchanged.
      'nav.dashboard', 'dashboard.title', 'dashboard.statusTitle',
      'nav.remotes', 'remotes.title', 'health.remotesTitle', 'config.remotes',
      'jobs.sides.remote', 'profiles.remote', 'wizard.configStep.remotePathRemote',
      'remotes.backup',
      'nav.backendVersion',
      'jobs.id', 'jobs.status',
      'jobs.directions.push', 'jobs.directions.pull', 'granular.push', 'granular.pull',
      'logs.debug', 'logs.info', 'notifications.severity.debug', 'notifications.severity.info',
      'common.system', 'syncCheck.details', 'backups.form.name',
      'notifications.channels.webpush', 'notifications.hostDetected',
      'notifications.channels.webhook', 'notifications.form.optional',
      'notifications.form.url', 'notifications.form.server', 'notifications.form.port',
      // Units: B, KB, MB.
      'granular.bytes', 'granular.kb', 'granular.mb',
    ],
    patterns: [],
  },
  fa: { keys: [], patterns: [] },
};

function flatten (node: unknown, prefix = '', out: Record<string, string> = {}): Record<string, string> {
  if (typeof node === 'string') {
    out[prefix] = node;
  } else if (node && typeof node === 'object') {
    for (const [k, v] of Object.entries(node)) flatten(v, prefix ? `${prefix}.${k}` : k, out);
  }
  return out;
}

function allowed (locale: 'de' | 'fa', key: string): boolean {
  return [SHARED, ALLOW[locale]].some(
    (a) => a.keys.includes(key) || a.patterns.some((p) => p.test(key))
  );
}

function placeholders (value: string): string[] {
  return [...new Set([...value.matchAll(/\{\{\s*([\w.]+)\s*\}\}/g)].map((m) => m[1]))].sort();
}

const english = flatten(en);
const locales = { de: flatten(de), fa: flatten(fa) };

describe('i18n translations', () => {
  for (const [name, values] of Object.entries(locales) as ['de' | 'fa', Record<string, string>][]) {
    it(`${name}.json has no values left in English`, () => {
      const untranslated = Object.keys(english).filter(
        (key) => values[key] === english[key] && !allowed(name, key)
      );
      expect(untranslated).toEqual([]);
    });

    it(`${name}.json allowlist has no stale entries`, () => {
      const a = ALLOW[name];
      const stale = a.keys.filter((key) => values[key] !== english[key]);
      expect(stale).toEqual([]);
    });

    it(`${name}.json uses the same {{placeholders}} as en.json`, () => {
      const mismatched = Object.keys(english)
        .filter((key) => key in values)
        .filter((key) => placeholders(values[key]).join() !== placeholders(english[key]).join())
        .map((key) => `${key}: en {${placeholders(english[key])}} vs ${name} {${placeholders(values[key])}}`);
      expect(mismatched).toEqual([]);
    });
  }

  // In right-to-left text a Latin token that starts with punctuation, such
  // as .omnisync-trash, --max-delete or /home/you, is drawn with the
  // punctuation on the wrong end (omnisync-trash.). A left-to-right mark
  // (U+200E) before the token keeps it in place.
  it('fa.json marks Latin tokens that start with punctuation as left-to-right', () => {
    const unmarked = Object.entries(locales.fa)
      .filter(([, value]) => /(^|[\s(«])(\.|--?|\/)[A-Za-z]/.test(value))
      .map(([key]) => key);
    expect(unmarked).toEqual([]);
  });

  it('detects an untranslated value and a placeholder mismatch', () => {
    expect(allowed('fa', 'syncConfirm.pushTitle')).toBe(false);
    expect(placeholders('Push {{name}} to {{ count }}')).toEqual(['count', 'name']);
  });
});

// A number in running text needs the plural form for its value ("1 conflict",
// "3 conflicts"): translate() picks key_one / key_other from vars.count with
// Intl.PluralRules. Every locale keeps the same key_one / key_other pair, even
// where both forms read the same (Persian nouns after a number stay singular).
const PLURAL_SUFFIX = /_(zero|one|two|few|many|other)$/;

describe('i18n plural forms', () => {
  it('every en.json string with {{count}} is a plural form', () => {
    const plain = Object.entries(english)
      .filter(([key, value]) => value.includes('{{count}}') && !PLURAL_SUFFIX.test(key))
      .map(([key]) => key);
    expect(plain).toEqual([]);
  });

  for (const [name, values] of Object.entries({ en: english, ...locales })) {
    it(`${name}.json has both _one and _other for every plural key in en.json`, () => {
      const bases = new Set(Object.keys(english).filter((key) => PLURAL_SUFFIX.test(key)).map((key) => key.replace(PLURAL_SUFFIX, '')));
      const missing = [...bases].flatMap((base) => [`${base}_one`, `${base}_other`]).filter((key) => !(key in values));
      expect(missing).toEqual([]);
    });
  }

  it('picks the plural form by the count', () => {
    expect(translate('en', 'granular.modifiedBoth', { count: 1 })).toBe('1 conflict');
    expect(translate('en', 'granular.modifiedBoth', { count: 2 })).toBe('2 conflicts');
    expect(translate('de', 'granular.modifiedBoth', { count: 1 })).toBe('1 Konflikt');
    expect(translate('de', 'granular.modifiedBoth', { count: 0 })).toBe('0 Konflikte');
  });
});
