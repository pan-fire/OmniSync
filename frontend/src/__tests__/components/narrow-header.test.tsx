import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { I18nProvider } from '@/i18n';
import { PageHeader } from '@/components/layout/page-header';
import { SidebarControlsProvider } from '@/components/layout/sidebar-controls';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';

// On a phone the profile page's header and tab list must not cut anything
// off. jsdom has no layout: these check the classes and the scrolling logic;
// e2e/smoke.spec.ts measures the real page at 390x844, in en and fa.

describe('PageHeader on a narrow screen', () => {
  it('wraps a long title instead of truncating it, and lets the badges and actions wrap', () => {
    render(
      <I18nProvider>
        <SidebarControlsProvider value={{ isSidebarCollapsed: false, toggleSidebar: vi.fn() }}>
          <PageHeader
            title="Documents and other long names"
            afterTitle={<span>Syncing</span>}
            actions={<button type="button">Act</button>}
          />
        </SidebarControlsProvider>
      </I18nProvider>
    );
    const title = screen.getByRole('heading', { level: 1 });
    expect(title).not.toHaveClass('truncate');
    expect(title).toHaveClass('wrap-break-word', 'min-w-0');
    expect(screen.getByText('Syncing').parentElement).toHaveClass('flex-wrap');
    expect(screen.getByRole('button', { name: 'Act' }).parentElement).toHaveClass('flex-wrap');
  });
});

describe('TabsList that scrolls sideways', () => {
  const TAB_WIDTH = 100;
  const LIST_WIDTH = 300;
  const descriptors = {
    scrollLeft:            Object.getOwnPropertyDescriptor(Element.prototype, 'scrollLeft'),
    getBoundingClientRect: Object.getOwnPropertyDescriptor(Element.prototype, 'getBoundingClientRect'),
  };

  // A 300px list of 100px tabs, laid out by hand: a tab's box moves with
  // the list's scrollLeft, like in a browser.
  function fakeLayout () {
    const scroll = new WeakMap<Element, number>();
    Object.defineProperty(Element.prototype, 'scrollLeft', {
      configurable: true,
      get (this: Element) { return scroll.get(this) ?? 0; },
      set (this: Element, value: number) { scroll.set(this, value); },
    });
    Object.defineProperty(Element.prototype, 'getBoundingClientRect', {
      configurable: true,
      value (this: Element) {
        if (this.getAttribute('role') === 'tab') {
          const list = this.parentElement!;
          const left = [...list.children].indexOf(this) * TAB_WIDTH - list.scrollLeft;
          return { left, right: left + TAB_WIDTH, top: 0, bottom: 30, width: TAB_WIDTH, height: 30 };
        }
        return { left: 0, right: LIST_WIDTH, top: 0, bottom: 30, width: LIST_WIDTH, height: 30 };
      },
    });
  }

  afterEach(() => {
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(Element.prototype, name, descriptor);
    }
  });

  function renderTabs (value: string) {
    return render(
      <Tabs defaultValue={value}>
        <TabsList className="max-w-full overflow-x-auto">
          {['a', 'b', 'c', 'd', 'e'].map((v) => <TabsTrigger key={v} value={v}>{v}</TabsTrigger>)}
        </TabsList>
      </Tabs>
    );
  }

  it('starts at the first tab, not scrolled part-way', () => {
    fakeLayout();
    renderTabs('a');
    expect(screen.getByRole('tablist').scrollLeft).toBe(0);
    expect(screen.getByRole('tablist')).toHaveClass('justify-center-safe');
  });

  it('scrolls an active tab that is out of view into view, and back', async () => {
    fakeLayout();
    renderTabs('e');
    const list = screen.getByRole('tablist');
    // Tab e spans 400-500: the list scrolls by 200 so it ends at 300.
    expect(list.scrollLeft).toBe(2 * TAB_WIDTH);
    await userEvent.setup().click(screen.getByRole('tab', { name: 'a' }));
    await waitFor(() => expect(list.scrollLeft).toBe(0));
  });
});
