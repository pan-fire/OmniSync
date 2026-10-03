import { describe, it, expect, vi, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider } from '@/i18n';
import { api } from '@/lib/api';
import { VersionInfo } from '@/components/layout/version-info';
import type { Health } from '@/types';
import packageJson from '../../../package.json';
import nextConfig from '../../../next.config';

const HEALTH: Health = {
  status:            'ok',
  rclone_installed:  true,
  remote_accessible: null,
  uptime_seconds:    1,
  database_ok:       true,
};

function renderVersion () {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}><I18nProvider>{children}</I18nProvider></QueryClientProvider>
  );
  return render(<VersionInfo />, { wrapper });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe('VersionInfo', () => {
  it('next.config inlines the package.json version into the client bundle', () => {
    expect(nextConfig.env?.NEXT_PUBLIC_OMNISYNC_VERSION).toBe(packageJson.version);
  });

  it('shows the web UI version', async () => {
    vi.stubEnv('NEXT_PUBLIC_OMNISYNC_VERSION', '0.9.0');
    vi.spyOn(api, 'getHealth').mockResolvedValue({ ...HEALTH, version: '0.9.0' });
    renderVersion();
    expect(screen.getByTestId('version-info')).toHaveTextContent('OmniSync 0.9.0');
    await waitFor(() => expect(api.getHealth).toHaveBeenCalled());
    expect(screen.queryByText(/Backend/)).not.toBeInTheDocument();
  });

  it('also shows the backend version when it differs', async () => {
    vi.stubEnv('NEXT_PUBLIC_OMNISYNC_VERSION', '0.9.0');
    vi.spyOn(api, 'getHealth').mockResolvedValue({ ...HEALTH, version: '0.10.0' });
    renderVersion();
    const backend = await screen.findByText(/Backend 0\.10\.0/);
    expect(backend.closest('[title]')).toHaveAttribute('title', expect.stringMatching(/different version/));
  });

  it('keeps the UI version when the backend is too old to report one', async () => {
    vi.stubEnv('NEXT_PUBLIC_OMNISYNC_VERSION', '0.9.0');
    vi.spyOn(api, 'getHealth').mockResolvedValue(HEALTH);
    renderVersion();
    await waitFor(() => expect(api.getHealth).toHaveBeenCalled());
    expect(screen.getByTestId('version-info')).toHaveTextContent(/^OmniSync 0\.9\.0$/);
  });

  it('keeps the UI version when /health fails', async () => {
    vi.stubEnv('NEXT_PUBLIC_OMNISYNC_VERSION', '0.9.0');
    vi.spyOn(api, 'getHealth').mockRejectedValue(new Error('down'));
    renderVersion();
    await waitFor(() => expect(api.getHealth).toHaveBeenCalled());
    expect(screen.getByTestId('version-info')).toHaveTextContent('OmniSync 0.9.0');
  });
});
