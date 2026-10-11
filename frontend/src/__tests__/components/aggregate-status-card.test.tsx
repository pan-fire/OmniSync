import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { I18nProvider, type Locale } from '@/i18n';
import fa from '@/i18n/locales/fa.json';
import { AggregateStatusCard } from '@/components/sync/aggregate-status-card';
import { formatDateTime } from '@/lib/format';
import type { AggregateStatus, ProfileSummary } from '@/types';

function summary (slug: string, lastSync: string | null): ProfileSummary {
  return { slug, name: slug, state: 'idle', last_sync: lastSync, pending_changes: 0, intervals_paused: false, last_error: null, resync_required: false };
}

function renderCard (status: AggregateStatus | undefined, isError = false, locale: Locale = 'en') {
  return render(
    <I18nProvider initialLocale={locale}>
      <AggregateStatusCard status={status} isError={isError} />
    </I18nProvider>
  );
}

describe('AggregateStatusCard', () => {
  it('shows an error when the status could not be loaded', () => {
    renderCard(undefined, true);
    expect(screen.getByText('Sync status')).toBeInTheDocument();
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
  });

  it('shows loading until the status arrives', () => {
    renderCard(undefined);
    expect(screen.getByText('Loading...')).toBeInTheDocument();
  });

  // The card reports the most recent sync of any profile.
  it('shows the state, profile count, latest sync and pending changes', () => {
    renderCard({
      overall_state:         'pushing',
      total_pending_changes: 7,
      paused_profiles:       [],
      profiles_summary:      [
        summary('a', '2026-03-01T10:00:00Z'),
        summary('b', null),
        summary('c', '2026-05-01T10:00:00Z'),
      ],
    });
    expect(screen.getByText('Pushing')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText(formatDateTime('2026-05-01T10:00:00Z', 'en'))).toBeInTheDocument();
    expect(screen.getByText('7')).toBeInTheDocument();
  });

  it('says there was no sync yet when no profile has synced', () => {
    renderCard({ overall_state: 'idle', total_pending_changes: 0, paused_profiles: [], profiles_summary: [summary('a', null)] });
    expect(screen.getByText('Idle')).toBeInTheDocument();
    expect(screen.getByText('No sync yet')).toBeInTheDocument();
  });

  it('speaks Persian', () => {
    renderCard({ overall_state: 'error', total_pending_changes: 0, paused_profiles: [], profiles_summary: [] }, false, 'fa');
    expect(screen.getByText(fa.dashboard.syncStatusTitle)).toBeInTheDocument();
    expect(screen.getByText(fa.dashboard.state.error)).toBeInTheDocument();
    expect(screen.getByText(fa.dashboard.noSync)).toBeInTheDocument();
  });
});
