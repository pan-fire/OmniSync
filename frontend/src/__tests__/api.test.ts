import { describe, it, expect, vi, afterEach } from 'vitest';
import fc from 'fast-check';
import { apiFetch } from '@/lib/api';

describe('API path prefixing', () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('for any endpoint path, the constructed fetch URL starts with /api', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.stringMatching(/^\/[a-zA-Z0-9/_\-?.=&]*$/).filter((s) => s.length >= 1),
        async (path) => {
          const mockFetch = vi.fn().mockResolvedValue({
            ok:   true,
            json: () => Promise.resolve({}),
          });
          globalThis.fetch = mockFetch;

          await apiFetch(path);

          expect(mockFetch).toHaveBeenCalledOnce();
          const url = mockFetch.mock.calls[0][0] as string;
          expect(url).toMatch(/^\/api/);
          expect(url).toBe(`/api${path}`);
        }
      ),
      { numRuns: 100 }
    );
  });
});
