package api

import (
	"errors"
	"fmt"
	"net/http"
)

// The types in this file mirror the Pydantic models in backend/api/schemas.py.
// Field names and JSON tags must match the backend exactly; the contract
// tests in tui/test/contract decode fixtures generated from those models.
// Timestamps are kept as the ISO 8601 strings the backend sends.

// --- Enums ---

type SyncState string

const (
	SyncStateIdle    SyncState = "idle"
	SyncStatePushing SyncState = "pushing"
	SyncStatePulling SyncState = "pulling"
	// SyncStateSyncing: a two-way sync (or a resync) is running.
	SyncStateSyncing SyncState = "syncing"
	SyncStateError   SyncState = "error"
)

// Busy reports whether a sync is running in this state.
func (s SyncState) Busy() bool {
	return s == SyncStatePushing || s == SyncStatePulling || s == SyncStateSyncing
}

// SyncDirection is what POST /profiles/{slug}/sync/start runs. Push and pull
// are one-way mirrors and exist in both modes; two_way ("Sync now") is only
// for two_way profiles.
type SyncDirection string

const (
	SyncDirectionPush   SyncDirection = "push"
	SyncDirectionPull   SyncDirection = "pull"
	SyncDirectionTwoWay SyncDirection = "two_way"
)

// SyncMode is how a profile syncs automatically.
type SyncMode string

const (
	// SyncModeTwoWay carries changes both ways (rclone bisync); a file
	// changed on both sides keeps both versions. The default for new
	// profiles.
	SyncModeTwoWay SyncMode = "two_way"
	// SyncModeMirror pushes local changes and pulls the remote on the
	// interval; the side that syncs last wins.
	SyncModeMirror SyncMode = "mirror"
)

// JobDirection is the kind of a sync job in the job history.
type JobDirection string

const (
	JobDirectionPush      JobDirection = "push"
	JobDirectionPull      JobDirection = "pull"
	JobDirectionSelective JobDirection = "selective"
	// JobDirectionTwoWay is a two-way sync (rclone bisync).
	JobDirectionTwoWay JobDirection = "two_way"
	// JobDirectionResync is a two-way resync: the union of both sides,
	// nothing deleted.
	JobDirectionResync JobDirection = "resync"
)

// FileSide is the folder a recorded file change happened in.
type FileSide string

const (
	FileSideLocal  FileSide = "local"
	FileSideRemote FileSide = "remote"
)

type JobStatus string

const (
	JobStatusRunning   JobStatus = "running"
	JobStatusCompleted JobStatus = "completed"
	JobStatusFailed    JobStatus = "failed"
)

type ChangeCategory string

const (
	ChangeCategoryLocalOnly      ChangeCategory = "local_only"
	ChangeCategoryRemoteOnly     ChangeCategory = "remote_only"
	ChangeCategoryModifiedLocal  ChangeCategory = "modified_local"
	ChangeCategoryModifiedRemote ChangeCategory = "modified_remote"
	ChangeCategoryModifiedBoth   ChangeCategory = "modified_both"
)

type FileAction string

const (
	FileActionPush     FileAction = "push"
	FileActionPull     FileAction = "pull"
	FileActionSkip     FileAction = "skip"
	FileActionManual   FileAction = "manual"
	FileActionKeepBoth FileAction = "keep_both"
)

type ConflictResolution string

const (
	ConflictKeepLocal  ConflictResolution = "keep_local"
	ConflictKeepRemote ConflictResolution = "keep_remote"
	ConflictKeepBoth   ConflictResolution = "keep_both"
	// ConflictDismiss closes the conflict record and changes no file.
	ConflictDismiss ConflictResolution = "dismiss"
)

type BackupTargetType string

const (
	BackupTargetLocal        BackupTargetType = "local"
	BackupTargetRemote       BackupTargetType = "remote"
	BackupTargetCustomRemote BackupTargetType = "custom_remote"
)

type BackupMode string

const (
	BackupModeArchive BackupMode = "archive"
	BackupModeMirror  BackupMode = "mirror"
)

type BackupJobStatus string

const (
	BackupJobRunning   BackupJobStatus = "running"
	BackupJobCompleted BackupJobStatus = "completed"
	BackupJobFailed    BackupJobStatus = "failed"
	BackupJobSkipped   BackupJobStatus = "skipped"
)

type RestoreScope string

const (
	RestoreScopeLocalOnly  RestoreScope = "local_only"
	RestoreScopeRemoteOnly RestoreScope = "remote_only"
	RestoreScopeBoth       RestoreScope = "both"
)

type NotificationSeverity string

const (
	SeverityDebug   NotificationSeverity = "debug"
	SeverityInfo    NotificationSeverity = "info"
	SeverityWarning NotificationSeverity = "warning"
	SeverityError   NotificationSeverity = "error"
)

type AuthType string

const (
	AuthTypeKey   AuthType = "key"
	AuthTypeOAuth AuthType = "oauth"
)

type FieldType string

const (
	FieldTypeText FieldType = "text"
	// FieldTypePassword is a secret: never shown again once stored.
	FieldTypePassword FieldType = "password"
	// FieldTypeSelect takes one of ProviderField.Options.
	FieldTypeSelect FieldType = "select"
	// FieldTypeRemotePath is "<existing remote>:<folder>" (a crypt
	// remote's target).
	FieldTypeRemotePath FieldType = "remote_path"
)

type WizardSessionStatus string

const (
	WizardPending   WizardSessionStatus = "pending"
	WizardCompleted WizardSessionStatus = "completed"
	WizardFailed    WizardSessionStatus = "failed"
)

// --- Error type ---

// ErrorResponse mirrors ErrorResponse: the body of every error answer
// (docs/api-errors.md). Detail is the message for people, Code the stable
// machine-readable code, Details extra data for some codes (errors,
// invalid_paths, names, replacement, retry_after), RequestID the id the
// backend's log lines of the request carry.
type ErrorResponse struct {
	Detail    string         `json:"detail"`
	Code      string         `json:"code"`
	Details   map[string]any `json:"details"`
	RequestID string         `json:"request_id"`
}

