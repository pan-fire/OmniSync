import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nProvider } from '@/i18n';
import { ProfileForm } from '@/components/profiles/profile-form';
import { validateProfileForm } from '@/lib/profile-validation';
import type { Profile } from '@/types';

// Radix Switch measures itself inside a form; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe () {}
  unobserve () {}
  disconnect () {}
} as unknown as typeof ResizeObserver;

const VALID = {
  name:                  'Docs',
  local_dir:             '/data/docs',
  remote_dir:            'gdrive:docs',
  debounce_seconds:      '5',
  pull_interval_minutes: '5',
  max_retries:           '3',
};

describe('validateProfileForm mirrors the backend checks', () => {
  it('accepts valid values', () => {
    expect(validateProfileForm(VALID)).toEqual({});
    expect(validateProfileForm({ ...VALID, remote_dir: 'my_remote-2:' })).toEqual({});
  });

  it.each([
    ['local_dir', 'data/docs'],
    ['local_dir', '~/docs'],
    ['remote_dir', 'docs'],
    ['remote_dir', ':docs'],
    ['remote_dir', '-rf:docs'],
    ['remote_dir', 'gdrive/sub:docs'],
    ['debounce_seconds', '0'],
    ['debounce_seconds', '1.5'],
    ['debounce_seconds', ''],
    ['pull_interval_minutes', '0'],
    ['max_retries', '0'],
    ['max_retries', '-1'],
    ['name', '  '],
  ])('rejects %s = %j', (field, value) => {
    const errors = validateProfileForm({ ...VALID, [field]: value });
    expect(Object.keys(errors)).toEqual([field]);
  });
});

describe('ProfileForm', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve([]) }) as unknown as typeof fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  function wrapper ({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider>{children}</I18nProvider>
      </QueryClientProvider>
    );
  }

  it('shows clear messages and does not submit invalid values', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<ProfileForm onSubmit={onSubmit} onCancel={vi.fn()} />, { wrapper });

    await user.type(screen.getByLabelText('Profile name'), 'Docs');
    await user.type(screen.getByLabelText('Local directory'), 'relative/path');
    await user.type(screen.getByLabelText('Remote directory'), 'no-colon');
    const debounce = screen.getByLabelText('Debounce (seconds)');
    await user.clear(debounce);
    await user.type(debounce, '0');
    await user.click(screen.getByRole('button', { name: 'Create Profile' }));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText(/Use an absolute path that starts with \//)).toBeInTheDocument();
    expect(screen.getByText(/Use <remote>:<path>/)).toBeInTheDocument();
    expect(screen.getByText('Enter a whole number of seconds, at least 1.')).toBeInTheDocument();
    expect(screen.getByLabelText('Local directory')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByLabelText('Local directory')).toHaveFocus();
  });

  it('submits once the values are valid', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<ProfileForm onSubmit={onSubmit} onCancel={vi.fn()} />, { wrapper });

    await user.type(screen.getByLabelText('Profile name'), 'Docs');
    await user.type(screen.getByLabelText('Local directory'), '/data/docs');
    await user.type(screen.getByLabelText('Remote directory'), 'gdrive:docs');
    await user.click(screen.getByRole('button', { name: 'Create Profile' }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].profile).toMatchObject({
      name:                  'Docs',
      local_dir:             '/data/docs',
      remote_dir:            'gdrive:docs',
      debounce_seconds:      5,
      pull_interval_minutes: 5,
      max_retries:           3,
    });
  });

  it('an initial "Same remote" backup needs remote:path and starts with the profile remote', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<ProfileForm onSubmit={onSubmit} onCancel={vi.fn()} />, { wrapper });

    await user.type(screen.getByLabelText('Profile name'), 'Docs');
    await user.type(screen.getByLabelText('Local directory'), '/data/docs');
    await user.type(screen.getByLabelText('Remote directory'), 'gdrive:docs');
    await user.click(screen.getByRole('switch', { name: 'Initial Backup Target' }));
    await user.click(screen.getByLabelText('Same remote'));

    const path = screen.getByLabelText('Target path');
    expect(path).toHaveValue('gdrive:');
    expect(screen.getByText(/Type the full rclone path, remote name and folder, e\.g\. gdrive:Backups\/my-profile/)).toBeInTheDocument();

    await user.clear(path);
    await user.type(path, 'Backups/docs');
    await user.click(screen.getByRole('button', { name: 'Create Profile' }));
    expect(await screen.findByText('Enter the full rclone path as remote:folder, e.g. gdrive:Backups/docs.')).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();

    await user.clear(path);
    await user.type(path, 'gdrive:Backups/docs');
    await user.click(screen.getByRole('button', { name: 'Create Profile' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].initialBackupTarget).toMatchObject({
      target_type: 'remote',
      target_path: 'gdrive:Backups/docs',
      remote_name: null,
    });
  });

  const fillValid = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.type(screen.getByLabelText('Profile name'), 'Docs');
    await user.type(screen.getByLabelText('Local directory'), '/data/docs');
    await user.type(screen.getByLabelText('Remote directory'), 'gdrive:docs');
  };

  it('a new profile defaults to two-way and sends it', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<ProfileForm onSubmit={onSubmit} onCancel={vi.fn()} />, { wrapper });

    const twoWay = screen.getByRole('radio', { name: /^Two-way \(recommended\)/ });
    const mirror = screen.getByRole('radio', { name: /^Mirror: one-way push\/pull only/ });
    expect(twoWay).toBeChecked();
    expect(mirror).not.toBeChecked();
    // Each choice is explained in plain words.
    expect(screen.getByText(/a file changed on both sides keeps both versions/)).toBeInTheDocument();
    expect(screen.getByText(/The side that syncs last wins/)).toBeInTheDocument();

    await fillValid(user);
    await user.click(screen.getByRole('button', { name: 'Create Profile' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].profile).toMatchObject({ sync_mode: 'two_way' });
  });

  it('a new profile can be created as a mirror', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<ProfileForm onSubmit={onSubmit} onCancel={vi.fn()} />, { wrapper });

    await fillValid(user);
    await user.click(screen.getByRole('radio', { name: /^Mirror: one-way push\/pull only/ }));
    await user.click(screen.getByRole('button', { name: 'Create Profile' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].profile).toMatchObject({ sync_mode: 'mirror' });
  });

  const EXISTING: Profile = {
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
    sync_mode:             'mirror',
  };

  it('editing keeps an existing mirror profile a mirror and does not resend the mode', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<ProfileForm profile={EXISTING} onSubmit={onSubmit} onCancel={vi.fn()} />, { wrapper });

    expect(screen.getByRole('radio', { name: /^Mirror: one-way push\/pull only/ })).toBeChecked();
    expect(screen.queryByTestId('sync-mode-change-note')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].profile).not.toHaveProperty('sync_mode');
  });

  it('switching an existing profile to two-way says the next sync is a resync and sends the mode', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<ProfileForm profile={EXISTING} onSubmit={onSubmit} onCancel={vi.fn()} />, { wrapper });

    await user.click(screen.getByRole('radio', { name: /^Two-way \(recommended\)/ }));
    expect(screen.getByTestId('sync-mode-change-note')).toHaveTextContent(/the next sync is a resync/);
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].profile).toMatchObject({ sync_mode: 'two_way' });
  });
});
