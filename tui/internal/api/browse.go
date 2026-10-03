package api

import (
	"context"
	"net/http"
	"net/url"
)

// BrowseLocal lists the subdirectories of a local folder on the backend's
// machine (GET /browse/local). An empty path lists the home folder. The
// backend only lists folders inside its browse roots (403 otherwise).
func (c *Client) BrowseLocal(ctx context.Context, path string) (*BrowseResponse, error) {
	q := url.Values{}
	if path != "" {
		q.Set("path", path)
	}
	return fetch[BrowseResponse](ctx, c, classRead, http.MethodGet, withQuery("/browse/local", q), nil)
}

// BrowseRemote lists the subdirectories of a remote folder (GET
// /browse/remote); path is "<remote>:" or "<remote>:<sub/path>". The backend
// asks the provider through rclone, so the call gets the probe deadline.
func (c *Client) BrowseRemote(ctx context.Context, path string) (*BrowseResponse, error) {
	q := url.Values{"path": {path}}
	return fetch[BrowseResponse](ctx, c, classProbe, http.MethodGet, withQuery("/browse/remote", q), nil)
}
