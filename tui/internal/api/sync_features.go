package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
)

// --- Live progress of a running sync ---

// ProgressFile is a file a running sync is transferring right now.
type ProgressFile struct {
	Name       string `json:"name"`
	Size       *int64 `json:"size"`
	Bytes      int64  `json:"bytes"`
	Percentage *int   `json:"percentage"`
}

// SyncProgress is rclone's latest stats of a running push, pull or two-way
// sync (about once a second). Totals grow while rclone is still listing.
// Speed is in bytes per second; EtaSeconds is nil while rclone cannot
// estimate it. CurrentFiles holds at most 5 of the files in flight.
type SyncProgress struct {
	Bytes        int64          `json:"bytes"`
	TotalBytes   int64          `json:"total_bytes"`
	Speed        float64        `json:"speed"`
	EtaSeconds   *int           `json:"eta_seconds"`
	FilesDone    int            `json:"files_done"`
	FilesTotal   int            `json:"files_total"`
	Checks       int            `json:"checks"`
	TotalChecks  int            `json:"total_checks"`
	CurrentFiles []ProgressFile `json:"current_files"`
}

// Percent is the share of bytes done (files when the size is unknown), or
// -1 while rclone has no totals yet.
func (p SyncProgress) Percent() int {
	switch {
	case p.TotalBytes > 0:
		return int(min(100, p.Bytes*100/p.TotalBytes))
	case p.FilesTotal > 0:
		return min(100, p.FilesDone*100/p.FilesTotal)
	}
	return -1
}

// --- Bandwidth limit and sync window ---

// SyncWindow says when automatic syncs (file watcher, interval) may run, in
// the server's local time. Days: 0 = Monday ... 6 = Sunday, the days a
// window starts on; an End before Start spans midnight.
type SyncWindow struct {
	Days  []int  `json:"days"`
	Start string `json:"start"`
	End   string `json:"end"`
}

// WindowUpdate sets (Window) or clears (nil Window) a profile's sync window
// in a ProfileUpdateRequest; it is sent as the window or as null.
type WindowUpdate struct {
	Window *SyncWindow
}

// MarshalJSON writes the window, or null to clear it.
func (w WindowUpdate) MarshalJSON() ([]byte, error) {
	if w.Window == nil {
		return []byte("null"), nil
	}
	return json.Marshal(w.Window)
}

// --- Pause all / resume all ---

// PauseAllResponse is the answer of POST /profiles/pause-all and
// /profiles/resume-all, by slug. StillPaused (resume-all): profiles that
// stay paused for another reason (differences to review, a restore, a
// needed resync), slug -> why; each needs that profile's own resume.
type PauseAllResponse struct {
	Changed     []string          `json:"changed"`
	Unchanged   []string          `json:"unchanged"`
	StillPaused map[string]string `json:"still_paused"`
}

// PauseAllProfiles pauses automatic syncing of every enabled profile
// ("Paused by user", kept across restarts). Syncs the user starts still run.
func (c *Client) PauseAllProfiles(ctx context.Context) (*PauseAllResponse, error) {
	return fetch[PauseAllResponse](ctx, c, classRead, http.MethodPost, "/profiles/pause-all", nil)
}

// ResumeAllProfiles lifts the user's pause of every profile. Pauses
// OmniSync set itself stay (StillPaused).
func (c *Client) ResumeAllProfiles(ctx context.Context) (*PauseAllResponse, error) {
	return fetch[PauseAllResponse](ctx, c, classProbe, http.MethodPost, "/profiles/resume-all", nil)
}

// PauseProfile pauses automatic syncing of one profile for the user;
// ResumeProfileIntervals lifts it.
func (c *Client) PauseProfile(ctx context.Context, slug string) (*ResumeIntervalsResponse, error) {
	return fetch[ResumeIntervalsResponse](ctx, c, classRead, http.MethodPost, escapedPath("/profiles/%s/sync/pause", slug), nil)
}

// --- Trash (.omnisync-trash) ---

// TrashSide is the folder whose trash is meant.
type TrashSide string

const (
	TrashSideLocal  TrashSide = "local"
	TrashSideRemote TrashSide = "remote"
)

// TrashEntry is a file in a profile's .omnisync-trash. ID is
// "<folder>/<path>": Folder the sync's timestamp folder, Path where the
// file was, relative to the synced folder.
type TrashEntry struct {
	ID        string  `json:"id"`
	Folder    string  `json:"folder"`
	Path      string  `json:"path"`
	Size      *int64  `json:"size"`
	Modified  *string `json:"modified"`
	TrashedAt *string `json:"trashed_at"`
}

// TrashListResponse lists one side's trash, newest sync first. The list is
// capped (Truncated); the totals count every file.
type TrashListResponse struct {
	Side       TrashSide    `json:"side"`
	Entries    []TrashEntry `json:"entries"`
	TotalFiles int          `json:"total_files"`
	TotalBytes int64        `json:"total_bytes"`
	Truncated  bool         `json:"truncated"`
}

// TrashActionRequest restores or deletes trash entries. Overwrite (restore
// only) also replaces a newer file at the original place; the replaced
// version goes to the trash first.
type TrashActionRequest struct {
	Side      TrashSide `json:"side"`
	IDs       []string  `json:"ids"`
	Overwrite bool      `json:"overwrite,omitempty"`
}

// TrashItemError is why one entry was not restored or deleted. Code:
// not_found, target_newer, target_is_folder, invalid or failed.
type TrashItemError struct {
	ID      string `json:"id"`
	Code    string `json:"code"`
	Message string `json:"message"`
}

// TrashActionResponse lists the entries done and the ones that failed.
type TrashActionResponse struct {
	Done   []string         `json:"done"`
	Failed []TrashItemError `json:"failed"`
}

// ProfileTrash lists one side's trash of a profile (the remote side is
// listed through rclone, so it gets the probe deadline).
func (c *Client) ProfileTrash(ctx context.Context, slug string, side TrashSide) (*TrashListResponse, error) {
	path := withQuery(escapedPath("/profiles/%s/trash", slug), url.Values{"side": {string(side)}})
	return fetch[TrashListResponse](ctx, c, classProbe, http.MethodGet, path, nil)
}

// RestoreFromTrash moves trashed files back to where they were.
func (c *Client) RestoreFromTrash(ctx context.Context, slug string, req TrashActionRequest) (*TrashActionResponse, error) {
	return fetch[TrashActionResponse](ctx, c, classLong, http.MethodPost, escapedPath("/profiles/%s/trash/restore", slug), req)
}

// DeleteFromTrash deletes trashed files for good. Call it only after the
// user confirmed.
func (c *Client) DeleteFromTrash(ctx context.Context, slug string, req TrashActionRequest) (*TrashActionResponse, error) {
	return fetch[TrashActionResponse](ctx, c, classLong, http.MethodPost, escapedPath("/profiles/%s/trash/delete", slug), req)
}
