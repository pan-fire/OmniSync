import type {
  SyncStartResponse,
  SyncStopResponse,
  SyncDirection,
  SyncJob,
  FileChange,
  Conflict,
  ConflictResolution,
  Remote,
  LogEntry,
  Health,
  RemoteHealthResponse,
  Provider,
  AuthorizeResponse,
  OAuthRedirect,
  WizardSession,
  CreateRemoteRequest,
  TestRemoteResult,
  SyncCheckResult,
  SyncPreview,
  DiffResponse,
  SelectiveSyncItem,
  SelectiveSyncResponse,
  ManualFlagsResponse,
  NotificationConfig,
  NotificationConfigUpdate,
  ChannelStatus,
  NotificationHistory,
  TestNotificationResponse,
  Profile,
  ProfileStatus,
  ProfileCreateRequest,
  ProfileUpdateRequest,
  GlobalConfig,
  TestSyncResult,
  BackupTarget,
  BackupTargetCreateRequest,
  BackupTargetUpdateRequest,
  BackupJob,
  Snapshot,
  SnapshotFilesQuery,
  SnapshotFilesResponse,
  RestoreFilesRequest,
  RestorePreview,
  RestoreRequest,
  RemoteStorageInfo,
  RemoteTestResult,
  RemoteDependencies,
  RemoteConfig,
  UpdateRemoteRequest,
  ImportPreview,
  ImportSelection,
  AggregateStatus,
  PauseAllResponse,
  TrashActionResponse,
  TrashList,
  TrashSide,
} from '@/types';
import { ApiError } from '@/types';
import { parseApiError } from '@/lib/api-error';
import { isLoginRequired, redirectToLogin } from '@/lib/auth/client';

const API_BASE = '/api';

/** The ApiError for an error answer (envelope: docs/api-errors.md). */
function apiError (status: number, body: unknown): ApiError {
  const { message, code, details } = parseApiError(status, body);
  return new ApiError(status, message, code, details);
}

/** Encode a relative file path for a `{file_path:path}` route, keeping the slashes. */
function encodePath (filePath: string): string {
  return filePath.split('/').map(encodeURIComponent).join('/');
}

export async function apiFetch<T> (path: string, options?: globalThis.RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    if (isLoginRequired(res.status, body)) redirectToLogin();
    throw apiError(res.status, body);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json();
}

/** Resolves after `ms` milliseconds, or rejects when `signal` aborts. */
function sleep (ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason);
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => {
      clearTimeout(timer);
      reject(signal.reason);
    }, { once: true });
  });
}

/** Failed polls in a row after which a wait for a job gives up. */
const MAX_POLL_FAILURES = 5;

export interface PollOptions {
  intervalMs: number;
  signal?:    AbortSignal;
}

/**
 * Call `read` every `intervalMs` until what it returns is no longer
 * running, and return that. A failed poll (the backend restarting, a proxy
 * hiccup) is retried; MAX_POLL_FAILURES in a row reject with the last error.
 */
async function pollUntilDone<T extends { status: string }> (
  read: () => Promise<T>,
  { intervalMs, signal }: PollOptions
): Promise<T> {
  let failures = 0;
  for (;;) {
    try {
      const current = await read();
      failures = 0;
      if (current.status !== 'running') return current;
    } catch (error) {
      failures += 1;
      if (failures >= MAX_POLL_FAILURES || signal?.aborted) throw error;
    }
    await sleep(intervalMs, signal);
  }
}

/** Poll GET /jobs/{jobId} until the sync job is no longer running, and return it (see pollUntilDone). */
export function waitForSyncJob (jobId: number, options: PollOptions): Promise<SyncJob> {
  return pollUntilDone(() => api.getJob(jobId), options);
}

/**
 * Poll GET .../backups/{targetId}/jobs/{jobId} until the backup or restore
 * ends, and return the job (see pollUntilDone). Starting one answers at once
 * with the running job.
 */
export function waitForBackupJob (slug: string, targetId: number, jobId: number, options: PollOptions): Promise<BackupJob> {
  return pollUntilDone(() => api.getBackupJob(slug, targetId, jobId), options);
}

/**
 * Poll GET /profiles/{slug}/sync/selective/{jobId} until the per-file
 * actions end, and return their result with the per-file errors.
 */
