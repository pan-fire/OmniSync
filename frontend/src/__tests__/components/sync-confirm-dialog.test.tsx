import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider } from '@/i18n';
import en from '@/i18n/locales/en.json';
import { useConfirmedSync } from '@/components/sync/sync-confirm-dialog';
import type { SyncDirection, SyncPreview, SyncPreviewCounts } from '@/types';
import { stubBackend } from '../helpers/fake-backend';

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const NONE: SyncPreviewCounts = { deletes: 0, replaces: 0, creates: 0, exceeds_max_delete: false };

function preview (overrides: Partial<SyncPreview> = {}): SyncPreview {
  return {
    push:       NONE,
    pull:       NONE,
    excluded:   0,
    max_delete: 50,
    error:      null,
    sync_mode:  'mirror',
    two_way:    null,
    ...overrides,
  };
}

function wrapper ({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return <QueryClientProvider client={qc}><I18nProvider>{children}</I18nProvider></QueryClientProvider>;
}

function Harness ({ direction }: { direction: SyncDirection }) {
  const { request, dialog } = useConfirmedSync();
  return (
    <>
      <button onClick={() => request(direction, [{ slug: 'docs', name: 'Docs' }])}>open</button>
      {dialog}
    </>
  );
}

/**
 * Open the confirmation for profile `docs` with `previewRoute` as the answer
 * to its preview (undefined: the preview request fails with 404).
 */
function renderDialog (direction: SyncDirection, previewRoute?: SyncPreview) {
  const starts: unknown[] = [];
  stubBackend({
    ...(previewRoute && { 'POST /profiles/docs/sync/preview': previewRoute }),
    'POST /profiles/docs/sync/start': (_: URL, init?: globalThis.RequestInit) => {
      starts.push(JSON.parse(String(init?.body)));
      // A refusal, so the test does not go on to poll the job.
      return { job_id: 1, state: 'error', error: 'stub' };
    },
  });
  render(<Harness direction={direction} />, { wrapper });
  fireEvent.click(screen.getByText('open'));
  return starts;
}

const confirmButton = (label: string) => screen.getByRole('button', { name: label });

describe('SyncConfirmDialog', () => {
  it('sends a confirmed push with force once the preview is in', async () => {
    const starts = renderDialog('push', preview());
    await screen.findByText('Delete limit: 50 files');
    const button = confirmButton(en.syncConfirm.confirmPush);
    expect(button).toBeEnabled();
    expect(button).toHaveAttribute('data-variant', 'default');
    expect(screen.queryByLabelText(en.syncConfirm.confirmWithoutPreview)).toBeNull();
    fireEvent.click(button);
    await waitFor(() => expect(starts).toEqual([{ direction: 'push', force: true }]));
  });

  it('shows the local names the sync cannot carry as they are', async () => {
    renderDialog('pull', preview({
      warnings: [
        { code: 'name_collision', count: 1, paths: ['café.txt'] },
        { code: 'symlink_shadow', count: 3, paths: ['clash.txt', 'photos/'] },
      ],
    }));
    const list = await screen.findByTestId('sync-warnings');
    expect(list).toHaveTextContent(en.syncWarnings.name_collision_one.replace('{{count}}', '1'));
    expect(list).toHaveTextContent('café.txt');
    expect(list).toHaveTextContent(en.syncWarnings.symlink_shadow_other.replace('{{count}}', '3'));
    expect(list).toHaveTextContent('photos/');
    expect(list).toHaveTextContent(en.syncWarnings.more_one.replace('{{count}}', '1'));
    // A warning is no reason to block the sync.
    expect(confirmButton(en.syncConfirm.confirmPull)).toBeEnabled();
  });

  it.each<[SyncDirection, Partial<SyncPreview>]>([
    ['push', { push: { ...NONE, deletes: 3 } }],
    ['pull', { pull: { ...NONE, deletes: 1 } }],
    ['two_way', {
      sync_mode: 'two_way',
      two_way:   { local: NONE, remote: { ...NONE, deletes: 2 }, conflicts: 0, resync: false, resync_required: false, error: null },
    }],
  ])('makes the %s button destructive when the preview reports deletions', async (direction, overrides) => {
    renderDialog(direction, preview(overrides));
    const label = { push: en.syncConfirm.confirmPush, pull: en.syncConfirm.confirmPull, two_way: en.syncConfirm.confirmTwoWay }[direction];
    await waitFor(() => expect(confirmButton(label)).toHaveAttribute('data-variant', 'destructive'));
    expect(confirmButton(label)).toBeEnabled();
  });

  it.each<[string, SyncDirection, SyncPreview | undefined]>([
    ['the preview request fails', 'pull', undefined],
    ['the preview reports an error', 'push', preview({ error: 'rclone check failed' })],
    ['the two-way dry run fails', 'two_way', preview({
      sync_mode: 'two_way',
      two_way:   { local: NONE, remote: NONE, conflicts: 0, resync: false, resync_required: false, error: 'dry run failed' },
    })],
  ])('asks for an acknowledgement before forcing a sync when %s', async (_, direction, previewRoute) => {
    const starts = renderDialog(direction, previewRoute);
    const checkbox = await screen.findByLabelText(en.syncConfirm.confirmWithoutPreview);
    expect(screen.getByText(en.syncConfirm.noPreviewWarning)).toBeInTheDocument();
    const label = { push: en.syncConfirm.confirmPush, pull: en.syncConfirm.confirmPull, two_way: en.syncConfirm.confirmTwoWay }[direction];
    const button = confirmButton(label);
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('data-variant', 'destructive');

    fireEvent.click(button);
    expect(starts).toEqual([]);

    fireEvent.click(checkbox);
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() => expect(starts).toEqual([{ direction, force: true }]));
  });

  it('starts the next confirmation unacknowledged', async () => {
    renderDialog('pull');
    fireEvent.click(await screen.findByLabelText(en.syncConfirm.confirmWithoutPreview));
    expect(confirmButton(en.syncConfirm.confirmPull)).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: en.common.cancel }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    fireEvent.click(screen.getByText('open'));
    await screen.findByLabelText(en.syncConfirm.confirmWithoutPreview);
    expect(confirmButton(en.syncConfirm.confirmPull)).toBeDisabled();
  });
});
