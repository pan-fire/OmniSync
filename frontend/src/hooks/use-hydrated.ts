'use client';

import { useSyncExternalStore } from 'react';

const subscribe = () => () => {};

/**
 * False while React hydrates the server HTML, true afterwards.
 *
 * The server renders every query as still loading. The layout hydrates
 * before the page, so a query the layout also runs (the sidebar's GET
 * /health) can have its answer in the cache by the time the page hydrates;
 * rendering that data then would not match the server HTML (React error
 * #418). Show such data only once hydrated.
 */
export function useHydrated (): boolean {
  return useSyncExternalStore(subscribe, () => true, () => false);
}
