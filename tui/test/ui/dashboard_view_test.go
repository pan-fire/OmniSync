package ui_test

import (
	"net/http"
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// busyDashboardBackend is dashboardBackend with two running syncs (docs
// pushing, pics pulling) next to an idle profile.
func busyDashboardBackend(t *testing.T) *backend {
	t.Helper()
	b := dashboardBackend(t)
	b.json("GET", "/profiles", 200, []any{
		profileJSON("docs", "Dokumente", "pushing"), profileJSON("pics", "Bilder", "pulling"), profileJSON("idle", "Ruhig", "idle"),
	})
	b.json("POST", "/profiles/docs/sync/stop", 200, map[string]any{"detail": "stopped"})
	b.json("POST", "/profiles/pics/sync/stop", 200, map[string]any{"detail": "stopped"})
	return b
}

// s lists the running syncs and asks first; y stops exactly those: one
// request per busy profile, none for the idle one, and each answer is
// reported.
func TestDashboardView_StopAllStopsOnlyRunningSyncs(t *testing.T) {
	b := busyDashboardBackend(t)
	m := openDashboard(t, b)
	b.reset()
	m = drive(t, m, press("s"))
	if !m.CapturesInput() || m.KeyHints() != "y:yes  n/Esc:no" {
		t.Fatalf("s did not ask: hints %q", m.KeyHints())
	}
	v := content(m)
	for _, want := range []string{"Stop 2 running sync(s)?", "Dokumente (docs): pushing", "Bilder (pics): pulling", "Automatic syncing continues"} {
		if !strings.Contains(v, want) {
			t.Errorf("prompt lacks %q:\n%s", want, v)
		}
	}
	if strings.Contains(v, "Ruhig (idle)") {
		t.Errorf("prompt lists the idle profile:\n%s", v)
	}
	if got := b.matching("POST"); len(got) != 0 {
		t.Fatalf("s sent %v before the answer", got)
	}
	_, flashes := listsFlashes(t, m, press("y"))
	got := b.matching("POST")
	if len(got) != 2 || !strings.Contains(strings.Join(got, " "), "POST /profiles/docs/sync/stop") ||
		!strings.Contains(strings.Join(got, " "), "POST /profiles/pics/sync/stop") {
		t.Errorf("stop requests = %v", got)
	}
	if strings.Join(flashes, "|") != "Sync stopped|Sync stopped" {
		t.Errorf("flashes = %v", flashes)
	}
}

// n and Esc cancel "Stop all": nothing is sent and the syncs keep running.
func TestDashboardView_StopAllCancelSendsNothing(t *testing.T) {
	for _, key := range []string{"n", "esc"} {
		t.Run(key, func(t *testing.T) {
			b := busyDashboardBackend(t)
			m := openDashboard(t, b)
			b.reset()
			m = drive(t, m, press("s"))
			m, flashes := listsFlashes(t, m, press(key))
			if got := b.matching("POST"); len(got) != 0 {
				t.Errorf("%s sent %v", key, got)
			}
			if m.CapturesInput() || strings.Join(flashes, "|") != "Stop all cancelled; the syncs keep running" {
				t.Errorf("%s: captures %v, flashes %v", key, m.CapturesInput(), flashes)
			}
		})
	}
}

// The prompt's answer stops the profiles it listed, even when a poll in
// between shows another profile syncing: the user did not see that one.
func TestDashboardView_StopAllStopsWhatThePromptListed(t *testing.T) {
	b := busyDashboardBackend(t)
	m := openDashboard(t, b)
	m = drive(t, m, press("s"))
	b.json("GET", "/profiles", 200, []any{
		profileJSON("docs", "Dokumente", "pushing"), profileJSON("pics", "Bilder", "pulling"), profileJSON("idle", "Ruhig", "pushing"),
	})
	m = drive(t, m, ui.TickMsg{})
	b.reset()
	_, _ = listsFlashes(t, m, press("y"))
	if got := strings.Join(b.matching("POST"), " "); strings.Contains(got, "/profiles/idle/") || !strings.Contains(got, "/profiles/docs/sync/stop") {
		t.Errorf("stop requests = %v", got)
	}
}

// With nothing running, s says so and sends nothing.
func TestDashboardView_StopAllWithNothingRunning(t *testing.T) {
	b := dashboardBackend(t)
	m := openDashboard(t, b)
	b.reset()
	_, flashes := listsFlashes(t, m, press("s"))
	if len(b.matching("POST")) != 0 || strings.Join(flashes, "|") != "No sync is running" {
		t.Errorf("requests %v, flashes %v", b.log(), flashes)
	}
}

// A refused stop shows the backend's reason.
func TestDashboardView_StopRefusedShowsTheDetail(t *testing.T) {
	b := busyDashboardBackend(t)
	b.json("POST", "/profiles/pics/sync/stop", 409, map[string]any{
		"detail": "No sync is running for 'pics'", "code": "sync_not_running", "details": map[string]any{"slug": "pics"}})
	m := openDashboard(t, b)
	m = drive(t, m, press("s"))
	_, flashes := listsFlashes(t, m, press("y"))
	joined := strings.Join(flashes, "|")
	if !strings.Contains(joined, "Sync stopped") || !strings.Contains(joined, "Stop failed: API error 409: No sync is running for 'pics'") {
		t.Errorf("flashes = %v", flashes)
	}
}

// Failed polls: with the database down, the error is shown instead of
// "Loading", and a failed health check keeps "Checking..." rather than
// claiming a state. Once health answers, failed network and remote probes
// say so.
func TestDashboardView_PollErrors(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/sync/status/aggregate", 500, map[string]any{"detail": "database is locked", "code": "db_busy", "details": nil})
	b.json("GET", "/profiles", 500, map[string]any{"detail": "database is locked", "code": "db_busy", "details": nil})
	b.json("GET", "/health", 503, map[string]any{"detail": "starting"})
	b.json("GET", "/health/network", 504, map[string]any{"detail": "probe timed out"})
	b.json("GET", "/health/remotes", 502, map[string]any{"detail": "rclone failed"})
	m := ui.NewDashboardModel(b.client())
	m = dashStep(t, m, tea.WindowSizeMsg{Width: 140, Height: 40})
	if !strings.Contains(content(m), "Loading dashboard...") {
		t.Errorf("no loading state:\n%s", content(m))
	}
	for _, msg := range runAll(m.Init()) {
		m = dashStep(t, m, msg)
	}
	v := content(m)
	if !strings.Contains(v, "Error: API error 500: database is locked") || !strings.Contains(v, "Checking...") || strings.Contains(v, "Loading dashboard") {
		t.Errorf("error state:\n%s", v)
	}
	b.json("GET", "/health", 200, map[string]any{"status": "ok", "rclone_installed": true, "database_ok": true, "uptime_seconds": 60})
	// An empty profile list may come back as JSON null.
	b.handle("GET", "/profiles", func(w http.ResponseWriter, _ map[string]any) { _, _ = w.Write([]byte("null")) })
	m = dashStep(t, m, press("r"))
	v = content(m)
	for _, want := range []string{
		"network check failed: API error 504: probe timed out",
		"remotes: unknown (check failed: API error 502: rclone failed)",
		"(empty)",
	} {
		if !strings.Contains(v, want) {
			t.Errorf("view lacks %q:\n%s", want, v)
		}
	}
}

// The aggregate status and the profile list are read separately: the list
// answering must not hide that the status failed (the profile answer used
// to clear the shared error, leaving "No data" and no reason).
func TestDashboardView_ProfileAnswerKeepsTheStatusError(t *testing.T) {
	b := dashboardBackend(t)
	b.json("GET", "/sync/status/aggregate", 500, map[string]any{"detail": "database is locked", "code": "db_busy", "details": nil})
	m := ui.NewDashboardModel(b.client())
	m = dashStep(t, m, tea.WindowSizeMsg{Width: 140, Height: 40})
	for _, msg := range runAll(m.Init()) {
		m = dashStep(t, m, msg)
	}
	if v := content(m); !strings.Contains(v, "Error: API error 500: database is locked") {
		t.Errorf("status error hidden:\n%s", v)
	}
	// And the other way round: a failed list shows, a recovered one clears.
	b.json("GET", "/sync/status/aggregate", 200, map[string]any{"overall_state": "idle"})
	b.json("GET", "/profiles", 503, map[string]any{"detail": "profiles unavailable"})
	m = dashStep(t, m, press("r"))
	if v := content(m); !strings.Contains(v, "Error: API error 503: profiles unavailable") {
		t.Errorf("profile list error hidden:\n%s", v)
	}
	b.json("GET", "/profiles", 200, []any{})
	m = dashStep(t, m, press("r"))
	if v := content(m); strings.Contains(v, "Error:") {
		t.Errorf("error kept after recovery:\n%s", v)
	}
}

// The bottom bar follows the mode: the prompt's keys while asking, Esc
// while counting, and w only when a mirror profile exists.
func TestDashboardView_KeyHintsFollowTheMode(t *testing.T) {
	b := dashboardBackend(t)
	b.json("GET", "/profiles", 200, []any{twoWayProfileJSON(false, false)})
	b.json("POST", "/profiles/docs/sync/preview", 200, previewJSON(0, 0, 50))
	m := openDashboard(t, b)
	if h := m.KeyHints(); strings.Contains(h, "w:all to two-way") || !strings.Contains(h, "s:stop") {
		t.Errorf("list hints = %q", h)
	}
	updated, cmd := m.Update(press("p"))
	m = updated.(ui.DashboardModel)
	if h := m.KeyHints(); h != "Esc:cancel" {
		t.Errorf("counting hints = %q", h)
	}
	for _, msg := range runAll(cmd) {
		m = dashStep(t, m, msg)
	}
	if h := m.KeyHints(); h != "y:yes  n/Esc:no" {
		t.Errorf("prompt hints = %q\n%s", h, content(m))
	}
	b.reset()
	m = dashStep(t, m, press("esc"))
	if len(b.matching("POST /profiles/docs/sync/start")) != 0 || m.CapturesInput() {
		t.Errorf("Esc at the prompt sent %v", b.matching("POST"))
	}
	if m.ViewID() != ui.ViewDashboard {
		t.Errorf("view id = %v", m.ViewID())
	}
	stop := ""
	for _, kb := range m.KeyBindings() {
		if kb.Key == "s" {
			stop = kb.Desc
		}
	}
	if stop != "Stop all running syncs (lists them, then asks)" {
		t.Errorf("s binding = %q", stop)
	}
}
