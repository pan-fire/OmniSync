package ui_test

import (
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// The view says it is loading at first; a failed poll shows the backend's
// reason.
func TestConflictsView_LoadingAndPollError(t *testing.T) {
	b := conflictsBackend(t)
	b.json("GET", "/conflicts", 500, map[string]any{"detail": "database is locked", "code": "db_busy", "details": nil})
	b.json("GET", "/profiles", 500, map[string]any{"detail": "database is locked"})
	m := drive(t, ui.NewConflictsModel(b.client()), tea.WindowSizeMsg{Width: 140, Height: 40})
	if !strings.Contains(content(m), "Loading...") {
		t.Errorf("no loading state:\n%s", content(m))
	}
	m = drive(t, m, runAll(m.Init())...)
	v := content(m)
	// A failed read must not read as "all clear" (it did before).
	if !strings.Contains(v, "Error: API error 500: database is locked") || strings.Contains(v, "Loading...") ||
		strings.Contains(v, "all clear") {
		t.Errorf("error state:\n%s", v)
	}
	// Without profiles, f has nothing to cycle and stays on all profiles.
	b.json("GET", "/conflicts", 200, []any{})
	m = drive(t, m, press("f"))
	if v := content(m); !strings.Contains(v, "Filter: all profiles") || strings.Contains(v, "Error:") || !strings.Contains(v, "No conflicts") {
		t.Errorf("after f:\n%s", v)
	}
}

// No conflicts: "all clear", naming the profile when filtered. Enter has
// nothing to resolve.
func TestConflictsView_EmptyStates(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/conflicts", 200, []any{})
	b.json("GET", "/profiles", 200, []any{profileJSON("docs", "Dokumente", "idle")})
	m := open(t, ui.NewConflictsModel(b.client()))
	if v := content(m); !strings.Contains(v, "No conflicts") || !strings.Contains(v, "all clear") {
		t.Errorf("empty:\n%s", v)
	}
	m = drive(t, m, press("enter"))
	if m.CapturesInput() {
		t.Error("Enter on an empty list opened the choices")
	}
	m = drive(t, m, press("f"))
	if v := content(m); !strings.Contains(v, "No conflicts for profile docs") {
		t.Errorf("filtered empty:\n%s", v)
	}
	b.reset()
	_ = drive(t, m, press("R"), ui.TickMsg{})
	if got := b.matching("GET /conflicts?profile=docs"); len(got) != 2 {
		t.Errorf("refresh and tick = %v", b.log())
	}
}

// While a resolve runs, Enter does not open another one: two requests
// for the same files must not race.
func TestConflictsView_EnterWaitsForTheRunningResolve(t *testing.T) {
	b := conflictsBackend(t)
	m := open(t, ui.NewConflictsModel(b.client()))
	m = drive(t, m, press("enter"), press("d"))
	updated, cmd := m.Update(press("y"))
	m = updated.(ui.ConflictsModel)
	confirmed, _ := find[tea.Msg](runAll(cmd))
	updated, resolve := m.Update(confirmed) // the resolve is held back
	m = updated.(ui.ConflictsModel)
	if v := content(m); !strings.Contains(v, "Resolving a.txt...") {
		t.Errorf("no progress shown:\n%s", v)
	}
	m, flashes := listsFlashes(t, m, press("enter"))
	if strings.Join(flashes, "|") != "Wait until the running resolve has finished" || m.CapturesInput() {
		t.Errorf("flashes = %v", flashes)
	}
	b.json("POST", "/conflicts/1/resolve", 200, map[string]any{"id": 1, "file_path": "a.txt", "resolved": true})
	var done tea.Msg
	for _, msg := range runAll(resolve) {
		done = msg
	}
	m, flashes = listsFlashes(t, m, done)
	if strings.Join(flashes, "|") != "Conflict for a.txt dismissed; no file was changed" {
		t.Errorf("flashes = %v", flashes)
	}
	if strings.Contains(content(m), "Resolving") {
		t.Errorf("still resolving:\n%s", content(m))
	}
}

// A confirmed keep reports which file was resolved; n reports that nothing
// changed.
func TestConflictsView_ResolveAndCancelAreReported(t *testing.T) {
	b := conflictsBackend(t)
	b.json("POST", "/conflicts/1/resolve", 200, map[string]any{"id": 1, "file_path": "a.txt", "resolved": true})
	m := open(t, ui.NewConflictsModel(b.client()))
	m = drive(t, m, press("enter"), press("r"))
	m, flashes := listsFlashes(t, m, press("n"))
	if strings.Join(flashes, "|") != "Keep remote cancelled; nothing was changed" || len(b.matching("POST")) != 0 {
		t.Errorf("cancel: flashes %v, requests %v", flashes, b.matching("POST"))
	}
	m = drive(t, m, press("enter"), press("l"))
	_, flashes = listsFlashes(t, m, press("y"))
	if strings.Join(flashes, "|") != "Keep local: a.txt resolved" {
		t.Errorf("flashes = %v", flashes)
	}
}

// The prompt names the profile as well as it is known: name only, slug
// only, or "(unknown)"; a dotfile's copy keeps its whole name.
func TestConflictsView_ProfileNamesAndDotfiles(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/conflicts", 200, []any{
		map[string]any{"id": 1, "job_id": nil, "file_path": ".bashrc", "local_modified": nil, "remote_modified": nil,
			"resolved": false, "resolution": nil, "profile_slug": nil, "profile_name": "Heim"},
		map[string]any{"id": 2, "job_id": nil, "file_path": "x.txt", "local_modified": nil, "remote_modified": nil,
			"resolved": false, "resolution": nil, "profile_slug": "work", "profile_name": nil},
		map[string]any{"id": 3, "job_id": nil, "file_path": "y.txt", "local_modified": nil, "remote_modified": nil,
			"resolved": false, "resolution": nil, "profile_slug": nil, "profile_name": nil},
	})
	b.json("GET", "/profiles", 200, []any{})
	m := open(t, ui.NewConflictsModel(b.client()))
	m = drive(t, m, press("enter"))
	v := content(m)
	if !strings.Contains(v, `in profile "Heim"`) || !strings.Contains(v, ".bashrc.conflict-<time>") || strings.Contains(v, "Local modified:") {
		t.Errorf("name only:\n%s", v)
	}
	if h := m.KeyHints(); !strings.Contains(h, "d:dismiss") {
		t.Errorf("choice hints = %q", h)
	}
	m = drive(t, m, press("x")) // not an action
	if !m.CapturesInput() || strings.Contains(content(m), "[y]es") {
		t.Error("an unknown key left the choices or asked")
	}
	m = drive(t, m, press("esc"), press("down"), press("enter"))
	if v := content(m); !strings.Contains(v, "in profile work") {
		t.Errorf("slug only:\n%s", v)
	}
	m = drive(t, m, press("esc"), press("down"), press("enter"), press("b"))
	if v := content(m); !strings.Contains(v, "in profile (unknown)") {
		t.Errorf("unknown profile:\n%s", v)
	}
	if h := m.KeyHints(); h != "y:yes  n/Esc:no" {
		t.Errorf("prompt hints = %q", h)
	}
	m = drive(t, m, press("esc"))
	if len(b.matching("POST")) != 0 || m.CapturesInput() {
		t.Errorf("Esc sent %v", b.matching("POST"))
	}
}

// A two-way conflict whose local copy name is unknown names the file
// itself for that side.
func TestConflictsView_TwoWayWithOneKeptName(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/conflicts", 200, []any{
		map[string]any{"id": 5, "job_id": 9, "file_path": "notes.md", "local_modified": nil, "remote_modified": nil,
			"resolved": false, "resolution": nil, "profile_slug": "docs", "profile_name": "Dokumente",
			"local_kept_as": nil, "remote_kept_as": "notes.remote-conflict1.md"},
	})
	b.json("GET", "/profiles", 200, []any{})
	m := open(t, ui.NewConflictsModel(b.client()))
	if v := content(m); !strings.Contains(v, "local version as notes.md, remote version as notes.remote-conflict1.md") {
		t.Errorf("kept-as sentence:\n%s", v)
	}
	if m.ViewID() != ui.ViewConflicts || !strings.Contains(m.KeyHints(), "Enter:resolve") {
		t.Errorf("view id or hints: %q", m.KeyHints())
	}
	found := false
	for _, kb := range m.KeyBindings() {
		if kb.Key == "d" && strings.Contains(kb.Desc, "asks first") {
			found = true
		}
	}
	if !found {
		t.Errorf("bindings = %v", m.KeyBindings())
	}
}
