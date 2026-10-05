import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { I18nProvider } from '@/i18n';
import fa from '@/i18n/locales/fa.json';
import { BatchToolbar } from '@/components/sync/batch-toolbar';

describe('BatchToolbar', () => {
  it.each([
    ['Push selected', 'push'],
    ['Pull selected', 'pull'],
    ['Skip selected', 'skip'],
    ['Mark manual', 'manual'],
  ])('"%s" applies %s to the selection', async (label, action) => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    render(<I18nProvider><BatchToolbar selectedCount={3} onAction={onAction} onClear={vi.fn()} /></I18nProvider>);
    expect(screen.getByText('3 selected')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: label }));
    expect(onAction).toHaveBeenCalledWith(action);
  });

  // While a sync runs the actions are locked, but the selection can still be cleared.
  it('disables the actions but not Clear', async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    const onClear = vi.fn();
    render(<I18nProvider><BatchToolbar selectedCount={1} onAction={onAction} onClear={onClear} disabled /></I18nProvider>);
    expect(screen.getByRole('button', { name: 'Mark manual' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Clear' }));
    expect(onClear).toHaveBeenCalledTimes(1);
    expect(onAction).not.toHaveBeenCalled();
  });

  it('speaks Persian', () => {
    render(<I18nProvider initialLocale="fa"><BatchToolbar selectedCount={2} onAction={vi.fn()} onClear={vi.fn()} /></I18nProvider>);
    expect(screen.getByRole('button', { name: fa.granular.markManualSelected })).toBeInTheDocument();
  });
});
