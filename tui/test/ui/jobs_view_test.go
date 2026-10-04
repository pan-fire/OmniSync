package ui_test

import (
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// jobsViewBackend serves three jobs of docs, one profile, and job 100's
// detail and files.
func jobsViewBackend(t *testing.T) *backend {
	t.Helper()
	b := newBackend(t)
	b.json("GET", "/jobs", 200, jobsJSON(3, 100))
	b.json("GET", "/profiles", 200, []any{profileJSON("docs", "Dokumente", "idle")})
	job := jobsJSON(1, 100)[0].(map[string]any)
	job["finished_at"] = "2026-09-27T08:05:00Z"
	job["files_changed"] = 2
	b.json("GET", "/jobs/100", 200, job)
	b.json("GET", "/jobs/100/files", 200, []any{
		map[string]any{"id": 1, "job_id": 100, "file_path": "Berichte/plan.odt", "action": "upload", "size_bytes": 2048, "side": "local"},
		map[string]any{"id": 2, "job_id": 100, "file_path": "alt.txt", "action": "delete", "size_bytes": nil, "side": nil},
	})
	return b
}

// Enter opens the highlighted job with its file changes; Esc returns to the
// list and reloads it. Ticks do not reload the list behind the detail.
func TestJobsView_DetailShowsFilesAndEscReturns(t *testing.T) {
	b := jobsViewBackend(t)
	m := open(t, ui.NewJobsModel(b.client()))
	b.reset()
	m = drive(t, m, press("enter"))
	if got := b.log(); len(got) != 2 || got[0] != "GET /jobs/100" || got[1] != "GET /jobs/100/files" {
		t.Errorf("detail requests = %v", got)
	}
	v := content(m)
	for _, want := range []string{"Job Detail", "ID: 100", "Profile: Dokumente", "Files: 2", "File Changes", "Berichte/plan.odt", "upload", "alt.txt"} {
		if !strings.Contains(v, want) {
			t.Errorf("detail lacks %q:\n%s", want, v)
		}
	}
	if h := m.KeyHints(); !strings.Contains(h, "Esc:back to list") {
		t.Errorf("detail hints = %q", h)
	}
	b.reset()
	m = drive(t, m, ui.TickMsg{}, press("down"))
	if len(b.log()) != 0 {
		t.Errorf("tick in the detail fetched %v", b.log())
	}
	m = drive(t, m, press("esc"))
	if got := b.matching("GET /jobs?"); len(got) != 1 {
		t.Errorf("Esc did not reload the list: %v", b.log())
	}
	if v := content(m); strings.Contains(v, "Job Detail") || !strings.Contains(v, "Filter: all profiles") {
		t.Errorf("not back in the list:\n%s", v)
	}
}

// A job that cannot be loaded shows the backend's reason in the detail
// instead of an empty summary.
func TestJobsView_DetailErrorIsShown(t *testing.T) {
	b := jobsViewBackend(t)
	b.json("GET", "/jobs/100", 404, map[string]any{"detail": "Job 100 not found", "code": "not_found", "details": map[string]any{"id": 100}})
	b.json("GET", "/jobs/100/files", 404, map[string]any{"detail": "Job 100 not found", "code": "not_found", "details": nil})
	m := open(t, ui.NewJobsModel(b.client()))
	m = drive(t, m, press("enter"))
	v := content(m)
	if !strings.Contains(v, "Error: API error 404: Job 100 not found") || strings.Contains(v, "ID: 100") {
		t.Errorf("detail:\n%s", v)
	}
}

// The list says it is loading at first; a failed poll shows the error, and
// the next good one clears it.
func TestJobsView_LoadingAndPollError(t *testing.T) {
	b := jobsViewBackend(t)
	b.json("GET", "/jobs", 500, map[string]any{"detail": "database is locked", "code": "db_busy", "details": nil})
	m := drive(t, ui.NewJobsModel(b.client()), tea.WindowSizeMsg{Width: 140, Height: 40})
	if v := content(m); !strings.Contains(v, "Loading...") || !strings.Contains(v, "Page 1") {
		t.Errorf("no loading state:\n%s", v)
	}
	m = drive(t, m, runAll(m.Init())...)
	if v := content(m); !strings.Contains(v, "Error: API error 500: database is locked") || strings.Contains(v, "Loading...") {
		t.Errorf("error not shown:\n%s", v)
	}
	b.json("GET", "/jobs", 200, jobsJSON(3, 100))
	m = drive(t, m, ui.TickMsg{})
	if v := content(m); strings.Contains(v, "Error:") || !strings.Contains(v, "Page 1 (only page)") {
		t.Errorf("tick did not recover:\n%s", v)
	}
}

// On a short first page there is nothing older or newer: n and N send
// nothing and the hints do not offer them. Enter on an empty list does
// nothing either.
func TestJobsView_NoPagingOnASinglePage(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/jobs", 200, []any{})
	b.json("GET", "/profiles", 200, []any{})
	m := open(t, ui.NewJobsModel(b.client()))
	if h := m.KeyHints(); strings.Contains(h, "n:older") || strings.Contains(h, "N:newer") {
		t.Errorf("hints = %q", h)
	}
	b.reset()
	m = drive(t, m, press("n"), press("N"), press("enter"), press("f"))
	if got := b.matching("GET /jobs?"); len(got) != 1 || got[0] != "GET /jobs?limit=20&skip=0" {
		t.Errorf("requests = %v", b.log())
	}
	if strings.Contains(content(m), "Job Detail") {
		t.Error("Enter on an empty list opened a detail")
	}
}

// Paging shows where the user is: more pages, then the last page; the
// hints offer exactly the possible directions.
func TestJobsView_PageLabelsAndHints(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/jobs", 200, jobsJSON(20, 100))
	b.json("GET", "/profiles", 200, []any{})
	m := open(t, ui.NewJobsModel(b.client()))
	if v, h := content(m), m.KeyHints(); !strings.Contains(v, "Page 1 (more: n)") || !strings.Contains(h, "n:older") || strings.Contains(h, "N:newer") {
		t.Errorf("first page: hints %q\n%s", h, v)
	}
	b.json("GET", "/jobs", 200, jobsJSON(5, 80))
	m = drive(t, m, press("n"))
	if v, h := content(m), m.KeyHints(); !strings.Contains(v, "Page 2 (last page)") || strings.Contains(h, "n:older") || !strings.Contains(h, "N:newer") {
		t.Errorf("last page: hints %q\n%s", h, v)
	}
	b.reset()
	_ = drive(t, m, press("r"))
	if got := b.log(); len(got) != 2 || got[0] != "GET /jobs?limit=20&skip=20" || got[1] != "GET /profiles" {
		t.Errorf("refresh = %v", got)
	}
}

// An answer for a page the user already left is dropped.
func TestJobsView_StaleAnswerDropped(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/jobs", 200, jobsJSON(20, 100))
	b.json("GET", "/profiles", 200, []any{})
	m := open(t, ui.NewJobsModel(b.client()))
	stale := m.Init() // asks for page 1
	updated, _ := m.Update(press("n"))
	m = updated.(ui.JobsModel)
	b.json("GET", "/jobs", 200, jobsJSON(1, 7))
	m = drive(t, m, runAll(stale)...)
	if v := content(m); strings.Contains(v, " 7 ") || !strings.Contains(v, "Page 2") {
		t.Errorf("stale page shown:\n%s", v)
	}
}

// The profile column falls back to the slug, then to the filtered profile,
// then to "-"; the view polls fast while a job runs.
func TestJobsView_ProfileFallbacksAndPolling(t *testing.T) {
	b := newBackend(t)
	running := map[string]any{"id": 3, "direction": "pull", "started_at": "2026-09-27T08:00:00Z", "finished_at": nil,
		"status": "running", "files_changed": 0, "conflicts": 0, "errors": 0, "profile_slug": "pics", "profile_name": nil}
	orphan := map[string]any{"id": 2, "direction": "push", "started_at": "2026-09-27T07:00:00Z", "finished_at": nil,
		"status": "failed", "files_changed": 0, "conflicts": 0, "errors": 1, "profile_slug": nil, "profile_name": nil}
	b.json("GET", "/jobs", 200, []any{running, orphan})
	b.json("GET", "/profiles", 200, []any{profileJSON("docs", "Dokumente", "idle")})
	m := open(t, ui.NewJobsModel(b.client()))
	if !m.PollSpec().IsActive(nil) {
		t.Error("a running job is not polled fast")
	}
	v := content(m)
	if f := strings.Fields(jobsViewRow(v, "running")); len(f) < 3 || f[2] != "pics" {
		t.Errorf("slug fallback: %v\n%s", f, v)
	}
	if f := strings.Fields(jobsViewRow(v, "failed")); len(f) < 2 || f[1] != "-" {
		t.Errorf("no-profile fallback: %v\n%s", f, v)
	}
	// Filtered to docs, a job without profile fields belongs to docs.
	b.json("GET", "/jobs", 200, []any{orphan})
	m = drive(t, m, press("f"))
	if v := content(m); !strings.Contains(v, "Filter: profile docs") || !strings.Contains(jobsViewRow(v, "failed"), "docs") {
		t.Errorf("filtered:\n%s", v)
	}
	if m.PollSpec().IsActive(nil) {
		t.Error("polled fast without a running job")
	}
	if m.ViewID() != ui.ViewJobs || len(m.KeyBindings()) == 0 {
		t.Error("view id or bindings missing")
	}
}

// jobsViewRow returns the first line of v that contains s.
func jobsViewRow(v, s string) string {
	for _, line := range strings.Split(v, "\n") {
		if strings.Contains(line, s) {
			return line
		}
	}
	return ""
}
