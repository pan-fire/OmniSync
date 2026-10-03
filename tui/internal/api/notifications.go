package api

import (
	"context"
	"net/http"
	"net/url"
	"strconv"
)

// NotificationConfig returns the current notification configuration.
func (c *Client) NotificationConfig(ctx context.Context) (*NotificationConfigResponse, error) {
	return fetch[NotificationConfigResponse](ctx, c, classRead, http.MethodGet, "/notifications/config", nil)
}

// UpdateNotificationConfig updates the notification configuration.
func (c *Client) UpdateNotificationConfig(ctx context.Context, req NotificationConfigUpdateRequest) (*NotificationConfigResponse, error) {
	return fetch[NotificationConfigResponse](ctx, c, classRead, http.MethodPut, "/notifications/config", req)
}

// ChannelStatus returns the status of all notification channels.
func (c *Client) ChannelStatus(ctx context.Context) (*ChannelStatusResponse, error) {
	return fetch[ChannelStatusResponse](ctx, c, classRead, http.MethodGet, "/notifications/channels/status", nil)
}

// NotificationHistory returns notification history with pagination.
func (c *Client) NotificationHistory(ctx context.Context, limit, offset int, profile string) (*NotificationHistoryResponse, error) {
	q := url.Values{"limit": {strconv.Itoa(limit)}, "offset": {strconv.Itoa(offset)}}
	if profile != "" {
		q.Set("profile", profile)
	}
	return fetch[NotificationHistoryResponse](ctx, c, classRead, http.MethodGet, withQuery("/notifications/history", q), nil)
}

// TestNotification sends a test notification to all enabled channels.
func (c *Client) TestNotification(ctx context.Context) (*TestNotificationResponse, error) {
	return c.TestNotificationChannel(ctx, "")
}

// TestNotificationChannel sends a test notification to one channel, even a
// turned-off one, or to all enabled channels when channel is empty. The
// backend answers 404 for an unknown channel.
func (c *Client) TestNotificationChannel(ctx context.Context, channel string) (*TestNotificationResponse, error) {
	var body any
	if channel != "" {
		body = TestNotificationRequest{Channel: &channel}
	}
	return fetch[TestNotificationResponse](ctx, c, classProbe, http.MethodPost, "/notifications/test", body)
}
