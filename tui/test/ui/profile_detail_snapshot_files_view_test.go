package ui_test

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

const snapFiles = "/profiles/docs/backups/3/snapshots/" + snap + "/files"

// snapshotTreeBackend serves a snapshot with docs/, docs/sub/ and files,
// answering by the path and search the browser asks for.
func snapshotTreeBackend(t *testing.T) *backend {
	t.Helper()
	b := backupsBackend(t, nil)
	dir := func(path, name string) map[string]any {
		return map[string]any{"path": path, "name": name, "is_dir": true, "size": 20, "file_count": 2}
	}
	file := func(path, name string) map[string]any {
		return map[string]any{"path": path, "name": name, "is_dir": false, "size": 3, "mod_time": "2026-09-27T08:00:00Z"}
	}
	b.mu.Lock()
	b.replies["GET "+snapFiles] = func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		var entries []any
		total := 0
		switch {
		case q.Get("search") != "":
			entries = []any{file("docs/sub/report.txt", "report.txt")}
			total = 1
		case q.Get("path") == "docs":
			entries = []any{dir("docs/sub", "sub"), file("docs/a.txt", "a.txt")}
			total = 2
		case q.Get("path") == "docs/sub":
			entries = []any{file("docs/sub/report.txt", "report.txt")}
			total = 1
		default:
			entries = []any{dir("docs", "docs"), file("top.txt", "top.txt")}
			total = 5 // more entries than the page holds
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"snapshot_id": snap, "path": q.Get("path"), "search": nil,
			"total": total, "offset": 0, "limit": 1000, "snapshot_files": 4, "entries": entries})
	}
	b.mu.Unlock()
	return b
}

// lastFilesQuery returns the query of the last snapshot listing request.
func lastFilesQuery(t *testing.T, b *backend) string {
	t.Helper()
	got := b.matching("GET " + snapFiles)
	if len(got) == 0 {
		t.Fatal("no listing request")
	}
	return got[len(got)-1]
}

// u and Backspace go up one folder at a time from a nested folder; at the
// top they send nothing. Enter on a file opens nothing.
func TestProfileDetailSnapshotFiles_NavigateUpAndDown(t *testing.T) {
	b := snapshotTreeBackend(t)
	m := openSnapshots(t, b)
	m = detailStep(t, m, press("f"))
	if v := view(m); !strings.Contains(v, "showing the first 2, search (/) to narrow") || !strings.Contains(v, ": /") {
		t.Fatalf("top folder:\n%s", v)
	}
	m = detailStep(t, m, press("enter"))
	m = detailStep(t, m, press("enter"))
	if q := lastFilesQuery(t, b); !strings.Contains(q, "path=docs%2Fsub") {
		t.Fatalf("nested folder request = %s", q)
	}
	if v := view(m); !strings.Contains(v, "/docs/sub") || !strings.Contains(v, "report.txt") {
		t.Fatalf("nested folder:\n%s", v)
	}
	b.reset()
	m = detailStep(t, m, press("enter")) // a file
	if got := b.log(); len(got) != 0 {
		t.Errorf("enter on a file sent %v", got)
	}
	m = detailStep(t, m, press("u"))
	if q := lastFilesQuery(t, b); !strings.Contains(q, "path=docs&") && !strings.HasSuffix(q, "path=docs") {
		t.Errorf("u went to %s, want docs", q)
	}
	m = detailStep(t, m, press("backspace"))
	if q := lastFilesQuery(t, b); strings.Contains(q, "path=") {
		t.Errorf("backspace went to %s, want the top", q)
	}
	b.reset()
	m = detailStep(t, m, press("u"))
	if got := b.log(); len(got) != 0 {
		t.Errorf("u at the top sent %v", got)
	}
	// r reloads the folder shown.
	_ = detailStep(t, m, press("r"))
	if got := b.matching("GET " + snapFiles); len(got) != 1 {
		t.Errorf("r sent %v", b.log())
	}
}

