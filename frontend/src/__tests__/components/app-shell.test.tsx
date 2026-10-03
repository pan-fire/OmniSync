import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppShell } from '@/components/layout/app-shell';
import { PageHeader } from '@/components/layout/page-header';
import { I18nProvider } from '@/i18n';

const mockPathname = vi.fn().mockReturnValue('/');
vi.mock('next/navigation', () => ({
  usePathname: () => mockPathname(),
}));

vi.mock('next-themes', () => ({
  useTheme: () => ({ theme: 'system', setTheme: vi.fn() }),
}));

function renderShell (initialCollapsed = false) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <AppShell initialCollapsed={initialCollapsed}>
          <PageHeader title="Profiles" />
        </AppShell>
      </I18nProvider>
    </QueryClientProvider>
  );
}

describe('AppShell', () => {
  beforeEach(() => {
    mockPathname.mockReturnValue('/');
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    document.cookie = 'omnisync-sidebar-collapsed=0; path=/';
  });

  it('has a skip link to the main content and one page heading', () => {
    renderShell();
    const skip = screen.getByRole('link', { name: 'Skip to main content' });
    expect(skip).toHaveAttribute('href', '#main-content');
    expect(document.getElementById('main-content')?.tagName).toBe('MAIN');
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  });

  it('sets the document title from the page header', () => {
    renderShell();
    expect(document.title).toBe('Profiles · OmniSync');
  });

  it('moves focus to the open button when the sidebar is collapsed, and back', async () => {
    const user = userEvent.setup();
    renderShell();
    await user.click(screen.getByRole('button', { name: 'Close sidebar' }));
    const openButton = screen.getByRole('button', { name: 'Open sidebar' });
    expect(openButton).toHaveFocus();
    await user.click(openButton);
    expect(screen.getByRole('button', { name: 'Close sidebar' })).toHaveFocus();
  });

  it('opens the mobile navigation drawer and closes it on navigation', async () => {
    const user = userEvent.setup();
    const { rerender } = renderShell();
    expect(screen.queryByTestId('mobile-nav')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Open navigation menu' }));
    const drawer = await screen.findByTestId('mobile-nav');
    expect(drawer).toHaveAttribute('role', 'dialog');

    await user.click(within(drawer).getByRole('link', { name: 'Jobs' }));
    mockPathname.mockReturnValue('/jobs');
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <I18nProvider>
          <AppShell>
            <PageHeader title="Jobs" />
          </AppShell>
        </I18nProvider>
      </QueryClientProvider>
    );
    await waitFor(() => expect(screen.queryByTestId('mobile-nav')).not.toBeInTheDocument());
  });

  it('closes the mobile drawer with Escape and returns focus to the menu button', async () => {
    const user = userEvent.setup();
    renderShell();
    const menuButton = screen.getByRole('button', { name: 'Open navigation menu' });
    await user.click(menuButton);
    await screen.findByTestId('mobile-nav');
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByTestId('mobile-nav')).not.toBeInTheDocument());
    expect(menuButton).toHaveFocus();
  });
});
