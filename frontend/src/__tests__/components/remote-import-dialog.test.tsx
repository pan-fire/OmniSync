import { describe, it, expect, vi, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider } from '@/i18n';
import { MAX_IMPORT_LENGTH, RemoteImportDialog } from '@/components/remotes/remote-import-dialog';
import { stubBackend } from '../helpers/fake-backend';

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const CONF = '[gdrive]\ntype = drive\n';

function renderDialog (existingNames: string[] = [], locale: 'en' | 'fa' = 'en') {
  const onOpenChange = vi.fn();
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <I18nProvider initialLocale={locale}>
        <RemoteImportDialog open onOpenChange={onOpenChange} existingNames={existingNames} />
      </I18nProvider>
    </QueryClientProvider>
  );
  return onOpenChange;
}

/** A file whose text is `text`; `size` overrides the size the input reports. */
function confFile (text: string, size?: number) {
  const file = new File([text], 'rclone.conf', { type: 'text/plain' });
  if (size !== undefined) Object.defineProperty(file, 'size', { value: size });
  return file;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('RemoteImportDialog input', () => {
  it('reads a chosen file into the text box', async () => {
    stubBackend({});
    const user = userEvent.setup();
    renderDialog();
    await user.upload(screen.getByLabelText('Choose the file'), confFile(CONF));
    await waitFor(() => expect(screen.getByLabelText('or paste its contents')).toHaveValue(CONF));
    expect(screen.getByRole('button', { name: 'Check file' })).toBeEnabled();
  });

  it('refuses a file that is too large without reading it', async () => {
    stubBackend({});
    renderDialog();
    fireEvent.change(screen.getByLabelText('Choose the file'), { target: { files: [confFile('x', MAX_IMPORT_LENGTH + 1)] } });
    expect(await screen.findByRole('alert')).toHaveTextContent('The file is too large');
    expect(screen.getByLabelText('or paste its contents')).toHaveValue('');
  });

  it('refuses pasted text that is too large before sending it', async () => {
    const { requests } = stubBackend({});
    renderDialog();
    fireEvent.change(screen.getByLabelText('or paste its contents'), { target: { value: 'x'.repeat(MAX_IMPORT_LENGTH + 1) } });
    fireEvent.click(screen.getByRole('button', { name: 'Check file' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The file is too large');
    expect(requests).toEqual([]);
  });

  it('Cancel closes the dialog', async () => {
    stubBackend({});
    const user = userEvent.setup();
    const onOpenChange = renderDialog();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('is titled in Persian', () => {
    stubBackend({});
    renderDialog([], 'fa');
    expect(screen.getByRole('dialog', { name: 'وارد کردن یک rclone.conf' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'بررسی فایل' })).toBeDisabled();
  });
});

describe('RemoteImportDialog preview and import', () => {
  async function check (user: ReturnType<typeof userEvent.setup>) {
    await user.type(screen.getByLabelText('or paste its contents'), 'conf');
    await user.click(screen.getByRole('button', { name: 'Check file' }));
  }

  it('says when the file holds no remotes', async () => {
    stubBackend({ 'POST /remotes/import/preview': { remotes: [], errors: [] } });
    const user = userEvent.setup();
    renderDialog();
    await check(user);
    expect(await screen.findByRole('alert')).toHaveTextContent('The file holds no remotes.');
  });

  it('shows a failed check', async () => {
    stubBackend({});
    const user = userEvent.setup();
    renderDialog();
    await check(user);
    expect(await screen.findByRole('alert')).toHaveTextContent('no route for POST /remotes/import/preview');
  });

  // A second clash on "-imported" moves on to "-imported-2".
  it('finds a free name past earlier imports, and refuses two remotes with one name', async () => {
    stubBackend({
      'POST /remotes/import/preview': {
        remotes: [
          { name: 'gdrive', type: 'drive', exists: true, problems: [], keys: [] },
          { name: 'nas', type: '', exists: false, problems: [], keys: [] },
        ],
        errors: [],
      },
    });
    const user = userEvent.setup();
    renderDialog(['gdrive', 'gdrive-imported']);
    await check(user);

    expect(await screen.findByLabelText('Import as')).toHaveValue('nas');
    expect(screen.getByText('?')).toBeInTheDocument();
    // A clashing remote starts unselected; selecting it offers the free name.
    await user.click(screen.getByRole('checkbox', { name: 'Import gdrive' }));
    expect(screen.getByLabelText('Import as', { selector: '#import-name-gdrive' })).toHaveValue('gdrive-imported-2');

    const nasName = screen.getByLabelText('Import as', { selector: '#import-name-nas' });
    await user.clear(nasName);
    await user.type(nasName, 'gdrive-imported-2');
    expect(screen.getAllByText('Two remotes would get this name.')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Import 2 remotes' })).toBeDisabled();
  });

  it('shows a refused import and Back returns to the file', async () => {
    stubBackend({
      'POST /remotes/import/preview': { remotes: [{ name: 'nas', type: 'sftp', exists: false, problems: [], keys: [] }], errors: [] },
    });
    const user = userEvent.setup();
    renderDialog();
    await check(user);

    await user.click(await screen.findByRole('button', { name: 'Import 1 remote' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('no route for POST /remotes/import');

    await user.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByLabelText('or paste its contents')).toHaveValue('conf');
  });
});
