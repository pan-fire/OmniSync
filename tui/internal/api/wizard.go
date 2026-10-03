package api

import (
	"context"
	"net/http"
)

// ListProviders returns the cloud storage providers the wizard supports.
func (c *Client) ListProviders(ctx context.Context) ([]ProviderResponse, error) {
	return fetchList[ProviderResponse](ctx, c, classRead, http.MethodGet, "/wizard/providers", nil)
}

// Authorize starts an OAuth flow and returns the URL the user must open.
func (c *Client) Authorize(ctx context.Context, req AuthorizeRequest) (*AuthorizeResponse, error) {
	return fetch[AuthorizeResponse](ctx, c, classRead, http.MethodPost, "/wizard/authorize", req)
}

// WizardSession returns the status of an OAuth wizard session.
func (c *Client) WizardSession(ctx context.Context, sessionID string) (*WizardSessionResponse, error) {
	return fetch[WizardSessionResponse](ctx, c, classRead, http.MethodGet, escapedPath("/wizard/sessions/%s", sessionID), nil)
}

// OAuthRedirectURI returns the redirect URI the backend uses for this
// client, which the user registers with their own OAuth app.
func (c *Client) OAuthRedirectURI(ctx context.Context) (*OAuthRedirectResponse, error) {
	return fetch[OAuthRedirectResponse](ctx, c, classRead, http.MethodGet, "/wizard/oauth/redirect-uri", nil)
}

// CancelSession cancels an OAuth wizard session.
func (c *Client) CancelSession(ctx context.Context, sessionID string) error {
	return c.call(ctx, classRead, http.MethodDelete, escapedPath("/wizard/sessions/%s", sessionID), nil, nil)
}

// CreateRemote creates an rclone remote from key-based params or, for OAuth
// providers, from a completed wizard session (req.SessionID).
func (c *Client) CreateRemote(ctx context.Context, req CreateRemoteRequest) error {
	if req.Params == nil {
		req.Params = map[string]string{}
	}
	return c.call(ctx, classProbe, http.MethodPost, "/wizard/create", req, nil)
}

// TestWizardRemote runs a connection test against a newly created remote.
func (c *Client) TestWizardRemote(ctx context.Context, name string) (*TestRemoteResponse, error) {
	return fetch[TestRemoteResponse](ctx, c, classProbe, http.MethodPost, "/wizard/test", TestRemoteRequest{Name: name})
}

// ReconnectRemote stores the token of a completed reconnect session (see
// AuthorizeRequest.RemoteName) in its remote.
func (c *Client) ReconnectRemote(ctx context.Context, name, sessionID string) error {
	return c.call(ctx, classProbe, http.MethodPost, "/wizard/reconnect",
		ReconnectRemoteRequest{Name: name, SessionID: sessionID}, nil)
}
