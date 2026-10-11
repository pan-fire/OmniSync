import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { I18nProvider } from '@/i18n';
import fa from '@/i18n/locales/fa.json';
import { ThemeSwitcher } from '@/components/layout/theme-switcher';

const setTheme = vi.fn();
let currentTheme: string | undefined = 'system';

vi.mock('next-themes', () => ({
  useTheme: () => ({ theme: currentTheme, setTheme }),
}));

beforeEach(() => {
  setTheme.mockClear();
  currentTheme = 'system';
});

describe('ThemeSwitcher', () => {
  // Every menu entry sets its theme.
  it.each([
    ['System', 'system'],
    ['Dark', 'dark'],
    ['Light', 'light'],
  ])('choosing %s sets the %s theme', async (label, theme) => {
    const user = userEvent.setup();
    render(<I18nProvider><ThemeSwitcher /></I18nProvider>);
    expect(screen.queryByRole('menu')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Theme' }));
    expect(await screen.findByRole('menu')).toBeInTheDocument();
    await user.click(screen.getByRole('menuitem', { name: label }));
    expect(setTheme).toHaveBeenCalledWith(theme);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  // The trigger icon tells the user which theme is active.
  it.each([
    ['dark', 'lucide-moon'],
    ['light', 'lucide-sun'],
    ['system', 'lucide-monitor'],
  ])('shows the %s theme icon on the trigger', (theme, icon) => {
    currentTheme = theme;
    render(<I18nProvider><ThemeSwitcher /></I18nProvider>);
    const svg = screen.getByRole('button', { name: 'Theme' }).querySelector('svg');
    expect(svg).toHaveClass(icon);
  });

  it('opens from the keyboard and closes with Escape without changing the theme', async () => {
    const user = userEvent.setup();
    render(<I18nProvider><ThemeSwitcher /></I18nProvider>);
    screen.getByRole('button', { name: 'Theme' }).focus();
    await user.keyboard('{Enter}');
    expect(await screen.findByRole('menu')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).toBeNull();
    expect(setTheme).not.toHaveBeenCalled();
  });

  it('speaks Persian', async () => {
    const user = userEvent.setup();
    render(<I18nProvider initialLocale="fa"><ThemeSwitcher /></I18nProvider>);
    await user.click(screen.getByRole('button', { name: fa.common.theme }));
    await user.click(await screen.findByRole('menuitem', { name: fa.common.dark }));
    expect(setTheme).toHaveBeenCalledWith('dark');
  });
});
