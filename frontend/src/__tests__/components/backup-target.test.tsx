import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import fc from 'fast-check';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider } from '@/i18n';
import { BackupTargetCard } from '@/components/profiles/backup-target-card';
import type { BackupTarget, BackupTargetType, BackupMode } from '@/types';

// --- Arbitraries ---
const targetTypeArb = fc.constantFrom<BackupTargetType>('local', 'remote', 'custom_remote');
const backupModeArb = fc.constantFrom<BackupMode>('archive', 'mirror');
const safeDateArb = fc.integer({ min: 946684800000, max: 4102444800000 }).map((ts) => new Date(ts).toISOString());

const backupTargetArb = fc.record({
  id:                  fc.nat({ max: 9999 }).map((n) => n + 1),
  profile_id:          fc.nat({ max: 9999 }).map((n) => n + 1),
  name:                fc.stringMatching(/^[A-Za-z][A-Za-z0-9 ]{0,20}[A-Za-z0-9]$/).filter((s) => s.trim().length > 0),
  target_path:         fc.stringMatching(/^\/[a-z0-9/]{1,40}$/),
  target_type:         targetTypeArb,
  remote_name:         fc.option(fc.stringMatching(/^[a-z]{3,10}$/), { nil: null }),
  retention_days:      fc.integer({ min: 1, max: 365 }),
  keep_last:           fc.integer({ min: 1, max: 1000 }),
  overdue:             fc.boolean(),
  encrypted:           fc.boolean(),
  verify_after_backup: fc.boolean(),
  last_verify_status:  fc.option(fc.constantFrom('verified' as const, 'failed' as const), { nil: null }),
  last_verify_message: fc.option(fc.stringMatching(/^[a-z ]{0,20}$/), { nil: null }),
  frequency_hours:     fc.integer({ min: 1, max: 8760 }),
  backup_mode:         backupModeArb,
  enabled:             fc.boolean(),
  last_liveness_ok:    fc.option(fc.boolean(), { nil: null }),
  last_liveness_error: fc.option(fc.stringMatching(/^[a-z ]{0,20}$/), { nil: null }),
  last_backup_at:      fc.option(safeDateArb, { nil: null }),
  last_backup_status:  fc.option(fc.constantFrom('completed', 'failed', 'running', 'skipped'), { nil: null }),
  next_scheduled_at:   fc.option(safeDateArb, { nil: null }),
  created_at:          safeDateArb,
  updated_at:          safeDateArb,
});

const TYPE_LABELS: Record<BackupTargetType, string> = {
  local:         'Local directory',
  remote:        'Same remote',
  custom_remote: 'Custom remote',
};
const MODE_LABELS: Record<BackupMode, string> = { archive: 'Archive', mirror: 'Mirror' };

function wrapper ({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <I18nProvider>{children}</I18nProvider>
    </QueryClientProvider>
  );
}

function renderCard (target: BackupTarget, overrides?: Partial<React.ComponentProps<typeof BackupTargetCard>>) {
  return render(
    <BackupTargetCard
      target={target}
      profileSlug="test-slug"
      onRunNow={vi.fn()}
      onEdit={vi.fn()}
      onToggleEnabled={vi.fn()}
      {...overrides}
    />,
    { wrapper }
  );
}