// ApiError is a non-2xx answer from the backend: the status and the parsed
// ErrorResponse. Detail is the raw body when it was not an error envelope.
// RequestID comes from the envelope or the X-Request-ID header.
type ApiError struct {
	StatusCode int
	Detail     string
	Code       string
	Details    map[string]any
	RequestID  string
	Err        error
}

func (e *ApiError) Error() string {
	if e.StatusCode == http.StatusUnauthorized {
		msg := "the backend refused the request (401 Unauthorized): set --api-key or OMNISYNC_API_KEY to the backend's API token"
		if e.Detail != "" {
			msg += " (" + e.Detail + ")"
		}
		return msg
	}
	if e.Detail != "" {
		msg := fmt.Sprintf("API error %d: %s", e.StatusCode, e.Detail)
		// A server error is the owner's to look up in the log: name the request.
		if e.StatusCode >= http.StatusInternalServerError && e.RequestID != "" {
			msg += " (request " + e.RequestID + ")"
		}
		return msg
	}
	if e.Err != nil {
		return fmt.Sprintf("API error %d: %s", e.StatusCode, e.Err.Error())
	}
	return fmt.Sprintf("API error %d", e.StatusCode)
}

func (e *ApiError) Unwrap() error {
	return e.Err
}

// IsStatus reports whether err is an *ApiError with the given HTTP status.
func IsStatus(err error, status int) bool {
	var apiErr *ApiError
	return errors.As(err, &apiErr) && apiErr.StatusCode == status
}

// HasCode reports whether err is an *ApiError with the given error code.
func HasCode(err error, code string) bool {
	var apiErr *ApiError
	return errors.As(err, &apiErr) && apiErr.Code == code
}

// IsUnauthorized reports whether the backend answered 401.
func IsUnauthorized(err error) bool {
	return IsStatus(err, http.StatusUnauthorized)
}

// --- Health ---

// HealthResponse mirrors HealthResponse. Status is "ok" or "degraded".
type HealthResponse struct {
	Status          string `json:"status"`
	RcloneInstalled bool   `json:"rclone_installed"`
	// Remote reachability is not part of GET /health; it comes from
	// GET /health/remotes (RemotesHealth).
	UptimeSeconds float64 `json:"uptime_seconds"`
	DatabaseOK    bool    `json:"database_ok"`
	// Version is the backend's release version.
	Version string `json:"version"`
}

// RemoteHealth is one remote in GET /health/remotes: whether the backend
// reached it, and the running profiles that sync with it.
type RemoteHealth struct {
	Remote     string   `json:"remote"`
	Accessible bool     `json:"accessible"`
	Profiles   []string `json:"profiles"`
	// AuthError: not accessible because the provider refused the
	// credentials.
	AuthError bool `json:"auth_error"`
}

// RemoteHealthResponse mirrors RemoteHealthResponse (GET /health/remotes).
// Remotes is empty when no running profile uses a remote.
type RemoteHealthResponse struct {
	Remotes []RemoteHealth `json:"remotes"`
}

// Healthy reports whether the backend considers itself healthy. It uses the
// backend's own verdict so the TUI, the CLI and GET /health agree.
func (h *HealthResponse) Healthy() bool {
	return h != nil && h.Status == "ok"
}

// NetworkCheck is one probe of GET /health/network (a free-form dict).
type NetworkCheck struct {
	OK    bool    `json:"ok"`
	Error *string `json:"error,omitempty"`
}

// NetworkHealthResponse is the answer of GET /health/network.
type NetworkHealthResponse struct {
	HttpxGoogle     NetworkCheck `json:"httpx_google"`
	HttpxCloudflare NetworkCheck `json:"httpx_cloudflare"`
	RcloneVersion   NetworkCheck `json:"rclone_version"`
	RcloneNetwork   NetworkCheck `json:"rclone_network"`
	DNSGoogle       NetworkCheck `json:"dns_google"`
}

// --- Sync status ---

type SyncStatusResponse struct {
	State           SyncState `json:"state"`
	LastSync        *string   `json:"last_sync"`
	CurrentJobID    *int      `json:"current_job_id"`
	FilesProcessed  int       `json:"files_processed"`
	Errors          int       `json:"errors"`
	PendingChanges  int       `json:"pending_changes"`
	IntervalsPaused bool      `json:"intervals_paused"`
	PausedAt        *string   `json:"paused_at"`
	LastError       *string   `json:"last_error"`
	// ResyncRequired: a two-way profile whose sync state is lost or
	// inconsistent. Automatic syncing is paused until the user confirms a
	// resync; LastError says why.
	ResyncRequired bool `json:"resync_required"`
	// UserPaused: the user paused automatic syncing (Pause / Pause all);
	// IntervalsPaused is then true as well. Only a resume lifts it.
	UserPaused bool `json:"user_paused"`
	// Progress is rclone's latest stats while a sync runs, else nil.
	Progress *SyncProgress `json:"progress"`
	// OutsideSyncWindow: the profile's sync window is closed now; automatic
	// syncs wait until NextWindowStart. WaitingForWindow: one is due then.
	OutsideSyncWindow bool    `json:"outside_sync_window"`
	NextWindowStart   *string `json:"next_window_start"`
	WaitingForWindow  bool    `json:"waiting_for_window"`
}

type PausedProfileSummary struct {
	Slug           string  `json:"slug"`
	Name           string  `json:"name"`
	PendingChanges int     `json:"pending_changes"`
	PausedAt       *string `json:"paused_at"`
	// UserPaused: paused by the user, not for differences to review.
	UserPaused bool `json:"user_paused"`
}

