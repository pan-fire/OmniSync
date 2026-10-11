import { describe, it, expect, vi, afterEach } from 'vitest';
import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider } from '@/i18n';
import { ProviderFieldInput } from '@/components/config/wizard/provider-field-input';
import type { ProviderField } from '@/types';
import { stubBackend } from '../helpers/fake-backend';

// The folder browser has its own tests; this one hands back a chosen folder.
vi.mock('@/components/shared/dir-browser', () => ({
  DirBrowser: ({ open, initialPath, onSelect }: { open: boolean; initialPath: string; onSelect: (p: string) => void }) => (open
    ? <div role="dialog" aria-label="browser">at {initialPath}<button onClick={() => onSelect('nas:vault/new')}>pick</button></div>
    : null),
}));

/** The input with its value kept in state, as the wizard does; `seen` records the changes. */
function Harness ({ field, initial = '', ownName, seen }: { field: ProviderField; initial?: string; ownName?: string; seen: string[] }) {
  const [value, setValue] = useState(initial);
  return (
    <ProviderFieldInput
      field={field}
      value={value}
      ownName={ownName}
      onChange={(v) => { seen.push(v); setValue(v); }}
    />
  );
}

function renderField (field: ProviderField, opts: { initial?: string; ownName?: string; locale?: 'en' | 'fa' } = {}) {
  const seen: string[] = [];
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <I18nProvider initialLocale={opts.locale ?? 'en'}>
        <Harness field={field} initial={opts.initial} ownName={opts.ownName} seen={seen} />
      </I18nProvider>
    </QueryClientProvider>
  );
  return seen;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ProviderFieldInput', () => {
  it('a select offers its options, and "—" when the field is optional', async () => {
    stubBackend({});
    const user = userEvent.setup();
    const seen = renderField({
      name: 'region', label: 'Region', field_type: 'select', required: false, help_text: 'Where the bucket is.', options: ['eu', 'us'],
    });
    const select = screen.getByLabelText('Region');
    expect(screen.getByRole('option', { name: '—' })).toBeInTheDocument();
    expect(screen.getByText('Where the bucket is.')).toBeInTheDocument();
    await user.selectOptions(select, 'us');
    expect(seen).toEqual(['us']);
  });

  it('a required select has no empty choice', () => {
    stubBackend({});
    renderField({ name: 'mode', label: 'Mode', field_type: 'select', required: true, help_text: '', options: ['a'] });
    expect(screen.queryByRole('option', { name: '—' })).not.toBeInTheDocument();
  });

  it('a password can be shown and hidden again', async () => {
    stubBackend({});
    const user = userEvent.setup();
    renderField({ name: 'pass', label: 'Password', field_type: 'password', required: true, help_text: '' });
    const input = screen.getByLabelText(/^Password/);
    expect(input).toHaveAttribute('type', 'password');
    await user.click(screen.getByRole('button', { name: 'Toggle password visibility' }));
    expect(input).toHaveAttribute('type', 'text');
    await user.click(screen.getByRole('button', { name: 'Toggle password visibility' }));
    expect(input).toHaveAttribute('type', 'password');
  });

  describe('remote and folder', () => {
    const FIELD: ProviderField = { name: 'remote', label: 'Target', field_type: 'remote_path', required: true, help_text: '' };

    it('picks a remote (not the one being set up), types a folder and browses for one', async () => {
      stubBackend({
        'GET /remotes': [
          { name: 'nas', type: 'sftp', last_verified: null },
          { name: 'vault', type: 'crypt', last_verified: null },
        ],
      });
      const user = userEvent.setup();
      const seen = renderField(FIELD, { ownName: 'vault' });

      const remote = await screen.findByRole('combobox', { name: 'Remote' });
      await screen.findByRole('option', { name: 'nas (sftp)' });
      expect(screen.queryByRole('option', { name: 'vault (crypt)' })).not.toBeInTheDocument();
      expect(screen.getByRole('textbox', { name: 'Folder' })).toBeDisabled();
      expect(screen.getByRole('button', { name: 'Browse' })).toBeDisabled();

      await user.selectOptions(remote, 'nas');
      await user.type(screen.getByRole('textbox', { name: 'Folder' }), 'x');
      expect(seen).toEqual(['nas:', 'nas:x']);

      await user.click(screen.getByRole('button', { name: 'Browse' }));
      expect(screen.getByRole('dialog', { name: 'browser' })).toHaveTextContent('at nas:');
      await user.click(screen.getByRole('button', { name: 'pick' }));
      expect(seen.at(-1)).toBe('nas:vault/new');

      // Choosing no remote clears the value.
      await user.selectOptions(remote, '');
      expect(seen.at(-1)).toBe('');
    });

    it('says to set up a remote first when there is none', async () => {
      stubBackend({ 'GET /remotes': [] });
      renderField(FIELD);
      expect(await screen.findByText('Set up the remote that should hold the encrypted files first.')).toBeInTheDocument();
    });

    it('is labelled in Persian, with the folder kept left to right', async () => {
      stubBackend({ 'GET /remotes': [] });
      renderField(FIELD, { locale: 'fa', initial: 'nas:docs' });
      expect(screen.getByRole('option', { name: 'یک ریموت انتخاب کنید' })).toBeInTheDocument();
      expect(screen.getByDisplayValue('docs').closest('[dir]')).toHaveAttribute('dir', 'ltr');
    });
  });
});