export function waitForSelectiveSync (slug: string, jobId: number, options: PollOptions): Promise<SelectiveSyncResponse> {
  return pollUntilDone(() => api.getSelectiveResult(slug, jobId), options);
}

/**
 * GET /health. A degraded backend (database or rclone missing) answers 503
 * with the same body, which is a result to show, not a failed request.
 */
async function fetchHealth (): Promise<Health> {
  const res = await fetch(`${API_BASE}/health`, { headers: { 'Content-Type': 'application/json' } });
  const body: unknown = await res.json().catch(() => null);
  if (isLoginRequired(res.status, body)) redirectToLogin();
  const isHealth = !!body && typeof body === 'object' && 'database_ok' in body && 'rclone_installed' in body;
  if (isHealth && (res.ok || res.status === 503)) {
    return body as Health;
  }
  throw apiError(res.status, body);
}

export const api = {
  getAggregateStatus: () => apiFetch<AggregateStatus>('/sync/status/aggregate'),
  getHealth:          fetchHealth,
  // Calls the providers (may refresh OAuth tokens): not for tight polling.
  getRemoteHealth:    () => apiFetch<RemoteHealthResponse>('/health/remotes'),
  getJob:             (id: number) => apiFetch<SyncJob>(`/jobs/${id}`),
  getJobFiles:        (id: number) => apiFetch<FileChange[]>(`/jobs/${id}/files`),
  getRemotes:         () => apiFetch<Remote[]>('/remotes'),
  deleteRemote:       (name: string, force?: boolean) =>
    apiFetch<void>(`/remotes/${encodeURIComponent(name)}${force ? '?force=true' : ''}`, { method: 'DELETE' }),
  getJobs: (skip = 0, limit = 20, profile?: string) =>
    apiFetch<SyncJob[]>(`/jobs?skip=${skip}&limit=${limit}${profile ? `&profile=${encodeURIComponent(profile)}` : ''}`),
  // GET /logs pages newest first; with a level, it pages through that
  // level's entries only.
  getLogs: (skip = 0, limit = 100, level?: string) =>
    apiFetch<LogEntry[]>(`/logs?skip=${skip}&limit=${limit}${level ? `&level=${encodeURIComponent(level)}` : ''}`),
  getConflicts: (profile?: string) =>
    apiFetch<Conflict[]>(`/conflicts${profile ? `?profile=${encodeURIComponent(profile)}` : ''}`),
  resolveConflict: (id: number, resolution: ConflictResolution) =>
    apiFetch<Conflict>(`/conflicts/${id}/resolve`, {
      method: 'POST',
      body:   JSON.stringify({ resolution }),
    }),

  // Wizard API methods
  fetchProviders:      () => apiFetch<Provider[]>('/wizard/providers'),
  // The redirect URI to register with the user's own OAuth app.
  getOAuthRedirectUri: () => apiFetch<OAuthRedirect>('/wizard/oauth/redirect-uri'),
  // With remote_name: reconnect that existing remote (finish with reconnectRemote).
  // Without it, client_id (and for Google client_secret) of the user's own app are required.
  startAuthorize:      (data: { provider_id: string; client_id?: string; client_secret?: string; remote_name?: string }) =>
    apiFetch<AuthorizeResponse>('/wizard/authorize', {
      method: 'POST',
      body:   JSON.stringify(data),
    }),
  getWizardSession: (sessionId: string) =>
    apiFetch<WizardSession>(`/wizard/sessions/${sessionId}`),
  cancelWizardSession: (sessionId: string) =>
    apiFetch<void>(`/wizard/sessions/${sessionId}`, { method: 'DELETE' }),
  createRemote: (data: CreateRemoteRequest) =>
    apiFetch<{ detail: string }>('/wizard/create', {
      method: 'POST',
      body:   JSON.stringify(data),
    }),
  testRemote: (name: string) =>
    apiFetch<TestRemoteResult>('/wizard/test', {
      method: 'POST',
      body:   JSON.stringify({ name }),
    }),
  // Stores the new token of a completed reconnect session in the remote.
  reconnectRemote: ({ name, sessionId }: { name: string; sessionId: string }) =>
    apiFetch<{ detail: string }>('/wizard/reconnect', {
      method: 'POST',
      body:   JSON.stringify({ name, session_id: sessionId }),
    }),

  // Notification API methods
  getNotificationConfig: () =>
    apiFetch<NotificationConfig>('/notifications/config'),
  updateNotificationConfig: (data: NotificationConfigUpdate) =>
    apiFetch<NotificationConfig>('/notifications/config', {
      method: 'PUT',
      body:   JSON.stringify(data),
    }),
  getChannelStatus: () =>
    apiFetch<ChannelStatus>('/notifications/channels/status'),
  getVapidPublicKey: () =>
    apiFetch<{ public_key: string }>('/notifications/vapid-public-key'),
  subscribePush: (subscription: { endpoint: string; keys: { p256dh: string; auth: string } }) =>
    apiFetch<{ detail: string }>('/notifications/push-subscription', {
      method: 'POST',
      body:   JSON.stringify(subscription),
    }),
  unsubscribePush: (endpoint: string) =>
    apiFetch<{ detail: string }>('/notifications/push-subscription', {
      method: 'DELETE',
      body:   JSON.stringify({ endpoint }),
    }),
  getNotificationHistory: (limit = 50, offset = 0) =>
    apiFetch<NotificationHistory>(`/notifications/history?limit=${limit}&offset=${offset}`),
  sendTestNotification: (channel?: string) =>
    apiFetch<TestNotificationResponse>('/notifications/test', {
      method: 'POST',
      body:   JSON.stringify(channel ? { channel } : {}),
    }),

  // Profile API methods
  getProfiles:   () => apiFetch<ProfileStatus[]>('/profiles'),
  getProfile:    (slug: string) => apiFetch<ProfileStatus>(`/profiles/${encodeURIComponent(slug)}`),
  createProfile: (data: ProfileCreateRequest) =>
    apiFetch<Profile>('/profiles', { method: 'POST', body: JSON.stringify(data) }),
  updateProfile: (slug: string, data: ProfileUpdateRequest) =>
    apiFetch<Profile>(`/profiles/${encodeURIComponent(slug)}`, {
      method: 'PUT',
      body:   JSON.stringify(data),
    }),
  deleteProfile: (slug: string) =>
    apiFetch<void>(`/profiles/${encodeURIComponent(slug)}?confirm=true`, { method: 'DELETE' }),
  enableProfile: (slug: string) =>
    apiFetch<Profile>(`/profiles/${encodeURIComponent(slug)}/enable`, { method: 'POST' }),
  disableProfile: (slug: string) =>
    apiFetch<Profile>(`/profiles/${encodeURIComponent(slug)}/disable`, { method: 'POST' }),

  // Profile-scoped sync operations. A start (and a resync) answers once the
  // run is under way; follow it with getJob(job_id) (see waitForSyncJob).
  startProfileSync: (slug: string, direction: SyncDirection, force?: boolean) =>
    apiFetch<SyncStartResponse>(`/profiles/${encodeURIComponent(slug)}/sync/start`, {
      method: 'POST',
      body:   JSON.stringify({ direction, ...(force && { force: true }) }),
    }),
  /**
   * POST /profiles/{slug}/sync/resync: a two-way resync (the union of both
   * sides, nothing deleted), then automatic syncing resumes. Only after the
   * user confirmed; the server refuses it without confirm. Answers like
   * startProfileSync.
   */
  resyncProfile: (slug: string) =>
    apiFetch<SyncStartResponse>(`/profiles/${encodeURIComponent(slug)}/sync/resync`, {
      method: 'POST',
      body:   JSON.stringify({ confirm: true }),
    }),
  stopProfileSync: (slug: string) =>
    apiFetch<SyncStopResponse>(`/profiles/${encodeURIComponent(slug)}/sync/stop`, { method: 'POST' }),
  checkProfileSync: (slug: string) =>
    apiFetch<SyncCheckResult>(`/profiles/${encodeURIComponent(slug)}/sync/check`, { method: 'POST' }),
  /**
   * What a push and a pull (and, for a two-way profile, the next two-way
   * sync) would delete and replace, and the delete limit. Changes nothing on
   * the server.
   */
  previewProfileSync: (slug: string) =>
    apiFetch<SyncPreview>(`/profiles/${encodeURIComponent(slug)}/sync/preview`, { method: 'POST' }),
  getProfileDiff: (slug: string, offset?: number, limit?: number) => {
    const params = new URLSearchParams();
    if (offset !== undefined) params.set('offset', String(offset));
    if (limit !== undefined) params.set('limit', String(limit));
    const qs = params.toString();
    return apiFetch<DiffResponse>(`/profiles/${encodeURIComponent(slug)}/diff${qs ? `?${qs}` : ''}`, { method: 'POST' });
  },
  // Per-file actions answer at once (status 'running'); follow them with
  // getSelectiveResult (see waitForSelectiveSync).
  profileSelectiveSync: (slug: string, items: SelectiveSyncItem[]) =>
    apiFetch<SelectiveSyncResponse>(`/profiles/${encodeURIComponent(slug)}/sync/selective`, {
      method: 'POST',
      body:   JSON.stringify({ items }),
    }),
  getSelectiveResult: (slug: string, jobId: number) =>
    apiFetch<SelectiveSyncResponse>(`/profiles/${encodeURIComponent(slug)}/sync/selective/${jobId}`),
  getProfileManualFlags: (slug: string) =>
    apiFetch<ManualFlagsResponse>(`/profiles/${encodeURIComponent(slug)}/manual-flags`),
  clearProfileManualFlag: (slug: string, filePath: string) =>
    apiFetch<void>(`/profiles/${encodeURIComponent(slug)}/manual-flags/${encodePath(filePath)}`, {
      method: 'DELETE',
    }),
  resumeProfileIntervals: (slug: string) =>
    apiFetch<{ detail: string }>(`/profiles/${encodeURIComponent(slug)}/sync/resume-intervals`, { method: 'POST' }),
  /** Pause automatic syncing of one profile for the user; resumeProfileIntervals lifts it. */
  pauseProfile: (slug: string) =>
    apiFetch<{ detail: string }>(`/profiles/${encodeURIComponent(slug)}/sync/pause`, { method: 'POST' }),
  /** Pause automatic syncing of every enabled profile ("Paused by user"). */
  pauseAllProfiles:  () => apiFetch<PauseAllResponse>('/profiles/pause-all', { method: 'POST' }),
  /** Lift the user's pause everywhere; pauses OmniSync set itself stay (still_paused). */
  resumeAllProfiles: () => apiFetch<PauseAllResponse>('/profiles/resume-all', { method: 'POST' }),

  // Trash (.omnisync-trash) of a profile, per side
  getProfileTrash: (slug: string, side: TrashSide) =>
    apiFetch<TrashList>(`/profiles/${encodeURIComponent(slug)}/trash?side=${side}`),
  restoreFromTrash: (slug: string, side: TrashSide, ids: string[], overwrite = false) =>
    apiFetch<TrashActionResponse>(`/profiles/${encodeURIComponent(slug)}/trash/restore`, {
      method: 'POST',
      body:   JSON.stringify({ side, ids, overwrite }),
    }),
  deleteFromTrash: (slug: string, side: TrashSide, ids: string[]) =>
    apiFetch<TrashActionResponse>(`/profiles/${encodeURIComponent(slug)}/trash/delete`, {
      method: 'POST',
      body:   JSON.stringify({ side, ids }),
    }),

  /**
   * Round-trip sync test (write, upload, verify, clean up a dummy file).
   * With a slug it tests the saved profile's folders, otherwise the given ones.
   */
  testSync: (localDir: string, remoteDir: string) =>
    apiFetch<TestSyncResult>('/config/test-sync', {
      method: 'POST',
      body:   JSON.stringify({ local_dir: localDir, remote_dir: remoteDir }),
    }),
  testProfileSync: (slug: string) =>
    apiFetch<TestSyncResult>(`/profiles/${encodeURIComponent(slug)}/config/test-sync`, { method: 'POST' }),

  // Global config
  getGlobalConfig:    () => apiFetch<GlobalConfig>('/config'),
  updateGlobalConfig: (data: Partial<GlobalConfig>) =>
    apiFetch<GlobalConfig>('/config', { method: 'PUT', body: JSON.stringify(data) }),

  // Backup target API methods
  getBackupTargets: (slug: string) =>
    apiFetch<BackupTarget[]>(`/profiles/${encodeURIComponent(slug)}/backups`),
  createBackupTarget: (slug: string, data: BackupTargetCreateRequest) =>
    apiFetch<BackupTarget>(`/profiles/${encodeURIComponent(slug)}/backups`, {
      method: 'POST',
      body:   JSON.stringify(data),
    }),
  updateBackupTarget: (slug: string, id: number, data: BackupTargetUpdateRequest) =>
    apiFetch<BackupTarget>(`/profiles/${encodeURIComponent(slug)}/backups/${id}`, {
      method: 'PUT',
      body:   JSON.stringify(data),
    }),
  deleteBackupTarget: (slug: string, id: number) =>
    apiFetch<void>(`/profiles/${encodeURIComponent(slug)}/backups/${id}?confirm=true`, {
      method: 'DELETE',
    }),
  // Backup runs and restores answer at once with the running job; follow it
  // with getBackupJob (see waitForBackupJob).
  runBackup: (slug: string, id: number) =>
    apiFetch<BackupJob>(`/profiles/${encodeURIComponent(slug)}/backups/${id}/run`, {
      method: 'POST',
    }),
  getBackupJob: (slug: string, id: number, jobId: number) =>
    apiFetch<BackupJob>(`/profiles/${encodeURIComponent(slug)}/backups/${id}/jobs/${jobId}`),
  getSnapshots: (slug: string, id: number) =>
    apiFetch<Snapshot[]>(`/profiles/${encodeURIComponent(slug)}/backups/${id}/snapshots`),
  restoreBackup: (slug: string, id: number, data: RestoreRequest) =>
    apiFetch<BackupJob>(`/profiles/${encodeURIComponent(slug)}/backups/${id}/restore`, {
      method: 'POST',
      body:   JSON.stringify(data),
    }),
  getSnapshotFiles: (slug: string, id: number, snapshotId: string, query: SnapshotFilesQuery = {}) => {
    const params = new URLSearchParams();
    if (query.path) params.set('path', query.path);
    if (query.search) params.set('search', query.search);
    if (query.offset) params.set('offset', String(query.offset));
    if (query.limit) params.set('limit', String(query.limit));
    const qs = params.toString();
    return apiFetch<SnapshotFilesResponse>(
      `/profiles/${encodeURIComponent(slug)}/backups/${id}/snapshots/${encodeURIComponent(snapshotId)}/files${qs ? `?${qs}` : ''}`
    );
  },
  previewRestore: (slug: string, id: number, data: RestoreRequest) =>
    apiFetch<RestorePreview>(`/profiles/${encodeURIComponent(slug)}/backups/${id}/restore/preview`, {
      method: 'POST',
      body:   JSON.stringify(data),
    }),
  restoreFiles: (slug: string, id: number, data: RestoreFilesRequest) =>
    apiFetch<BackupJob>(`/profiles/${encodeURIComponent(slug)}/backups/${id}/restore-files`, {
      method: 'POST',
      body:   JSON.stringify(data),
    }),

  // Extended remote API methods
  getRemoteStorageInfo: (name: string) =>
    apiFetch<RemoteStorageInfo>(`/remotes/${encodeURIComponent(name)}/about`),
  testRemoteConnection: (name: string) =>
    apiFetch<RemoteTestResult>(`/remotes/${encodeURIComponent(name)}/test`, { method: 'POST' }),
  getRemoteDependencies: (name: string) =>
    apiFetch<RemoteDependencies>(`/remotes/${encodeURIComponent(name)}/dependencies`),
  getRemoteConfig: (name: string) =>
    apiFetch<RemoteConfig>(`/remotes/${encodeURIComponent(name)}/config`),
  updateRemote: ({ name, data }: { name: string; data: UpdateRemoteRequest }) =>
    apiFetch<{ detail: string }>(`/remotes/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body:   JSON.stringify(data),
    }),
  // An uploaded rclone.conf: which remotes it holds, which clash or are refused.
  previewImport: (content: string) =>
    apiFetch<ImportPreview>('/remotes/import/preview', {
      method: 'POST',
      body:   JSON.stringify({ content }),
    }),
  importRemotes: ({ content, remotes }: { content: string; remotes: ImportSelection[] }) =>
    apiFetch<{ imported: string[] }>('/remotes/import', {
      method: 'POST',
      body:   JSON.stringify({ content, remotes }),
    }),
};
