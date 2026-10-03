package api

import (
	"context"
	"net/http"
	"net/url"
	"strconv"
	"time"
)

// ListBackupTargets returns backup targets for a profile.
func (c *Client) ListBackupTargets(ctx context.Context, slug string) ([]BackupTargetResponse, error) {
	return fetchList[BackupTargetResponse](ctx, c, classRead, http.MethodGet,
		escapedPath("/profiles/%s/backups", slug), nil)
}

// CreateBackupTarget creates a new backup target for a profile.
func (c *Client) CreateBackupTarget(ctx context.Context, slug string, req BackupTargetCreateRequest) (*BackupTargetResponse, error) {
	return fetch[BackupTargetResponse](ctx, c, classRead, http.MethodPost,
		escapedPath("/profiles/%s/backups", slug), req)
}

// UpdateBackupTarget updates an existing backup target.
func (c *Client) UpdateBackupTarget(ctx context.Context, slug string, id int, req BackupTargetUpdateRequest) (*BackupTargetResponse, error) {
	return fetch[BackupTargetResponse](ctx, c, classRead, http.MethodPut,
		escapedPath("/profiles/%s/backups/%d", slug, id), req)
}

// DeleteBackupTarget deletes a backup target. The backend refuses deletes
// without ?confirm=true, so call this only after the user confirmed.
func (c *Client) DeleteBackupTarget(ctx context.Context, slug string, id int) error {
	path := withQuery(escapedPath("/profiles/%s/backups/%d", slug, id), url.Values{"confirm": {"true"}})
	return c.call(ctx, classRead, http.MethodDelete, path, nil, nil)
}

// RunBackup starts a backup job for a target. The backend answers at once
// (202) with the job in status "running"; follow it with GetBackupJob or
// WaitForBackupJob. A refusal (409 backup_running / sync_busy, 404, 422)
// comes back as an *ApiError.
func (c *Client) RunBackup(ctx context.Context, slug string, id int) (*BackupJobResponse, error) {
	return fetch[BackupJobResponse](ctx, c, classRead, http.MethodPost,
		escapedPath("/profiles/%s/backups/%d/run", slug, id), nil)
}

// ListSnapshots returns snapshots for a backup target (it may list a remote).
func (c *Client) ListSnapshots(ctx context.Context, slug string, id int) ([]SnapshotResponse, error) {
	return fetchList[SnapshotResponse](ctx, c, classProbe, http.MethodGet,
		escapedPath("/profiles/%s/backups/%d/snapshots", slug, id), nil)
}

// RestoreSnapshot starts a full restore from a backup snapshot. Like
// RunBackup it answers at once with the job running; follow it with
// WaitForBackupJob.
func (c *Client) RestoreSnapshot(ctx context.Context, slug string, id int, req RestoreRequest) (*BackupJobResponse, error) {
	return fetch[BackupJobResponse](ctx, c, classRead, http.MethodPost,
		escapedPath("/profiles/%s/backups/%d/restore", slug, id), req)
}

// ListSnapshotFiles returns one folder of a snapshot ("" for the top), or the
// files whose path contains search, a page at a time. Reading an archive on a
// remote downloads it once, so this may take a while.
func (c *Client) ListSnapshotFiles(ctx context.Context, slug string, id int, snapshotID, path, search string, offset, limit int) (*SnapshotFilesResponse, error) {
	q := url.Values{"offset": {strconv.Itoa(offset)}, "limit": {strconv.Itoa(limit)}}
	if path != "" {
		q.Set("path", path)
	}
	if search != "" {
		q.Set("search", search)
	}
	return fetch[SnapshotFilesResponse](ctx, c, classLong, http.MethodGet,
		withQuery(escapedPath("/profiles/%s/backups/%d/snapshots/%s/files", slug, id, snapshotID), q), nil)
}

// PreviewRestore reports what a full restore would add, replace and remove,
// without changing anything.
func (c *Client) PreviewRestore(ctx context.Context, slug string, id int, req RestoreRequest) (*RestorePreviewResponse, error) {
	return fetch[RestorePreviewResponse](ctx, c, classLong, http.MethodPost,
		escapedPath("/profiles/%s/backups/%d/restore/preview", slug, id), req)
}

// RestoreFiles starts restoring chosen files and folders of a snapshot.
// Like RunBackup it answers at once with the job running; follow it with
// WaitForBackupJob. 404 snapshot_not_found and 409 snapshot_unavailable come
// back as an *ApiError.
func (c *Client) RestoreFiles(ctx context.Context, slug string, id int, req RestoreFilesRequest) (*BackupJobResponse, error) {
	return fetch[BackupJobResponse](ctx, c, classRead, http.MethodPost,
		escapedPath("/profiles/%s/backups/%d/restore-files", slug, id), req)
}

// GetBackupJob returns one backup or restore job of a target.
func (c *Client) GetBackupJob(ctx context.Context, slug string, targetID, jobID int) (*BackupJobResponse, error) {
	return fetch[BackupJobResponse](ctx, c, classRead, http.MethodGet,
		escapedPath("/profiles/%s/backups/%d/jobs/%d", slug, targetID, jobID), nil)
}

// WaitForBackupJob polls GetBackupJob every interval until the job is no
// longer running (completed, failed or skipped), and returns it. Failed
// polls are retried as in WaitForSyncJob. Cancel ctx to stop waiting; the job
// itself keeps running.
func (c *Client) WaitForBackupJob(ctx context.Context, slug string, targetID, jobID int, interval time.Duration) (*BackupJobResponse, error) {
	return pollUntilDone(ctx, interval,
		func(ctx context.Context) (*BackupJobResponse, error) {
			return c.GetBackupJob(ctx, slug, targetID, jobID)
		},
		func(job *BackupJobResponse) bool { return job.Status != BackupJobRunning })
}
