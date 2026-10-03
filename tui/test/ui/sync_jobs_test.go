package ui_test

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// A sync start answers once the run is under way (202 with the job id); the
// TUI then polls GET /jobs/{id} until the job ends and flashes the outcome.

func jobJSON(id int, status string, filesChanged int) map[string]any {
	return map[string]any{
		"id": id, "direction": "two_way", "started_at": "2026-01-01T00:00:00Z",
		"finished_at": nil, "status": status, "files_changed": filesChanged, "conflicts": 0, "errors": 0,
	}
}

// jobAnswers serves GET /jobs/{id}: the given statuses in order (the last
// repeats), and returns how many polls there were.
func jobAnswers(b *backend, id int, statuses ...string) *atomic.Int32 {
	var polls atomic.Int32
	b.handle("GET", "/jobs/"+strconv.Itoa(id), func(w http.ResponseWriter, _ map[string]any) {
		n := int(polls.Add(1)) - 1
		if n >= len(statuses) {
			n = len(statuses) - 1
		}
		_ = json.NewEncoder(w).Encode(jobJSON(id, statuses[n], 4))
	})
	return &polls
}

// flashesOf feeds msg to update and keeps feeding back what the commands
// produce (each may take up to wait, e.g. a sync command following its job),
// and returns the flash texts.
func flashesOf(update func(tea.Msg) tea.Cmd, msg tea.Msg, wait time.Duration) []string {
	var out []string
	queue := []tea.Msg{msg}
	for i := 0; i < 50 && len(queue) > 0; i++ {
		next := queue[0]
		queue = queue[1:]
		switch next := next.(type) {
		case ui.FlashMsg:
			out = append(out, next.Text)
			continue
		case ui.NavigateMsg, ui.PollResultMsg:
			continue
		}
		queue = append(queue, run(update(next), wait)...)
	}
	return out
}

// flashesAfter presses key on a profile view and returns the flashes.
func flashesAfter(m ui.ProfileDetailModel, key string, wait time.Duration) []string {
	return flashesOf(func(msg tea.Msg) tea.Cmd {
		updated, cmd := m.Update(msg)
		m = updated.(ui.ProfileDetailModel)
		return cmd
	}, press(key), wait)
}

// syncFlashes drops the "... started" flashes.
func syncFlashes(all []string) []string {
	var out []string
	for _, f := range all {
		if !strings.Contains(f, " started") {
			out = append(out, f)
		}
	}
	return out
}

func TestSyncJob_SyncNowFollowsTheJobUntilItFinishes(t *testing.T) {
	b := twoWayBackend(t, false, false)
	b.json("POST", "/profiles/docs/sync/start", 202, map[string]any{"job_id": 9, "state": "syncing", "error": nil})
	polls := jobAnswers(b, 9, "running", "completed")
	m := openDetail(t, b)

	got := syncFlashes(flashesAfter(m, "n", 3*ui.FastPollInterval))

	if strings.Join(got, "|") != "Two-way sync of docs finished" {
		t.Errorf("flashes = %v", got)
	}
	if polls.Load() != 2 {
		t.Errorf("job polls = %d, want 2", polls.Load())
	}
}

func TestSyncJob_RefusalAnsweredAtOnceIsShownWithoutPolling(t *testing.T) {
	b := twoWayBackend(t, false, false)
	reason := "The sync marker .omnisync is missing from the remote folder."
	b.json("POST", "/profiles/docs/sync/start", 200, map[string]any{"job_id": 9, "state": "error", "error": reason})
	polls := jobAnswers(b, 9, "failed")
	m := openDetail(t, b)

	got := syncFlashes(flashesAfter(m, "n", time.Second))

	if len(got) != 1 || got[0] != "Two-way sync of docs ended in an error: "+reason {
		t.Errorf("flashes = %v", got)
	}
	if polls.Load() != 0 {
		t.Errorf("polled a refused job %d time(s)", polls.Load())
	}
}

func TestSyncJob_FailedJobShowsTheProfilesLastError(t *testing.T) {
	b := twoWayBackend(t, false, false)
	b.json("POST", "/profiles/docs/sync/start", 202, map[string]any{"job_id": 9, "state": "syncing"})
	jobAnswers(b, 9, "failed")
	b.json("GET", "/profiles/docs/sync/status", 200, map[string]any{"state": "idle", "last_error": "Stopped by user."})
	m := openDetail(t, b)

	got := syncFlashes(flashesAfter(m, "n", time.Second))

	if len(got) != 1 || got[0] != "Two-way sync of docs ended in an error: Stopped by user." {
		t.Errorf("flashes = %v", got)
	}
}

func TestSyncJob_LostJobIsReported(t *testing.T) {
	b := twoWayBackend(t, false, false)
	b.json("POST", "/profiles/docs/sync/start", 202, map[string]any{"job_id": 9, "state": "syncing"})
	// No GET /jobs/9: a 404 ends the wait at once.
	m := openDetail(t, b)

	got := syncFlashes(flashesAfter(m, "n", time.Second))

	if len(got) != 1 || !strings.HasPrefix(got[0], "Lost track of the two-way sync of docs") {
		t.Errorf("flashes = %v", got)
	}
}

func TestSyncJob_ResyncFollowsTheJob(t *testing.T) {
	b := twoWayBackend(t, false, true)
	b.json("POST", "/profiles/docs/sync/resync", 202, map[string]any{"job_id": 10, "state": "syncing"})
	polls := jobAnswers(b, 10, "completed")
	m := openDetail(t, b)
	m = detailStep(t, m, press("R"))
	if !m.CapturesInput() {
		t.Fatalf("Resync must ask first:\n%s", view(m))
	}

	got := syncFlashes(flashesAfter(m, "y", time.Second))

	if len(got) != 1 || !strings.HasPrefix(got[0], "Resync of docs finished") {
		t.Errorf("flashes = %v", got)
	}
	if polls.Load() != 1 {
		t.Errorf("job polls = %d", polls.Load())
	}
}

// Push all: each profile's job is followed; the outcome is flashed per profile.
func TestSyncJob_DashboardPushAllFollowsEachJob(t *testing.T) {
	b := dashboardBackend(t)
	b.json("POST", "/profiles/docs/sync/start", 202, map[string]any{"job_id": 1, "state": "pushing"})
	b.json("POST", "/profiles/pics/sync/start", 200, map[string]any{"job_id": 2, "state": "error", "error": "Refusing to push: the local folder is empty."})
	jobAnswers(b, 1, "completed")
	m := openDashboard(t, b)
	m = dashStep(t, m, press("p"))
	if !m.CapturesInput() {
		t.Fatal("expected a confirmation")
	}
	// pics has no preview, so the push needs the typed acknowledgement.
	for _, k := range typeKeys(ui.ForceWord) {
		m = dashStep(t, m, k)
	}

	flashes := flashesOf(func(msg tea.Msg) tea.Cmd {
		updated, cmd := m.Update(msg)
		m = updated.(ui.DashboardModel)
		return cmd
	}, press("enter"), time.Second)

	joined := strings.Join(flashes, "|")
	for _, want := range []string{"Push of docs finished", "Push of pics ended in an error: Refusing to push: the local folder is empty."} {
		if !strings.Contains(joined, want) {
			t.Errorf("flashes %v lack %q", flashes, want)
		}
	}
}
