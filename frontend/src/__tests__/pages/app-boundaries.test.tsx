import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';
import { I18nProvider } from '@/i18n';
import AppError from '@/app/error';
import NotFound from '@/app/not-found';
import Loading from '@/app/loading';
import LoginPage from '@/app/login/page';
import { Toaster } from '@/components/ui/sonner';

vi.mock('@/components/auth/login-form', () => ({
  LoginForm: ({ next }: { next?: string }) => <p>login form, next: {next ?? 'none'}</p>,
}));

afterEach(() => {
  vi.restoreAllMocks();
});

describe('Error boundary page', () => {
  it('shows the error, logs it, and retries', async () => {
    const log = vi.spyOn(console, 'error').mockImplementation(() => {});
    const reset = vi.fn();
    const error = new Error('Database is locked');
    render(<I18nProvider><AppError error={error} reset={reset} /></I18nProvider>);

    expect(screen.getByRole('alert')).toHaveTextContent('This page failed to load');
    expect(screen.getByText('Database is locked')).toBeInTheDocument();
    expect(log).toHaveBeenCalledWith(error);
    await userEvent.setup().click(screen.getByRole('button', { name: 'Retry' }));
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it('falls back to a generic text, in Persian too', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<I18nProvider initialLocale="fa"><AppError error={new Error('')} reset={vi.fn()} /></I18nProvider>);
    expect(screen.getByRole('heading', { name: 'بارگذاری این صفحه ناموفق بود' })).toBeInTheDocument();
  });
});

describe('Not found and loading pages', () => {
  it('a missing page links back to the dashboard', () => {
    render(<I18nProvider><NotFound /></I18nProvider>);
    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to the dashboard' })).toHaveAttribute('href', '/');
  });

  it('a missing page in Persian', () => {
    render(<I18nProvider initialLocale="fa"><NotFound /></I18nProvider>);
    expect(screen.getByRole('link', { name: 'بازگشت به داشبورد' })).toBeInTheDocument();
  });

  it('loading is announced as a status', () => {
    render(<I18nProvider><Loading /></I18nProvider>);
    expect(screen.getByRole('status')).toHaveTextContent('Loading...');
  });
});

describe('Login page', () => {
  it('passes a single next path to the form', async () => {
    render(await LoginPage({ searchParams: Promise.resolve({ next: '/profiles' }) }));
    expect(screen.getByText('login form, next: /profiles')).toBeInTheDocument();
  });

  // ?next=a&next=b is ambiguous: ignore it rather than pick one.
  it('ignores a repeated or missing next', async () => {
    const { unmount } = render(await LoginPage({ searchParams: Promise.resolve({ next: ['/a', '/b'] }) }));
    expect(screen.getByText('login form, next: none')).toBeInTheDocument();
    unmount();
    render(await LoginPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByText('login form, next: none')).toBeInTheDocument();
  });
});

describe('Toaster', () => {
  beforeEach(() => {
    // Sonner follows the system theme through matchMedia, which jsdom lacks.
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches:             false,
      media:               query,
      addEventListener:    () => {},
      removeEventListener: () => {},
      addListener:         () => {},
      removeListener:      () => {},
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows toasts right to left in Persian', async () => {
    render(<I18nProvider initialLocale="fa"><Toaster /></I18nProvider>);
    act(() => { toast.success('ذخیره شد'); });
    const message = await screen.findByText('ذخیره شد');
    expect(message.closest('[dir]')).toHaveAttribute('dir', 'rtl');
  });

  it('shows toasts left to right in English', async () => {
    render(<I18nProvider><Toaster /></I18nProvider>);
    act(() => { toast.error('Saved failed'); });
    const message = await screen.findByText('Saved failed');
    expect(message.closest('[dir]')).toHaveAttribute('dir', 'ltr');
  });
});
