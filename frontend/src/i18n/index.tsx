'use client';

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from 'react';

import { Direction } from 'radix-ui';

import en from './locales/en.json';
import fa from './locales/fa.json';
import de from './locales/de.json';
import {
  DEFAULT_LOCALE,
  LOCALE_COOKIE,
  localeDir,
  writePreferenceCookie,
  type Locale,
} from './config';

export type { Locale };

type TranslationMap = Record<string, unknown>;

const translations: Record<Locale, TranslationMap> = { en, fa, de };

function getNestedValue (obj: TranslationMap, key: string): string | undefined {
  const parts = key.split('.');
  let current: unknown = obj;
  for (const part of parts) {
    if (current === null || current === undefined || typeof current !== 'object') {
      return undefined;
    }
    current = (current as Record<string, unknown>)[part];
  }
  return typeof current === 'string' ? current : undefined;
}

const warnedKeys = new Set<string>();

function warnMissing (locale: Locale, key: string) {
  if (process.env.NODE_ENV !== 'development' || warnedKeys.has(`${locale}:${key}`)) return;
  warnedKeys.add(`${locale}:${key}`);
  console.warn(`[i18n] Missing translation "${key}" for locale "${locale}"`);
}

/**
 * The string for `key` (or its plural form for `count`) in `locale`. A key
 * missing there falls back to English, then to the key itself, so users see
 * text rather than "section.someKey".
 */
function lookup (locale: Locale, key: string, count: number | undefined): string | undefined {
  const map = translations[locale];
  if (count !== undefined) {
    const form = new Intl.PluralRules(locale).select(count);
    const plural = getNestedValue(map, `${key}_${form}`) ?? getNestedValue(map, `${key}_other`);
    if (plural !== undefined) return plural;
  }
  return getNestedValue(map, key);
}

export type TranslationVars = Record<string, string | number>;

// First Strong Isolate ... Pop Directional Isolate.
const FSI = '\u2068';
const PDI = '\u2069';

/**
 * Fill `{{name}}` placeholders. In a right-to-left locale each value is
 * wrapped in Unicode isolates, so a path such as `/home/you/Sync` or
 * `gdrive:Backup` keeps its own direction inside a Persian sentence.
 */
function interpolate (text: string, vars?: TranslationVars, isolate = false): string {
  if (!vars) return text;
  return text.replace(/\{\{(\w+)\}\}/g, (match, name: string) => {
    if (!(name in vars)) return match;
    const value = String(vars[name]);
    return isolate ? `${FSI}${value}${PDI}` : value;
  });
}

/**
 * Look up `key`. When `vars.count` is a number and the locale has
 * `key_one` / `key_other` variants, the plural form for the locale is used.
 */
export function translate (locale: Locale, key: string, vars?: TranslationVars): string {
  const count = vars && typeof vars.count === 'number' ? vars.count : undefined;
  let text = lookup(locale, key, count);
  if (text === undefined) {
    warnMissing(locale, key);
    text = locale === DEFAULT_LOCALE ? undefined : lookup(DEFAULT_LOCALE, key, count);
  }
  return interpolate(text ?? key, vars, localeDir(locale) === 'rtl');
}

interface I18nContextValue {
  t:         (key: string, vars?: TranslationVars) => string;
  locale:    Locale;
  setLocale: (locale: Locale) => void;
}

const I18nContext = createContext<I18nContextValue>({
  t:         (key: string, vars?: TranslationVars) => translate(DEFAULT_LOCALE, key, vars),
  locale:    DEFAULT_LOCALE,
  setLocale: () => {},
});

interface I18nProviderProps {
  children:       ReactNode;
  /**
   * The locale the server rendered with (from the locale cookie). Server
   * and first client render use the same value, so hydration matches.
   */
  initialLocale?: Locale;
}

export function I18nProvider ({ children, initialLocale = DEFAULT_LOCALE }: I18nProviderProps) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);

  const setLocale = useCallback((newLocale: Locale) => {
    setLocaleState(newLocale);
    writePreferenceCookie(LOCALE_COOKIE, newLocale);
  }, []);

  // Keep <html lang/dir> in step when the locale changes on the client.
  useEffect(() => {
    document.documentElement.setAttribute('dir', localeDir(locale));
    document.documentElement.setAttribute('lang', locale);
  }, [locale]);

  const t = useCallback(
    (key: string, vars?: TranslationVars): string => translate(locale, key, vars),
    [locale]
  );

  return (
    <I18nContext.Provider value={{ t, locale, setLocale }}>
      {/* Radix menus, tabs, selects and scroll areas read the direction
          from context, not from <html dir>. */}
      <Direction.DirectionProvider dir={localeDir(locale)}>
        {children}
      </Direction.DirectionProvider>
    </I18nContext.Provider>
  );
}

export function useTranslation () {
  return useContext(I18nContext);
}