type ProfileSummary struct {
	Slug            string    `json:"slug"`
	Name            string    `json:"name"`
	State           SyncState `json:"state"`
	LastSync        *string   `json:"last_sync"`
	PendingChanges  int       `json:"pending_changes"`
	IntervalsPaused bool      `json:"intervals_paused"`
	LastError       *string   `json:"last_error"`
	ResyncRequired  bool      `json:"resync_required"`
	UserPaused      bool      `json:"user_paused"`
	// Progress is rclone's latest stats while a sync runs, else nil.
	Progress *SyncProgress `json:"progress"`
}

type AggregateStatusResponse struct {
	OverallState        SyncState              `json:"overall_state"`
	TotalPendingChanges int                    `json:"total_pending_changes"`
	PausedProfiles      []PausedProfileSummary `json:"paused_profiles"`
	ProfilesSummary     []ProfileSummary       `json:"profiles_summary"`
}

// --- Profiles ---

type ProfileResponse struct {
	ID                  int      `json:"id"`
	Slug                string   `json:"slug"`
	Name                string   `json:"name"`
	LocalDir            string   `json:"local_dir"`
	RemoteDir           string   `json:"remote_dir"`
	DebounceSeconds     int      `json:"debounce_seconds"`
	PullIntervalMinutes int      `json:"pull_interval_minutes"`
	RcloneFilter        []string `json:"rclone_filter"`
	RcloneArgs          []string `json:"rclone_args"`
	MaxRetries          int      `json:"max_retries"`
	Enabled             bool     `json:"enabled"`
	CreatedAt           string   `json:"created_at"`
	UpdatedAt           string   `json:"updated_at"`
	SyncMode            SyncMode `json:"sync_mode"`
	// MirrorNoticeDismissed: the user hid the mirror-mode explanation for
	// this profile (in the web UI or the TUI).
	MirrorNoticeDismissed bool `json:"mirror_notice_dismissed"`
	// Bwlimit is rclone --bwlimit for the profile's syncs (a rate or a
	// timetable such as "08:00,512k 19:00,10M 23:00,off"), or nil.
	Bwlimit *string `json:"bwlimit"`
	// SyncWindow limits automatic syncs to these times; nil: any time.
	SyncWindow *SyncWindow `json:"sync_window"`
}

// ShowMirrorNotice reports whether the mirror-mode explanation is shown:
// the profile mirrors and the user has not dismissed it.
func (p ProfileResponse) ShowMirrorNotice() bool {
	return !p.TwoWay() && !p.MirrorNoticeDismissed
}

// TwoWay reports whether the profile syncs both ways.
func (p ProfileResponse) TwoWay() bool {
	return p.SyncMode == SyncModeTwoWay
}

type ProfileStatusResponse struct {
	ProfileResponse
	State           SyncState `json:"state"`
	LastSync        *string   `json:"last_sync"`
	CurrentJobID    *int      `json:"current_job_id"`
	FilesProcessed  int       `json:"files_processed"`
	Errors          int       `json:"errors"`
	PendingChanges  int       `json:"pending_changes"`
	IntervalsPaused bool      `json:"intervals_paused"`
	PausedAt        *string   `json:"paused_at"`
	// LastError says why the last sync failed or was refused.
	LastError *string `json:"last_error"`
	// MaxDelete is the effective delete limit: the number of files one sync
	// may delete before rclone stops it. nil means no limit. A two-way sync
	// applies it to each side and checks it before changing anything.
	MaxDelete *int `json:"max_delete"`
	// ResyncRequired: see SyncStatusResponse.ResyncRequired.
	ResyncRequired bool `json:"resync_required"`
	// UserPaused: the user paused automatic syncing (Pause / Pause all);
	// IntervalsPaused is then true as well. Only a resume lifts it.
	UserPaused bool `json:"user_paused"`
	// Progress is rclone's latest stats while a sync runs, else nil.
	Progress *SyncProgress `json:"progress"`
	// OutsideSyncWindow: the profile's sync window is closed now; automatic
	// syncs wait until NextWindowStart. WaitingForWindow: one is due then.
	OutsideSyncWindow bool    `json:"outside_sync_window"`
	NextWindowStart   *string `json:"next_window_start"`
	WaitingForWindow  bool    `json:"waiting_for_window"`
}

type ProfileCreateRequest struct {
	Name                string   `json:"name"`
	LocalDir            string   `json:"local_dir"`
	RemoteDir           string   `json:"remote_dir"`
	DebounceSeconds     int      `json:"debounce_seconds,omitempty"`
	PullIntervalMinutes int      `json:"pull_interval_minutes,omitempty"`
	RcloneFilter        []string `json:"rclone_filter,omitempty"`
	RcloneArgs          []string `json:"rclone_args,omitempty"`
	MaxRetries          int      `json:"max_retries,omitempty"`
	// SyncMode defaults to two_way on the backend when omitted.
	SyncMode SyncMode `json:"sync_mode,omitempty"`
	// Bwlimit and SyncWindow: see ProfileResponse; omitted means none.
	Bwlimit    *string     `json:"bwlimit,omitempty"`
	SyncWindow *SyncWindow `json:"sync_window,omitempty"`
}

type ProfileUpdateRequest struct {
	Name                *string   `json:"name,omitempty"`
	LocalDir            *string   `json:"local_dir,omitempty"`
	RemoteDir           *string   `json:"remote_dir,omitempty"`
	DebounceSeconds     *int      `json:"debounce_seconds,omitempty"`
	PullIntervalMinutes *int      `json:"pull_interval_minutes,omitempty"`
	RcloneFilter        *[]string `json:"rclone_filter,omitempty"`
	RcloneArgs          *[]string `json:"rclone_args,omitempty"`
	MaxRetries          *int      `json:"max_retries,omitempty"`
	// SyncMode nil leaves the mode unchanged. Switching to two_way makes the
	// next sync a resync (the union of both sides); switching to mirror
	// forgets the two-way state.
	SyncMode *SyncMode `json:"sync_mode,omitempty"`
	// MirrorNoticeDismissed hides (true) or shows again (false) the
	// mirror-mode explanation; nil leaves it unchanged.
	MirrorNoticeDismissed *bool `json:"mirror_notice_dismissed,omitempty"`
	// Bwlimit nil leaves the limit unchanged; "" clears it.
	Bwlimit *string `json:"bwlimit,omitempty"`
	// SyncWindow nil leaves the window unchanged; a WindowUpdate with a nil
	// Window clears it.
	SyncWindow *WindowUpdate `json:"sync_window,omitempty"`
}

