package ui_test

import (
	"fmt"
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

func dashboardBackend(t *testing.T) *backend {
	t.Helper()
	b := newBackend(t)
	b.json("GET", "/sync/status/aggregate", 200, map[string]any{
		"overall_state": "error", "total_pending_changes": 3, "paused_profiles": []any{},
		"profiles_summary": []any{map[string]any{"slug": "docs", "name": "Dokumente", "state": "idle", "last_sync": "2026-09-27T08:30:00Z", "pending_changes": 1, "intervals_paused": false}},
	})
	// Like the real backend: /health does not contact remotes and sends null.
	b.json("GET", "/health", 200, map[string]any{"status": "ok", "rclone_installed": true, "remote_accessible": nil, "database_ok": true, "uptime_seconds": 7200})
	b.json("GET", "/health/remotes", 200, map[string]any{"remotes": []any{
		map[string]any{"remote": "gdrive", "accessible": true, "profiles": []any{"docs"}},
		map[string]any{"remote": "onedrive", "accessible": false, "profiles": []any{"pics"}},
	}})
	b.json("GET", "/health/network", 200, map[string]any{"dns_google": map[string]any{"ok": true}, "httpx_cloudflare": map[string]any{"ok": true}, "rclone_network": map[string]any{"ok": false, "error": "x"}})
	off := profileJSON("off", "Aus", "idle")
	off["enabled"] = false
	docs := profileJSON("docs", "Dokumente", "idle")
	docs["max_delete"] = 25
	docs["intervals_paused"] = true
	pics := profileJSON("pics", "Bilder", "error")
	pics["last_error"] = "Local folder '/home/u/pics' is missing"
	pics["max_delete"] = nil
	b.json("GET", "/profiles", 200, []any{docs, pics, off})
	b.json("POST", "/profiles/docs/sync/preview", 200, map[string]any{
		"push":     map[string]any{"deletes": 3, "replaces": 2, "creates": 7, "exceeds_max_delete": false},
		"pull":     map[string]any{"deletes": 4, "replaces": 1, "creates": 0, "exceeds_max_delete": false},
		"excluded": 0, "max_delete": 25, "error": nil,
	})
	// pics: the preview fails, so its counts are unavailable.
	b.json("POST", "/profiles/pics/sync/preview", 500, map[string]any{"detail": "Could not list the remote folder."})
	b.json("POST", "/profiles/docs/sync/start", 200, map[string]any{"job_id": 1, "state": "idle"})
	b.json("POST", "/profiles/pics/sync/start", 200, map[string]any{"job_id": 2, "state": "error"})
	return b
}

func dashStep(t *testing.T, m ui.DashboardModel, msg tea.Msg) ui.DashboardModel {
	t.Helper()
	queue := []tea.Msg{msg}
	for i := 0; i < 50 && len(queue) > 0; i++ {
		next := queue[0]
		queue = queue[1:]
		switch next.(type) {
		case ui.FlashMsg, ui.NavigateMsg:
			continue
		}
		updated, cmd := m.Update(next)
		m = updated.(ui.DashboardModel)
		queue = append(queue, runAll(cmd)...)
	}
	return m
}

func openDashboard(t *testing.T, b *backend) ui.DashboardModel {
	t.Helper()
	m := ui.NewDashboardModel(b.client())
	m = dashStep(t, m, tea.WindowSizeMsg{Width: 140, Height: 40})
	for _, msg := range runAll(m.Init()) {
		m = dashStep(t, m, msg)
	}
	return m
}

func TestDashboard_Renders(t *testing.T) {
	b := dashboardBackend(t)
	m := openDashboard(t, b)
	v := stripANSI(m.View().Content)
	for _, want := range []string{"Sync Status", "Health", "status: ", "ok", "2.0h", "Dokumente", "Bilder", "internet"} {
		if !strings.Contains(v, want) {
			t.Errorf("dashboard lacks %q:\n%s", want, v)
		}
	}
}

// Item 8: a profile in the error state shows why. last_error comes with
// GET /profiles; no per-profile status lookups.
func TestDashboard_ShowsLastError(t *testing.T) {
	b := dashboardBackend(t)
	m := openDashboard(t, b)
	if v := stripANSI(m.View().Content); !strings.Contains(v, "Bilder: Local folder '/home/u/pics' is missing") {
		t.Errorf("last error missing:\n%s", v)
	}
	for _, r := range b.log() {
		if strings.HasSuffix(r, "/sync/status") {
			t.Errorf("extra status lookup: %s", r)
		}
	}
}

// The network probe is slow; it runs on open and on 'r', not on every tick.
func TestDashboard_TickSkipsNetworkProbe(t *testing.T) {
	b := dashboardBackend(t)
	m := openDashboard(t, b)
	b.reset()
	m = dashStep(t, m, ui.TickMsg{})
	if len(b.matching("GET /health/network")) != 0 {
		t.Error("network probe on tick")
	}
	_ = dashStep(t, m, press("r"))
	if len(b.matching("GET /health/network")) != 1 {
		t.Error("no network probe on refresh")
	}
}

// GET /health sends remote_accessible: null; the dashboard takes remote
// reachability from GET /health/remotes instead of showing null as down.
func TestDashboard_RemoteReachabilityFromHealthRemotes(t *testing.T) {
	b := dashboardBackend(t)
	m := openDashboard(t, b)
	v := stripANSI(m.View().Content)
	g := theme.Glyphs()
	want := "remotes: gdrive: " + g.Check + "  onedrive: " + g.Cross
	if !strings.Contains(v, want) {
		t.Errorf("dashboard lacks %q:\n%s", want, v)
	}
	if strings.Contains(v, "remote: ") {
		t.Errorf("dashboard still shows /health's remote field:\n%s", v)
	}
	if len(b.matching("GET /health/remotes")) != 1 {
		t.Errorf("requests = %v", b.log())
	}
}

func TestDashboard_RemoteReachabilityUnknownOrNone(t *testing.T) {
	b := dashboardBackend(t)
	b.json("GET", "/health/remotes", 500, map[string]any{"detail": "boom"})
	v := stripANSI(openDashboard(t, b).View().Content)
	if !strings.Contains(v, "remotes: unknown (check failed") {
		t.Errorf("failed check not shown as unknown:\n%s", v)
	}

	b.json("GET", "/health/remotes", 200, map[string]any{"remotes": []any{}})
	v = stripANSI(openDashboard(t, b).View().Content)
	if !strings.Contains(v, "remotes: none in use by a running profile") {
		t.Errorf("empty list not explained:\n%s", v)
	}
}

// The remote check calls providers; it runs on open and on 'r', not per tick.
func TestDashboard_TickSkipsRemoteProbe(t *testing.T) {
	b := dashboardBackend(t)
	m := openDashboard(t, b)
	b.reset()
	m = dashStep(t, m, ui.TickMsg{})
	if len(b.matching("GET /health/remotes")) != 0 {
		t.Error("remote probe on tick")
	}
	_ = dashStep(t, m, press("r"))
	if len(b.matching("GET /health/remotes")) != 1 {
		t.Error("no remote probe on refresh")
	}
}

// starts returns the recorded POST .../sync/start requests.
func starts(b *backend) []string {
	var out []string
	for _, r := range b.log() {
		if strings.HasSuffix(r, "/sync/start") {
			out = append(out, r)
		}
	}
	return out
}

// Push all previews every enabled profile, asks once, then syncs
// every enabled profile through the per-profile endpoint with force=true
// (the backend has no single-engine /sync/start). The prompt lists each profile's
// deletes and replaces and its own delete limit.
func TestDashboard_PushAllAsksThenUsesProfileEndpoints(t *testing.T) {
	b := dashboardBackend(t)
	m := openDashboard(t, b)
	b.reset()

	m = dashStep(t, m, press("p"))
	if !m.CapturesInput() {
		t.Fatal("expected a confirmation")
	}
	v := stripANSI(m.View().Content)
	for _, want := range []string{"Push all 2 enabled profile(s)",
		"Dokumente (docs): deletes 3, replaces 2 on the remote; delete limit 25 files, automatic syncs paused",
		"Bilder (pics): counts unavailable; no delete limit", "DELETED on the remote", ".omnisync-trash", "stops at the", "paused are synced as well"} {
		if !strings.Contains(v, want) {
			t.Errorf("prompt lacks %q:\n%s", want, v)
		}
	}
	if strings.Contains(v, "Aus (off)") {
		t.Error("disabled profile listed")
	}
	if strings.Contains(v, "50") {
		t.Errorf("prompt shows the old fixed limit:\n%s", v)
	}
	previews := b.matching("POST /profiles/")
	if fmt.Sprint(previews) != "[POST /profiles/docs/sync/preview POST /profiles/pics/sync/preview]" &&
		fmt.Sprint(previews) != "[POST /profiles/pics/sync/preview POST /profiles/docs/sync/preview]" {
		t.Errorf("requests before the answer = %v, want one preview per enabled profile", previews)
	}
	m = dashStep(t, m, press("n"))
	if len(starts(b)) != 0 {
		t.Fatalf("syncs after 'n': %v", b.log())
	}
	m = dashStep(t, m, press("p"))
	m = dashStep(t, m, press("esc"))
	if len(starts(b)) != 0 || m.CapturesInput() {
		t.Fatalf("syncs after Esc: %v", b.log())
	}

	// pics has no preview: 'y' alone sends nothing, "force" + Enter does.
	m = dashStep(t, m, press("p"))
	m = dashStep(t, m, press("y"))
	if len(starts(b)) != 0 || !m.CapturesInput() {
		t.Fatalf("'y' forced syncs without a preview: %v", b.log())
	}
	m = dashStep(t, m, press("backspace"))
	_ = typeForce(t, m)
	if len(b.matching("POST /sync/")) != 0 {
		t.Error("a /sync/* route the backend does not have was used")
	}
	for _, slug := range []string{"docs", "pics"} {
		bodies := b.bodiesOf("POST /profiles/" + slug + "/sync/start")
		if len(bodies) != 1 {
			t.Errorf("%s: starts = %v", slug, b.log())
			continue
		}
		if bodies[0]["direction"] != "push" || bodies[0]["force"] != true {
			t.Errorf("%s: start body = %v, want direction push, force true", slug, bodies[0])
		}
	}
	if len(b.matching("POST /profiles/off/")) != 0 {
		t.Error("disabled profile synced")
	}
}

func TestDashboard_PullAllConfirmedForcesEachProfile(t *testing.T) {
	b := dashboardBackend(t)
	m := openDashboard(t, b)
	b.reset()
	m = dashStep(t, m, press("l"))
	v := stripANSI(m.View().Content)
	for _, want := range []string{"Pull all 2 enabled profile(s)", "DELETED locally",
		"Dokumente (docs): deletes 4, replaces 1 locally; delete limit 25 files", "Bilder (pics): counts unavailable; no delete limit"} {
		if !strings.Contains(v, want) {
			t.Errorf("prompt lacks %q:\n%s", want, v)
		}
	}
	_ = typeForce(t, m)
	for _, slug := range []string{"docs", "pics"} {
		bodies := b.bodiesOf("POST /profiles/" + slug + "/sync/start")
		if len(bodies) != 1 || bodies[0]["direction"] != "pull" || bodies[0]["force"] != true {
			t.Errorf("%s: start bodies = %v", slug, bodies)
		}
	}
}

// The limit shown is the preview's (the effective one); a sync that would
// go over it is flagged, per profile.
func TestDashboard_SyncAllFlagsProfilesOverTheLimit(t *testing.T) {
	b := dashboardBackend(t)
	b.json("POST", "/profiles/pics/sync/preview", 200, map[string]any{
		"push":     map[string]any{"deletes": 120, "replaces": 0, "creates": 0, "exceeds_max_delete": true},
		"pull":     map[string]any{"deletes": 0, "replaces": 0, "creates": 0, "exceeds_max_delete": false},
		"excluded": 0, "max_delete": 40, "error": "partial listing",
	})
	m := openDashboard(t, b)
	m = dashStep(t, m, press("p"))
	v := stripANSI(m.View().Content)
	for _, want := range []string{"Bilder (pics): deletes 120, replaces 0 on the remote", "stops at the delete limit",
		"(may be incomplete: the comparison reported a problem); delete limit 40 files", "1 profile(s) above would stop at their delete limit"} {
		if !strings.Contains(v, want) {
			t.Errorf("prompt lacks %q:\n%s", want, v)
		}
	}
}

// The prompt opens only when every preview has answered; Esc while counting
// cancels, and late previews then open nothing.
func TestDashboard_SyncAllWaitsForPreviewsAndCanBeCancelled(t *testing.T) {
	b := dashboardBackend(t)
	m := openDashboard(t, b)
	updated, cmd := m.Update(press("p"))
	m = updated.(ui.DashboardModel)
	if !m.CapturesInput() || !strings.Contains(stripANSI(m.View().Content), "Counting what a push of 2 profile(s) would change") {
		t.Fatalf("no counting view:\n%s", stripANSI(m.View().Content))
	}
	late := runAll(cmd)
	if len(late) != 2 {
		t.Fatalf("preview results = %d, want 2", len(late))
	}
	updated, _ = m.Update(late[0])
	m = updated.(ui.DashboardModel)
	if v := stripANSI(m.View().Content); !strings.Contains(v, "1 of 2 done") || strings.Contains(v, "Push all 2") {
		t.Fatalf("prompt before every preview answered:\n%s", v)
	}
	m = dashStep(t, m, press("esc"))
	if m.CapturesInput() {
		t.Fatal("Esc did not cancel the counting")
	}
	m = dashStep(t, m, late[1])
	if m.CapturesInput() || len(starts(b)) != 0 {
		t.Fatalf("a late preview reopened the prompt or synced: %v", b.log())
	}
}

func TestDashboard_EnterOpensSelectedProfile(t *testing.T) {
	b := dashboardBackend(t)
	m := openDashboard(t, b)
	m = dashStep(t, m, press("down"))
	_, cmd := m.Update(press("enter"))
	nav, ok := find[ui.NavigateMsg](runAll(cmd))
	if !ok || nav.Target != ui.ViewProfileDetail || nav.Param != "pics" {
		t.Errorf("navigate = %+v", nav)
	}
}

// typeForce types the acknowledgement for a sync without a preview and
// presses Enter.
func typeForce(t *testing.T, m ui.DashboardModel) ui.DashboardModel {
	t.Helper()
	for _, k := range typeKeys(ui.ForceWord) {
		m = dashStep(t, m, k)
	}
	return dashStep(t, m, press("enter"))
}

// A profile whose preview failed ("counts unavailable") is sent with force
// only after the explicit acknowledgement, like the web UI's checkbox:
// 'y' and a wrong or partial word send nothing.
func TestDashboard_SyncAllWithoutPreviewNeedsTypedForce(t *testing.T) {
	b := dashboardBackend(t)
	m := openDashboard(t, b)
	b.reset()
	m = dashStep(t, m, press("p"))
	if v := stripANSI(m.View().Content); !strings.Contains(v, `Type "force" and press Enter to sync without a preview`) ||
		strings.Contains(v, "[y]es") {
		t.Errorf("prompt does not ask for the acknowledgement:\n%s", v)
	}
	for _, k := range []string{"y", "Y", "enter"} {
		m = dashStep(t, m, press(k))
	}
	for _, k := range typeKeys("forc") {
		m = dashStep(t, m, k)
	}
	m = dashStep(t, m, press("enter"))
	if len(starts(b)) != 0 || !m.CapturesInput() {
		t.Fatalf("synced without the acknowledgement: %v", b.log())
	}
	m = dashStep(t, m, press("esc"))
	if len(starts(b)) != 0 || m.CapturesInput() {
		t.Fatalf("Esc must cancel without syncing: %v", b.log())
	}
	// 'n' before typing anything still cancels.
	m = dashStep(t, m, press("p"))
	m = dashStep(t, m, press("n"))
	if len(starts(b)) != 0 || m.CapturesInput() {
		t.Fatalf("'n' must cancel without syncing: %v", b.log())
	}
}

// With every preview in, plain 'y' confirms as before.
func TestDashboard_SyncAllWithPreviewsConfirmsWithY(t *testing.T) {
	b := dashboardBackend(t)
	b.json("POST", "/profiles/pics/sync/preview", 200, map[string]any{
		"push":     map[string]any{"deletes": 0, "replaces": 0, "creates": 1, "exceeds_max_delete": false},
		"pull":     map[string]any{"deletes": 0, "replaces": 0, "creates": 0, "exceeds_max_delete": false},
		"excluded": 0, "max_delete": nil, "error": nil,
	})
	m := openDashboard(t, b)
	b.reset()
	m = dashStep(t, m, press("p"))
	if v := stripANSI(m.View().Content); !strings.Contains(v, "[y]es") {
		t.Errorf("prompt:\n%s", v)
	}
	_ = dashStep(t, m, press("y"))
	if got := starts(b); len(got) != 2 {
		t.Errorf("starts = %v", got)
	}
}
