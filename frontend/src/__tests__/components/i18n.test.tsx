import { describe, it, expect, beforeEach } from 'vitest';
import fc from 'fast-check';
import { render, act, cleanup } from '@testing-library/react';
import { renderToString } from 'react-dom/server';
import faLocale from '@/i18n/locales/fa.json';
import { I18nProvider, useTranslation, type Locale } from '@/i18n';

const localeArb = fc.constantFrom<Locale>('en', 'fa', 'de');

function TestComponent ({
  onReady,
}: {
  onReady: (setLocale: (l: Locale) => void) => void;
}) {
  const { setLocale } = useTranslation();
  onReady(setLocale);
  return null;
}

describe('Locale switching sets correct direction', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('dir');
    document.documentElement.removeAttribute('lang');
  });

  it('for any supported locale, "fa" sets direction to "rtl", "en" and "de" set direction to "ltr"', () => {
    fc.assert(
      fc.property(localeArb, (locale) => {
        cleanup();
        localStorage.clear();

        let setLocaleFn: (l: Locale) => void = () => {};

        render(
          <I18nProvider>
            <TestComponent
              onReady={(fn) => {
                setLocaleFn = fn;
              }}
            />
          </I18nProvider>
        );

        act(() => {
          setLocaleFn(locale);
        });

        const expectedDir = locale === 'fa' ? 'rtl' : 'ltr';
        expect(document.documentElement.getAttribute('dir')).toBe(expectedDir);
        expect(document.documentElement.getAttribute('lang')).toBe(locale);
      }),
      { numRuns: 100 }
    );
  });
});

// The locale comes from the server (cookie), not from localStorage
// during the first render, so server and client HTML match.
describe('Locale without hydration mismatch', () => {
  function Title () {
    const { t } = useTranslation();
    return <h1>{t('nav.dashboard')}</h1>;
  }

  beforeEach(() => {
    cleanup();
    localStorage.clear();
    document.cookie = 'omnisync-locale=; path=/; max-age=0';
  });

  it('renders the server-provided locale on the server', () => {
    const html = renderToString(<I18nProvider initialLocale="de"><Title /></I18nProvider>);
    expect(html).toContain('Dashboard');
    const fa = renderToString(<I18nProvider initialLocale="fa"><Title /></I18nProvider>);
    expect(fa).toContain(faLocale.nav.dashboard);
  });

  it('the first client render ignores localStorage; a saved choice is migrated to the cookie after mount', () => {
    localStorage.setItem('omnisync-locale', 'fa');
    const seen: string[] = [];
    function Probe () {
      const { locale } = useTranslation();
      seen.push(locale);
      return null;
    }
    render(<I18nProvider initialLocale="en"><Probe /></I18nProvider>);
    expect(seen[0]).toBe('en');
    expect(seen.at(-1)).toBe('fa');
    expect(document.cookie).toContain('omnisync-locale=fa');
    expect(localStorage.getItem('omnisync-locale')).toBeNull();
    expect(document.documentElement.getAttribute('dir')).toBe('rtl');
  });

  it('setLocale stores the choice in a cookie the server can read', () => {
    let setLocaleFn: (l: Locale) => void = () => {};
    render(
      <I18nProvider>
        <TestComponent onReady={(fn) => { setLocaleFn = fn; }} />
      </I18nProvider>
    );
    act(() => setLocaleFn('de'));
    expect(document.cookie).toContain('omnisync-locale=de');
    expect(document.documentElement.getAttribute('lang')).toBe('de');
  });
});
