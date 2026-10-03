package api

import (
	"context"
	"net/http"
	"net/url"
)

// ListConflicts returns unresolved conflicts, optionally for one profile.
func (c *Client) ListConflicts(ctx context.Context, profile string) ([]ConflictResponse, error) {
	q := url.Values{}
	if profile != "" {
		q.Set("profile", profile)
	}
	return fetchList[ConflictResponse](ctx, c, classRead, http.MethodGet, withQuery("/conflicts", q), nil)
}

// ResolveConflict resolves a conflict. keep_local copies the local file over
// the remote one and keep_remote the remote file over the local one (the
// replaced version goes to .omnisync-trash on that side); keep_both keeps
// both versions on both sides (the remote one as <name>.conflict-<time>);
// dismiss only closes the record and changes no file. For a conflict a
// two-way sync found (LocalKeptAs/RemoteKeptAs set) both versions already
// exist: keep_local / keep_remote keep only that version under FilePath (the
// other copy goes to .omnisync-trash), keep_both and dismiss close the
// record and leave both files. The backend answers
// 409 when the action is not possible now and 502 when rclone failed, both
// with a detail message. File actions copy through rclone, so the call has
// no deadline of its own.
func (c *Client) ResolveConflict(ctx context.Context, id int, resolution ConflictResolution) (*ConflictResponse, error) {
	return fetch[ConflictResponse](ctx, c, classLong, http.MethodPost,
		escapedPath("/conflicts/%d/resolve", id), ConflictResolveRequest{Resolution: resolution})
}