// --- Sync actions ---

type SyncStartRequest struct {
	Direction SyncDirection `json:"direction"`
	Force     bool          `json:"force,omitempty"`
}

// ResyncRequest is the body of POST /profiles/{slug}/sync/resync. The
// backend refuses it unless Confirm is true.
type ResyncRequest struct {
	Confirm bool `json:"confirm"`
}

// SyncStartResponse answers a sync start or resync: HTTP 202 while the job
// runs (follow it with GetJob), 200 when the run ended before changing
// anything, with the refusal in Error.
type SyncStartResponse struct {
	JobID int       `json:"job_id"`
	State SyncState `json:"state"`
	Error *string   `json:"error"`
	// Note, e.g. that the sync started outside the profile's sync window.
	Note *string `json:"note"`
}

type SyncStopResponse struct {
	State   SyncState `json:"state"`
	Message string    `json:"message"`
}

// SyncPreviewCounts is what one bulk sync direction would do to its
// destination.
type SyncPreviewCounts struct {
	// Deletes counts files only on the destination (moved to .omnisync-trash).
	Deletes int `json:"deletes"`
	// Replaces counts files on both sides that differ (the old version goes
	// to .omnisync-trash).
	Replaces int `json:"replaces"`
	// Creates counts files only on the source.
	Creates int `json:"creates"`
	// ExceedsMaxDelete is true when the sync would stop at the delete limit.
	ExceedsMaxDelete bool `json:"exceeds_max_delete"`
}

// TwoWayPreview is what the next two-way sync of a two_way profile would do
// (rclone bisync --dry-run).
type TwoWayPreview struct {
	// Local counts the changes to the local folder, Remote those to the
	// remote. Deleted and replaced files go to that side's .omnisync-trash;
	// ExceedsMaxDelete marks a side that would lose more files than the
	// delete limit (the sync then stops before changing anything).
	Local  SyncPreviewCounts `json:"local"`
	Remote SyncPreviewCounts `json:"remote"`
	// Conflicts counts files changed on both sides; both versions are kept.
	Conflicts int `json:"conflicts"`
	// Resync: the next run is a resync (the union of both sides, nothing
	// deleted).
	Resync bool `json:"resync"`
	// ResyncRequired: automatic syncing is paused until the user confirms a
	// resync.
	ResyncRequired bool `json:"resync_required"`
	// Error is set when the dry run failed.
	Error *string `json:"error"`
}

// SyncPreviewResponse is the answer of POST /profiles/{slug}/sync/preview: a
// push and a pull, counted, with nothing changed on the server. For a
// two_way profile TwoWay previews the next two-way sync as well.
type SyncPreviewResponse struct {
	Push SyncPreviewCounts `json:"push"`
	Pull SyncPreviewCounts `json:"pull"`
	// Excluded counts differing files bulk syncs leave out (manual flags,
	// unresolved conflicts).
	Excluded int `json:"excluded"`
	// MaxDelete is the profile's effective delete limit; nil means no limit.
	MaxDelete *int           `json:"max_delete"`
	Error     *string        `json:"error"`
	SyncMode  SyncMode       `json:"sync_mode"`
	TwoWay    *TwoWayPreview `json:"two_way"`
}

// Counts returns the counts for a push or a pull (TwoWay holds the counts of
// a two-way sync).
func (p *SyncPreviewResponse) Counts(dir SyncDirection) SyncPreviewCounts {
	if dir == SyncDirectionPull {
		return p.Pull
	}
	return p.Push
}

// --- Diff ---

type FileDiff struct {
	Path          string         `json:"path"`
	Category      ChangeCategory `json:"category"`
	LocalSize     *int64         `json:"local_size"`
	RemoteSize    *int64         `json:"remote_size"`
	LocalModTime  *string        `json:"local_mod_time"`
	RemoteModTime *string        `json:"remote_mod_time"`
	IsConflict    bool           `json:"is_conflict"`
	ManualFlag    bool           `json:"manual_flag"`
}

type DiffSummary struct {
	LocalOnly      int `json:"local_only"`
	RemoteOnly     int `json:"remote_only"`
	ModifiedLocal  int `json:"modified_local"`
	ModifiedRemote int `json:"modified_remote"`
	ModifiedBoth   int `json:"modified_both"`
	Manual         int `json:"manual"`
	Total          int `json:"total"`
}

type DiffPagination struct {
	Offset  int  `json:"offset"`
	Limit   int  `json:"limit"`
	Total   int  `json:"total"`
	HasMore bool `json:"has_more"`
}

type DiffResponse struct {
	Files      []FileDiff      `json:"files"`
	Summary    DiffSummary     `json:"summary"`
	Pagination *DiffPagination `json:"pagination"`
	Error      *string         `json:"error"`
}

// --- Selective sync ---

type SelectiveSyncItem struct {
	Path   string     `json:"path"`
	Action FileAction `json:"action"`
}

type SelectiveSyncRequest struct {
	Items []SelectiveSyncItem `json:"items"`
}

type FileError struct {
	Path  string `json:"path"`
	Error string `json:"error"`
}

