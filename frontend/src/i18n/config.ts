/**
 * Locale settings shared by the server layout and the client provider.
 * No 'use client' here: the root layout reads the locale cookie on the
 * server so the first HTML already has the right lang/dir and text.
 */

export type Locale = 'en' | 'fa' | 'de';

export const LOCALES: readonly Locale[] = ['en', 'fa', 'de'];
export const DEFAULT_LOCALE: Locale = 'en';
export const LOCALE_COOKIE = 'omnisync-locale';
export const SIDEBAR_COOKIE = 'omnisync-sidebar-collapsed';

const ONE_YEAR = 60 * 60 * 24 * 365;

export function parseLocale (value: string | null | undefined): Locale | null {
  return value === 'en' || value === 'fa' || value === 'de' ? value : null;
}

export function localeDir (locale: Locale): 'rtl' | 'ltr' {
  return locale === 'fa' ? 'rtl' : 'ltr';
}

/** Persist a UI preference in a cookie the server layout can read. */
export function writePreferenceCookie (name: string, value: string): void {
  if (typeof document === 'undefined') return;
  document.cookie = `${name}=${encodeURIComponent(value)}; path=/; max-age=${ONE_YEAR}; samesite=lax`;
}
