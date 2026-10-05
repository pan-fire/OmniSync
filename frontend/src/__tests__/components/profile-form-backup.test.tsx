import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nProvider, type Locale } from '@/i18n';
import { ProfileForm } from '@/components/profiles/profile-form';
import type { Remote } from '@/types';

// Radix Switch measures itself inside a form; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe () {}
  unobserve () {}
  disconnect () {}
} as unknown as typeof ResizeObserver;

const REMOTES: Remote[] = [
  { name: 'gdrive', type: 'drive', last_verified: null },
  { name: 'nas', type: 'sftp', last_verified: null },
];

let remotesOk: boolean;
let fetchMock: ReturnType<typeof vi.fn>;
const originalFetch = globalThis.fetch;

function respond (data: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(data) });
}

beforeEach(() => {
  remotesOk = true;
  fetchMock = vi.fn((url: string) => {
    if (url === '/api/remotes') return remotesOk ? respond(REMOTES) : respond({ detail: 'rclone missing' }, 500);
    if (url.startsWith('/api/browse/remote')) return respond({ current: 'nas:Backups', parent: 'nas:', entries: [] });
    if (url === '/api/wizard/providers') return respond([]);
    if (url.startsWith('/api/browse/local')) return respond({ current: '/data/docs', parent: '/data', entries: [] });
    return respond({});
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function renderForm (locale: Locale = 'en') {
  const onSubmit = vi.fn();
  const onCancel = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Wrapper ({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}><I18nProvider initialLocale={locale}>{children}</I18nProvider></QueryClientProvider>;
  }
  render(<ProfileForm onSubmit={onSubmit} onCancel={onCancel} />, { wrapper: Wrapper });
  return { onSubmit, onCancel };
}

// fireEvent instead of typing: this form is long and typing it is slow under load.
function fillValid () {
  fireEvent.change(screen.getByLabelText('Profile name'), { target: { value: 'Docs' } });
  fireEvent.change(screen.getByLabelText('Local directory'), { target: { value: '/data/docs' } });
  fireEvent.change(screen.getByLabelText('Remote directory'), { target: { value: 'gdrive:docs' } });
}

function replace (label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe('ProfileForm sync settings', () => {
  it('sends the intervals, retries and rclone arguments as typed', async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderForm();
    fillValid();
    fireEvent.change(screen.getByLabelText('Debounce (seconds)'), { target: { value: '10' } });
    fireEvent.change(screen.getByLabelText('Pull interval (minutes)'), { target: { value: '15' } });
    fireEvent.change(screen.getByLabelText('Max retries'), { target: { value: '7' } });
    fireEvent.change(screen.getByLabelText('rclone filter'), { target: { value: '- *.tmp\n' } });
    fireEvent.change(screen.getByLabelText('rclone arguments'), { target: { value: '--transfers=8\n  \n--checkers=4' } });
    await user.click(screen.getByRole('button', { name: 'Create Profile' }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0]).toMatchObject({
      profile: {
        debounce_seconds:      10,
        pull_interval_minutes: 15,
        max_retries:           7,
        rclone_filter:         ['- *.tmp'],
        rclone_args:           ['--transfers=8', '--checkers=4'],
      },
      initialBackupTarget: null,
    });
  });

  it('cancel hands back without submitting', async () => {
    const user = userEvent.setup();
    const { onSubmit, onCancel } = renderForm();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('opens the folder chooser only for an absolute local folder', async () => {
    const user = userEvent.setup();
    renderForm();
    const choose = screen.getByRole('button', { name: 'Choose folders' });
    expect(choose).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Local directory'), { target: { value: '/data/docs' } });
    expect(choose).toBeEnabled();
    await user.click(choose);
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });
});

describe('ProfileForm remotes', () => {
  // Failing to load the remotes must not leave the picker empty for good.
  it('explains a failed remote list and retries it', async () => {
    remotesOk = false;
    const user = userEvent.setup();
    renderForm();
    expect(await screen.findByText('Could not load the remotes.')).toBeInTheDocument();

    remotesOk = true;
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('button', { name: /gdrive/ })).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/remotes')).toHaveLength(2);
  });

  it('opens the setup wizard when there are no remotes', async () => {
    fetchMock.mockImplementation((url: string) => respond(url === '/api/remotes' || url === '/api/wizard/providers' ? [] : {}));
    const user = userEvent.setup();
    renderForm();
    await user.click(await screen.findByRole('button', { name: 'Setup Wizard' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });
});

describe('ProfileForm initial backup target', () => {
  it('backs up to a custom remote and browses it for the folder', async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderForm();
    fillValid();
    fireEvent.click(screen.getByRole('switch', { name: 'Initial Backup Target' }));
    replace('Name', 'Offsite');
    fireEvent.click(screen.getByLabelText('Custom remote'));
    const pickers = await screen.findAllByRole('button', { name: /nas/ });
    fireEvent.click(pickers[1]);
    expect(screen.getByLabelText('Target path')).toHaveValue('nas:');

    await user.click(screen.getByRole('button', { name: 'Browse for the target folder' }));
    const browser = await screen.findByRole('dialog');
    await within(browser).findByText('nas:Backups');
    await user.click(within(browser).getByRole('button', { name: 'Select this folder' }));
    await waitFor(() => expect(screen.getByLabelText('Target path')).toHaveValue('nas:Backups'));
    fireEvent.click(screen.getByRole('button', { name: 'Create Profile' }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].initialBackupTarget).toMatchObject({
      name:        'Offsite',
      target_path: 'nas:Backups',
      target_type: 'custom_remote',
      remote_name: 'nas',
    });
  });

  it('sends the chosen mode, schedule and limits', async () => {
    const { onSubmit } = renderForm();
    fillValid();
    fireEvent.click(screen.getByRole('switch', { name: 'Initial Backup Target' }));
    replace('Target path', '/backups/docs');
    fireEvent.click(screen.getByLabelText(/^Mirror/, { selector: 'input[name="initial_backup_mode"]' }));
    replace('Retention (days)', '90');
    replace('Frequency (hours)', '12');
    fireEvent.click(screen.getByRole('switch', { name: 'Enable scheduled backups' }));
    fireEvent.click(screen.getByRole('button', { name: 'Create Profile' }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].initialBackupTarget).toEqual({
      name:            'Primary Backup',
      target_path:     '/backups/docs',
      target_type:     'local',
      remote_name:     null,
      retention_days:  90,
      frequency_hours: 12,
      backup_mode:     'mirror',
      enabled:         false,
    });
  });

  it('names each invalid backup field and does not submit', async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderForm();
    fillValid();
    await user.click(screen.getByRole('switch', { name: 'Initial Backup Target' }));
    replace('Name', '');
    await user.click(screen.getByLabelText('Custom remote'));
    replace('Retention (days)', '0');
    replace('Frequency (hours)', '9999');
    await user.click(screen.getByRole('button', { name: 'Create Profile' }));

    expect(screen.getByText('Enter a name for the backup target.')).toBeInTheDocument();
    expect(screen.getByText('Enter the target path.')).toBeInTheDocument();
    expect(screen.getByText('Choose the remote to back up to.')).toBeInTheDocument();
    expect(screen.getByText('Enter a whole number of days from 1 to 365.')).toBeInTheDocument();
    expect(screen.getByText('Enter a whole number of hours from 1 to 8760.')).toBeInTheDocument();
    expect(screen.getByLabelText('Name')).toHaveFocus();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('retries the remote list from the backup remote picker', async () => {
    remotesOk = false;
    const user = userEvent.setup();
    renderForm();
    await user.click(screen.getByRole('switch', { name: 'Initial Backup Target' }));
    await user.click(screen.getByLabelText('Custom remote'));
    const retries = await screen.findAllByRole('button', { name: 'Retry' });
    expect(retries).toHaveLength(2);

    remotesOk = true;
    await user.click(retries[1]);
    expect(await screen.findAllByRole('button', { name: /nas/ })).toHaveLength(2);
  });

  it('shows the backup errors in Persian', async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderForm('fa');
    await user.click(screen.getByRole('switch', { name: 'مقصد پشتیبان‌گیری اولیه' }));
    await user.click(screen.getByRole('button', { name: 'ایجاد پروفایل' }));
    expect(screen.getByText('مسیر مقصد را وارد کنید.')).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