// SelectiveSyncResponse describes a selective sync run. The start answers
// at once with Status "running"; Errors fills in once the run has ended.
type SelectiveSyncResponse struct {
	JobID int `json:"job_id"`
	// Status is "running", "completed" or "failed".
	Status    JobStatus   `json:"status"`
	Total     int         `json:"total"`
	Succeeded int         `json:"succeeded"`
	Failed    int         `json:"failed"`
	Errors    []FileError `json:"errors"`
}

// SyncCheckResponse is the answer of POST /profiles/{slug}/sync/check.
type SyncCheckResponse struct {
	HasChanges bool     `json:"has_changes"`
	LocalOnly  []string `json:"local_only"`
	RemoteOnly []string `json:"remote_only"`
	Differ     []string `json:"differ"`
	Error      *string  `json:"error"`
}

// ManualFlagsResponse lists the files flagged for manual handling.
type ManualFlagsResponse struct {
	Flags []string `json:"flags"`
}

type ResumeIntervalsResponse struct {
	Detail string `json:"detail"`
}

// --- Jobs ---

type SyncJobResponse struct {
	ID           int          `json:"id"`
	Direction    JobDirection `json:"direction"`
	StartedAt    string       `json:"started_at"`
	FinishedAt   *string      `json:"finished_at"`
	Status       JobStatus    `json:"status"`
	FilesChanged int          `json:"files_changed"`
	Conflicts    int          `json:"conflicts"`
	Errors       int          `json:"errors"`
	ProfileSlug  *string      `json:"profile_slug"`
	ProfileName  *string      `json:"profile_name"`
}

type FileChangeResponse struct {
	ID        int    `json:"id"`
	JobID     int    `json:"job_id"`
	FilePath  string `json:"file_path"`
	Action    string `json:"action"`
	SizeBytes *int64 `json:"size_bytes"`
	// Side is the folder that changed; nil for changes recorded before it
	// was known.
	Side *FileSide `json:"side"`
}

// --- Conflicts ---

type ConflictResponse struct {
	ID int `json:"id"`
	// JobID is nil for a conflict a diff found (no sync job involved).
	JobID          *int                `json:"job_id"`
	FilePath       string              `json:"file_path"`
	LocalModified  *string             `json:"local_modified"`
	RemoteModified *string             `json:"remote_modified"`
	Resolved       bool                `json:"resolved"`
	Resolution     *ConflictResolution `json:"resolution"`
	ProfileSlug    *string             `json:"profile_slug"`
	ProfileName    *string             `json:"profile_name"`
	// LocalKeptAs and RemoteKeptAs are set for a conflict a two-way sync
	// found: both versions already exist on both sides under these names
	// (one of them is normally FilePath). keep_local / keep_remote keep only
	// that version under FilePath (the other copy goes to .omnisync-trash);
	// keep_both and dismiss close the record and leave both files.
	LocalKeptAs  *string `json:"local_kept_as"`
	RemoteKeptAs *string `json:"remote_kept_as"`
}

// TwoWay reports whether a two-way sync found this conflict and already kept
// both versions.
func (c ConflictResponse) TwoWay() bool {
	return (c.LocalKeptAs != nil && *c.LocalKeptAs != "") || (c.RemoteKeptAs != nil && *c.RemoteKeptAs != "")
}

type ConflictResolveRequest struct {
	Resolution ConflictResolution `json:"resolution"`
}

// --- Remotes ---

// DirEntry is one folder in a directory listing.
type DirEntry struct {
	Name string `json:"name"`
	Path string `json:"path"`
}

// BrowseResponse is the answer of GET /browse/local and /browse/remote: the
// folders inside Current. Parent is nil at the top of what may be browsed.
type BrowseResponse struct {
	Current string     `json:"current"`
	Parent  *string    `json:"parent"`
	Entries []DirEntry `json:"entries"`
}

// RemoteResponse is one remote of GET /remotes, with what OmniSync can do
// with it: ProviderID is the wizard provider of its type (nil for types
// the wizard does not offer), Editable means PUT /remotes/{name} works,
// Reconnectable that it signs in with OAuth. AuthError: its last test,
// health check or sync was refused by the provider.
type RemoteResponse struct {
	Name          string  `json:"name"`
	Type          string  `json:"type"`
	LastVerified  *string `json:"last_verified"`
	ProviderID    *string `json:"provider_id"`
	Editable      bool    `json:"editable"`
	Reconnectable bool    `json:"reconnectable"`
	AuthError     bool    `json:"auth_error"`
}

type RemoteTestResponse struct {
	Success   bool    `json:"success"`
	LatencyMs *int    `json:"latency_ms"`
	Error     *string `json:"error"`
	// AuthError: the provider refused the credentials.
	AuthError bool `json:"auth_error"`
}

// RemoteConfigField is one setting of an existing remote. A secret's value
// is never sent: IsSet says whether one is stored.
type RemoteConfigField struct {
	Name   string `json:"name"`
	Value  string `json:"value"`
	IsSet  bool   `json:"is_set"`
	Secret bool   `json:"secret"`
}

// RemoteConfigResponse is GET /remotes/{name}/config: the wizard fields of
// a remote, secrets masked, no token. OtherKeys names the settings the
// wizard has no field for; an update keeps them.
type RemoteConfigResponse struct {
	Name       string              `json:"name"`
	Type       string              `json:"type"`
	ProviderID string              `json:"provider_id"`
	Fields     []RemoteConfigField `json:"fields"`
	OtherKeys  []string            `json:"other_keys"`
}

// UpdateRemoteRequest is the body of PUT /remotes/{name}. Left-out fields
// stay; a non-secret set to "" is removed; a secret set to "" is kept and
// another value replaces it; Clear removes secrets.
type UpdateRemoteRequest struct {
	Params map[string]string `json:"params"`
	Clear  []string          `json:"clear"`
}

// ImportConfigRequest is the body of POST /remotes/import/preview.
type ImportConfigRequest struct {
	Content string `json:"content"`
}

// ImportRemoteSelection names a section of the uploaded file and the name
// to add it under (nil: its own).
type ImportRemoteSelection struct {
	Source string  `json:"source"`
	Name   *string `json:"name,omitempty"`
}

