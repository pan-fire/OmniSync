package api

import (
	"context"
	"net/http"
)

// Health returns backend health information. GET /health is the only route
// that works without the API key.
func (c *Client) Health(ctx context.Context) (*HealthResponse, error) {
	return fetch[HealthResponse](ctx, c, classRead, http.MethodGet, "/health", nil)
}

// RemotesHealth checks every remote a running profile syncs with. The
// backend calls each provider (and may refresh OAuth tokens), so this is a
// probe, not something to poll on every tick. Needs the API key.
func (c *Client) RemotesHealth(ctx context.Context) (*RemoteHealthResponse, error) {
	return fetch[RemoteHealthResponse](ctx, c, classProbe, http.MethodGet, "/health/remotes", nil)
}

// NetworkHealth runs the backend's outbound network diagnostics, which can
// take more than half a minute.
func (c *Client) NetworkHealth(ctx context.Context) (*NetworkHealthResponse, error) {
	return fetch[NetworkHealthResponse](ctx, c, classProbe, http.MethodGet, "/health/network", nil)
}
