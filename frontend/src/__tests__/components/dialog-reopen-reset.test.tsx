import { describe, it, expect, vi, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { I18nProvider } from '@/i18n';
import { DirBrowser } from '@/components/shared/dir-browser';
import { FolderRulesDialog } from '@/components/profiles/folder-rules-dialog';
import { stubBackend } from '../helpers/fake-backend';

// A dialog that keeps its content mounted while closed, as Radix does during
// the close animation. Reopening then must still start from fresh state:
// the reset follows the opening itself, not the content's unmounting.
vi.mock('@/components/ui/dialog', () => {
  const Pass = ({ children }: { children?: ReactNode }) => <div>{children}</div>;
  return {
    Dialog:            ({ children }: { children?: ReactNode }) => <div>{children}</div>,
    DialogContent:     Pass,
    DialogHeader:      Pass,
    DialogFooter:      Pass,
    DialogTitle:       Pass,
    DialogDescription: Pass,
  };
});
vi.mock('@/components/ui/scroll-area', () => ({ ScrollArea: ({ children }: { children?: ReactNode }) => <div>{children}</div> }));

function Wrapper ({ children }: { children: ReactNode }) {
  return <I18nProvider initialLocale="en">{children}</I18nProvider>;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('dialogs reopened before their content unmounts', () => {
  it('DirBrowser starts over at the initial path', async () => {
    const { requests } = stubBackend({
      'GET /browse/local': (url: URL) => (url.searchParams.get('path') === '/data/photos'
        ? { current: '/data/photos', parent: '/data', entries: [] }
        : { current: '/data', parent: null, entries: [{ name: 'photos', path: '/data/photos' }] }),
    });
    const user = userEvent.setup();
    const props = { onOpenChange: vi.fn(), mode: 'local' as const, initialPath: '/data' };
    const { rerender } = render(<DirBrowser open {...props} />, { wrapper: Wrapper });
    await user.click(await screen.findByRole('button', { name: 'photos' }));
    expect(await screen.findByText('/data/photos')).toBeInTheDocument();

    rerender(<DirBrowser open={false} {...props} />);
    expect(screen.getByText('/data/photos')).toBeInTheDocument();
    rerender(<DirBrowser open {...props} />);
    expect(screen.queryByText('/data/photos')).not.toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'photos' })).toBeInTheDocument();
    expect(requests).toHaveLength(3);
  });

  it('FolderRulesDialog drops unapplied choices', async () => {
    stubBackend({
      'GET /browse/local': { current: '/sync', parent: null, entries: [{ name: 'Big', path: '/sync/Big' }] },
    });
    const user = userEvent.setup();
    const props = { onOpenChange: vi.fn(), localDir: '/sync', rules: '', syncMode: 'mirror' as const, onApply: vi.fn() };
    const { rerender } = render(<FolderRulesDialog open {...props} />, { wrapper: Wrapper });
    await user.click(await screen.findByRole('checkbox', { name: 'Big' }));
    expect(screen.getByRole('checkbox', { name: 'Big' })).toHaveAttribute('aria-checked', 'false');

    rerender(<FolderRulesDialog open={false} {...props} />);
    rerender(<FolderRulesDialog open {...props} />);
    expect(await screen.findByRole('checkbox', { name: 'Big' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByTestId('folder-rules-preview')).not.toHaveTextContent('/Big/**');
  });
});