// ImportRemotesRequest is the body of POST /remotes/import.
type ImportRemotesRequest struct {
	Content string                  `json:"content"`
	Remotes []ImportRemoteSelection `json:"remotes"`
}

// ImportCandidate is one remote of an uploaded rclone.conf. Exists: a
// remote of that name is configured already. Problems: why it cannot be
// imported. Values are never returned.
type ImportCandidate struct {
	Name     string   `json:"name"`
	Type     string   `json:"type"`
	Exists   bool     `json:"exists"`
	Problems []string `json:"problems"`
	Keys     []string `json:"keys"`
}

// ImportPreviewResponse is the answer of POST /remotes/import/preview.
// Errors: the file as a whole could not be read.
type ImportPreviewResponse struct {
	Remotes []ImportCandidate `json:"remotes"`
	Errors  []string          `json:"errors"`
}

// ImportRemotesResponse lists the names the remotes were added under.
type ImportRemotesResponse struct {
	Imported []string `json:"imported"`
}

type RemoteStorageInfoResponse struct {
	TotalBytes   *int64 `json:"total_bytes"`
	UsedBytes    *int64 `json:"used_bytes"`
	FreeBytes    *int64 `json:"free_bytes"`
	TrashedBytes *int64 `json:"trashed_bytes"`
	Supported    bool   `json:"supported"`
}

type RemoteDependencyProfile struct {
	Slug string `json:"slug"`
	Name string `json:"name"`
}

type RemoteDependencyBackupTarget struct {
	ProfileSlug string `json:"profile_slug"`
	TargetName  string `json:"target_name"`
	TargetID    int    `json:"target_id"`
}

type RemoteDependenciesResponse struct {
	Profiles      []RemoteDependencyProfile      `json:"profiles"`
	BackupTargets []RemoteDependencyBackupTarget `json:"backup_targets"`
}

// Empty reports whether nothing depends on the remote.
func (d *RemoteDependenciesResponse) Empty() bool {
	return d == nil || (len(d.Profiles) == 0 && len(d.BackupTargets) == 0)
}

// --- Wizard ---

type ProviderField struct {
	Name      string    `json:"name"`
	Label     string    `json:"label"`
	FieldType FieldType `json:"field_type"`
	Required  bool      `json:"required"`
	HelpText  string    `json:"help_text"`
	// Options are the allowed values of a FieldTypeSelect field.
	Options []string `json:"options"`
	// Default is the value a new remote starts with.
	Default string `json:"default"`
}

type ProviderResponse struct {
	ID          string          `json:"id"`
	DisplayName string          `json:"display_name"`
	Icon        string          `json:"icon"`
	AuthType    AuthType        `json:"auth_type"`
	Fields      []ProviderField `json:"fields"`
	DefaultName string          `json:"default_name"`
	SetupGuide  string          `json:"setup_guide"`
}

type AuthorizeRequest struct {
	ProviderID   string  `json:"provider_id"`
	ClientID     *string `json:"client_id,omitempty"`
	ClientSecret *string `json:"client_secret,omitempty"`
	// RemoteName reconnects that existing remote: the completed session
	// then goes to POST /wizard/reconnect, which replaces only its token.
	RemoteName *string `json:"remote_name,omitempty"`
}

// ReconnectRemoteRequest is the body of POST /wizard/reconnect.
type ReconnectRemoteRequest struct {
	Name      string `json:"name"`
	SessionID string `json:"session_id"`
}

// AuthorizeResponse is the answer of POST /wizard/authorize. The provider
// redirects the browser to the backend's callback (RedirectURI), so the
// session completes by itself; the TUI polls it.
type AuthorizeResponse struct {
	SessionID   string `json:"session_id"`
	AuthURL     string `json:"auth_url"`
	RedirectURI string `json:"redirect_uri"`
}

// OAuthRedirectResponse is the answer of GET /wizard/oauth/redirect-uri: the
// exact redirect URI the user registers with their own OAuth app.
type OAuthRedirectResponse struct {
	RedirectURI string `json:"redirect_uri"`
}

// WizardSessionResponse is the answer of GET /wizard/sessions/{id}. The
// backend no longer returns the OAuth token; /wizard/create looks it up
// from the session id.
type WizardSessionResponse struct {
	SessionID string              `json:"session_id"`
	Status    WizardSessionStatus `json:"status"`
	AuthURL   *string             `json:"auth_url"`
	Error     *string             `json:"error"`
}

// CreateRemoteRequest is the body of POST /wizard/create. OAuth remotes send
// the completed wizard session id instead of a token.
type CreateRemoteRequest struct {
	Name       string            `json:"name"`
	ProviderID string            `json:"provider_id"`
	Params     map[string]string `json:"params"`
	SessionID  *string           `json:"session_id,omitempty"`
}

type TestRemoteRequest struct {
	Name string `json:"name"`
}

type TestRemoteResponse struct {
	Success bool    `json:"success"`
	Error   *string `json:"error"`
	// AuthError: the provider refused the credentials.
	AuthError bool `json:"auth_error"`
}

// --- Backups ---

type BackupTargetResponse struct {
	ID                int              `json:"id"`
	ProfileID         int              `json:"profile_id"`
	Name              string           `json:"name"`
	TargetPath        string           `json:"target_path"`
	TargetType        BackupTargetType `json:"target_type"`
	RemoteName        *string          `json:"remote_name"`
	RetentionDays     int              `json:"retention_days"`
	KeepLast          int              `json:"keep_last"`
	FrequencyHours    int              `json:"frequency_hours"`
	BackupMode        BackupMode       `json:"backup_mode"`
	Enabled           bool             `json:"enabled"`
	Encrypted         bool             `json:"encrypted"`
	VerifyAfterBackup bool             `json:"verify_after_backup"`
	Overdue           bool             `json:"overdue"`
	LastLivenessOK    *bool            `json:"last_liveness_ok"`
	LastLivenessError *string          `json:"last_liveness_error"`
	LastBackupAt      *string          `json:"last_backup_at"`
	LastBackupStatus  *string          `json:"last_backup_status"`
	LastVerifyStatus  *string          `json:"last_verify_status"`
	LastVerifyMessage *string          `json:"last_verify_message"`
	NextScheduledAt   *string          `json:"next_scheduled_at"`
	CreatedAt         string           `json:"created_at"`
	UpdatedAt         string           `json:"updated_at"`
}

