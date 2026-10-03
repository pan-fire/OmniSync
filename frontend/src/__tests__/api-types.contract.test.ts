import { describe, expectTypeOf, it } from 'vitest';
import type { components } from '@/types/api.gen';
import type * as T from '@/types';

// The hand-written API types in src/types/index.ts against the types
// generated from the backend's OpenAPI schema (src/types/api.gen.ts, see
// scripts/gen-openapi.py). These are type-level checks: `pnpm typecheck`
// fails on a mismatch, and the error names the type and the fields.
//
// Response types (backend -> UI) may leave out fields the UI does not use,
// but every field they declare must exist on the backend model, and every
// value the backend can send must fit it (a field the backend may send as
// null or leave out must not be typed as always present).
//
// Request types (UI -> backend) may only send fields the backend model
// declares, and must fit them.

type Schema<K extends keyof components['schemas']> = components['schemas'][K];

/** Fields of Hand that Gen does not have. */
type ExtraKeys<Hand, Gen> = Exclude<keyof Hand, keyof Gen>;

/** Fields whose backend values do not fit the hand-written type. */
type ResponseMismatch<Hand, Gen> = {
  [K in keyof Hand & keyof Gen]-?: [Gen[K]] extends [Hand[K]] ? never : K
}[keyof Hand & keyof Gen];

/** Fields whose hand-written values the backend would not accept. */
type RequestMismatch<Hand, Gen> = {
  [K in keyof Hand & keyof Gen]-?: [Hand[K]] extends [Gen[K]] ? never : K
}[keyof Hand & keyof Gen];

/** Required fields of Gen that Hand leaves optional or out. */
type MissingRequired<Hand, Gen> = {
  [K in keyof Gen]-?: undefined extends Gen[K] ? never : K extends keyof Hand ? (undefined extends Hand[K] ? K : never) : K
}[keyof Gen];

/**
 * The backend model as sent. Pydantic serializes every field, also those
 * with a default, which the schema lists as optional; so for responses a
 * field is always present (null where the model allows it).
 */
type Sent<T> = T extends readonly (infer U)[]
  ? Sent<U>[]
  : T extends object ? { [K in keyof T]-?: Sent<Exclude<T[K], undefined>> } : T;

/** As Sent, for routes with response_model_exclude_none: a field that would be null is left out. */
type SentExcludeNone<T> = T extends readonly (infer U)[]
  ? SentExcludeNone<U>[]
  : T extends object
    ? { [K in keyof T as null extends T[K] ? never : K]-?: SentExcludeNone<Exclude<T[K], undefined>> } &
      { [K in keyof T as null extends T[K] ? K : never]+?: SentExcludeNone<Exclude<T[K], null | undefined>> }
    : T;

/** never when Hand reads Gen correctly; else the offending field names. */
type ResponseProblems<Hand, Gen> = ExtraKeys<Hand, Gen> | ResponseMismatch<Hand, Sent<Gen>>;
/** As ResponseProblems, for the routes with response_model_exclude_none (GET/PUT /notifications/config). */
type ExcludeNoneProblems<Hand, Gen> = ExtraKeys<Hand, Gen> | ResponseMismatch<Hand, SentExcludeNone<Gen>>;

/**
 * Fields the backend types as a plain string that the UI narrows to the
 * values the backend writes (severity, verify status, snapshot kind, SMTP
 * security) and the types that hold them, and
 * TestSyncResponse.steps (a list of plain objects in the backend model).
 * Checked without them; the rest of each type still is.
 */
type Narrowed<Hand, Keys extends keyof Hand> = Omit<Hand, Keys>;
/** never when Hand is a valid body for Gen; else the offending field names. */
type RequestProblems<Hand, Gen> = ExtraKeys<Hand, Gen> | RequestMismatch<Hand, Gen> | MissingRequired<Hand, Gen>;

