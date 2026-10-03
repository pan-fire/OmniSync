import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import fc from 'fast-check';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider } from '@/i18n';
import { api } from '@/lib/api';
import { getHealthChecks, HealthIndicators } from '@/components/sync/health-indicators';
import type { Health } from '@/types';

const healthArb = fc.record({
  status:            fc.constantFrom('ok', 'degraded', 'error'),
  rclone_installed:  fc.boolean(),
  remote_accessible: fc.constant(null),
  uptime_seconds:    fc.float({ min: 0, max: 86400 * 365, noNaN: true }),
  database_ok:       fc.boolean(),
});

const HEALTHY: Health = {
  status:            'ok',
  rclone_installed:  true,
  remote_accessible: null,
  uptime_seconds:    3660,
  database_ok:       true,
};

// Feature: frontend-dashboard, Property 12: Health indicator rendering with failure warnings
describe('Property 12: Health indicator rendering with failure warnings', () => {
  // Validates: Requirements 8.1, 8.2
  it('for any Health object, getHealthChecks returns the local checks with correct ok values', () => {
    fc.assert(
      fc.property(healthArb, (health: Health) => {
        const checks = getHealthChecks(health);
        expect(checks).toHaveLength(2);
        expect(checks[0].ok).toBe(health.rclone_installed);
        expect(checks[1].ok).toBe(health.database_ok);
      }),
      { numRuns: 100 }
    );
  });

  it('for any Health with at least one false field, at least one check has ok=false', () => {
    fc.assert(
      fc.property(
        healthArb.filter((h) => !h.rclone_installed || !h.database_ok),
        (health: Health) => {
          const checks = getHealthChecks(health);
          const failingChecks = checks.filter((c) => !c.ok);
          expect(failingChecks.length).toBeGreaterThan(0);
        }
      ),
      { numRuns: 100 }
    );
  });

  it('never turns the always-null remote_accessible into a failed check', () => {
    const checks = getHealthChecks({ ...HEALTHY, remote_accessible: null });
    expect(checks.every((c) => c.ok)).toBe(true);
    expect(checks.map((c) => c.labelKey)).not.toContain('health.remoteAccessible');
  });
});

describe('HealthIndicators with GET /health/remotes', () => {
  const originalFetch = globalThis.fetch;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  function respond (data: unknown, status = 200) {
    return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(data) });
  }

  function renderCard () {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}><I18nProvider>{children}</I18nProvider></QueryClientProvider>
    );
    return render(<HealthIndicators health={HEALTHY} isError={false} />, { wrapper });
  }

  it('asks /health/remotes', async () => {
    fetchMock.mockImplementation(() => respond({ remotes: [] }));
    renderCard();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][0]).toBe('/api/health/remotes');
  });

  it('shows a reachable remote with the profiles that use it', async () => {
    fetchMock.mockImplementation(() => respond({ remotes: [{ remote: 'gdrive', accessible: true, profiles: ['docs', 'photos'] }] }));
    renderCard();
    expect(await screen.findByText('gdrive reachable')).toBeInTheDocument();
    expect(screen.getByText('used by docs, photos')).toBeInTheDocument();
    expect(screen.queryByText('Warning')).not.toBeInTheDocument();
  });

  it('warns about a remote that is not reachable', async () => {
    fetchMock.mockImplementation(() => respond({
      remotes: [
        { remote: 'gdrive', accessible: true, profiles: ['docs'] },
        { remote: 'box', accessible: false, profiles: ['work'] },
      ],
    }));
    renderCard();
    expect(await screen.findByText('box not reachable')).toBeInTheDocument();
    expect(screen.getByText('gdrive reachable')).toBeInTheDocument();
    expect(screen.getAllByText('Warning')).toHaveLength(1);
  });

  it('while the check runs it says so and claims nothing about the remotes', () => {
    fetchMock.mockImplementation(() => new Promise(() => {}));
    renderCard();
    expect(screen.getByText('Checking the remotes…')).toBeInTheDocument();
    expect(screen.queryByText(/not reachable/)).not.toBeInTheDocument();
    expect(screen.queryByText('Warning')).not.toBeInTheDocument();
  });

  it('a failed check is "unknown", not "not reachable"', async () => {
    fetchMock.mockImplementation(() => respond({ detail: 'boom' }, 500));
    renderCard();
    expect(await screen.findByText('Remote reachability unknown: the check failed.')).toBeInTheDocument();
    expect(screen.queryByText(/not reachable/)).not.toBeInTheDocument();
    expect(screen.queryByText('Warning')).not.toBeInTheDocument();
  });

  it('shows no remote row when no running profile uses a remote', async () => {
    fetchMock.mockImplementation(() => respond({ remotes: [] }));
    renderCard();
    await waitFor(() => expect(screen.queryByText('Checking the remotes…')).not.toBeInTheDocument());
    expect(screen.queryByText(/reachab/)).not.toBeInTheDocument();
    expect(screen.queryByText('Remote accessible')).not.toBeInTheDocument();
  });
});

describe('api.getHealth', () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('returns the body of a degraded (503) backend instead of failing', async () => {
    const degraded = { ...HEALTHY, status: 'degraded', rclone_installed: false };
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 503, json: () => Promise.resolve(degraded) }) as unknown as typeof fetch;
    await expect(api.getHealth()).resolves.toEqual(degraded);
  });

  it('still fails for other errors', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500, json: () => Promise.resolve({ detail: 'boom' }) }) as unknown as typeof fetch;
    await expect(api.getHealth()).rejects.toThrow('boom');
  });
});
