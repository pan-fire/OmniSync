import { describe, it, expect, vi, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { I18nProvider } from '@/i18n';
import { DirBrowser } from '@/components/shared/dir-browser';
import { stubBackend } from '../helpers/fake-backend';

// The scroll area measures itself; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe () {}
  unobserve () {}
  disconnect () {}
} as unknown as typeof ResizeObserver;

// A two-level tree: /data holds "photos", which holds nothing.
function stubTree () {
  return stubBackend({
    'GET /browse/local': (url: URL) => {
      const path = url.searchParams.get('path');
      return path === '/data/photos'
        ? { current: '/data/photos', parent: '/data', entries: [] }
        : { current: '/data', parent: null, entries: [{ name: 'photos', path: '/data/photos' }] };
    },
  });
}

function Wrapper ({ children }: { children: ReactNode }) {
  return <I18nProvider initialLocale="en">{children}</I18nProvider>;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('DirBrowser', () => {
  it('starts over at the initial path each time it is reopened', async () => {
    // The reset happens when the dialog opens, not in an effect: a reopened
    // browser must not show the folder the last visit ended in.
    const { requests } = stubTree();
    const user = userEvent.setup();
    const props = { onOpenChange: vi.fn(), mode: 'local' as const, initialPath: '/data', onSelect: vi.fn() };
    const { rerender } = render(<DirBrowser open {...props} />, { wrapper: Wrapper });
    await user.click(await screen.findByRole('button', { name: 'photos' }));
    expect(await screen.findByText('/data/photos')).toBeInTheDocument();

    rerender(<DirBrowser open={false} {...props} />);
    rerender(<DirBrowser open {...props} />);
    expect(screen.queryByText('/data/photos')).not.toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Loading...' })).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'photos' })).toBeInTheDocument();
    expect(requests).toEqual([
      'GET /browse/local?path=%2Fdata',
      'GET /browse/local?path=%2Fdata%2Fphotos',
      'GET /browse/local?path=%2Fdata',
    ]);
  });

  it('does not browse again when the initial path changes while it is open', async () => {
    // Select fills the caller's field (the initial path) while the dialog
    // closes; that must not list the new folder too.
    const { requests } = stubTree();
    const props = { onOpenChange: vi.fn(), mode: 'local' as const, onSelect: vi.fn() };
    const { rerender } = render(<DirBrowser open initialPath="/data" {...props} />, { wrapper: Wrapper });
    expect(await screen.findByRole('button', { name: 'photos' })).toBeInTheDocument();
    rerender(<DirBrowser open initialPath="/data/photos" {...props} />);
    expect(screen.getByText('/data')).toBeInTheDocument();
    expect(requests).toEqual(['GET /browse/local?path=%2Fdata']);
  });

  it('opens a remote browser without a start path empty, without asking the backend', () => {
    const { requests } = stubTree();
    render(<DirBrowser open onOpenChange={vi.fn()} mode="remote" initialPath="" />, { wrapper: Wrapper });
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: 'Browse remote directory' })).toBeInTheDocument();
    expect(requests).toEqual([]);
  });
});
