import { describe, it, expect, vi, afterEach } from 'vitest';
import { api } from '@/lib/api';

describe('API client pause feature', () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('startProfileSync with force sends force: true in body', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok:   true,
      json: () => Promise.resolve({ job_id: 1, state: 'pushing' }),
    });
    globalThis.fetch = mockFetch;

    await api.startProfileSync('docs', 'push', true);

    expect(mockFetch).toHaveBeenCalledOnce();
    expect(mockFetch.mock.calls[0][0]).toBe('/api/profiles/docs/sync/start');
    const body = JSON.parse(mockFetch.mock.calls[0][1].body as string);
    expect(body).toEqual({ direction: 'push', force: true });
  });

  it('startProfileSync without force does not include force key', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok:   true,
      json: () => Promise.resolve({ job_id: 1, state: 'pulling' }),
    });
    globalThis.fetch = mockFetch;

    await api.startProfileSync('docs', 'pull');

    expect(mockFetch).toHaveBeenCalledOnce();
    const body = JSON.parse(mockFetch.mock.calls[0][1].body as string);
    expect(body).toEqual({ direction: 'pull' });
  });

  it('resumeProfileIntervals calls POST /profiles/{slug}/sync/resume-intervals', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok:   true,
      json: () => Promise.resolve({ detail: 'Intervals resumed successfully.' }),
    });
    globalThis.fetch = mockFetch;

    const result = await api.resumeProfileIntervals('docs');

    expect(mockFetch).toHaveBeenCalledOnce();
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe('/api/profiles/docs/sync/resume-intervals');
    expect(options.method).toBe('POST');
    expect(result.detail).toBe('Intervals resumed successfully.');
  });
});
