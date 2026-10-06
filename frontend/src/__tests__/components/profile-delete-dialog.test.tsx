import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { I18nProvider, type Locale } from '@/i18n';
import { ProfileDeleteDialog } from '@/components/profiles/profile-delete-dialog';

const PROFILE = { name: 'Docs', local_dir: '/data/docs', remote_dir: 'gdrive:docs' };

function renderDialog (locale: Locale = 'en', isPending = false) {
  const onCancel = vi.fn();
  const onConfirm = vi.fn();
  render(
    <I18nProvider initialLocale={locale}>
      <ProfileDeleteDialog profile={PROFILE} onCancel={onCancel} onConfirm={onConfirm} isPending={isPending} />
    </I18nProvider>
  );
  return { onCancel, onConfirm, dialog: screen.getByRole('dialog') };
}

describe('ProfileDeleteDialog', () => {
  it('lists both folders it keeps', () => {
    const { dialog } = renderDialog();
    const keeps = within(dialog).getByTestId('profile-delete-keeps');
    expect(keeps).toHaveTextContent('/data/docs');
    expect(keeps).toHaveTextContent('gdrive:docs');
  });

  // Escape or a click outside is a cancel, never a delete.
  it('Escape cancels without confirming', async () => {
    const user = userEvent.setup();
    const { onCancel, onConfirm } = renderDialog();
    await user.keyboard('{Escape}');
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('cannot confirm twice while the delete runs', () => {
    const { dialog } = renderDialog('en', true);
    expect(within(dialog).getByRole('button', { name: 'Delete profile' })).toBeDisabled();
  });

  it('says in Persian that no files are deleted', async () => {
    const user = userEvent.setup();
    const { dialog, onConfirm } = renderDialog('fa');
    expect(dialog).toHaveTextContent('هیچ فایلی حذف نمی‌شود');
    await user.click(within(dialog).getByRole('button', { name: 'حذف پروفایل' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
