package api

import (
	"context"
	"net/http"
	"net/url"
	"strconv"
)

// ListJobs returns sync jobs, newest first, optionally for one profile.
func (c *Client) ListJobs(ctx context.Context, skip, limit int, profile string) ([]SyncJobResponse, error) {
	q := url.Values{"skip": {strconv.Itoa(skip)}, "limit": {strconv.Itoa(limit)}}
	if profile != "" {
		q.Set("profile", profile)
	}
	return fetchList[SyncJobResponse](ctx, c, classRead, http.MethodGet, withQuery("/jobs", q), nil)
}

// GetJob returns a specific job by ID.
func (c *Client) GetJob(ctx context.Context, id int) (*SyncJobResponse, error) {
	return fetch[SyncJobResponse](ctx, c, classRead, http.MethodGet, escapedPath("/jobs/%d", id), nil)
}

// GetJobFiles returns the file changes of a job.
func (c *Client) GetJobFiles(ctx context.Context, id int) ([]FileChangeResponse, error) {
	return fetchList[FileChangeResponse](ctx, c, classRead, http.MethodGet, escapedPath("/jobs/%d/files", id), nil)
}
