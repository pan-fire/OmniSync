import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { I18nProvider, type Locale } from '@/i18n';
import fa from '@/i18n/locales/fa.json';
import { ConflictDialog } from '@/components/sync/conflict-dialog';
import type { FileDiff } from '@/types';

const FILE: FileDiff = {
  path:            '/data/docs/report.odt',
  category:        'modified_both',
  local_size:      10,
  remote_size:     12,
  local_mod_time:  '2026-01-02T10:00:00Z',
  remote_mod_time: '2026-01-02T09:00:00Z',
  is_conflict:     true,
  manual_flag:     false,
};

function renderDialog (remaining = 0, locale: Locale = 'en', file: FileDiff | null = FILE) {
  const onResolve = vi.fn();
  const onClose = vi.fn();
  render(
    <I18nProvider initialLocale={locale}>
      <ConflictDialog file={file} remaining={remaining} onResolve={onResolve} onClose={onClose} />
    </I18nProvider>
  );
  return { onResolve, onClose };
}

describe('ConflictDialog', () => {
  it('is closed without a file', () => {
    renderDialog(0, 'en', null);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  // Nothing is resolved until the user picks one of the four choices.
  it.each([
    ['Keep local version (push)', 'push'],
    ['Keep remote version (pull)', 'pull'],
    ['Keep both versions', 'keep_both'],
    ['Mark manual', 'manual'],
  ])('"%s" resolves the file with %s', async (label, action) => {
    const user = userEvent.setup();
    const { onResolve, onClose } = renderDialog();
    expect(screen.getByRole('dialog')).toHaveTextContent('/data/docs/report.odt');
    expect(onResolve).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: label }));
    expect(onResolve).toHaveBeenCalledWith(action, false);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('a single conflict offers Cancel and no "apply to remaining"', async () => {
    const user = userEvent.setup();
    const { onResolve, onClose } = renderDialog();
    expect(screen.queryByRole('checkbox')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onResolve).not.toHaveBeenCalled();
  });

  it('with queued conflicts, the choice can apply to all of them', async () => {
    const user = userEvent.setup();
    const { onResolve } = renderDialog(2);
    expect(screen.getByText('2 more conflicting files are waiting for a decision.')).toBeInTheDocument();
    const apply = screen.getByRole('checkbox', { name: 'Use this choice for the 2 remaining conflicts too' });
    await user.click(apply);
    expect(apply).toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Keep remote version (pull)' }));
    expect(onResolve).toHaveBeenCalledWith('pull', true);
    // The tick does not carry over to the next conflict.
    expect(apply).not.toBeChecked();
  });

  it('leaving the queued conflicts calls onClose and resets the tick', async () => {
    const user = userEvent.setup();
    const { onClose, onResolve } = renderDialog(1);
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: 'Leave the conflicts for now' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onResolve).not.toHaveBeenCalled();
  });

  it('Escape closes the dialog without resolving anything', async () => {
    const user = userEvent.setup();
    const { onClose, onResolve } = renderDialog(1);
    await user.click(screen.getByRole('checkbox'));
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onResolve).not.toHaveBeenCalled();
  });

  it('speaks Persian right to left', () => {
    renderDialog(0, 'fa');
    expect(document.documentElement).toHaveAttribute('dir', 'rtl');
    expect(screen.getByRole('heading', { name: fa.granular.conflictTitle })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: fa.granular.keepLocal })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: fa.common.cancel })).toBeInTheDocument();
  });
});
