import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { api, apiFetch, waitForSyncJob } from '@/lib/api';
import { ApiError } from '@/types';

// Requests of the remaining client methods, and the edge cases of apiFetch,
// getHealth and the job polling.
describe('API client, remaining routes', () => {
  const originalFetch = globalThis.fetch;
  let mockFetch: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockFetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve({}) });
    globalThis.fetch = mockFetch as unknown as typeof fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.useRealTimers();
  });

  const lastUrl = () => mockFetch.mock.calls.at(-1)?.[0] as string;
  const lastInit = () => mockFetch.mock.calls.at(-1)?.[1] as { method?: string; body?: string };

  it('jobs, profiles and sync control', async () => {
    await api.getJobFiles(4);
    expect(lastUrl()).toBe('/api/jobs/4/files');

    await api.createProfile({ name: 'Docs' } as Parameters<typeof api.createProfile>[0]);
    expect(lastUrl()).toBe('/api/profiles');
    expect(lastInit().method).toBe('POST');
    expect(JSON.parse(lastInit().body ?? '{}')).toEqual({ name: 'Docs' });

    await api.enableProfile('my docs');
    expect(lastUrl()).toBe('/api/profiles/my%20docs/enable');
    expect(lastInit().method).toBe('POST');

    await api.stopProfileSync('docs');
    expect(lastUrl()).toBe('/api/profiles/docs/sync/stop');
    expect(lastInit().method).toBe('POST');

    await api.checkProfileSync('docs');
    expect(lastUrl()).toBe('/api/profiles/docs/sync/check');
    expect(lastInit().method).toBe('POST');
  });

  it('backup targets are updated with PUT and deleted with confirm', async () => {
    await api.updateBackupTarget('docs', 3, { name: 'Offsite' } as Parameters<typeof api.updateBackupTarget>[2]);
    expect(lastUrl()).toBe('/api/profiles/docs/backups/3');
    expect(lastInit().method).toBe('PUT');
    expect(JSON.parse(lastInit().body ?? '{}')).toEqual({ name: 'Offsite' });

    mockFetch.mockResolvedValueOnce({ ok: true, status: 204, json: () => Promise.reject(new Error('no body')) });
    await expect(api.deleteBackupTarget('docs', 3)).resolves.toBeUndefined();
    expect(lastUrl()).toBe('/api/profiles/docs/backups/3?confirm=true');
    expect(lastInit().method).toBe('DELETE');
  });

  it('snapshot files pass every query parameter that is set', async () => {
    await api.getSnapshotFiles('docs', 2, 'abc/1', { path: 'a b', search: 'x', offset: 100, limit: 50 });
    expect(lastUrl()).toBe('/api/profiles/docs/backups/2/snapshots/abc%2F1/files?path=a+b&search=x&offset=100&limit=50');
    await api.getSnapshotFiles('docs', 2, 'abc');
    expect(lastUrl()).toBe('/api/profiles/docs/backups/2/snapshots/abc/files');
  });

  it('an error answer that is not JSON still becomes an ApiError with the status', async () => {
    mockFetch.mockResolvedValueOnce({
      ok:      false,
      status:  502,
      headers: new Headers({ 'x-request-id': 'req-1' }),
      json:    () => Promise.reject(new SyntaxError('Unexpected token <')),
    });
    const error = await apiFetch('/jobs').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(502);
    expect((error as ApiError).requestId).toBe('req-1');
  });

  it('a health answer that is not JSON is an error', async () => {
    mockFetch.mockResolvedValueOnce({
      ok:      false,
      status:  502,
      headers: new Headers(),
      json:    () => Promise.reject(new SyntaxError('bad')),
    });
    await expect(api.getHealth()).rejects.toBeInstanceOf(ApiError);
  });

  // A wait that the caller aborts must stop polling at once.
  it('a job wait stops when aborted between polls', async () => {
    vi.useFakeTimers();
    mockFetch.mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve({ id: 1, status: 'running' }) });
    const controller = new AbortController();
    const wait = waitForSyncJob(1, { intervalMs: 1_000, signal: controller.signal });
    const outcome = expect(wait).rejects.toThrow('stop');
    await vi.advanceTimersByTimeAsync(0);
    controller.abort(new Error('stop'));
    await outcome;
    const calls = mockFetch.mock.calls.length;
    await vi.advanceTimersByTimeAsync(5_000);
    expect(mockFetch).toHaveBeenCalledTimes(calls);
  });

  it('a job wait with an aborted signal rejects without waiting', async () => {
    mockFetch.mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve({ id: 1, status: 'running' }) });
    const controller = new AbortController();
    controller.abort(new Error('gone'));
    await expect(waitForSyncJob(1, { intervalMs: 1_000, signal: controller.signal })).rejects.toThrow('gone');
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });
});
