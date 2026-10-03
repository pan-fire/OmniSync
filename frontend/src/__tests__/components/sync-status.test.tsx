import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { getPollingInterval } from '@/hooks/use-profile-sync';
import type { SyncStatus, SyncState } from '@/types';

const syncStateArb = fc.constantFrom<SyncState>('idle', 'pushing', 'pulling', 'syncing', 'error');

describe('Polling interval matches sync state', () => {
  it('returns 2000ms for active states and 30000ms for inactive states', () => {
    fc.assert(
      fc.property(syncStateArb, (state) => {
        const interval = getPollingInterval(state);
        if (state === 'pushing' || state === 'pulling' || state === 'syncing') {
          expect(interval).toBe(2000);
        } else {
          expect(interval).toBe(30000);
        }
      }),
      { numRuns: 100 }
    );
  });

  it('returns 30000ms for undefined state', () => {
    expect(getPollingInterval(undefined)).toBe(30000);
  });
});

import { render, within } from '@testing-library/react';
import { SyncStatusCard } from '@/components/sync/sync-status-card';
import { I18nProvider } from '@/i18n';
import { formatDateTime } from '@/lib/format';

const syncStatusStateArb = fc.constantFrom<SyncState>('idle', 'pushing', 'pulling', 'syncing', 'error');

const safeDateArb = fc.integer({ min: 946684800000, max: 4102444800000 }).map((ts) => new Date(ts).toISOString());

const syncStatusArb = fc.record({
  state:            syncStatusStateArb,
  last_sync:        fc.option(safeDateArb, { nil: null }),
  current_job_id:   fc.option(fc.nat(), { nil: null }),
  files_processed:  fc.nat({ max: 10000 }),
  errors:           fc.nat({ max: 100 }),
  pending_changes:  fc.nat({ max: 100 }),
  intervals_paused: fc.boolean(),
  paused_at:        fc.option(safeDateArb, { nil: null }),
  resync_required:  fc.boolean(),
});

const stateTranslations: Record<SyncState, string> = {
  idle:    'Idle',
  pushing: 'Pushing',
  pulling: 'Pulling',
  syncing: 'Syncing',
  error:   'Error',
};

describe('Sync status rendering completeness', () => {
  it('for any valid SyncStatus, the rendered component contains state, last_sync (when present), files_processed, and errors', () => {
    fc.assert(
      fc.property(syncStatusArb, (status: SyncStatus) => {
        const { unmount, container } = render(
          <I18nProvider>
            <SyncStatusCard status={status} isError={false} />
          </I18nProvider>
        );

        const view = within(container);

        // State should be rendered as translated text
        expect(view.getByText(stateTranslations[status.state])).toBeInTheDocument();

        // files_processed should be rendered (use getAllByText since value may duplicate errors)
        const fpMatches = view.getAllByText(String(status.files_processed));
        expect(fpMatches.length).toBeGreaterThanOrEqual(1);

        // errors count should be rendered
        const errMatches = view.getAllByText(String(status.errors));
        expect(errMatches.length).toBeGreaterThanOrEqual(1);

        // last_sync should be rendered when present
        if (status.last_sync) {
          const syncMatches = view.getAllByText(formatDateTime(status.last_sync, 'en'));
          expect(syncMatches.length).toBeGreaterThanOrEqual(1);
        }

        unmount();
      }),
      { numRuns: 100 }
    );
  });
});
