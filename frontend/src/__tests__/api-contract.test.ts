import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { api } from '@/lib/api';
import { parseApiError } from '@/lib/api-error';
import { ApiError } from '@/types';

// Requests the client sends must match the FastAPI routes in backend/api/routes.
describe('API client matches the backend routes', () => {
  const originalFetch = globalThis.fetch;
  let mockFetch: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockFetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve([]) });
    globalThis.fetch = mockFetch as unknown as typeof fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  const lastUrl = () => mockFetch.mock.calls.at(-1)?.[0] as string;
  const lastInit = () => mockFetch.mock.calls.at(-1)?.[1] as { method?: string; body?: string };

  it('jobs are paged with skip/limit (jobs.py), not page', async () => {
    await api.getJobs(40, 20);
    expect(lastUrl()).toBe('/api/jobs?skip=40&limit=20');
    await api.getJobs(0, 20, 'my docs');
    expect(lastUrl()).toBe('/api/jobs?skip=0&limit=20&profile=my%20docs');
  });

  it('logs are paged with skip/limit and filtered by level on the server (logs.py)', async () => {
    await api.getLogs();
    expect(lastUrl()).toBe('/api/logs?skip=0&limit=100');
    await api.getLogs(200, 100, 'ERROR');
    expect(lastUrl()).toBe('/api/logs?skip=200&limit=100&level=ERROR');
  });

  it('manual flags use /profiles/{slug}/manual-flags (profiles.py)', async () => {
    await api.getProfileManualFlags('docs');
    expect(lastUrl()).toBe('/api/profiles/docs/manual-flags');

    await api.clearProfileManualFlag('docs', 'dir/a b.txt');
    expect(lastUrl()).toBe('/api/profiles/docs/manual-flags/dir/a%20b.txt');
    expect(lastInit().method).toBe('DELETE');
  });

  it('there is no client for the non-existent POST /remotes or the legacy /sync routes', () => {
    const names = Object.keys(api);
    for (const gone of ['addRemote', 'getConfig', 'updateConfig', 'startSync', 'stopSync', 'checkSync', 'getSyncStatus', 'getDiff', 'selectiveSync']) {
      expect(names).not.toContain(gone);
    }
  });

  it('push/pull start per profile', async () => {
    mockFetch.mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve({ job_id: 1, state: 'pushing' }) });
    await api.startProfileSync('docs', 'push');
    expect(lastUrl()).toBe('/api/profiles/docs/sync/start');
    expect(JSON.parse(lastInit().body ?? '{}')).toEqual({ direction: 'push' });
  });

  it('Sync now of a two-way profile starts direction two_way', async () => {
    mockFetch.mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve({ job_id: 1, state: 'syncing' }) });
    await api.startProfileSync('docs', 'two_way', true);
    expect(lastUrl()).toBe('/api/profiles/docs/sync/start');
    expect(JSON.parse(lastInit().body ?? '{}')).toEqual({ direction: 'two_way', force: true });
  });

  it('a resync is POST /profiles/{slug}/sync/resync with confirm', async () => {
    mockFetch.mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve({ job_id: 2, state: 'syncing' }) });
    await api.resyncProfile('my docs');
    expect(lastUrl()).toBe('/api/profiles/my%20docs/sync/resync');
    expect(lastInit().method).toBe('POST');
    expect(JSON.parse(lastInit().body ?? '{}')).toEqual({ confirm: true });
  });

  it('the sync mode is changed with PUT /profiles/{slug}', async () => {
    await api.updateProfile('docs', { sync_mode: 'two_way' });
    expect(lastUrl()).toBe('/api/profiles/docs');
    expect(lastInit().method).toBe('PUT');
    expect(JSON.parse(lastInit().body ?? '{}')).toEqual({ sync_mode: 'two_way' });
  });

  it('the confirmation preview is POST /profiles/{slug}/sync/preview (profiles.py)', async () => {
    await api.previewProfileSync('my docs');
    expect(lastUrl()).toBe('/api/profiles/my%20docs/sync/preview');
    expect(lastInit().method).toBe('POST');
  });

  it('conflicts are resolved with one of the four resolutions (conflicts.py)', async () => {
    for (const resolution of ['keep_local', 'keep_remote', 'keep_both', 'dismiss'] as const) {
      await api.resolveConflict(3, resolution);
      expect(lastUrl()).toBe('/api/conflicts/3/resolve');
      expect(JSON.parse(lastInit().body ?? '{}')).toEqual({ resolution });
    }
  });
});

describe('error messages', () => {
  it('the envelope gives message, code and details', () => {
    const err = parseApiError(422, {
      detail:  "debounce_seconds: Input should be greater than or equal to 1; remote_dir: Value error, must look like '<remote>:<path>'",
      code:    'validation_failed',
      details: { errors: [{ loc: ['body', 'debounce_seconds'], msg: 'Input should be greater than or equal to 1', type: 'greater_than_equal' }] },
    });
    expect(err.message).toBe(
      "debounce_seconds: Input should be greater than or equal to 1; remote_dir: Value error, must look like '<remote>:<path>'"
    );
    expect(err.code).toBe('validation_failed');
    expect(err.details?.errors).toHaveLength(1);
  });

  it('details are optional', () => {
    const err = parseApiError(422, { detail: "'ssh' is not a SFTP setting", code: 'invalid_params' });
    expect(err).toEqual({ message: "'ssh' is not a SFTP setting", code: 'invalid_params', details: undefined });
  });

  it('bodies that are not an envelope fall back to the status', () => {
    expect(parseApiError(404, { detail: 'Profile not found' }).message).toBe('Profile not found');
    expect(parseApiError(500, null).message).toBe('Request failed (500)');
    expect(parseApiError(502, '<html>Bad Gateway</html>').message).toBe('Request failed (502)');
    expect(parseApiError(422, { detail: [{ msg: 'old list' }] }).message).toBe('Request failed (422)');
  });

  it('apiFetch throws an ApiError with the message, code and details', async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok:     false,
      status: 409,
      json:   () => Promise.resolve({ detail: 'Remotes with these names exist already: gdrive', code: 'name_clash', details: { names: ['gdrive'] } }),
    });
    try {
      const error = await api.getProfiles().catch((e: unknown) => e);
      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({
        status:  409,
        message: 'Remotes with these names exist already: gdrive',
        code:    'name_clash',
        details: { names: ['gdrive'] },
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
