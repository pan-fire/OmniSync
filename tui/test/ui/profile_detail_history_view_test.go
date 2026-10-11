package ui_test

import (
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// A failed history load shows the envelope's detail instead of an empty
// "no jobs" message, which would be wrong.
func TestProfileDetailHistory_LoadError(t *testing.T) {
	b := detailBackend(t)
	b.json("GET", "/jobs", 503, errorEnvelope("The job database is locked", "db_locked"))
	m := openHistory(t, b)
	v := view(m)
	if !strings.Contains(v, "API error 503: The job database is locked") || strings.Contains(v, "No sync jobs") {
		t.Errorf("history error view:\n%s", v)
	}
	// Enter has no job to open.
	b.reset()
	m = detailStep(t, m, press("enter"))
	if got := b.matching("GET /jobs/"); len(got) != 0 || strings.Contains(view(m), "Sync job") {
		t.Errorf("enter opened a job: %v", got)
	}
}

// On the first page N (newer) sends nothing; n with no more pages neither.
func TestProfileDetailHistory_NoPagingPastTheEnds(t *testing.T) {
	b := detailBackend(t)
	b.json("GET", "/jobs", 200, jobsJSON(3, 100))
	m := openHistory(t, b)
	b.reset()
	m = detailStep(t, m, press("N"))
	m = detailStep(t, m, press("n"))
	if got := b.log(); len(got) != 0 {
		t.Errorf("paging past the ends sent %v", got)
	}
	if v := view(m); !strings.Contains(v, "Page 1 (only page)") || strings.Contains(v, "n:older") || strings.Contains(v, "N:newer") {
		t.Errorf("view:\n%s", v)
	}
}

// Paging quickly forward and back: the late answer for the page the user
// left is dropped, so the table and the page label agree.
func TestProfileDetailHistory_StalePageDropped(t *testing.T) {
	b := historyBackend(t)
	m := openHistory(t, b)
	updated, older := m.Update(press("n"))
	m = updated.(ui.ProfileDetailModel)
	updated, newer := m.Update(press("N"))
	m = updated.(ui.ProfileDetailModel)
	for _, msg := range runAll(newer) {
		m = detailStep(t, m, msg)
	}
	for _, msg := range runAll(older) { // the answer for page 2 arrives last
		m = detailStep(t, m, msg)
	}
	v := view(m)
	if !strings.Contains(v, "Page 1 (more: n)") || !strings.Contains(v, "100") || strings.Contains(v, "78") {
		t.Errorf("page 2's late answer replaced page 1:\n%s", v)
	}
}

// A job whose detail and files cannot be loaded shows the error in the
// detail view; keys there move the file table and do not page the list.
func TestProfileDetailHistory_JobDetailErrors(t *testing.T) {
	b := detailBackend(t)
	b.json("GET", "/jobs", 200, jobsJSON(1, 100))
	b.json("GET", "/jobs/100", 404, errorEnvelope("Job 100 not found", "not_found"))
	b.json("GET", "/jobs/100/files", 404, errorEnvelope("Job 100 not found", "not_found"))
	m := openHistory(t, b)
	m = detailStep(t, m, press("enter"))
	v := view(m)
	if !strings.Contains(v, "Sync job 100") || !strings.Contains(v, "API error 404: Job 100 not found") || strings.Contains(v, "Status:") {
		t.Errorf("job detail error view:\n%s", v)
	}
	b.reset()
	for _, k := range []string{"down", "n", "N", "r"} {
		m = detailStep(t, m, press(k))
	}
	if got := b.log(); len(got) != 0 {
		t.Errorf("keys in the job detail sent %v", got)
	}
	if !strings.Contains(view(m), "Esc:back to history") {
		t.Errorf("left the job detail:\n%s", view(m))
	}
}

// The poll tick refreshes the list but not while a job's detail is open
// (it would reset the detail's table).
func TestProfileDetailHistory_TickSkipsTheDetail(t *testing.T) {
	b := historyBackend(t)
	m := openHistory(t, b)
	m = detailStep(t, m, press("enter"))
	b.reset()
	_ = detailStep(t, m, ui.TickMsg{})
	if got := b.matching("GET /jobs?"); len(got) != 0 {
		t.Errorf("tick in the job detail reloaded the list: %v", got)
	}
}
