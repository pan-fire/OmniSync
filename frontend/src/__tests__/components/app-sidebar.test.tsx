import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppSidebar } from '@/components/layout/app-sidebar';
import { I18nProvider } from '@/i18n';

// Mock next/navigation
const mockPathname = vi.fn().mockReturnValue('/');
vi.mock('next/navigation', () => ({
  usePathname: () => mockPathname(),
}));

// Mock next-themes
vi.mock('next-themes', () => ({
  useTheme: () => ({ theme: 'system', setTheme: vi.fn() }),
}));

function renderSidebar (pathname: string = '/') {
  mockPathname.mockReturnValue(pathname);
  // The sidebar's version line reads GET /health through React Query.
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <AppSidebar isCollapsed={false} onToggle={() => {}} />
      </I18nProvider>
    </QueryClientProvider>
  );
}

describe('AppSidebar', () => {
  beforeEach(() => {
    mockPathname.mockReturnValue('/');
  });

  it('renders all 5 navigation links', () => {
    renderSidebar();
    expect(screen.getByRole('link', { name: /dashboard/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /jobs/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /configuration/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /logs/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /conflicts/i })).toBeInTheDocument();
  });

  it('renders correct hrefs for each link', () => {
    renderSidebar();
    expect(screen.getByRole('link', { name: /dashboard/i })).toHaveAttribute('href', '/');
    expect(screen.getByRole('link', { name: /jobs/i })).toHaveAttribute('href', '/jobs');
    expect(screen.getByRole('link', { name: /configuration/i })).toHaveAttribute('href', '/config');
    expect(screen.getByRole('link', { name: /logs/i })).toHaveAttribute('href', '/logs');
    expect(screen.getByRole('link', { name: /conflicts/i })).toHaveAttribute('href', '/conflicts');
  });

  it('highlights Dashboard link when on /', () => {
    renderSidebar('/');
    const dashboardLink = screen.getByRole('link', { name: /dashboard/i });
    expect(dashboardLink.closest('button, [class]')).toHaveClass('bg-sidebar-accent');
  });

  it('highlights Jobs link when on /jobs', () => {
    renderSidebar('/jobs');
    const jobsLink = screen.getByRole('link', { name: /jobs/i });
    expect(jobsLink.closest('button, [class]')).toHaveClass('bg-sidebar-accent');
  });

  it('highlights Config link when on /config', () => {
    renderSidebar('/config');
    const configLink = screen.getByRole('link', { name: /configuration/i });
    expect(configLink.closest('button, [class]')).toHaveClass('bg-sidebar-accent');
  });

  it('highlights Logs link when on /logs', () => {
    renderSidebar('/logs');
    const logsLink = screen.getByRole('link', { name: /logs/i });
    expect(logsLink.closest('button, [class]')).toHaveClass('bg-sidebar-accent');
  });

  it('highlights Conflicts link when on /conflicts', () => {
    renderSidebar('/conflicts');
    const conflictsLink = screen.getByRole('link', { name: /conflicts/i });
    expect(conflictsLink.closest('button, [class]')).toHaveClass('bg-sidebar-accent');
  });

  it('does not highlight non-active links', () => {
    renderSidebar('/jobs');
    const dashboardLink = screen.getByRole('link', { name: /dashboard/i });
    expect(dashboardLink.closest('button, [class]')).not.toHaveClass('bg-sidebar-accent');
  });

  it('the collapsed sidebar is inert, so its links are not tabbable', () => {
    mockPathname.mockReturnValue('/');
    const { container } = render(
      <QueryClientProvider client={new QueryClient()}>
        <I18nProvider>
          <AppSidebar isCollapsed onToggle={() => {}} />
        </I18nProvider>
      </QueryClientProvider>
    );
    const aside = container.querySelector('aside')!;
    expect(aside).toHaveAttribute('inert');
    expect(aside).toHaveAttribute('aria-hidden', 'true');
    expect(screen.queryByRole('link', { name: /dashboard/i })).not.toBeInTheDocument();
  });

  it('the expanded sidebar is reachable and marks the current page', () => {
    renderSidebar('/jobs');
    expect(screen.getByRole('link', { name: /jobs/i })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', { name: 'Close sidebar' })).toBeInTheDocument();
  });
});
