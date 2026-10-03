package api

import (
	"context"
	"net/http"
	"net/url"
)

// ListRemotes returns all configured rclone remotes.
func (c *Client) ListRemotes(ctx context.Context) ([]RemoteResponse, error) {
	return fetchList[RemoteResponse](ctx, c, classRead, http.MethodGet, "/remotes", nil)
}

// TestRemote tests connectivity to a remote.
func (c *Client) TestRemote(ctx context.Context, name string) (*RemoteTestResponse, error) {
	return fetch[RemoteTestResponse](ctx, c, classProbe, http.MethodPost, escapedPath("/remotes/%s/test", name), nil)
}

// RemoteStorageInfo returns storage information for a remote.
func (c *Client) RemoteStorageInfo(ctx context.Context, name string) (*RemoteStorageInfoResponse, error) {
	return fetch[RemoteStorageInfoResponse](ctx, c, classProbe, http.MethodGet, escapedPath("/remotes/%s/about", name), nil)
}

// RemoteDependencies returns the profiles and backup targets using a remote.
func (c *Client) RemoteDependencies(ctx context.Context, name string) (*RemoteDependenciesResponse, error) {
	return fetch[RemoteDependenciesResponse](ctx, c, classRead, http.MethodGet, escapedPath("/remotes/%s/dependencies", name), nil)
}

// DeleteRemote deletes a remote. With force the backend deletes it even if
// profiles or backup targets still use it.
func (c *Client) DeleteRemote(ctx context.Context, name string, force bool) error {
	q := url.Values{}
	if force {
		q.Set("force", "true")
	}
	return c.call(ctx, classRead, http.MethodDelete, withQuery(escapedPath("/remotes/%s", name), q), nil, nil)
}

// RemoteConfig returns a remote's settings for editing: secrets masked,
// no token.
func (c *Client) RemoteConfig(ctx context.Context, name string) (*RemoteConfigResponse, error) {
	return fetch[RemoteConfigResponse](ctx, c, classRead, http.MethodGet, escapedPath("/remotes/%s/config", name), nil)
}

// UpdateRemote changes a remote's settings in place.
func (c *Client) UpdateRemote(ctx context.Context, name string, req UpdateRemoteRequest) error {
	if req.Params == nil {
		req.Params = map[string]string{}
	}
	if req.Clear == nil {
		req.Clear = []string{}
	}
	return c.call(ctx, classProbe, http.MethodPut, escapedPath("/remotes/%s", name), req, nil)
}

// PreviewImport lists the remotes of an rclone.conf's text: which can be
// imported, which clash with an existing name, which are refused.
func (c *Client) PreviewImport(ctx context.Context, content string) (*ImportPreviewResponse, error) {
	return fetch[ImportPreviewResponse](ctx, c, classProbe, http.MethodPost, "/remotes/import/preview",
		ImportConfigRequest{Content: content})
}

// ImportRemotes adds the selected remotes of an rclone.conf's text, all or
// none.
func (c *Client) ImportRemotes(ctx context.Context, req ImportRemotesRequest) (*ImportRemotesResponse, error) {
	return fetch[ImportRemotesResponse](ctx, c, classProbe, http.MethodPost, "/remotes/import", req)
}
