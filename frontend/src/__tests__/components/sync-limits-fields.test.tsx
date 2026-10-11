import { describe, it, expect, vi } from 'vitest';
import { useState } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { I18nProvider, type Locale } from '@/i18n';
import {
  SyncLimitsFields, initialSyncLimits, syncLimitsErrors, type SyncLimitsValue,
} from '@/components/profiles/sync-limits-fields';
import type { Profile } from '@/types';

// The window switch is a Radix Switch that measures itself.
globalThis.ResizeObserver ??= class {
  observe () {}
  unobserve () {}
  disconnect () {}
} as unknown as typeof ResizeObserver;

/** Holds the value like the profile form does, and shows the errors it would. */
function Harness ({ initial, rcloneArgs = '', onChange }: {
  initial:     SyncLimitsValue;
  rcloneArgs?: string;
  onChange?:   (v: SyncLimitsValue) => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <SyncLimitsFields
      value={value}
      errors={syncLimitsErrors(value, rcloneArgs)}
      onChange={(v) => { setValue(v); onChange?.(v); }}
    />
  );
}

function renderFields (initial: SyncLimitsValue, opts: { rcloneArgs?: string; locale?: Locale } = {}) {
  const onChange = vi.fn();
  render(
    <I18nProvider initialLocale={opts.locale ?? 'en'}>
      <Harness initial={initial} rcloneArgs={opts.rcloneArgs} onChange={onChange} />
    </I18nProvider>
  );
  return { onChange };
}

describe('initialSyncLimits', () => {
  it('reads the profile limit and window', () => {
    const profile = { bwlimit: '10M', sync_window: { days: [5, 6], start: '01:00', end: '05:00' } } as unknown as Profile;
    expect(initialSyncLimits(profile)).toEqual({ bwlimit: '10M', windowOn: true, days: [5, 6], start: '01:00', end: '05:00' });
  });
});

describe('SyncLimitsFields bandwidth limit', () => {
  it('shows the help, then the error for an invalid limit', () => {
    renderFields(initialSyncLimits());
    const input = screen.getByLabelText('Bandwidth limit');
    expect(screen.getByText(/Optional\. A rate such as 10M/)).toBeInTheDocument();
    expect(input).not.toHaveAttribute('aria-invalid');

    fireEvent.change(input, { target: { value: 'fast please' } });
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input).toHaveAccessibleDescription(/Not a valid limit/);
  });

  it('warns when the rclone arguments set the limit too', () => {
    renderFields({ ...initialSyncLimits(), bwlimit: '1M' }, { rcloneArgs: '--bwlimit 2M' });
    expect(screen.getByText('--bwlimit is also set in the rclone arguments. Remove one of them.')).toBeInTheDocument();
  });
});

describe('SyncLimitsFields sync window', () => {
  it('switching it on shows the days and times', async () => {
    const user = userEvent.setup();
    const { onChange } = renderFields(initialSyncLimits());
    expect(screen.queryByRole('group', { name: 'Days' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('switch', { name: 'Sync window' }));

    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ windowOn: true }));
    const days = within(screen.getByRole('group', { name: 'Days' })).getAllByRole('button');
    expect(days.map((d) => d.textContent)).toEqual(['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']);
    expect(days.every((d) => d.getAttribute('aria-pressed') === 'true')).toBe(true);
    expect(screen.getByLabelText('From')).toHaveValue('22:00');
    expect(screen.getByLabelText('Until')).toHaveValue('06:00');
    expect(screen.getByText(/Server time/)).toBeInTheDocument();
  });

  it('toggles days and asks for at least one', async () => {
    const user = userEvent.setup();
    const { onChange } = renderFields({ ...initialSyncLimits(), windowOn: true, days: [0] });
    const monday = screen.getByRole('button', { name: 'Mon' });

    await user.click(monday);
    expect(monday).toHaveAttribute('aria-pressed', 'false');
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ days: [] }));
    expect(screen.getByText('Choose at least one day.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Sun' }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ days: [6] }));
    expect(screen.queryByText('Choose at least one day.')).not.toBeInTheDocument();
  });

  it('refuses a window that starts when it ends', () => {
    const { onChange } = renderFields({ ...initialSyncLimits(), windowOn: true });
    fireEvent.change(screen.getByLabelText('From'), { target: { value: '06:00' } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ start: '06:00' }));
    expect(screen.getByText('Enter a start and an end time that differ.')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Until'), { target: { value: '07:00' } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ end: '07:00' }));
    expect(screen.queryByText('Enter a start and an end time that differ.')).not.toBeInTheDocument();
  });

  it('names the days in Persian', () => {
    renderFields({ ...initialSyncLimits(), windowOn: true }, { locale: 'fa' });
    expect(screen.getByLabelText('محدودیت پهنای باند')).toBeInTheDocument();
    const days = within(screen.getByRole('group', { name: 'روزها' })).getAllByRole('button');
    const expected = new Intl.DateTimeFormat('fa', { weekday: 'short', timeZone: 'UTC' }).format(Date.UTC(2026, 9, 5, 12));
    expect(days[0]).toHaveTextContent(expected);
  });
});
