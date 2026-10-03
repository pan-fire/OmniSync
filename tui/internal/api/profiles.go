package api

import (
	"context"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// ListProfiles returns all profiles with their sync status.
func (c *Client) ListProfiles(ctx context.Context) ([]ProfileStatusResponse, error) {
	return fetchList[ProfileStatusResponse](ctx, c, classRead, http.MethodGet, "/profiles", nil)
}

// GetProfile returns a single profile by slug.
func (c *Client) GetProfile(ctx context.Context, slug string) (*ProfileStatusResponse, error) {
	return fetch[ProfileStatusResponse](ctx, c, classRead, http.MethodGet, escapedPath("/profiles/%s", slug), nil)
}

// CreateProfile creates a new sync profile.
func (c *Client) CreateProfile(ctx context.Context, req ProfileCreateRequest) (*ProfileResponse, error) {
	return fetch[ProfileResponse](ctx, c, classProbe, http.MethodPost, "/profiles", req)
}

// UpdateProfile updates an existing profile.
func (c *Client) UpdateProfile(ctx context.Context, slug string, req ProfileUpdateRequest) (*ProfileResponse, error) {
	return fetch[ProfileResponse](ctx, c, classProbe, http.MethodPut, escapedPath("/profiles/%s", slug), req)
}

// DeleteProfile deletes a profile. The backend refuses deletes without
// ?confirm=true, so call this only after the user confirmed.
func (c *Client) DeleteProfile(ctx context.Context, slug string) error {
	path := withQuery(escapedPath("/profiles/%s", slug), url.Values{"confirm": {"true"}})
	return c.call(ctx, classProbe, http.MethodDelete, path, nil, nil)
}

// EnableProfile enables a profile and starts its sync engine.
func (c *Client) EnableProfile(ctx context.Context, slug string) (*ProfileResponse, error) {
	return fetch[ProfileResponse](ctx, c, classProbe, http.MethodPost, escapedPath("/profiles/%s/enable", slug), nil)
}

// DisableProfile disables a profile and stops its sync engine.
func (c *Client) DisableProfile(ctx context.Context, slug string) (*ProfileResponse, error) {
	return fetch[ProfileResponse](ctx, c, classProbe, http.MethodPost, escapedPath("/profiles/%s/disable", slug), nil)
}

// ProfileSyncStatus returns the sync status of one profile, including
// last_error. The backend answers 404 when the profile's engine is not
// running (for example, when the profile is disabled).
func (c *Client) ProfileSyncStatus(ctx context.Context, slug string) (*SyncStatusResponse, error) {
	return fetch[SyncStatusResponse](ctx, c, classRead, http.MethodGet, escapedPath("/profiles/%s/sync/status", slug), nil)
}

// StartProfileSync starts a push, a pull or (SyncDirectionTwoWay, "Sync now")
// a two-way sync for one profile. The backend runs it in the background and
// answers (202) once rclone starts changing files, with the job to follow
// (FollowSync, WaitForSyncJob). A run its safety checks refuse (sync marker,
// empty side, delete limit, pending resync) is answered at once with state
// "error" and the reason in Error. Those checks may take a while, so the
// call gets the probe deadline.
//
// While a profile's intervals are paused the backend refuses a start (409)
// unless force is set. Set force only for a sync the user confirmed; the
// backend still runs every safety check (preflight, sync marker, empty-source
// refusal, delete limit, trash). A two-way start is also refused (409) for a
// mirror profile, while the profile needs a resync, and while another sync
// of the profile runs.
func (c *Client) StartProfileSync(ctx context.Context, slug string, dir SyncDirection, force bool) (*SyncStartResponse, error) {
	return fetch[SyncStartResponse](ctx, c, classProbe, http.MethodPost,
		escapedPath("/profiles/%s/sync/start", slug), SyncStartRequest{Direction: dir, Force: force})
}

// ResyncProfile runs a resync of a two-way profile: both folders become the
// union of both sides and nothing is deleted (where a file differs, the
// newer version wins and the older goes to .omnisync-trash). Automatic
// syncing resumes afterwards. It always sends confirm=true, so call it only
// after the user confirmed. The backend answers 409 for a mirror profile;
// otherwise it answers like StartProfileSync.
func (c *Client) ResyncProfile(ctx context.Context, slug string) (*SyncStartResponse, error) {
	return fetch[SyncStartResponse](ctx, c, classProbe, http.MethodPost,
		escapedPath("/profiles/%s/sync/resync", slug), ResyncRequest{Confirm: true})
}

// StopProfileSync stops the sync of one profile.
func (c *Client) StopProfileSync(ctx context.Context, slug string) (*SyncStopResponse, error) {
	return fetch[SyncStopResponse](ctx, c, classProbe, http.MethodPost, escapedPath("/profiles/%s/sync/stop", slug), nil)
}

// PreviewProfileSync counts what a push and a pull would delete, replace and
// create, and returns the profile's delete limit; for a two-way profile it
// also previews the next two-way sync per side. It changes nothing on the
// server (not the pending count, the cached diff or the pause state). It
// runs rclone over both trees, so it has no deadline of its own. The backend
// answers 404 when the profile is not running.
func (c *Client) PreviewProfileSync(ctx context.Context, slug string) (*SyncPreviewResponse, error) {
	return fetch[SyncPreviewResponse](ctx, c, classLong, http.MethodPost, escapedPath("/profiles/%s/sync/preview", slug), nil)
}

// ProfileDiff returns the per-file diff of a profile. limit 0 returns every
// file; otherwise the answer carries a pagination object.
func (c *Client) ProfileDiff(ctx context.Context, slug string, offset, limit int) (*DiffResponse, error) {
	q := url.Values{"offset": {strconv.Itoa(offset)}, "limit": {strconv.Itoa(limit)}}
	return fetch[DiffResponse](ctx, c, classLong, http.MethodPost, withQuery(escapedPath("/profiles/%s/diff", slug), q), nil)
}

// ProfileSelectiveSync starts applying per-file actions. The backend answers
// at once (202) with Status "running" and zero counts; follow the run with
// GetSelectiveResult or WaitForSelectiveSync. A refusal (409 no_cached_diff /
// sync_busy, 400 invalid_paths) comes back as an *ApiError.
func (c *Client) ProfileSelectiveSync(ctx context.Context, slug string, items []SelectiveSyncItem) (*SelectiveSyncResponse, error) {
	return fetch[SelectiveSyncResponse](ctx, c, classRead, http.MethodPost,
		escapedPath("/profiles/%s/sync/selective", slug), SelectiveSyncRequest{Items: items})
}

// GetSelectiveResult returns the state of a selective sync run: the counts,
// and the per-file errors once it has ended (an old run has counts only).
func (c *Client) GetSelectiveResult(ctx context.Context, slug string, jobID int) (*SelectiveSyncResponse, error) {
	return fetch[SelectiveSyncResponse](ctx, c, classRead, http.MethodGet,
		escapedPath("/profiles/%s/sync/selective/%d", slug, jobID), nil)
}

// WaitForSelectiveSync polls GetSelectiveResult every interval until the run
// is no longer running, and returns it. Failed polls are retried as in
// WaitForSyncJob. Cancel ctx to stop waiting; the run itself keeps going.
func (c *Client) WaitForSelectiveSync(ctx context.Context, slug string, jobID int, interval time.Duration) (*SelectiveSyncResponse, error) {
	return pollUntilDone(ctx, interval,
		func(ctx context.Context) (*SelectiveSyncResponse, error) {
			return c.GetSelectiveResult(ctx, slug, jobID)
		},
		func(r *SelectiveSyncResponse) bool { return r.Status != JobStatusRunning })
}

// ResumeProfileIntervals resumes paused sync intervals for a profile.
func (c *Client) ResumeProfileIntervals(ctx context.Context, slug string) (*ResumeIntervalsResponse, error) {
	return fetch[ResumeIntervalsResponse](ctx, c, classRead, http.MethodPost, escapedPath("/profiles/%s/sync/resume-intervals", slug), nil)
}

// TestProfileSync runs a round-trip test sync for a profile.
func (c *Client) TestProfileSync(ctx context.Context, slug string) (*TestSyncResponse, error) {
	return fetch[TestSyncResponse](ctx, c, classProbe, http.MethodPost, escapedPath("/profiles/%s/config/test-sync", slug), nil)
}

// CheckProfileSync compares both folders now (the startup check): it lists
// the files only on one side and those that differ, and refreshes the
// pending count. It runs rclone over both trees, so it has no deadline of
// its own. The backend answers 404 when the profile is not running.
func (c *Client) CheckProfileSync(ctx context.Context, slug string) (*SyncCheckResponse, error) {
	return fetch[SyncCheckResponse](ctx, c, classLong, http.MethodPost, escapedPath("/profiles/%s/sync/check", slug), nil)
}

// ManualFlags returns the files of a profile flagged for manual handling.
func (c *Client) ManualFlags(ctx context.Context, slug string) (*ManualFlagsResponse, error) {
	return fetch[ManualFlagsResponse](ctx, c, classRead, http.MethodGet, escapedPath("/profiles/%s/manual-flags", slug), nil)
}

// ClearManualFlag removes the manual flag of one file. path is relative to
// the profile folders; its '/' separators are kept, every segment is
// escaped. The backend answers 404 when the file has no flag.
func (c *Client) ClearManualFlag(ctx context.Context, slug, path string) error {
	segments := strings.Split(strings.TrimLeft(path, "/"), "/")
	for i, s := range segments {
		segments[i] = url.PathEscape(s)
	}
	return c.call(ctx, classRead, http.MethodDelete,
		escapedPath("/profiles/%s/manual-flags/", slug)+strings.Join(segments, "/"), nil, nil)
}