// / searches the whole snapshot (results show full paths); Backspace leaves
// the search for the folder it started in.
func TestProfileDetailSnapshotFiles_Search(t *testing.T) {
	b := snapshotTreeBackend(t)
	m := openSnapshots(t, b)
	m = detailStep(t, m, press("f"))
	m = detailStep(t, m, press("/"))
	if !m.CapturesInput() {
		t.Fatalf("no search form:\n%s", view(m))
	}
	for _, k := range typeKeys("report") {
		m = detailStep(t, m, k)
	}
	m = detailStep(t, m, press("enter"))
	if q := lastFilesQuery(t, b); !strings.Contains(q, "search=report") {
		t.Fatalf("search request = %s", q)
	}
	if v := view(m); !strings.Contains(v, `search "report"`) || !strings.Contains(v, "docs/sub/report.txt") {
		t.Fatalf("search view:\n%s", v)
	}
	m = detailStep(t, m, press("backspace"))
	if q := lastFilesQuery(t, b); strings.Contains(q, "search=") {
		t.Errorf("backspace kept the search: %s", q)
	}
	if v := view(m); strings.Contains(v, "search") && strings.Contains(v, `"report"`) {
		t.Errorf("still in the search:\n%s", v)
	}
}

// Restoring needs a selection: R and O without one only say so; c clears a
// selection; n on the restore prompt sends nothing.
func TestProfileDetailSnapshotFiles_SelectionRules(t *testing.T) {
	b := snapshotTreeBackend(t)
	m := openSnapshots(t, b)
	m = detailStep(t, m, press("f"))
	for _, k := range []string{"R", "O"} {
		if got := flashesAfter(m, k, time.Second); strings.Join(got, "|") != "Select files or folders with Space first" {
			t.Errorf("%s flashes = %q", k, got)
		}
	}
	m = detailStep(t, m, press(" "))
	if !strings.Contains(view(m), "1 selected") {
		t.Fatalf("selection:\n%s", view(m))
	}
	m = detailStep(t, m, press(" ")) // Space again unselects
	if !strings.Contains(view(m), "0 selected") {
		t.Fatalf("second Space did not unselect:\n%s", view(m))
	}
	m = detailStep(t, m, press(" "))
	m = detailStep(t, m, press("c"))
	if !strings.Contains(view(m), "0 selected") {
		t.Errorf("c did not clear:\n%s", view(m))
	}
	m = detailStep(t, m, press(" "))
	m = detailStep(t, m, press("R"))
	if v := view(m); !strings.Contains(v, "Restore 1 selected item(s) of snapshot "+snap) {
		t.Fatalf("restore prompt:\n%s", v)
	}
	m = detailStep(t, m, press("n"))
	if got := b.matching("POST /profiles/docs/backups/"); len(got) != 0 || m.CapturesInput() {
		t.Errorf("n restored: %v", got)
	}
	// Esc leaves the browser for the snapshots, then for the targets.
	m = detailStep(t, m, press("esc"))
	if v := view(m); !strings.Contains(v, snap) || strings.Contains(v, "Enter:open folder") {
		t.Errorf("Esc did not return to the snapshots:\n%s", v)
	}
	m = detailStep(t, m, press("esc"))
	if !strings.Contains(view(m), "Nightly") {
		t.Errorf("second Esc did not return to the targets:\n%s", view(m))
	}
}

// A listing that fails shows the envelope's detail in the browser.
func TestProfileDetailSnapshotFiles_ListingError(t *testing.T) {
	b := backupsBackend(t, nil)
	b.json("GET", snapFiles, 502, errorEnvelope("The archive could not be downloaded", "remote_error"))
	m := openSnapshots(t, b)
	m = detailStep(t, m, press("f"))
	if v := view(m); !strings.Contains(v, "API error 502: The archive could not be downloaded") {
		t.Errorf("listing error not shown:\n%s", v)
	}
}

// While a folder loads the browser says so; the late answer for a folder
// the user already left is dropped.
func TestProfileDetailSnapshotFiles_LoadingAndStaleAnswer(t *testing.T) {
	b := snapshotTreeBackend(t)
	m := openSnapshots(t, b)
	m = detailStep(t, m, press("f"))
	updated, docsCmd := m.Update(press("enter")) // opens docs/, answer not delivered
	m = updated.(ui.ProfileDetailModel)
	if v := view(m); !strings.Contains(v, "Loading") {
		t.Errorf("no loading state:\n%s", v)
	}
	m = detailStep(t, m, press("u")) // back to the top, answered
	for _, msg := range runAll(docsCmd) {
		m = detailStep(t, m, msg)
	}
	if v := view(m); !strings.Contains(v, "top.txt") || strings.Contains(v, "a.txt") {
		t.Errorf("docs/ answer replaced the top folder:\n%s", v)
	}
}