type BackupTargetCreateRequest struct {
	Name           string           `json:"name"`
	TargetPath     string           `json:"target_path"`
	TargetType     BackupTargetType `json:"target_type"`
	RemoteName     *string          `json:"remote_name,omitempty"`
	RetentionDays  int              `json:"retention_days,omitempty"`
	KeepLast       int              `json:"keep_last,omitempty"`
	FrequencyHours int              `json:"frequency_hours,omitempty"`
	BackupMode     BackupMode       `json:"backup_mode,omitempty"`
	Enabled        *bool            `json:"enabled,omitempty"`
	// Encrypts the target; never returned by the backend. Losing it loses the backups.
	EncryptionPassphrase *string `json:"encryption_passphrase,omitempty"`
	VerifyAfterBackup    *bool   `json:"verify_after_backup,omitempty"`
}

type BackupTargetUpdateRequest struct {
	Name              *string           `json:"name,omitempty"`
	TargetPath        *string           `json:"target_path,omitempty"`
	TargetType        *BackupTargetType `json:"target_type,omitempty"`
	RemoteName        *string           `json:"remote_name,omitempty"`
	RetentionDays     *int              `json:"retention_days,omitempty"`
	KeepLast          *int              `json:"keep_last,omitempty"`
	FrequencyHours    *int              `json:"frequency_hours,omitempty"`
	BackupMode        *BackupMode       `json:"backup_mode,omitempty"`
	Enabled           *bool             `json:"enabled,omitempty"`
	VerifyAfterBackup *bool             `json:"verify_after_backup,omitempty"`
}

type BackupJobResponse struct {
	ID         int             `json:"id"`
	TargetID   int             `json:"target_id"`
	StartedAt  string          `json:"started_at"`
	FinishedAt *string         `json:"finished_at"`
	Status     BackupJobStatus `json:"status"`
	Direction  string          `json:"direction"`
	SizeBytes  *int64          `json:"size_bytes"`
	SnapshotID *string         `json:"snapshot_id"`
	// ErrorCode names why the job failed or was skipped (path_overlap,
	// target_unreachable, backup_failed, interrupted, ...); nil otherwise.
	ErrorCode *string `json:"error_code"`
	// ErrorMessage is a public message for ErrorCode (never rclone output).
	ErrorMessage *string `json:"error_message"`
	// "verified", "failed" or nil (not verified).
	VerifyStatus  *string `json:"verify_status"`
	VerifyMessage *string `json:"verify_message"`
}

type SnapshotResponse struct {
	SnapshotID string `json:"snapshot_id"`
	CreatedAt  string `json:"created_at"`
	SizeBytes  *int64 `json:"size_bytes"`
	Status     string `json:"status"`
	// Latest marks the most recent backup of the target.
	Latest bool `json:"latest"`
}

type RestoreRequest struct {
	SnapshotID   string       `json:"snapshot_id"`
	RestoreScope RestoreScope `json:"restore_scope"`
}

// SnapshotFileEntry is one file or folder of a snapshot.
type SnapshotFileEntry struct {
	Path      string  `json:"path"`
	Name      string  `json:"name"`
	IsDir     bool    `json:"is_dir"`
	Size      *int64  `json:"size"`
	ModTime   *string `json:"mod_time"`
	FileCount *int    `json:"file_count"`
}

// SnapshotFilesResponse is one page of a snapshot folder (or search).
type SnapshotFilesResponse struct {
	SnapshotID    string              `json:"snapshot_id"`
	Path          string              `json:"path"`
	Search        *string             `json:"search"`
	Entries       []SnapshotFileEntry `json:"entries"`
	Total         int                 `json:"total"`
	Offset        int                 `json:"offset"`
	Limit         int                 `json:"limit"`
	SnapshotFiles int                 `json:"snapshot_files"`
}

// RestoreFilesRequest restores chosen files and folders of a snapshot, to
// their original place (TargetDir nil) or into another local folder.
type RestoreFilesRequest struct {
	SnapshotID string   `json:"snapshot_id"`
	Paths      []string `json:"paths"`
	TargetDir  *string  `json:"target_dir,omitempty"`
}

// RestorePreviewSide is what a full restore would change in one folder.
type RestorePreviewSide struct {
	Side             string   `json:"side"`
	Path             string   `json:"path"`
	Added            int      `json:"added"`
	Replaced         int      `json:"replaced"`
	Removed          int      `json:"removed"`
	Unchanged        int      `json:"unchanged"`
	AddedExamples    []string `json:"added_examples"`
	ReplacedExamples []string `json:"replaced_examples"`
	RemovedExamples  []string `json:"removed_examples"`
}

type RestorePreviewResponse struct {
	SnapshotID   string               `json:"snapshot_id"`
	RestoreScope RestoreScope         `json:"restore_scope"`
	Sides        []RestorePreviewSide `json:"sides"`
}

// --- Notifications ---

// ChannelConfig is a channel's configuration as the backend reports it.
// Webhook, Ntfy and Email are set for the channel of that name only;
// secrets are never returned, only whether one is stored (*Set).
type ChannelConfig struct {
	Enabled     bool                 `json:"enabled"`
	MinSeverity NotificationSeverity `json:"min_severity"`
	Webhook     *WebhookSettingsView `json:"webhook,omitempty"`
	Ntfy        *NtfySettingsView    `json:"ntfy,omitempty"`
	Email       *EmailSettingsView   `json:"email,omitempty"`
}

