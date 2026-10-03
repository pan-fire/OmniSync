package ui_test

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// historyBackend serves the profile's jobs: a full page at skip=0, three
// older jobs at skip=20, and job 100's detail and file changes.
func historyBackend(t *testing.T) *backend {
	t.Helper()
	b := detailBackend(t)
	b.mu.Lock()
	b.replies["GET /jobs"] = func(w http.ResponseWriter, r *http.Request) {
		skip, _ := strconv.Atoi(r.URL.Query().Get("skip"))
		jobs := jobsJSON(20, 100)
		if skip >= 20 {
			jobs = jobsJSON(3, 80)
		}
		_ = json.NewEncoder(w).Encode(jobs)
	}
	b.mu.Unlock()
	b.json("GET", "/jobs/100", 200, jobsJSON(1, 100)[0])
	b.json("GET", "/jobs/100/files", 200, []any{
		map[string]any{"id": 1, "job_id": 100, "file_path": "Briefe/a.txt", "action": "upload", "size_bytes": 2048, "side": "remote"},
	})
	return b
}

// openHistory opens Profile Detail on the History tab (the fourth).
func openHistory(t *testing.T, b *backend) ui.ProfileDetailModel {
	t.Helper()
	m := openDetail(t, b)
	for i := 0; i < 3; i++ {
		m = detailStep(t, m, press("right"))
	}
	return m
}

// The History tab lists this profile's jobs (GET /jobs?profile=<slug>),
// without a Profile column, and pages through them 20 at a time.
func TestProfileDetail_HistoryListsAndPagesProfileJobs(t *testing.T) {
	b := historyBackend(t)
	m := openHistory(t, b)
	v := view(m)
	for _, want := range []string{"History", "Sync history", "Page 1 (more: n)", "Direction", "push", "completed", "100"} {
		if !strings.Contains(v, want) {
			t.Errorf("history view lacks %q:\n%s", want, v)
		}
	}
	for _, line := range strings.Split(v, "\n") {
		if strings.Contains(line, "Direction") && strings.Contains(line, "Profile") {
			t.Errorf("history table has a Profile column: %q", line)
		}
	}
	m = detailStep(t, m, press("n"))
	if v = view(m); !strings.Contains(v, "Page 2 (last page)") || !strings.Contains(v, "80") || !strings.Contains(v, "N:newer") {
		t.Errorf("page 2:\n%s", v)
	}
	m = detailStep(t, m, press("n")) // no more pages: nothing sent
	_ = detailStep(t, m, press("N"))
	want := []string{
		"GET /jobs?limit=20&profile=docs&skip=0",
		"GET /jobs?limit=20&profile=docs&skip=20",
		"GET /jobs?limit=20&profile=docs&skip=0",
	}
	if got := b.matching("GET /jobs?"); fmt.Sprint(got) != fmt.Sprint(want) {
		t.Errorf("queries = %v, want %v", got, want)
	}
}

// Leaving the tab and coming back does not load the history again; r does.
func TestProfileDetail_HistoryLoadsOnce(t *testing.T) {
	b := historyBackend(t)
	m := openHistory(t, b)
	m = detailStep(t, m, press("left"))
	m = detailStep(t, m, press("right"))
	if got := b.matching("GET /jobs?"); len(got) != 1 {
		t.Errorf("history loaded %d times: %v", len(got), got)
	}
	_ = detailStep(t, m, press("r"))
	if got := b.matching("GET /jobs?"); len(got) != 2 {
		t.Errorf("r did not reload: %v", got)
	}
}

// Enter opens a job's detail and file changes as in the Jobs view; Esc goes
// back to the list, and a second Esc leaves Profile Detail.
func TestProfileDetail_HistoryJobDetail(t *testing.T) {
	b := historyBackend(t)
	m := openHistory(t, b)
	m = detailStep(t, m, press("enter"))
	v := view(m)
	for _, want := range []string{"Sync job 100", "Status:", "File Changes", "Briefe/a.txt", "upload", "Esc:back to history"} {
		if !strings.Contains(v, want) {
			t.Errorf("job detail lacks %q:\n%s", want, v)
		}
	}
	if strings.Contains(v, "Profile:") {
		t.Errorf("job detail repeats the profile:\n%s", v)
	}
	if len(b.matching("GET /jobs/100")) != 2 {
		t.Errorf("requests = %v", b.matching("GET /jobs/100"))
	}
	updated, cmd := m.Update(press("esc"))
	m = updated.(ui.ProfileDetailModel)
	if cmd != nil {
		t.Errorf("Esc in the job detail left the view")
	}
	if v = view(m); !strings.Contains(v, "Sync history") {
		t.Errorf("Esc did not return to the list:\n%s", v)
	}
	_, cmd = m.Update(press("esc"))
	if _, ok := find[ui.NavigateMsg](runAll(cmd)); !ok {
		t.Error("second Esc did not leave Profile Detail")
	}
}

// With no jobs the tab says so instead of showing an empty table.
func TestProfileDetail_HistoryEmpty(t *testing.T) {
	b := detailBackend(t)
	b.json("GET", "/jobs", 200, []any{})
	m := openHistory(t, b)
	if v := view(m); !strings.Contains(v, "No sync jobs for this profile yet") || !strings.Contains(v, "Page 1 (only page)") {
		t.Errorf("view:\n%s", v)
	}
}
