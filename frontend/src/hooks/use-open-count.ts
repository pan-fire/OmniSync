'use client';

import { useState } from 'react';

/**
 * How many times `open` has turned true. A dialog keys its body with it, so
 * every opening starts from fresh state, even when it reopens while the
 * close animation still holds the old body on screen.
 *
 * The count is adjusted while rendering, React's pattern for state that
 * follows a prop (react.dev/learn/you-might-not-need-an-effect), so the
 * reset costs no extra commit.
 */
export function useOpenCount (open: boolean): number {
  const [count, setCount] = useState(open ? 1 : 0);
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setCount(count + 1);
  }
  return count;
}