type WebhookHeaderView struct {
	Name     string `json:"name"`
	ValueSet bool   `json:"value_set"`
}

type WebhookSettingsView struct {
	URL       string              `json:"url"`
	AllowHTTP bool                `json:"allow_http"`
	Headers   []WebhookHeaderView `json:"headers"`
}

type NtfySettingsView struct {
	Server      string `json:"server"`
	Topic       string `json:"topic"`
	AllowHTTP   bool   `json:"allow_http"`
	Username    string `json:"username"`
	TokenSet    bool   `json:"token_set"`
	PasswordSet bool   `json:"password_set"`
}

type EmailSettingsView struct {
	Host        string   `json:"host"`
	Port        int      `json:"port"`
	Security    string   `json:"security"`
	Username    string   `json:"username"`
	PasswordSet bool     `json:"password_set"`
	FromAddr    string   `json:"from_addr"`
	To          []string `json:"to"`
}

type NotificationConfigResponse struct {
	Channels map[string]ChannelConfig `json:"channels"`
}

// ChannelConfigUpdate changes only the fields that are set. A secret left
// empty keeps the stored one; Clear removes it.
type ChannelConfigUpdate struct {
	Enabled     *bool                  `json:"enabled,omitempty"`
	MinSeverity *NotificationSeverity  `json:"min_severity,omitempty"`
	Webhook     *WebhookSettingsUpdate `json:"webhook,omitempty"`
	Ntfy        *NtfySettingsUpdate    `json:"ntfy,omitempty"`
	Email       *EmailSettingsUpdate   `json:"email,omitempty"`
}

// WebhookHeaderUpdate is one header; an empty Value keeps the stored value
// of a header of the same name.
type WebhookHeaderUpdate struct {
	Name  string `json:"name"`
	Value string `json:"value"`
}

// WebhookSettingsUpdate: Headers, when set, replaces the stored list.
type WebhookSettingsUpdate struct {
	URL       *string                `json:"url,omitempty"`
	AllowHTTP *bool                  `json:"allow_http,omitempty"`
	Headers   *[]WebhookHeaderUpdate `json:"headers,omitempty"`
}

type NtfySettingsUpdate struct {
	Server    *string  `json:"server,omitempty"`
	Topic     *string  `json:"topic,omitempty"`
	AllowHTTP *bool    `json:"allow_http,omitempty"`
	Username  *string  `json:"username,omitempty"`
	Token     *string  `json:"token,omitempty"`
	Password  *string  `json:"password,omitempty"`
	Clear     []string `json:"clear,omitempty"` // "token", "password"
}

type EmailSettingsUpdate struct {
	Host     *string   `json:"host,omitempty"`
	Port     *int      `json:"port,omitempty"`
	Security *string   `json:"security,omitempty"` // starttls, tls, none
	Username *string   `json:"username,omitempty"`
	Password *string   `json:"password,omitempty"`
	FromAddr *string   `json:"from_addr,omitempty"`
	To       *[]string `json:"to,omitempty"`
	Clear    []string  `json:"clear,omitempty"` // "password"
}

type NotificationConfigUpdateRequest struct {
	Channels map[string]ChannelConfigUpdate `json:"channels"`
}

// TestNotificationRequest picks one channel for POST /notifications/test;
// without a body the test goes to every enabled channel.
type TestNotificationRequest struct {
	Channel *string `json:"channel,omitempty"`
}

type ChannelStatusInfo struct {
	Available           bool     `json:"available"`
	DetectionMethod     *string  `json:"detection_method"`
	HostOS              *string  `json:"host_os"`
	MissingDependencies []string `json:"missing_dependencies"`
	PermissionStatus    *string  `json:"permission_status"`
}

type ChannelStatusResponse struct {
	Channels map[string]ChannelStatusInfo `json:"channels"`
}

type NotificationLogEntry struct {
	ID                int      `json:"id"`
	EventType         string   `json:"event_type"`
	Severity          string   `json:"severity"`
	Title             string   `json:"title"`
	Body              string   `json:"body"`
	Timestamp         string   `json:"timestamp"`
	ChannelsDelivered []string `json:"channels_delivered"`
}

type NotificationHistoryResponse struct {
	Items []NotificationLogEntry `json:"items"`
	Total int                    `json:"total"`
}

type TestNotificationResponse struct {
	Success           bool              `json:"success"`
	ChannelsDelivered []string          `json:"channels_delivered"`
	Errors            map[string]string `json:"errors"`
}

// --- Config ---

type GlobalConfigResponse struct {
	LogLevel string `json:"log_level"`
	// Days of job history kept (0: all); the newest jobs are kept regardless.
	HistoryDays int `json:"history_days"`
}

type GlobalConfigUpdateRequest struct {
	LogLevel    *string `json:"log_level,omitempty"`
	HistoryDays *int    `json:"history_days,omitempty"`
}

type TestSyncRequest struct {
	LocalDir  string `json:"local_dir"`
	RemoteDir string `json:"remote_dir"`
}

type TestSyncResponse struct {
	Success bool             `json:"success"`
	Steps   []map[string]any `json:"steps"`
	Error   *string          `json:"error"`
}

// --- Logs ---

// LogEntryResponse is one entry of the backend's log. Logger, RequestID and
// Exc are empty when the backend sent none (older backends, older lines).
type LogEntryResponse struct {
	Timestamp string `json:"timestamp"`
	Level     string `json:"level"`
	Message   string `json:"message"`
	// Logger is the logger that wrote the entry; backend.audit for the audit
	// trail of user actions.
	Logger string `json:"logger"`
	// RequestID is the id of the API request the entry was written in.
	RequestID string `json:"request_id"`
	// Exc is the traceback or further lines that belong to the entry.
	Exc string `json:"exc"`
}
