import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider, type Locale } from '@/i18n';
import { FolderRulesDialog } from '@/components/profiles/folder-rules-dialog';

// The Radix checkboxes measure themselves; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe () {}
  unobserve () {}
  disconnect () {}
} as unknown as typeof ResizeObserver;

const originalFetch = globalThis.fetch;
let failPaths: Set<string>;

function respond (data: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(data) });
}

beforeEach(() => {
  failPaths = new Set();
  globalThis.fetch = vi.fn((url: string) => {
    const path = decodeURIComponent(String(url).split('path=')[1] ?? '');
    if (failPaths.has(path)) return respond({ detail: `Permission denied: ${path}` }, 403);
    const children: Record<string, string[]> = { '/sync': ['Big', 'Docs'], '/sync/Big': ['keep'] };
    const entries = (children[path] ?? []).map((name) => ({ name, path: `${path}/${name}` }));
    return respond({ current: path, parent: null, entries });
  }) as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function renderDialog (locale: Locale = 'en') {
  const onOpenChange = vi.fn();
  const onApply = vi.fn();
  const client = new QueryClient();
  function Wrapper ({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}><I18nProvider initialLocale={locale}>{children}</I18nProvider></QueryClientProvider>;
  }
  render(
    <FolderRulesDialog open onOpenChange={onOpenChange} localDir="/sync" rules="" syncMode="mirror" onApply={onApply} />,
    { wrapper: Wrapper }
  );
  return { onOpenChange, onApply };
}

describe('FolderRulesDialog', () => {
  it('shows loading, then the folders', async () => {
    renderDialog();
    expect(screen.getByRole('status')).toHaveTextContent('Loading...');
    expect(await screen.findByRole('checkbox', { name: 'Big' })).toBeInTheDocument();
  });

  it('explains a folder that cannot be listed', async () => {
    failPaths.add('/sync');
    renderDialog();
    expect(await screen.findByRole('alert')).toHaveTextContent('Permission denied: /sync');
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('explains a subfolder that cannot be listed', async () => {
    failPaths.add('/sync/Big');
    const user = userEvent.setup();
    renderDialog();
    await user.click(await screen.findByRole('button', { name: 'Show folders in Big' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Permission denied: /sync/Big');
  });

  it('hides an opened folder again without reloading it', async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(await screen.findByRole('button', { name: 'Show folders in Big' }));
    expect(await screen.findByRole('checkbox', { name: 'keep' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Hide folders in Big' }));
    expect(screen.queryByRole('checkbox', { name: 'keep' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Show folders in Big' }));
    expect(screen.getByRole('checkbox', { name: 'keep' })).toBeInTheDocument();
    expect(vi.mocked(globalThis.fetch).mock.calls.filter(([url]) => String(url).includes('%2Fsync%2FBig'))).toHaveLength(1);
  });

  it('cancel closes without applying rules', async () => {
    const user = userEvent.setup();
    const { onOpenChange, onApply } = renderDialog();
    await user.click(await screen.findByRole('checkbox', { name: 'Big' }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(onApply).not.toHaveBeenCalled();
  });

  it('is titled in Persian', async () => {
    renderDialog('fa');
    expect(screen.getByRole('dialog', { name: 'انتخاب پوشه‌ها برای همگام‌سازی' })).toBeInTheDocument();
    expect(await screen.findByText('کل پوشه')).toBeInTheDocument();
  });
});
