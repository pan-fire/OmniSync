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

// Mount once and rerender per run: a fresh mount (query client, i18n, the
// wizard and delete dialogs) per run made 100 runs take over 3 s under
// coverage on a loaded machine. Each row is read once instead of a text
// query per field, which is cheaper and checks name and type stay together.
function Manager ({ remotes }: { remotes: Remote[] }) {
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <RemoteManager remotes={remotes} />
      </I18nProvider>
    </QueryClientProvider>
  );
}
const queryClient = new QueryClient();

describe('Remote list rendering completeness', () => {
  it("for any non-empty Remote array, the rendered list contains each remote's name and type", () => {
    const { container, rerender } = render(<Manager remotes={[]} />);
    fc.assert(
      fc.property(nonEmptyRemotesArb, (remotes: Remote[]) => {
        rerender(<Manager remotes={remotes} />);

        // [name, type] of each row, in order
        const rows = Array.from(container.querySelectorAll('span.font-medium'), (name) =>
          [name.textContent, name.nextElementSibling?.textContent]);
        expect(rows).toEqual(remotes.map((remote) => [remote.name, remote.type]));
        for (const remote of remotes) {
          expect(within(container).getByLabelText(`Delete remote ${remote.name}`)).toHaveRole('button');
        }
      }),
      { numRuns: 50 }
    );
  });
});
