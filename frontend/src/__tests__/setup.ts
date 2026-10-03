import '@testing-library/jest-dom/vitest';
import { vi } from 'vitest';

// jsdom does not implement scrollTo on Element or window
Element.prototype.scrollTo = () => {};
window.scrollTo = () => {};

// jsdom has no layout engine so @tanstack/react-virtual cannot calculate
// visible rows. Mock useVirtualizer to return all items for testability.
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: (opts: { count: number; estimateSize: () => number }) => ({
    getVirtualItems: () =>
      Array.from({ length: opts.count }, (_, i) => ({
        index: i,
        start: i * opts.estimateSize(),
        size:  opts.estimateSize(),
        key:   i,
      })),
    getTotalSize:   () => opts.count * opts.estimateSize(),
    measureElement: () => {},
  }),
}));