// --- Tests ---
describe('BackupTargetCard', () => {
  it('renders name, target_type, and backup_mode for any valid target', () => {
    fc.assert(
      fc.property(backupTargetArb, (target) => {
        const { unmount, container } = renderCard(target);
        // Name should appear in the card title
        const title = container.querySelector('[data-slot="card-title"]');
        expect(title?.textContent).toBe(target.name);
        // target_type and backup_mode are shown as translated labels
        expect(container.textContent).toContain(TYPE_LABELS[target.target_type]);
        expect(container.textContent).toContain(MODE_LABELS[target.backup_mode]);
        unmount();
      }),
      { numRuns: 20 }
    );
  });

  it('shows Unreachable badge when last_liveness_ok is false', () => {
    const target: BackupTarget = {
      id:                  1,
      profile_id:          1,
      name:                'Test',
      target_path:         '/bak',
      target_type:         'local',
      remote_name:         null,
      retention_days:      30,
      keep_last:           3,
      overdue:             false,
      encrypted:           false,
      verify_after_backup: false,
      last_verify_status:  null,
      last_verify_message: null,
      frequency_hours:     24,
      backup_mode:         'archive',
      enabled:             true,
      last_liveness_ok:    false,
      last_liveness_error: 'timeout',
      last_backup_at:      null,
      last_backup_status:  null,
      next_scheduled_at:   null,
      created_at:          new Date().toISOString(),
      updated_at:          new Date().toISOString(),
    };
    const { unmount } = renderCard(target);
    expect(screen.getByText('Unreachable')).toBeInTheDocument();
    unmount();
  });

  it('does not show Unreachable badge when last_liveness_ok is true', () => {
    const target: BackupTarget = {
      id:                  1,
      profile_id:          1,
      name:                'Test',
      target_path:         '/bak',
      target_type:         'local',
      remote_name:         null,
      retention_days:      30,
      keep_last:           3,
      overdue:             false,
      encrypted:           false,
      verify_after_backup: false,
      last_verify_status:  null,
      last_verify_message: null,
      frequency_hours:     24,
      backup_mode:         'archive',
      enabled:             true,
      last_liveness_ok:    true,
      last_liveness_error: null,
      last_backup_at:      null,
      last_backup_status:  null,
      next_scheduled_at:   null,
      created_at:          new Date().toISOString(),
      updated_at:          new Date().toISOString(),
    };
    const { unmount } = renderCard(target);
    expect(screen.queryByText('Unreachable')).not.toBeInTheDocument();
    unmount();
  });

  it('Run Now is disabled when isRunning is true', () => {
    const target: BackupTarget = {
      id:                  1,
      profile_id:          1,
      name:                'Test',
      target_path:         '/bak',
      target_type:         'local',
      remote_name:         null,
      retention_days:      30,
      keep_last:           3,
      overdue:             false,
      encrypted:           false,
      verify_after_backup: false,
      last_verify_status:  null,
      last_verify_message: null,
      frequency_hours:     24,
      backup_mode:         'archive',
      enabled:             true,
      last_liveness_ok:    null,
      last_liveness_error: null,
      last_backup_at:      null,
      last_backup_status:  null,
      next_scheduled_at:   null,
      created_at:          new Date().toISOString(),
      updated_at:          new Date().toISOString(),
    };
    const { unmount } = renderCard(target, { isRunning: true });
    const btn = screen.getByText('Running…').closest('button');
    expect(btn).toBeDisabled();
    unmount();
  });

  it('Run Now is disabled when target is not enabled', () => {
    const target: BackupTarget = {
      id:                  1,
      profile_id:          1,
      name:                'Test',
      target_path:         '/bak',
      target_type:         'local',
      remote_name:         null,
      retention_days:      30,
      keep_last:           3,
      overdue:             false,
      encrypted:           false,
      verify_after_backup: false,
      last_verify_status:  null,
      last_verify_message: null,
      frequency_hours:     24,
      backup_mode:         'archive',
      enabled:             false,
      last_liveness_ok:    null,
      last_liveness_error: null,
      last_backup_at:      null,
      last_backup_status:  null,
      next_scheduled_at:   null,
      created_at:          new Date().toISOString(),
      updated_at:          new Date().toISOString(),
    };
    const { unmount } = renderCard(target);
    const btn = screen.getByText('Run Now').closest('button');
    expect(btn).toBeDisabled();
    unmount();
  });

  it('calls onEdit with target when Edit is clicked', () => {
    const onEdit = vi.fn();
    const target: BackupTarget = {
      id:                  1,
      profile_id:          1,
      name:                'Test',
      target_path:         '/bak',
      target_type:         'local',
      remote_name:         null,
      retention_days:      30,
      keep_last:           3,
      overdue:             false,
      encrypted:           false,
      verify_after_backup: false,
      last_verify_status:  null,
      last_verify_message: null,
      frequency_hours:     24,
      backup_mode:         'archive',
      enabled:             true,
      last_liveness_ok:    null,
      last_liveness_error: null,
      last_backup_at:      null,
      last_backup_status:  null,
      next_scheduled_at:   null,
      created_at:          new Date().toISOString(),
      updated_at:          new Date().toISOString(),
    };
    const { unmount } = renderCard(target, { onEdit });
    fireEvent.click(screen.getByText('Edit'));
    expect(onEdit).toHaveBeenCalledWith(target);
    unmount();
  });

  it('calls onRunNow with id when Run Now is clicked', () => {
    const onRunNow = vi.fn();
    const target: BackupTarget = {
      id:                  42,
      profile_id:          1,
      name:                'Test',
      target_path:         '/bak',
      target_type:         'local',
      remote_name:         null,
      retention_days:      30,
      keep_last:           3,
      overdue:             false,
      encrypted:           false,
      verify_after_backup: false,
      last_verify_status:  null,
      last_verify_message: null,
      frequency_hours:     24,
      backup_mode:         'archive',
      enabled:             true,
      last_liveness_ok:    null,
      last_liveness_error: null,
      last_backup_at:      null,
      last_backup_status:  null,
      next_scheduled_at:   null,
      created_at:          new Date().toISOString(),
      updated_at:          new Date().toISOString(),
    };
    const { unmount } = renderCard(target, { onRunNow });
    fireEvent.click(screen.getByText('Run Now'));
    expect(onRunNow).toHaveBeenCalledWith(42);
    unmount();
  });

  it('expand shows History section', async () => {
    // GET .../snapshots returns no snapshots
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve([]) }));
    const target: BackupTarget = {
      id:                  1,
      profile_id:          1,
      name:                'Test',
      target_path:         '/bak',
      target_type:         'local',
      remote_name:         null,
      retention_days:      30,
      keep_last:           3,
      overdue:             false,
      encrypted:           false,
      verify_after_backup: false,
      last_verify_status:  null,
      last_verify_message: null,
      frequency_hours:     24,
      backup_mode:         'archive',
      enabled:             true,
      last_liveness_ok:    null,
      last_liveness_error: null,
      last_backup_at:      null,
      last_backup_status:  null,
      next_scheduled_at:   null,
      created_at:          new Date().toISOString(),
      updated_at:          new Date().toISOString(),
    };
    const { unmount } = renderCard(target);
    fireEvent.click(screen.getByText('History'));
    // BackupHistory should now be rendered
    expect(await screen.findByText('No backup history yet')).toBeInTheDocument();
    unmount();
    vi.unstubAllGlobals();
  });

  it('shows the Overdue badge only for an overdue target', () => {
    const target: BackupTarget = {
      id:                  3,
      profile_id:          1,
      name:                'Weekly',
      target_path:         '/bak',
      target_type:         'local',
      remote_name:         null,
      retention_days:      30,
      keep_last:           5,
      overdue:             true,
      encrypted:           false,
      verify_after_backup: false,
      last_verify_status:  null,
      last_verify_message: null,
      frequency_hours:     24,
      backup_mode:         'archive',
      enabled:             true,
      last_liveness_ok:    true,
      last_liveness_error: null,
      last_backup_at:      null,
      last_backup_status:  'failed',
      next_scheduled_at:   null,
      created_at:          new Date().toISOString(),
      updated_at:          new Date().toISOString(),
    };
    const first = renderCard(target);
    expect(screen.getByText('Overdue')).toBeInTheDocument();
    expect(screen.getByText('Keeps at least 5')).toBeInTheDocument();
    first.unmount();
    renderCard({ ...target, overdue: false });
    expect(screen.queryByText('Overdue')).not.toBeInTheDocument();
  });

  it('Delete asks for confirmation before calling onDelete', () => {
    const onDelete = vi.fn();
    const target: BackupTarget = {
      id:                  7,
      profile_id:          1,
      name:                'Nightly',
      target_path:         '/bak',
      target_type:         'local',
      remote_name:         null,
      retention_days:      30,
      keep_last:           3,
      overdue:             false,
      encrypted:           false,
      verify_after_backup: false,
      last_verify_status:  null,
      last_verify_message: null,
      frequency_hours:     24,
      backup_mode:         'archive',
      enabled:             true,
      last_liveness_ok:    null,
      last_liveness_error: null,
      last_backup_at:      null,
      last_backup_status:  null,
      next_scheduled_at:   null,
      created_at:          new Date().toISOString(),
      updated_at:          new Date().toISOString(),
    };
    const { unmount } = renderCard(target, { onDelete });
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(onDelete).not.toHaveBeenCalled();
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent('Nightly');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    expect(onDelete).toHaveBeenCalledWith(7);
    unmount();
  });
});
