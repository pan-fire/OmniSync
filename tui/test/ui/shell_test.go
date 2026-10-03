package ui_test

import (
	"strings"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/config"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// hintView is a stub view with its own bottom-bar hints.
type hintView struct {
	*stubView
	hints string
}

func (h hintView) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	_, cmd := h.stubView.Update(msg)
	return h, cmd
}

func (h hintView) KeyHints() string { return h.hints }

func lines(s string) []string { return strings.Split(stripANSI(s), "\n") }

// The top bar shows the app name, backend URL, aggregate sync state
// and a clock.
func TestShell_TopBarURLStateAndClock(t *testing.T) {
	app, _ := newApp(t, "http://backend.example:8000")
	app, _ = step(t, app, tea.WindowSizeMsg{Width: 120, Height: 30})
	app, _ = step(t, app, ui.ConnectionStatusMsg{Connected: true, Healthy: true, LatencyMs: 7, OverallState: api.SyncStatePushing})
	at := time.Date(2026, 9, 28, 14, 5, 9, 0, time.Local)
	app, _ = step(t, app, ui.TickMsg{Time: at})
	top := lines(app.View().Content)[0]
	for _, want := range []string{"OmniSync", "http://backend.example:8000", "[PUSHING]", "14:05:09"} {
		if !strings.Contains(top, want) {
			t.Errorf("top bar %q lacks %q", top, want)
		}
	}
	if tabs := lines(app.View().Content)[1]; !strings.Contains(tabs, "1:Dashboard") || !strings.Contains(tabs, "8:Config") {
		t.Errorf("tab row = %q", tabs)
	}
	// While disconnected the state is unknown, not a stale one.
	app, _ = step(t, app, ui.ConnectionStatusMsg{Connected: false})
	if top := lines(app.View().Content)[0]; !strings.Contains(top, "[UNKNOWN]") {
		t.Errorf("top bar after disconnect = %q", top)
	}
}

// The health check also reads the aggregate state for the badge.
func TestShell_HealthCheckReadsAggregateState(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/health", 200, map[string]any{"status": "ok", "rclone_installed": true, "database_ok": true, "uptime_seconds": 1})
	b.json("GET", "/sync/status/aggregate", 200, map[string]any{"overall_state": "error"})
	app := ui.NewApp(b.client(), &config.Config{URL: b.srv.URL})
	msg, ok := find[ui.ConnectionStatusMsg](runAll(app.Init()))
	if !ok || !msg.Connected || msg.OverallState != api.SyncStateError {
		t.Fatalf("status = %+v", msg)
	}
}

// The bottom bar shows the latency and the active view's own keys,
// then the global ones; a view that owns the keyboard shows only its keys.
func TestShell_BottomBarShowsViewKeys(t *testing.T) {
	app, stubs := newApp(t, "http://127.0.0.1:1")
	h := hintView{stubView: stubs[ui.ViewJobs], hints: "Enter:detail  f:filter"}
	app.RegisterView(h)
	app = connected(t, app)
	app, _ = step(t, app, press("3"))
	all := lines(app.View().Content)
	bottom := all[len(all)-1]
	for _, want := range []string{"5ms", "Enter:detail  f:filter", "?:help"} {
		if !strings.Contains(bottom, want) {
			t.Errorf("bottom bar %q lacks %q", bottom, want)
		}
	}
	h.capture = true
	all = lines(app.View().Content)
	if bottom = all[len(all)-1]; strings.Contains(bottom, "?:help") || !strings.Contains(bottom, "f:filter") {
		t.Errorf("capturing bottom bar = %q", bottom)
	}
	// A view without hints gets the global keys.
	h.capture = false
	app, _ = step(t, app, press("4"))
	all = lines(app.View().Content)
	if bottom = all[len(all)-1]; !strings.Contains(bottom, "?:help") {
		t.Errorf("bottom bar = %q", bottom)
	}
}

// tabX returns a column inside the tab of view id on the tab row.
func tabX(id ui.ViewID) int {
	x := 0
	for i := 0; i < int(id); i++ {
		x += len([]rune(ui.ViewLabels[ui.ViewID(i)])) + 3 // "N:" + label + " "
	}
	return x + 1
}

func click(x, y int) tea.MouseClickMsg {
	return tea.MouseClickMsg(tea.Mouse{X: x, Y: y, Button: tea.MouseLeft})
}

// The mouse is on, and clicking a tab switches to its view.
func TestShell_ClickTabSwitchesView(t *testing.T) {
	app, stubs := newApp(t, "http://127.0.0.1:1")
	app = connected(t, app)
	if app.View().MouseMode != tea.MouseModeCellMotion {
		t.Fatalf("mouse mode = %v", app.View().MouseMode)
	}
	for _, id := range []ui.ViewID{ui.ViewJobs, ui.ViewConfig, ui.ViewDashboard, ui.ViewNotifications} {
		app, _ = step(t, app, click(tabX(id), 1))
		if app.ActiveView() != id {
			t.Errorf("click on %s: active = %s", ui.ViewLabels[id], ui.ViewLabels[app.ActiveView()])
		}
	}
	// Clicks off the tab row do nothing.
	app, _ = step(t, app, click(tabX(ui.ViewJobs), 5))
	if app.ActiveView() != ui.ViewNotifications {
		t.Error("click below the tabs switched views")
	}
	// A view that owns the keyboard keeps it.
	stubs[ui.ViewNotifications].capture = true
	app, _ = step(t, app, click(tabX(ui.ViewJobs), 1))
	if app.ActiveView() != ui.ViewNotifications {
		t.Error("click switched away from an open form")
	}
}

// The wheel scrolls the active view like Up/Down.
func TestShell_WheelScrollsView(t *testing.T) {
	app, stubs := newApp(t, "http://127.0.0.1:1")
	app = connected(t, app)
	app, _ = step(t, app, tea.MouseWheelMsg(tea.Mouse{Button: tea.MouseWheelDown}))
	app, _ = step(t, app, tea.MouseWheelMsg(tea.Mouse{Button: tea.MouseWheelUp}))
	_ = app
	if got := strings.Join(stubs[ui.ViewDashboard].keys, ","); got != "down,up" {
		t.Errorf("view got %q", got)
	}
}

// --no-mouse / OMNISYNC_NO_MOUSE leave the mouse to the terminal.
func TestShell_NoMouse(t *testing.T) {
	client := api.NewClient("http://127.0.0.1:1", "", "test")
	app := ui.NewApp(client, &config.Config{URL: "http://127.0.0.1:1", NoMouse: true})
	app.RegisterView(newStubView(ui.ViewDashboard))
	app = connected(t, app)
	if app.View().MouseMode != tea.MouseModeNone {
		t.Errorf("mouse mode = %v", app.View().MouseMode)
	}
}

// Every main view names its keys for the bottom bar.
func TestShell_MainViewsHaveKeyHints(t *testing.T) {
	client := api.NewClient("http://127.0.0.1:1", "", "test")
	views := []ui.View{
		ui.NewDashboardModel(client), ui.NewProfilesModel(client), ui.NewJobsModel(client), ui.NewLogsModel(client),
		ui.NewConflictsModel(client), ui.NewRemotesModel(client), ui.NewNotificationsModel(client), ui.NewConfigModel(client),
	}
	for _, v := range views {
		h, ok := v.(ui.KeyHinter)
		if !ok || h.KeyHints() == "" {
			t.Errorf("%s has no key hints", ui.ViewLabels[v.ViewID()])
		}
	}
}
