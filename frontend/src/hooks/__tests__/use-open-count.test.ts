import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useOpenCount } from '@/hooks/use-open-count';

describe('useOpenCount', () => {
  it('counts each opening, not each render or closing', () => {
    const { result, rerender } = renderHook(({ open }) => useOpenCount(open), { initialProps: { open: false } });
    expect(result.current).toBe(0);
    rerender({ open: false });
    expect(result.current).toBe(0);

    rerender({ open: true });
    expect(result.current).toBe(1);
    rerender({ open: true });
    expect(result.current).toBe(1);

    // Closing keeps the count, so the closing dialog keeps its body.
    rerender({ open: false });
    expect(result.current).toBe(1);
    rerender({ open: true });
    expect(result.current).toBe(2);
  });

  it('counts a dialog mounted open as opened once', () => {
    const { result } = renderHook(() => useOpenCount(true));
    expect(result.current).toBe(1);
  });
});
