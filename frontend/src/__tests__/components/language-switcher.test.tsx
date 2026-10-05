import { afterEach, describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { I18nProvider } from '@/i18n';
import fa from '@/i18n/locales/fa.json';
import de from '@/i18n/locales/de.json';
import { LanguageSwitcher } from '@/components/layout/language-switcher';

afterEach(() => {
  document.documentElement.setAttribute('dir', 'ltr');
  document.documentElement.setAttribute('lang', 'en');
});

describe('LanguageSwitcher', () => {
  it('marks the current language and lists each in its own language', async () => {
    const user = userEvent.setup();
    render(<I18nProvider><LanguageSwitcher /></I18nProvider>);
    await user.click(screen.getByRole('button', { name: 'Language' }));
    const english = await screen.findByRole('menuitem', { name: 'English' });
    expect(english).toHaveAttribute('aria-current', 'true');
    const persian = screen.getByRole('menuitem', { name: 'فارسی' });
    expect(persian).toHaveAttribute('lang', 'fa');
    expect(persian).not.toHaveAttribute('aria-current');
  });

  // Persian is right to left: the whole page must flip.
  it('switching to Persian sets dir=rtl and translates the UI', async () => {
    const user = userEvent.setup();
    render(<I18nProvider><LanguageSwitcher /></I18nProvider>);
    expect(document.documentElement).toHaveAttribute('dir', 'ltr');
    await user.click(screen.getByRole('button', { name: 'Language' }));
    await user.click(await screen.findByRole('menuitem', { name: 'فارسی' }));
    expect(document.documentElement).toHaveAttribute('dir', 'rtl');
    expect(document.documentElement).toHaveAttribute('lang', 'fa');
    expect(screen.getByRole('button', { name: fa.common.language })).toBeInTheDocument();
    expect(document.cookie).toContain('fa');
  });

  it('switching from Persian to German goes back to left to right', async () => {
    const user = userEvent.setup();
    render(<I18nProvider initialLocale="fa"><LanguageSwitcher /></I18nProvider>);
    expect(document.documentElement).toHaveAttribute('dir', 'rtl');
    await user.click(screen.getByRole('button', { name: fa.common.language }));
    await user.click(await screen.findByRole('menuitem', { name: 'Deutsch' }));
    expect(document.documentElement).toHaveAttribute('dir', 'ltr');
    expect(document.documentElement).toHaveAttribute('lang', 'de');
    expect(screen.getByRole('button', { name: de.common.language })).toBeInTheDocument();
  });
});
