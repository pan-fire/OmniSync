import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nProvider } from '@/i18n';
import { BackupHistory } from '@/components/profiles/backup-history';
import { ScrollArea } from '@/components/ui/scroll-area';
import type { Snapshot } from '@/types';

// A long snapshot list scrolls inside its card instead of growing past it.
// jsdom has no layout, so this checks the structure that makes the cap
// work (a flex-column root whose viewport may shrink below its content) and
// that every row stays reachable from the keyboard; e2e/smoke.spec.ts checks
// the real layout in a browser.

const SNAPSHOTS: Snapshot[] = [0, 1, 2, 3, 4, 5].map((day) => ({
  snapshot_id: `2026-09-${30 - day}T030000Z`,
  created_at:  `2026-09-${30 - day}T03:00:00Z`,
  size_bytes:  1024,
  status:      'completed',
  latest:      day === 0,
}));

const originalFetch = globalThis.fetch;

beforeEach(() => {
  globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(SNAPSHOTS) })) as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function wrapper ({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}><I18nProvider>{children}</I18nProvider></QueryClientProvider>;
}

describe('ScrollArea with a height cap', () => {
  it('lets the viewport shrink to the cap instead of growing with the content', () => {
    const { container } = render(<ScrollArea className="max-h-40"><p>content</p></ScrollArea>);
    const root = container.querySelector('[data-slot="scroll-area"]');
    const viewport = container.querySelector('[data-slot="scroll-area-viewport"]');
    expect(root).toHaveClass('max-h-40', 'flex', 'flex-col');
    expect(viewport).toHaveClass('min-h-0', 'flex-1');
  });
});

describe('BackupHistory with many snapshots', () => {
  it('lists all six inside the capped scroll area', async () => {
    render(<BackupHistory profileSlug="docs" targetId={3} />, { wrapper });
    const restores = await screen.findAllByRole('button', { name: 'Restore' });
    expect(restores).toHaveLength(6);
    const area = restores[0].closest('[data-slot="scroll-area"]');
    expect(area).toHaveClass('max-h-40');
    for (const button of restores) expect(area).toContainElement(button);
  });

  it('reaches every row with the keyboard', async () => {
    const user = userEvent.setup();
    render(<BackupHistory profileSlug="docs" targetId={3} />, { wrapper });
    await screen.findAllByRole('button', { name: 'Restore' });
    const order = screen.getAllByRole('button', { name: /Browse|Restore/ });
    expect(order).toHaveLength(12);
    for (const button of order) {
      await user.tab();
      expect(button).toHaveFocus();
    }
  });
});
