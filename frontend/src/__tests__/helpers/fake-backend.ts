import { vi } from 'vitest';

/** A response body, or a function of the request that returns one. */
type Route = unknown | ((url: URL, init?: globalThis.RequestInit) => unknown);

/**
 * Stubs `fetch` with a tiny backend: `'<METHOD> <path>'` (without the /api
 * prefix) -> JSON body. Unknown routes answer 404, so a test sees which
 * requests the real hooks made (`requests`) instead of mocking the hooks.
 */
export function stubBackend (routes: Record<string, Route>) {
  const requests: string[] = [];
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: globalThis.RequestInit) => {
    const raw = input instanceof Request ? input.url : String(input);
    const url = new URL(raw, 'http://localhost');
    const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
    const key = `${method} ${url.pathname.replace(/^\/api/, '')}`;
    requests.push(`${key}${url.search}`);
    if (!(key in routes)) {
      return new Response(JSON.stringify({ detail: `no route for ${key}` }), { status: 404 });
    }
    const route = routes[key];
    const body = typeof route === 'function' ? route(url, init) : route;
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  vi.stubGlobal('fetch', fetchMock);
  return { requests, fetchMock };
}

export const EMPTY_DIFF = {
  files:      [],
  summary:    { local_only: 0, remote_only: 0, modified_local: 0, modified_remote: 0, modified_both: 0, manual: 0, total: 0 },
  error:      null,
  pagination: { offset: 0, limit: 100, total: 0, has_more: false },
};
