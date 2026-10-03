import { describe, it, expect, vi } from 'vitest';
import { translate } from '@/i18n';

// A German catalogue that lacks most keys, to see the English fallback.
vi.mock('@/i18n/locales/de.json', () => ({
  default: { granular: { toastPushed: '{{path}} gepusht' } },
}));

describe('translate', () => {
  it('fills placeholders and picks the plural form from count', () => {
    expect(translate('en', 'syncCheck.title', { count: 1 })).toBe('1 unsynced change detected');
    expect(translate('en', 'syncCheck.title', { count: 3 })).toBe('3 unsynced changes detected');
    expect(translate('en', 'granular.toastPushed', { path: 'a/b.txt' })).toBe('Pushed a/b.txt');
  });

  it('isolates interpolated values in Persian so paths keep their order', () => {
    const text = translate('fa', 'granular.toastPushed', { path: '/home/you/Sync' });
    expect(text).toContain('\u2068/home/you/Sync\u2069');
    // Left-to-right locales get the value as is.
    expect(translate('de', 'granular.toastPushed', { path: '/x' })).toBe('/x gepusht');
  });

  it('falls back to English for a key missing in the locale, then to the key', () => {
    expect(translate('de', 'nav.dashboard')).toBe('Dashboard');
    expect(translate('de', 'syncCheck.title', { count: 2 })).toBe('2 unsynced changes detected');
    expect(translate('de', 'no.such.key')).toBe('no.such.key');
  });
});
