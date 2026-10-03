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

// Backup runs, restores and selective syncs answer 202 at once with the job
// running; the client follows them with GET .../jobs/{id} and GET
// .../sync/selective/{id}.

// pollServer serves path with the given status codes and bodies in order
// (the last pair repeats) and counts the polls.
func pollServer(t *testing.T, path string, codes []int, bodies []map[string]any) (*api.Client, *atomic.Int32) {
	t.Helper()
	var polls atomic.Int32
	mux := http.NewServeMux()
	mux.HandleFunc("GET "+path, func(w http.ResponseWriter, _ *http.Request) {
		n := int(polls.Add(1)) - 1
		if n >= len(codes) {
			n = len(codes) - 1
		}
		w.WriteHeader(codes[n])
		if codes[n] == 200 {
			_ = json.NewEncoder(w).Encode(bodies[n])
		} else {
			_ = json.NewEncoder(w).Encode(map[string]any{"detail": "unavailable"})
		}
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return api.NewClient(srv.URL, "", "test"), &polls
}

func backupJob(status string, code, message any) map[string]any {
	return map[string]any{"id": 12, "target_id": 3, "started_at": "x", "finished_at": nil, "status": status,
		"direction": "backup", "size_bytes": nil, "snapshot_id": nil, "error_code": code, "error_message": message}
}

func TestGetBackupJob_Path(t *testing.T) {
	f := newFakeBackend(t)
	f.on("GET", "/profiles/my docs/backups/3/jobs/12", 200, backupJob("completed", nil, nil))
	job, err := f.client("").GetBackupJob(context.Background(), "my docs", 3, 12)
	if err != nil || job.ID != 12 || job.Status != api.BackupJobCompleted {
		t.Fatalf("job = %+v, err = %v", job, err)
	}
	if r := f.last(); r.Method != "GET" || r.RawPath != "/profiles/my%20docs/backups/3/jobs/12" {
		t.Errorf("sent %s %s", r.Method, r.RawPath)
	}
}

func TestWaitForBackupJob_PollsUntilTheJobEnds(t *testing.T) {
	c, polls := pollServer(t, "/profiles/docs/backups/3/jobs/12", []int{200, 503, 200},
		[]map[string]any{backupJob("running", nil, nil), nil,
			backupJob("failed", "target_unreachable", "The backup target cannot be reached.")})
	job, err := c.WaitForBackupJob(context.Background(), "docs", 3, 12, time.Millisecond)
	if err != nil || job.Status != api.BackupJobFailed {
		t.Fatalf("job = %+v, err = %v", job, err)
	}
	if job.ErrorCode == nil || *job.ErrorCode != "target_unreachable" || job.ErrorMessage == nil ||
		*job.ErrorMessage != "The backup target cannot be reached." {
		t.Errorf("error = %v / %v", job.ErrorCode, job.ErrorMessage)
	}
	if polls.Load() != 3 {
		t.Errorf("polls = %d, want 3", polls.Load())
	}
}

func TestWaitForBackupJob_MissingJobAndCancel(t *testing.T) {
	c, polls := pollServer(t, "/profiles/docs/backups/3/jobs/12", []int{404}, nil)
	if _, err := c.WaitForBackupJob(context.Background(), "docs", 3, 12, time.Millisecond); !api.IsStatus(err, 404) {
		t.Errorf("err = %v", err)
	}
	if polls.Load() != 1 {
		t.Errorf("a missing job was polled %d times", polls.Load())
	}

	c, _ = pollServer(t, "/profiles/docs/backups/3/jobs/12", []int{200}, []map[string]any{backupJob("running", nil, nil)})
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	if _, err := c.WaitForBackupJob(ctx, "docs", 3, 12, 10*time.Millisecond); !errors.Is(err, context.DeadlineExceeded) {
		t.Errorf("err = %v", err)
	}
}

func TestBackupStarts_AnswerRunningOrRefuse(t *testing.T) {
	f := newFakeBackend(t)
	f.on("POST", "/profiles/docs/backups/3/run", 202, backupJob("running", nil, nil))
	f.on("POST", "/profiles/docs/backups/3/restore-files", 409,
		map[string]any{"detail": "A sync of this profile is running; try again when it has finished."})
	f.on("POST", "/profiles/docs/backups/4/run", 409, map[string]any{"detail": "Backup already running for this target"})
	c := f.client("")
	ctx := context.Background()

	job, err := c.RunBackup(ctx, "docs", 3)
	if err != nil || job.ID != 12 || job.Status != api.BackupJobRunning {
		t.Fatalf("run = %+v, err = %v", job, err)
	}
	if _, err = c.RunBackup(ctx, "docs", 4); !api.IsStatus(err, 409) {
		t.Errorf("already running: %v", err)
	}
	_, err = c.RestoreFiles(ctx, "docs", 3, api.RestoreFilesRequest{SnapshotID: "s", Paths: []string{"a"}})
	if !api.IsStatus(err, 409) {
		t.Errorf("sync busy: %v", err)
	}
}

func selectiveRun(status string, succeeded, failed int, errs []any) map[string]any {
	return map[string]any{"job_id": 4, "status": status, "total": 2, "succeeded": succeeded, "failed": failed, "errors": errs}
}

func TestWaitForSelectiveSync_PollsUntilTheRunEnds(t *testing.T) {
	c, polls := pollServer(t, "/profiles/docs/sync/selective/4", []int{200, 200},
		[]map[string]any{selectiveRun("running", 1, 0, []any{}),
			selectiveRun("completed", 1, 1, []any{map[string]any{"path": "x.txt", "error": "permission denied"}})})
	run, err := c.WaitForSelectiveSync(context.Background(), "docs", 4, time.Millisecond)
	if err != nil || run.Status != api.JobStatusCompleted || run.Failed != 1 || len(run.Errors) != 1 ||
		run.Errors[0].Path != "x.txt" {
		t.Fatalf("run = %+v, err = %v", run, err)
	}
	if polls.Load() != 2 {
		t.Errorf("polls = %d, want 2", polls.Load())
	}
}

func TestProfileSelectiveSync_BusyIsRefused(t *testing.T) {
	f := newFakeBackend(t)
	f.on("POST", "/profiles/docs/sync/selective", 409, map[string]any{"detail": "A backup of this profile is running."})
	f.on("GET", "/profiles/docs/sync/selective/4", 200, selectiveRun("failed", 0, 0, []any{}))
	c := f.client("")
	if _, err := c.ProfileSelectiveSync(context.Background(), "docs",
		[]api.SelectiveSyncItem{{Path: "a", Action: api.FileActionPush}}); !api.IsStatus(err, 409) {
		t.Errorf("err = %v", err)
	}
	run, err := c.GetSelectiveResult(context.Background(), "docs", 4)
	if err != nil || run.Status != api.JobStatusFailed || f.last().Path != "/profiles/docs/sync/selective/4" {
		t.Errorf("result = %+v, err = %v", run, err)
	}
}
