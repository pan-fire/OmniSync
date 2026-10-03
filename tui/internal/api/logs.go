package api

import (
	"context"
	"net/http"
	"net/url"
	"strconv"
)

// GetLogs returns log entries, newest first.
func (c *Client) GetLogs(ctx context.Context, skip, limit int) ([]LogEntryResponse, error) {
	return c.GetLogsLevel(ctx, skip, limit, "")
}

// GetLogsLevel returns log entries of one level (DEBUG, INFO, WARNING, ERROR
// or CRITICAL; empty for all), newest first. limit is 1-200.
func (c *Client) GetLogsLevel(ctx context.Context, skip, limit int, level string) ([]LogEntryResponse, error) {
	q := url.Values{"skip": {strconv.Itoa(skip)}, "limit": {strconv.Itoa(limit)}}
	if level != "" {
		q.Set("level", level)
	}
	return fetchList[LogEntryResponse](ctx, c, classRead, http.MethodGet, withQuery("/logs", q), nil)
}
