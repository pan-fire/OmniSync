import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { I18nProvider } from '@/i18n';
import { SyncStatusCard } from '@/components/sync/sync-status-card';
import { ProfileCard } from '@/components/profiles/profile-card';
import type { ProfileStatus, SyncStatus } from '@/types';

const REFUSAL = 'Refused: /data/docs has no .omnisync-check marker (is the drive mounted?)';

const STATUS: SyncStatus = {
  state:            'error',
  last_sync:        null,
  current_job_id:   null,
  files_processed:  0,
  errors:           1,
  pending_changes:  0,
  intervals_paused: false,
  paused_at:        null,
  last_error:       REFUSAL,
  resync_required:  false,
};

describe('last_error display', () => {
  it('the status card explains why the last sync failed', () => {
    render(<I18nProvider><SyncStatusCard status={STATUS} isError={false} /></I18nProvider>);
    expect(screen.getByText('Error')).toBeInTheDocument();
    expect(screen.getByText(REFUSAL)).toBeInTheDocument();
  });

  it('the status card shows nothing extra without last_error', () => {
    render(<I18nProvider><SyncStatusCard status={{ ...STATUS, state: 'idle', last_error: null }} isError={false} /></I18nProvider>);
    expect(screen.queryByTestId('last-error')).not.toBeInTheDocument();
  });

  it('the profile card shows last_error', () => {
    const profile: ProfileStatus = {
      ...STATUS,
      id:                    1,
      slug:                  'docs',
      name:                  'Docs',
      local_dir:             '/data/docs',
      remote_dir:            'gdrive:docs',
      debounce_seconds:      5,
      pull_interval_minutes: 5,
      rclone_filter:         [],
      rclone_args:           [],
      max_retries:           3,
      enabled:               true,
      created_at:            '2026-01-01T00:00:00Z',
      updated_at:            '2026-01-01T00:00:00Z',
      last_error:            REFUSAL,
      max_delete:            50,
      sync_mode:             'mirror',
    };
    render(
      <I18nProvider>
        <ProfileCard profile={profile} onToggle={vi.fn()} onDelete={vi.fn()} onSync={vi.fn()} onSyncNow={vi.fn()} />
      </I18nProvider>
    );
    expect(screen.getByTestId('last-error')).toHaveTextContent(REFUSAL);
  });
});
