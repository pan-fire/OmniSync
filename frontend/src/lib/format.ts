import type { Locale } from '@/i18n';

/**
 * Locale-aware formatting shared by the pages. Timestamps from the API are
 * ISO strings; they are shown in the app's locale, not the browser's.
 */

/** Medium date and short time ("Sep 27, 2026, 2:03 PM" in English), or `fallback` when absent/invalid. */
export function formatDateTime (iso: string | null | undefined, locale: Locale, fallback = '—'): string {
  if (!iso) return fallback;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(d);
}

/** "5 minutes ago" / "in 3 hours", in the app's locale. */
export function formatRelativeTime (iso: string | null | undefined, locale: Locale, fallback = '—'): string {
  if (!iso) return fallback;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const seconds = Math.round((d.getTime() - Date.now()) / 1000);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' });
  const abs = Math.abs(seconds);
  if (abs < 60) return rtf.format(seconds, 'second');
  const minutes = Math.round(seconds / 60);
  if (Math.abs(minutes) < 60) return rtf.format(minutes, 'minute');
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return rtf.format(hours, 'hour');
  const days = Math.round(hours / 24);
  return rtf.format(days, 'day');
}

// Intl has no exabyte unit: EB is formatted as petabyte with the unit
// label swapped (every app locale writes these abbreviations in Latin).
const BYTE_UNITS = ['byte', 'kilobyte', 'megabyte', 'gigabyte', 'terabyte', 'petabyte', 'exabyte'] as const;

/** Human-readable size ("1.5 MB"), or `fallback` when unknown. Beyond EB the number grows. */
export function formatBytes (bytes: number | null | undefined, locale: Locale, fallback = '—'): string {
  if (bytes == null || Number.isNaN(bytes)) return fallback;
  let value = bytes;
  let unit = 0;
  while (Math.abs(value) >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024;
    unit++;
  }
  unit = Math.min(Math.max(unit, 0), BYTE_UNITS.length - 1);
  const exa = BYTE_UNITS[unit] === 'exabyte';
  const format = new Intl.NumberFormat(locale, {
    style:                 'unit',
    unit:                  exa ? 'petabyte' : BYTE_UNITS[unit],
    unitDisplay:           'short',
    maximumFractionDigits: unit === 0 ? 0 : 1,
  });
  if (!exa) return format.format(value);
  return format.formatToParts(value).map((p) => (p.type === 'unit' ? 'EB' : p.value)).join('');
}
