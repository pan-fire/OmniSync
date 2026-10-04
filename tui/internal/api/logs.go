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
	return c.GetLogsFiltered(ctx, skip, limit, level, "")
}

// Log categories GET /logs filters by.
const (
	// LogCategoryAudit is the audit trail of user actions (backend.audit).
	LogCategoryAudit = "audit"
	// LogCategoryErrors is the ERROR and CRITICAL entries.
	LogCategoryErrors = "errors"
)

// GetLogsFiltered returns log entries of one level and one category (audit or
// errors; empty for all), newest first. limit is 1-200.
func (c *Client) GetLogsFiltered(ctx context.Context, skip, limit int, level, category string) ([]LogEntryResponse, error) {
	q := url.Values{"skip": {strconv.Itoa(skip)}, "limit": {strconv.Itoa(limit)}}
	if level != "" {
		q.Set("level", level)
	}
	if category != "" {
		q.Set("category", category)
	}
	return fetchList[LogEntryResponse](ctx, c, classRead, http.MethodGet, withQuery("/logs", q), nil)
}
