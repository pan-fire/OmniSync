import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { I18nProvider, type Locale } from '@/i18n';
import { SyncStatusCard } from '@/components/sync/sync-status-card';
import { AggregateStatusCard } from '@/components/sync/aggregate-status-card';
import en from '@/i18n/locales/en.json';
import de from '@/i18n/locales/de.json';
import fa from '@/i18n/locales/fa.json';
import type { AggregateStatus, SyncStatus } from '@/types';

// The summary cards are named for what they show, not after the page
// ("Dashboard"): "Sync status" on the dashboard, "Status" on a profile.

const STATUS: SyncStatus = {
  state:            'idle',
  last_sync:        null,
  current_job_id:   null,
  files_processed:  0,
  errors:           0,
  pending_changes:  0,
  intervals_paused: false,
  paused_at:        null,
  resync_required:  false,
};

const AGGREGATE: AggregateStatus = {
  overall_state:         'idle',
  total_pending_changes: 0,
  paused_profiles:       [],
  profiles_summary:      [],
};

function cardTitle (container: HTMLElement): string | null | undefined {
  return container.querySelector('[data-slot="card-title"]')?.textContent;
}

const LOCALES = { en, de, fa } as const;

describe('status card titles', () => {
  for (const [locale, strings] of Object.entries(LOCALES) as [Locale, typeof en][]) {
    it(`the profile card is titled "${strings.dashboard.statusTitle}" in ${locale}`, () => {
      for (const props of [{ status: STATUS, isError: false }, { status: undefined, isError: false }, { status: undefined, isError: true }]) {
        const { container, unmount } = render(
          <I18nProvider initialLocale={locale}><SyncStatusCard {...props} /></I18nProvider>
        );
        expect(cardTitle(container)).toBe(strings.dashboard.statusTitle);
        unmount();
      }
    });

    it(`the dashboard card is titled "${strings.dashboard.syncStatusTitle}" in ${locale}`, () => {
      const { container } = render(
        <I18nProvider initialLocale={locale}><AggregateStatusCard status={AGGREGATE} isError={false} /></I18nProvider>
      );
      expect(cardTitle(container)).toBe(strings.dashboard.syncStatusTitle);
      expect(cardTitle(container)).not.toBe(strings.dashboard.title);
    });
  }
});