describe('hand-written response types read the backend models', () => {
  it('sync and jobs', () => {
    expectTypeOf<ResponseProblems<T.ProgressFile, Schema<'ProgressFileResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.SyncProgress, Schema<'SyncProgress'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.SyncWindow, Schema<'SyncWindow'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.SyncStatus, Schema<'SyncStatusResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.SyncStartResponse, Schema<'SyncStartResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.SyncStopResponse, Schema<'SyncStopResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.SyncJob, Schema<'SyncJobResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.FileChange, Schema<'FileChangeResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.Conflict, Schema<'ConflictResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.SyncCheckResult, Schema<'SyncCheckResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.SyncPreviewCounts, Schema<'SyncPreviewCounts'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.TwoWayPreview, Schema<'TwoWayPreview'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.SyncPreview, Schema<'SyncPreviewResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.FileDiff, Schema<'FileDiff'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.DiffSummary, Schema<'DiffSummary'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.DiffPagination, Schema<'DiffPagination'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.DiffResponse, Schema<'DiffResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.FileError, Schema<'FileError'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.SelectiveSyncResponse, Schema<'SelectiveSyncResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.ManualFlagsResponse, Schema<'ManualFlagsResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.PausedProfileSummary, Schema<'PausedProfileSummary'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.ProfileSummary, Schema<'ProfileSummary'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.AggregateStatus, Schema<'AggregateStatusResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.PauseAllResponse, Schema<'PauseAllResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.TrashEntry, Schema<'TrashEntry'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.TrashList, Schema<'TrashListResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.TrashItemError, Schema<'TrashItemError'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.TrashActionResponse, Schema<'TrashActionResponse'>>>().toBeNever();
  });

  it('health, logs, config and errors', () => {
    expectTypeOf<ResponseProblems<T.Health, Schema<'HealthResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.RemoteHealth, Schema<'RemoteHealth'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.RemoteHealthResponse, Schema<'RemoteHealthResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.LogEntry, Schema<'LogEntryResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.GlobalConfig, Schema<'GlobalConfigResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<Narrowed<T.TestSyncResult, 'steps'>, Schema<'TestSyncResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.DirEntry, Schema<'DirEntry'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.BrowseResponse, Schema<'BrowseResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.ErrorResponse, Schema<'ErrorResponse'>>>().toBeNever();
  });

  it('profiles', () => {
    expectTypeOf<ResponseProblems<T.Profile, Schema<'ProfileResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.ProfileStatus, Schema<'ProfileStatusResponse'>>>().toBeNever();
  });

  it('remotes and the wizard', () => {
    expectTypeOf<ResponseProblems<T.Remote, Schema<'RemoteResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.ProviderField, Schema<'ProviderFieldResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.Provider, Schema<'ProviderResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.AuthorizeResponse, Schema<'AuthorizeResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.WizardSession, Schema<'WizardSessionResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.TestRemoteResult, Schema<'TestRemoteResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.RemoteConfigField, Schema<'RemoteConfigField'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.RemoteConfig, Schema<'RemoteConfigResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.ImportCandidate, Schema<'ImportCandidate'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.ImportPreview, Schema<'ImportPreviewResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.RemoteStorageInfo, Schema<'RemoteStorageInfoResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.RemoteTestResult, Schema<'RemoteTestResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.RemoteDependencyProfile, Schema<'RemoteDependencyProfile'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.RemoteDependencyBackupTarget, Schema<'RemoteDependencyBackupTarget'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.RemoteDependencies, Schema<'RemoteDependenciesResponse'>>>().toBeNever();
  });

  it('notifications', () => {
    expectTypeOf<ExcludeNoneProblems<T.WebhookSettings, Schema<'WebhookSettingsView'>>>().toBeNever();
    expectTypeOf<ExcludeNoneProblems<T.NtfySettings, Schema<'NtfySettingsView'>>>().toBeNever();
    expectTypeOf<ExcludeNoneProblems<Narrowed<T.EmailSettings, 'security'>, Schema<'EmailSettingsView'>>>().toBeNever();
    expectTypeOf<ExcludeNoneProblems<Narrowed<T.ChannelConfig, 'min_severity' | 'email'>, Schema<'ChannelConfig'>>>().toBeNever();
    expectTypeOf<ExcludeNoneProblems<Narrowed<T.NotificationConfig, 'channels'>, Schema<'NotificationConfigResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.ChannelStatusInfo, Schema<'ChannelStatusInfo'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.ChannelStatus, Schema<'ChannelStatusResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<Narrowed<T.NotificationLogEntry, 'severity'>, Schema<'NotificationLogEntry'>>>().toBeNever();
    expectTypeOf<ResponseProblems<Narrowed<T.NotificationHistory, 'items'>, Schema<'NotificationHistoryResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.TestNotificationResponse, Schema<'TestNotificationResponse'>>>().toBeNever();
  });

  it('backups', () => {
    expectTypeOf<ResponseProblems<Narrowed<T.BackupTarget, 'last_verify_status'>, Schema<'BackupTargetResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<Narrowed<T.BackupJob, 'verify_status'>, Schema<'BackupJobResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<Narrowed<T.Snapshot, 'kind'>, Schema<'SnapshotResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.SnapshotFileEntry, Schema<'SnapshotFileEntry'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.SnapshotFilesResponse, Schema<'SnapshotFilesResponse'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.RestorePreviewSide, Schema<'RestorePreviewSide'>>>().toBeNever();
    expectTypeOf<ResponseProblems<T.RestorePreview, Schema<'RestorePreviewResponse'>>>().toBeNever();
  });
});

