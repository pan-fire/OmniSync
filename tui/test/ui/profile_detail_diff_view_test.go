package ui_test

import (
	"strings"
	"testing"
	"time"

	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// diffFilesBackend serves a diff with one file per category; the first one
// is a conflict flagged for manual handling, the second has a remote size.
func diffFilesBackend(t *testing.T) *backend {
	t.Helper()
	b := detailBackend(t)
	b.json("POST", "/profiles/docs/diff", 200, map[string]any{
		"files": []any{
			map[string]any{"path": "only-local.txt", "category": "local_only", "local_size": 10, "is_conflict": true, "manual_flag": true},
			map[string]any{"path": "only-remote.txt", "category": "remote_only", "remote_size": 2048},
			map[string]any{"path": "mod-local.txt", "category": "modified_local", "local_size": 1, "remote_size": 2},
			map[string]any{"path": "mod-remote.txt", "category": "modified_remote", "local_size": 1, "remote_size": 2},
			map[string]any{"path": "mod-both.txt", "category": "modified_both", "local_size": 1, "remote_size": 2},
		},
		"summary": map[string]any{"total": 5, "local_only": 1, "remote_only": 1, "modified_local": 1,
			"modified_remote": 1, "modified_both": 1, "manual": 1},
	})
	b.json("POST", "/profiles/docs/sync/selective", 202,
		map[string]any{"job_id": 7, "status": "completed", "total": 1, "succeeded": 1, "failed": 0, "errors": []any{}})
	return b
}

// openDiff opens Profile Detail on the Differences tab.
func openDiff(t *testing.T, b *backend) ui.ProfileDetailModel {
	t.Helper()
	return detailStep(t, openDetail(t, b), press("right"))
}

// f cycles the filter through every category and back to All; each step
// shows only that category's files, so a user can narrow a long diff.
func TestProfileDetailDiff_FilterCyclesCategories(t *testing.T) {
	b := diffFilesBackend(t)
	m := openDiff(t, b)
	all := []string{"only-local.txt", "only-remote.txt", "mod-local.txt", "mod-remote.txt", "mod-both.txt"}
	steps := []struct {
		label string
		shown string
	}{
		{"Local Only", "only-local.txt"},
		{"Remote Only", "only-remote.txt"},
		{"Mod Local", "mod-local.txt"},
		{"Mod Remote", "mod-remote.txt"},
		{"Mod Both", "mod-both.txt"},
	}
	for _, s := range steps {
		m = detailStep(t, m, press("f"))
		v := view(m)
		if !strings.Contains(v, "Filter: "+s.label) {
			t.Fatalf("filter label is not %q:\n%s", s.label, v)
		}
		for _, f := range all {
			if strings.Contains(v, f) != (f == s.shown) {
				t.Errorf("filter %s: %s shown = %v:\n%s", s.label, f, strings.Contains(v, f), v)
			}
		}
	}
	m = detailStep(t, m, press("f"))
	v := view(m)
	if !strings.Contains(v, "Filter: All") {
		t.Fatalf("filter did not wrap to All:\n%s", v)
	}
	for _, f := range all {
		if !strings.Contains(v, f) {
			t.Errorf("All lacks %s:\n%s", f, v)
		}
	}
	// Filtering is local: only the first diff was requested.
	if got := b.matching("POST /profiles/docs/diff"); len(got) != 1 {
		t.Errorf("diff requests = %v", got)
	}
}

// The table shows sizes and the summary counts, and names the file p/l/s/m
// act on, or how many are selected.
func TestProfileDetailDiff_TableShowsSizesAndSelection(t *testing.T) {
	m := openDiff(t, diffFilesBackend(t))
	v := view(m)
	for _, want := range []string{"Total: 5", "Local only: 1", "Mod both: 1", "Manual: 1", "2.0 KB",
		"Current file: only-local.txt", "p/l/s/m apply to this file"} {
		if !strings.Contains(v, want) {
			t.Errorf("diff view lacks %q:\n%s", want, v)
		}
	}
	m = detailStep(t, m, press(" "))
	m = detailStep(t, m, press("down"))
	m = detailStep(t, m, press(" "))
	if v := view(m); !strings.Contains(v, "2 selected") {
		t.Errorf("selection count missing:\n%s", v)
	}
}

// Pushing or pulling single files replaces the other side's copies, so it
// asks first: n and Esc send nothing, y sends exactly the selected files
// with the chosen action.
func TestProfileDetailDiff_SelectivePushPullConfirms(t *testing.T) {
	for _, tc := range []struct {
		key, action, verb, where string
	}{
		{"p", "push", "Push", "the remote copies are replaced by the local ones"},
		{"l", "pull", "Pull", "the local copies are replaced by the remote ones"},
	} {
		t.Run(tc.action, func(t *testing.T) {
			b := diffFilesBackend(t)
			m := openDiff(t, b)
			m = detailStep(t, m, press(" "))
			m = detailStep(t, m, press("down"))
			m = detailStep(t, m, press(" "))
			for _, cancel := range []string{"n", "esc"} {
				m = detailStep(t, m, press(tc.key))
				v := view(m)
				if !m.CapturesInput() || !strings.Contains(v, tc.verb+" 2 selected file(s) of profile Dokumente (docs)?") ||
					!strings.Contains(v, tc.where) {
					t.Fatalf("no selective prompt:\n%s", v)
				}
				m = detailStep(t, m, press(cancel))
				if m.CapturesInput() || len(b.matching("POST /profiles/docs/sync/selective")) != 0 {
					t.Fatalf("%q did not cancel without sending: %v", cancel, b.log())
				}
			}
			m = detailStep(t, m, press(tc.key))
			_ = detailStep(t, m, press("y"))
			bodies := b.bodiesOf("POST /profiles/docs/sync/selective")
			if len(bodies) != 1 {
				t.Fatalf("selective requests = %v", bodies)
			}
			items, _ := bodies[0]["items"].([]any)
			if len(items) != 2 {
				t.Fatalf("items = %v", bodies[0])
			}
			paths := map[string]bool{}
			for _, it := range items {
				item := it.(map[string]any)
				if item["action"] != tc.action {
					t.Errorf("item %v: action is not %s", item, tc.action)
				}
				paths[item["path"].(string)] = true
			}
			if !paths["only-local.txt"] || !paths["only-remote.txt"] {
				t.Errorf("paths = %v, want the two selected files", paths)
			}
		})
	}
}

// s (skip) and m (manual) do not overwrite anything and need no prompt:
// they apply to the highlighted file when nothing is selected.
func TestProfileDetailDiff_SkipAndManualApplyToHighlightedFile(t *testing.T) {
	for key, action := range map[string]string{"s": "skip", "m": "manual"} {
		t.Run(action, func(t *testing.T) {
			b := diffFilesBackend(t)
			m := openDiff(t, b)
			m = detailStep(t, m, press("down"))
			got := strings.Join(flashesAfter(m, key, time.Second), "|")
			if !strings.Contains(got, "Selective sync of 1 file(s) started (job 7)") || !strings.Contains(got, "Selective sync applied (1 file(s))") {
				t.Errorf("flashes = %v", got)
			}
			bodies := b.bodiesOf("POST /profiles/docs/sync/selective")
			if len(bodies) != 1 {
				t.Fatalf("selective requests = %v", bodies)
			}
			items, _ := bodies[0]["items"].([]any)
			if len(items) != 1 || items[0].(map[string]any)["path"] != "only-remote.txt" || items[0].(map[string]any)["action"] != action {
				t.Errorf("items = %v", items)
			}
		})
	}
}

// With nothing in the diff, p/l/s/m have no file to act on: no prompt and
// no request.
func TestProfileDetailDiff_EmptyDiffSendsNothing(t *testing.T) {
	b := detailBackend(t)
	b.json("POST", "/profiles/docs/diff", 200, map[string]any{"files": []any{}, "summary": map[string]any{"total": 0}})
	m := openDiff(t, b)
	for _, k := range []string{"p", "l", "s", "m"} {
		m = detailStep(t, m, press(k))
		if m.CapturesInput() {
			t.Fatalf("%q opened a prompt for no files:\n%s", k, view(m))
		}
	}
	if got := b.matching("POST /profiles/docs/sync/selective"); len(got) != 0 {
		t.Errorf("selective requests for an empty diff: %v", got)
	}
	if v := view(m); !strings.Contains(v, "Total: 0") || !strings.Contains(v, "(empty)") {
		t.Errorf("empty diff view:\n%s", v)
	}
}

// A failed comparison shows the API error envelope's detail, and r asks
// again; the backend's own comparison problem (error field) is shown too.
func TestProfileDetailDiff_ErrorsAndReload(t *testing.T) {
	b := detailBackend(t)
	b.json("POST", "/profiles/docs/diff", 409, map[string]any{
		"detail": "Profile 'docs' is not running", "code": "profile_not_running", "details": map[string]any{"slug": "docs"}})
	m := openDiff(t, b)
	if v := view(m); !strings.Contains(v, "API error 409: Profile 'docs' is not running") || !strings.Contains(v, "press 'r' to load") {
		t.Fatalf("diff error not shown:\n%s", v)
	}
	b.json("POST", "/profiles/docs/diff", 200, map[string]any{
		"files":   []any{map[string]any{"path": "x.txt", "category": "local_only", "local_size": 1}},
		"summary": map[string]any{"total": 1, "local_only": 1}, "error": "remote listing incomplete"})
	m = detailStep(t, m, press("r"))
	v := view(m)
	if !strings.Contains(v, "x.txt") || !strings.Contains(v, "remote listing incomplete") || strings.Contains(v, "not running") {
		t.Errorf("reloaded diff:\n%s", v)
	}
	if got := b.matching("POST /profiles/docs/diff"); len(got) != 2 {
		t.Errorf("diff requests = %v", got)
	}
}

// While the comparison runs the tab says so instead of an empty table.
func TestProfileDetailDiff_LoadingState(t *testing.T) {
	b := diffFilesBackend(t)
	m := openDetail(t, b)
	updated, _ := m.Update(press("right")) // the diff answer is not delivered
	m = updated.(ui.ProfileDetailModel)
	if v := view(m); !strings.Contains(v, "Comparing local and remote") || strings.Contains(v, "Total:") {
		t.Errorf("loading view:\n%s", v)
	}
}

// k on the Overview jumps to the Differences tab and compares at once; o
// goes back to the Overview.
func TestProfileDetailDiff_OverviewKOpensDifferences(t *testing.T) {
	b := diffFilesBackend(t)
	m := openDetail(t, b)
	m = detailStep(t, m, press("k"))
	if v := view(m); !strings.Contains(v, "Total: 5") || !strings.Contains(v, "f:filter") {
		t.Errorf("k did not show the differences:\n%s", v)
	}
	if got := b.matching("POST /profiles/docs/diff"); len(got) != 1 {
		t.Errorf("diff requests = %v", got)
	}
	m = detailStep(t, m, press("o"))
	if strings.Contains(view(m), "f:filter") {
		t.Errorf("o did not return to the Overview:\n%s", view(m))
	}
}

// When a sync of the profile ends, a loaded diff is out of date: it is
// compared again so the tab does not show files the sync already handled.
func TestProfileDetailDiff_ReloadsWhenASyncEnds(t *testing.T) {
	b := diffFilesBackend(t)
	m := openDiff(t, b)
	status := func(state string) ui.PollResultMsg {
		return ui.PollResultMsg{ViewID: ui.ViewProfileDetail, Data: ui.ProfileDetailData{Slug: "docs",
			Value: &api.ProfileStatusResponse{ProfileResponse: api.ProfileResponse{Slug: "docs", Name: "Dokumente"}, State: api.SyncState(state)}}}
	}
	updated, _ := m.Update(status("pushing")) // the spinner's ticks are not run
	m = updated.(ui.ProfileDetailModel)
	b.reset()
	_ = detailStep(t, m, status("idle"))
	if got := b.matching("POST /profiles/docs/diff"); len(got) != 1 {
		t.Errorf("diff not compared again after the sync: %v", b.log())
	}
}
