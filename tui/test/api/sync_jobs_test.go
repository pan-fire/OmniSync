package api_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/pan-fire/OmniSync/tui/internal/api"
)

// jobServer answers GET /jobs/7 with the given status codes and job statuses
// in order (the last pair repeats), and the profile status with lastError.
func jobServer(t *testing.T, codes []int, statuses []string, lastError string) (*api.Client, *atomic.Int32) {
	t.Helper()
	var polls atomic.Int32
	mux := http.NewServeMux()
	mux.HandleFunc("GET /jobs/7", func(w http.ResponseWriter, _ *http.Request) {
		n := int(polls.Add(1)) - 1
		if n >= len(codes) {
			n = len(codes) - 1
		}
		w.WriteHeader(codes[n])
		if codes[n] == 200 {
			_ = json.NewEncoder(w).Encode(map[string]any{
				"id": 7, "direction": "push", "started_at": "2026-01-01T00:00:00Z", "finished_at": nil,
				"status": statuses[n], "files_changed": 2, "conflicts": 0, "errors": 0,
			})
		} else {
			_ = json.NewEncoder(w).Encode(map[string]any{"detail": "unavailable"})
		}
	})
	mux.HandleFunc("GET /profiles/docs/sync/status", func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"state": "error", "last_error": lastError})
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return api.NewClient(srv.URL, "", "test"), &polls
}

func TestWaitForSyncJob_PollsUntilTheJobEnds(t *testing.T) {
	c, polls := jobServer(t, []int{200, 200, 200}, []string{"running", "running", "completed"}, "")
	job, err := c.WaitForSyncJob(context.Background(), 7, time.Millisecond)
	if err != nil || job.Status != api.JobStatusCompleted || job.FilesChanged != 2 {
		t.Fatalf("job = %+v, err = %v", job, err)
	}
	if polls.Load() != 3 {
		t.Errorf("polls = %d", polls.Load())
	}
}

func TestWaitForSyncJob_RetriesServerErrorsButNotMissingJobs(t *testing.T) {
	c, _ := jobServer(t, []int{502, 503, 200}, []string{"", "", "failed"}, "")
	job, err := c.WaitForSyncJob(context.Background(), 7, time.Millisecond)
	if err != nil || job.Status != api.JobStatusFailed {
		t.Fatalf("job = %+v, err = %v", job, err)
	}

	c, polls := jobServer(t, []int{502}, []string{""}, "")
	if _, err := c.WaitForSyncJob(context.Background(), 7, time.Millisecond); !api.IsStatus(err, 502) {
		t.Errorf("err = %v", err)
	}
	if polls.Load() != api.MaxJobPollFailures {
		t.Errorf("polls = %d, want %d", polls.Load(), api.MaxJobPollFailures)
	}

	c, polls = jobServer(t, []int{404}, []string{""}, "")
	if _, err := c.WaitForSyncJob(context.Background(), 7, time.Millisecond); !api.IsStatus(err, 404) {
		t.Errorf("err = %v", err)
	}
	if polls.Load() != 1 {
		t.Errorf("a missing job was polled %d times", polls.Load())
	}
}

func TestWaitForSyncJob_StopsWhenCancelled(t *testing.T) {
	c, _ := jobServer(t, []int{200}, []string{"running"}, "")
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	if _, err := c.WaitForSyncJob(ctx, 7, 10*time.Millisecond); !errors.Is(err, context.DeadlineExceeded) {
		t.Errorf("err = %v", err)
	}
}

func TestFollowSync_Outcomes(t *testing.T) {
	reason := "The sync marker .omnisync is missing from the remote folder."
	c, polls := jobServer(t, []int{200}, []string{"completed"}, "")
	// Refused at once: no polling, the reason from the start.
	out, err := c.FollowSync(context.Background(), "docs",
		&api.SyncStartResponse{JobID: 7, State: api.SyncStateError, Error: &reason}, time.Millisecond)
	if err != nil || !out.Failed || out.Reason != reason || out.Job != nil || polls.Load() != 0 {
		t.Fatalf("refused: out = %+v, err = %v, polls = %d", out, err, polls.Load())
	}
	// Accepted and completed.
	out, err = c.FollowSync(context.Background(), "docs",
		&api.SyncStartResponse{JobID: 7, State: api.SyncStatePushing}, time.Millisecond)
	if err != nil || out.Failed || out.Job == nil || out.Job.ID != 7 {
		t.Fatalf("completed: out = %+v, err = %v", out, err)
	}
	// Accepted and failed: the reason is the profile's last_error.
	c, _ = jobServer(t, []int{200, 200}, []string{"running", "failed"}, "Stopped by user.")
	out, err = c.FollowSync(context.Background(), "docs",
		&api.SyncStartResponse{JobID: 7, State: api.SyncStateSyncing}, time.Millisecond)
	if err != nil || !out.Failed || out.Reason != "Stopped by user." {
		t.Fatalf("failed: out = %+v, err = %v", out, err)
	}
}