describe('hand-written request types fit the backend models', () => {
  it('request bodies', () => {
    expectTypeOf<RequestProblems<T.SyncWindow, Schema<'SyncWindow'>>>().toBeNever();
    expectTypeOf<RequestProblems<T.ProfileCreateRequest, Schema<'ProfileCreateRequest'>>>().toBeNever();
    expectTypeOf<RequestProblems<T.ProfileUpdateRequest, Schema<'ProfileUpdateRequest'>>>().toBeNever();
    expectTypeOf<RequestProblems<T.SelectiveSyncItem, Schema<'SelectiveSyncItem'>>>().toBeNever();
    expectTypeOf<RequestProblems<T.CreateRemoteRequest, Schema<'CreateRemoteRequest'>>>().toBeNever();
    expectTypeOf<RequestProblems<T.UpdateRemoteRequest, Schema<'UpdateRemoteRequest'>>>().toBeNever();
    expectTypeOf<RequestProblems<T.ImportSelection, Schema<'ImportRemoteSelection'>>>().toBeNever();
    expectTypeOf<RequestProblems<T.WebhookHeaderUpdate, Schema<'WebhookHeaderUpdate'>>>().toBeNever();
    expectTypeOf<RequestProblems<T.WebhookSettingsUpdate, Schema<'WebhookSettingsUpdate'>>>().toBeNever();
    expectTypeOf<RequestProblems<T.NtfySettingsUpdate, Schema<'NtfySettingsUpdate'>>>().toBeNever();
    expectTypeOf<RequestProblems<T.EmailSettingsUpdate, Schema<'EmailSettingsUpdate'>>>().toBeNever();
    expectTypeOf<RequestProblems<T.ChannelConfigUpdate, Schema<'ChannelConfigUpdate'>>>().toBeNever();
    expectTypeOf<RequestProblems<T.NotificationConfigUpdate, Schema<'NotificationConfigUpdateRequest'>>>().toBeNever();
    expectTypeOf<RequestProblems<T.BackupTargetCreateRequest, Schema<'BackupTargetCreateRequest'>>>().toBeNever();
    expectTypeOf<RequestProblems<T.BackupTargetUpdateRequest, Schema<'BackupTargetUpdateRequest'>>>().toBeNever();
    expectTypeOf<RequestProblems<T.RestoreRequest, Schema<'RestoreRequest'>>>().toBeNever();
    expectTypeOf<RequestProblems<T.RestoreFilesRequest, Schema<'RestoreFilesRequest'>>>().toBeNever();
  });
});
