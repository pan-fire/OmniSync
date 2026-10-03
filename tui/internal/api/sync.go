package api

import (
	"context"
	"errors"
	"net/http"
	"time"
)

// AggregateStatus returns the aggregate sync status across all profiles.
func (c *Client) AggregateStatus(ctx context.Context) (*AggregateStatusResponse, error) {
	return fetch[AggregateStatusResponse](ctx, c, classRead, http.MethodGet, "/sync/status/aggregate", nil)
}

// MaxJobPollFailures is how many failed polls in a row WaitForSyncJob (and
// WaitForBackupJob, WaitForSelectiveSync)
// tolerates (the backend restarting, a proxy hiccup) before it gives up.
const MaxJobPollFailures = 5

// WaitForSyncJob polls GET /jobs/{jobID} every interval until the job is no
// longer running, and returns it. A failed poll is retried, up to
// MaxJobPollFailures in a row; a 4xx answer (no such job) ends the wait at
// once. Cancel ctx to stop waiting; the sync itself keeps running.
func (c *Client) WaitForSyncJob(ctx context.Context, jobID int, interval time.Duration) (*SyncJobResponse, error) {
	return pollUntilDone(ctx, interval,
		func(ctx context.Context) (*SyncJobResponse, error) { return c.GetJob(ctx, jobID) },
		func(job *SyncJobResponse) bool { return job.Status != JobStatusRunning })
}

// pollUntilDone calls get every interval until done reports the answer as
// final, and returns that answer. A failed poll is retried, up to
// MaxJobPollFailures in a row; a 4xx answer ends the wait at once. Cancel ctx
// to stop waiting.
func pollUntilDone[T any](ctx context.Context, interval time.Duration, get func(context.Context) (*T, error), done func(*T) bool) (*T, error) {
	failures := 0
	for {
		v, err := get(ctx)
		switch {
		case err == nil:
			failures = 0
			if done(v) {
				return v, nil
			}
		case ctx.Err() != nil:
			return nil, ctx.Err()
		case isClientError(err):
			return nil, err
		default:
			failures++
			if failures >= MaxJobPollFailures {
				return nil, err
			}
		}
		timer := time.NewTimer(interval)
		select {
		case <-ctx.Done():
			timer.Stop()
			return nil, ctx.Err()
		case <-timer.C:
		}
	}
}

func isClientError(err error) bool {
	var apiErr *ApiError
	return errors.As(err, &apiErr) && apiErr.StatusCode >= 400 && apiErr.StatusCode < 500
}

// SyncOutcome is how a sync started with StartProfileSync or ResyncProfile
// ended.
type SyncOutcome struct {
	// Job is the finished job; nil when the start was refused at once.
	Job *SyncJobResponse
	// Failed: the run was refused, failed or was stopped.
	Failed bool
	// Reason is why it failed: the refusal, or the profile's last_error
	// (empty when the backend gave none).
	Reason string
}

// FollowSync waits until the sync that start describes has ended. The
// backend answers a start once rclone begins changing files; a run that its
// safety checks refused ended already, with the reason in start.Error.
// Otherwise the job is polled every interval (WaitForSyncJob), and a failed
// job's reason is the profile's last_error.
func (c *Client) FollowSync(ctx context.Context, slug string, start *SyncStartResponse, interval time.Duration) (*SyncOutcome, error) {
	if start.Error != nil || start.State == SyncStateError {
		out := &SyncOutcome{Failed: true}
		if start.Error != nil {
			out.Reason = *start.Error
		}
		return out, nil
	}
	job, err := c.WaitForSyncJob(ctx, start.JobID, interval)
	if err != nil {
		return nil, err
	}
	out := &SyncOutcome{Job: job, Failed: job.Status == JobStatusFailed}
	if out.Failed {
		if st, err := c.ProfileSyncStatus(ctx, slug); err == nil && st.LastError != nil {
			out.Reason = *st.LastError
		}
	}
	return out, nil
}
