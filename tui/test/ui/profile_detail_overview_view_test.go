package ui_test

import (
	"sort"
	"strings"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

// errorEnvelope is the backend's error body.
func errorEnvelope(detail, code string) map[string]any {
	return map[string]any{"detail": detail, "code": code, "details": map[string]any{"slug": "docs"}}
}

// The Overview's stop (s), resume intervals (i) and test sync (x) each send
// their one request and report the outcome; a refusal shows the envelope's
// detail so the user learns why.
func TestProfileDetailOverview_ActionsAndErrorEnvelopes(t *testing.T) {
	cases := []struct {
		name, key, path string
		status          int
		reply           any
		want            []string
	}{
		{"stop", "s", "/profiles/docs/sync/stop", 200, map[string]any{"detail": "stopped"}, []string{"Sync stopped"}},
		{"stop refused", "s", "/profiles/docs/sync/stop", 409, errorEnvelope("No sync is running for 'docs'", "sync_not_running"),
			[]string{"Error: API error 409: No sync is running for 'docs'"}},
		{"resume", "i", "/profiles/docs/sync/resume-intervals", 200, map[string]any{"detail": "resumed"}, []string{"Intervals resumed"}},
		{"resume refused", "i", "/profiles/docs/sync/resume-intervals", 404, errorEnvelope("Profile 'docs' is not running", "profile_not_running"),
			[]string{"Error: API error 404: Profile 'docs' is not running"}},
		{"test passes", "x", "/profiles/docs/config/test-sync", 200, map[string]any{"success": true, "steps": []any{}},
			[]string{"Running test sync...", "Test sync passed"}},
		{"test fails", "x", "/profiles/docs/config/test-sync", 200,
			map[string]any{"success": false, "steps": []any{}, "error": "remote not writable"},
			[]string{"Running test sync...", "Test sync failed: remote not writable"}},
		{"test refused", "x", "/profiles/docs/config/test-sync", 400, errorEnvelope("Test sync needs a running profile", "invalid_state"),
			[]string{"Running test sync...", "Error: API error 400: Test sync needs a running profile"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			b := detailBackend(t)
			b.json("POST", tc.path, tc.status, tc.reply)
			m := openDetail(t, b)
			b.reset()
			got := flashesAfter(m, tc.key, time.Second)
			if strings.Join(got, "|") != strings.Join(tc.want, "|") {
				t.Errorf("flashes = %q, want %q", got, tc.want)
			}
			if posts := b.matching("POST "); len(posts) != 1 || posts[0] != "POST "+tc.path {
				t.Errorf("requests = %v", posts)
			}
		})
	}
}

// s while a sync runs stops it: the spinner goes away and the view refetches
// the profile.
func TestProfileDetailOverview_StopEndsTheRunningState(t *testing.T) {
	b := detailBackend(t)
	b.json("POST", "/profiles/docs/sync/stop", 200, map[string]any{"detail": "stopped"})
	m := openDetail(t, b)
	busy := &api.ProfileStatusResponse{ProfileResponse: api.ProfileResponse{Slug: "docs", Name: "Dokumente"}, State: "pushing"}
	updated, _ := m.Update(ui.PollResultMsg{ViewID: ui.ViewProfileDetail, Data: ui.ProfileDetailData{Slug: "docs", Value: busy}})
	m = updated.(ui.ProfileDetailModel)
	if !strings.Contains(view(m), "Syncing") {
		t.Fatalf("no running state:\n%s", view(m))
	}
	b.reset()
	m = detailStep(t, m, press("s"))
	if got := b.log(); len(got) != 2 || got[0] != "POST /profiles/docs/sync/stop" || got[1] != "GET /profiles/docs" {
		t.Errorf("requests = %v", got)
	}
	if strings.Contains(view(m), "Syncing") {
		t.Errorf("still shown as running after the stop:\n%s", view(m))
	}
}

// r refetches the profile; an unknown key sends nothing.
func TestProfileDetailOverview_RefreshAndUnknownKey(t *testing.T) {
	b := detailBackend(t)
	m := openDetail(t, b)
	b.reset()
	m = detailStep(t, m, press("q"))
	if got := b.log(); len(got) != 0 {
		t.Errorf("unknown key sent %v", got)
	}
	_ = detailStep(t, m, press("r"))
	if got := b.log(); len(got) != 1 || got[0] != "GET /profiles/docs" {
		t.Errorf("r sent %v", got)
	}
}

// When the app shows Profile Detail again it runs Init: the profile and the
// data of the tab left open are loaded again, nothing else.
func TestProfileDetailOverview_InitReloadsTheOpenTab(t *testing.T) {
	b := diffFilesBackend(t)
	b.json("GET", "/profiles/docs/backups", 200, []any{})
	b.json("GET", "/jobs", 200, []any{})
	b.json("GET", "/profiles/docs/trash", 200, map[string]any{"side": "local", "entries": []any{}, "total_files": 0, "total_bytes": 0})
	m := openDetail(t, b)
	want := []string{
		"POST /profiles/docs/diff?limit=0&offset=0",
		"GET /profiles/docs/backups",
		"GET /jobs?limit=20&profile=docs&skip=0",
		"GET /profiles/docs/trash?side=local",
	}
	for _, w := range want {
		m = detailStep(t, m, press("right"))
		b.reset()
		for _, msg := range runAll(m.Init()) {
			m = detailStep(t, m, msg)
		}
		got := b.log()
		sort.Strings(got)
		expect := []string{"GET /profiles/docs", w}
		sort.Strings(expect)
		if strings.Join(got, "|") != strings.Join(expect, "|") {
			t.Errorf("Init requests = %v, want the profile and %s", got, w)
		}
	}
	if cmd := ui.NewProfileDetailModel(b.client()).Init(); cmd != nil {
		t.Error("Init without a profile loads something")
	}
}

// The poll tick refreshes the profile, and on the History list also the
// history (a running job's status changes); without a profile it is ignored.
func TestProfileDetailOverview_TickRefreshes(t *testing.T) {
	b := detailBackend(t)
	b.json("GET", "/jobs", 200, []any{})
	m := openDetail(t, b)
	b.reset()
	m = detailStep(t, m, ui.TickMsg{})
	if got := b.log(); len(got) != 1 || got[0] != "GET /profiles/docs" {
		t.Errorf("tick on the Overview = %v", got)
	}
	for i := 0; i < 3; i++ {
		m = detailStep(t, m, press("right"))
	}
	b.reset()
	_ = detailStep(t, m, ui.TickMsg{})
	if got := b.matching("GET /jobs?"); len(got) != 1 {
		t.Errorf("tick on History = %v", b.log())
	}
	empty := ui.NewProfileDetailModel(b.client())
	if _, cmd := empty.Update(ui.TickMsg{}); cmd != nil {
		t.Error("tick without a profile loads something")
	}
}

// keyDescs joins the help screen's key descriptions.
func keyDescs(keys []components.KeyBinding) string {
	var b strings.Builder
	for _, k := range keys {
		b.WriteString(k.Key + " " + k.Desc + "\n")
	}
	return b.String()
}

// The help screen lists the keys of the tab (and sub-view) shown, so a user
// does not see keys that do nothing there.
func TestProfileDetailOverview_KeyBindingsFollowTheTab(t *testing.T) {
	b := backupsBackend(t, nil)
	b.json("POST", "/profiles/docs/diff", 200, map[string]any{"files": []any{}, "summary": map[string]any{}})
	b.json("GET", "/jobs", 200, jobsJSON(1, 100))
	b.json("GET", "/jobs/100", 200, jobsJSON(1, 100)[0])
	b.json("GET", "/jobs/100/files", 200, []any{})
	b.json("GET", "/profiles/docs/trash", 200, map[string]any{"side": "local", "entries": []any{}, "total_files": 0, "total_bytes": 0})
	b.json("GET", "/profiles/docs/backups/3/snapshots/"+snap+"/files", 200, map[string]any{"snapshot_id": snap, "path": "",
		"total": 0, "offset": 0, "limit": 1000, "snapshot_files": 0, "entries": []any{}})

	check := func(where string, m ui.ProfileDetailModel, want, notWant []string) {
		t.Helper()
		got := keyDescs(m.KeyBindings())
		for _, w := range append(want, "Switch tab") {
			if !strings.Contains(got, w) {
				t.Errorf("%s: help lacks %q:\n%s", where, w, got)
			}
		}
		for _, w := range notWant {
			if strings.Contains(got, w) {
				t.Errorf("%s: help shows %q:\n%s", where, w, got)
			}
		}
	}

	check("not loaded", ui.NewProfileDetailModel(b.client()), []string{"Resync", "Switch this mirror profile"}, nil)
	m := openDetail(t, b)
	check("overview (mirror)", m, []string{"Stop sync", "Test sync", "Resume intervals", "Switch this mirror profile",
		"Push: make the remote match local"}, []string{"Resync", "one-way override"})
	m = detailStep(t, m, press("right"))
	check("differences", m, []string{"Cycle filter", "Push selected files (asks first)"}, []string{"Stop sync"})
	m = detailStep(t, m, press("right"))
	check("backup targets", m, []string{"Toggle enabled", "Delete target (asks first)"}, nil)
	m = detailStep(t, m, press("enter"))
	check("snapshots", m, []string{"Restore snapshot", "Browse the snapshot"}, []string{"Toggle enabled"})
	m = detailStep(t, m, press("f"))
	check("snapshot files", m, []string{"Search the snapshot", "Restore the selection into another folder"}, nil)
	m = detailStep(t, m, press("esc"))
	m = detailStep(t, m, press("esc"))
	m = detailStep(t, m, press("right"))
	check("history", m, []string{"View job detail and file changes", "older/newer jobs"}, nil)
	m = detailStep(t, m, press("enter"))
	check("history job", m, []string{"Move through the job's file changes", "Back to the history list"}, []string{"View job detail"})
	m = detailStep(t, m, press("esc"))
	m = detailStep(t, m, press("right"))
	check("trash", m, []string{"Switch between the local and the remote trash", "Delete the selected files for good (asks first)"}, nil)
}

// A two-way profile's Overview help names the two-way keys and calls push
// and pull one-way overrides.
func TestProfileDetailOverview_KeyBindingsTwoWay(t *testing.T) {
	b := detailBackend(t)
	p := profileJSON("docs", "Dokumente", "idle")
	p["sync_mode"] = "two_way"
	b.json("GET", "/profiles/docs", 200, p)
	m := openDetail(t, b)
	got := keyDescs(m.KeyBindings())
	for _, w := range []string{"Sync now: two-way sync", "Resync", "Push: one-way override"} {
		if !strings.Contains(got, w) {
			t.Errorf("help lacks %q:\n%s", w, got)
		}
	}
	if strings.Contains(got, "Switch this mirror profile") {
		t.Errorf("two-way help offers the switch to two-way:\n%s", got)
	}
}

// Resizing the terminal re-lays out the tables; the view keeps its content.
func TestProfileDetailOverview_ResizeKeepsContent(t *testing.T) {
	b := detailBackend(t)
	m := openDetail(t, b)
	m = detailStep(t, m, tea.WindowSizeMsg{Width: 80, Height: 30})
	if v := view(m); !strings.Contains(v, "Dokumente") {
		t.Errorf("view after resize:\n%s", v)
	}
}
