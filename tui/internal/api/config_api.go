package api

import (
	"context"
	"net/http"
)

// GetConfig returns the global configuration.
func (c *Client) GetConfig(ctx context.Context) (*GlobalConfigResponse, error) {
	return fetch[GlobalConfigResponse](ctx, c, classRead, http.MethodGet, "/config", nil)
}

// UpdateConfig updates the global configuration.
func (c *Client) UpdateConfig(ctx context.Context, req GlobalConfigUpdateRequest) (*GlobalConfigResponse, error) {
	return fetch[GlobalConfigResponse](ctx, c, classRead, http.MethodPut, "/config", req)
}

// TestSync runs a round-trip test sync between two directories.
func (c *Client) TestSync(ctx context.Context, req TestSyncRequest) (*TestSyncResponse, error) {
	return fetch[TestSyncResponse](ctx, c, classProbe, http.MethodPost, "/config/test-sync", req)
}
