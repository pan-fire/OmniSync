import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { render, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider } from '@/i18n';
import { RemoteManager } from '@/components/config/remote-manager';
import type { Remote } from '@/types';

// --- Remote Arbitraries ---

const remoteArb: fc.Arbitrary<Remote> = fc.record({
  name:          fc.stringMatching(/^[a-z][a-z0-9_-]{0,14}$/),
  type:          fc.constantFrom('s3', 'gdrive', 'dropbox', 'onedrive', 'b2', 'sftp'),
  last_verified: fc.option(fc.integer({ min: 1577836800000, max: 1893456000000 }).map((ms) => new Date(ms).toISOString()), { nil: null }),
});

const nonEmptyRemotesArb = fc
  .array(remoteArb, { minLength: 1, maxLength: 5 })
  .map((remotes) =>
    remotes.map((r, i) => ({ ...r, name: `${r.name}${i}` }))
  );

describe('Remote list rendering completeness', () => {
  it("for any non-empty Remote array, the rendered list contains each remote's name and type", () => {
    fc.assert(
      fc.property(nonEmptyRemotesArb, (remotes: Remote[]) => {
        const { unmount, container } = render(
          <QueryClientProvider client={new QueryClient()}>
            <I18nProvider>
              <RemoteManager remotes={remotes} />
            </I18nProvider>
          </QueryClientProvider>
        );

        const view = within(container);

        for (const remote of remotes) {
          expect(view.getAllByText(remote.name).length).toBeGreaterThanOrEqual(1);
          expect(view.getAllByText(remote.type).length).toBeGreaterThanOrEqual(1);
        }

        unmount();
      }),
      { numRuns: 100 }
    );
  });
});
