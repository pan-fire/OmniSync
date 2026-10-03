package ui_test

import (
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/config"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// tickWait is long enough for a tea.Tick of ui.TickInterval to fire.
const tickWait = ui.TickInterval + 300*time.Millisecond

func countTicks(msgs []tea.Msg) int {
	n := 0
	for _, m := range msgs {
		if _, ok := m.(ui.TickMsg); ok {
			n++
		}
	}
	return n
}

func TestPolling_BackoffSequence(t *testing.T) {
	app, _ := newApp(t, "http://127.0.0.1:1")
	want := []time.Duration{2, 4, 8, 16, 30, 30, 30}
	for i, w := range want {
		app, _ = step(t, app, ui.ConnectionStatusMsg{Connected: false})
		if got := app.Backoff(); got != w*time.Second {
			t.Errorf("failure %d: backoff = %v, want %v", i+1, got, w*time.Second)
		}
	}
	app, _ = step(t, app, ui.ConnectionStatusMsg{Connected: true, Healthy: true})
	if app.Backoff() != 0 {
		t.Errorf("backoff not reset: %v", app.Backoff())
	}
	app, _ = step(t, app, ui.ConnectionStatusMsg{Connected: false})
	if app.Backoff() != 2*time.Second {
		t.Errorf("backoff after reset = %v", app.Backoff())
	}
}

// Exactly one tick loop. Init starts it, each tick re-arms it once,
// and neither connection results nor retries start another.
func TestPolling_SingleTickLoop(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/health", 200, map[string]any{"status": "ok"})
	app, _ := newApp(t, b.srv.URL)

	if n := countTicks(run(app.Init(), tickWait)); n != 1 {
		t.Fatalf("Init started %d tick loops, want 1", n)
	}

	// Startup failure + several manual retries: no new loops.
	app, _ = step(t, app, tea.WindowSizeMsg{Width: 80, Height: 24})
	app, cmd := step(t, app, ui.ConnectionStatusMsg{Connected: false})
	if n := countTicks(run(cmd, tickWait)); n != 0 {
		t.Errorf("a failed health check started %d tick loops", n)
	}
	for i := 0; i < 3; i++ {
		app, cmd = step(t, app, press("r"))
		msgs := run(cmd, tickWait)
		if n := countTicks(msgs); n != 0 {
			t.Errorf("retry %d started %d tick loops", i, n)
		}
		if st, ok := find[ui.ConnectionStatusMsg](msgs); ok {
			app, cmd = step(t, app, st)
			if n := countTicks(run(cmd, tickWait)); n != 0 {
				t.Errorf("reconnect started %d tick loops", n)
			}
		}
	}

	// A tick re-arms exactly one tick.
	_, cmd = step(t, app, ui.TickMsg{Time: time.Now()})
	if n := countTicks(run(cmd, tickWait)); n != 1 {
		t.Errorf("tick re-armed %d ticks, want 1", n)
	}
}

// Health keeps being checked while connected, so a backend that goes
// away is noticed and "Reconnecting..." appears.
func TestPolling_HealthCheckedWhileConnected(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/health", 200, map[string]any{"status": "ok"})
	app, _ := newApp(t, b.srv.URL)
	app = connected(t, app)
	b.reset()

	// Not due yet.
	_, cmd := step(t, app, ui.TickMsg{Time: time.Now()})
	runAll(cmd)
	if n := len(b.matching("GET /health")); n != 0 {
		t.Errorf("health checked %d times before it was due", n)
	}

	// Due: the check runs, and a failure shows "Reconnecting...".
	b.json("GET", "/health", 503, map[string]any{"detail": "down"})
	app, cmd = step(t, app, ui.TickMsg{Time: time.Now().Add(ui.HealthInterval + time.Second)})
	msgs := runAll(cmd)
	if n := len(b.matching("GET /health")); n != 1 {
		t.Fatalf("health checked %d times when due, want 1", n)
	}
	st, ok := find[ui.ConnectionStatusMsg](msgs)
	if !ok || st.Connected {
		t.Fatalf("expected a failed connection status, got %+v", msgs)
	}
	_, cmd = step(t, app, st)
	flash, ok := find[ui.FlashMsg](runAll(cmd))
	if !ok || flash.Text != "Reconnecting..." {
		t.Errorf("expected the Reconnecting flash, got %+v", flash)
	}
}

// Poll intervals come from each view's own data.
func TestPolling_PerViewPollState(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/sync/status/aggregate", 200, map[string]any{"overall_state": "idle"})
	b.json("GET", "/health", 200, map[string]any{"status": "ok"})
	b.json("GET", "/profiles", 200, []any{})
	b.json("GET", "/jobs", 200, []any{})
	client := b.client()
	app := ui.NewApp(client, &config.Config{URL: b.srv.URL})
	app.RegisterView(ui.NewDashboardModel(client))
	app.RegisterView(ui.NewJobsModel(client))
	app = connected(t, app)

	// A busy aggregate for the dashboard must not make the Jobs view poll
	// fast, and a Jobs result must not change the dashboard's interval.
	app, _ = step(t, app, ui.PollResultMsg{ViewID: ui.ViewDashboard, Data: &api.AggregateStatusResponse{OverallState: api.SyncStatePushing}})
	app, _ = step(t, app, ui.PollResultMsg{ViewID: ui.ViewJobs, Data: []api.SyncJobResponse{}})
	app, _ = step(t, app, press("3"))
	b.reset()
	now := time.Now()
	_, cmd := step(t, app, ui.TickMsg{Time: now.Add(ui.FastPollInterval + 100*time.Millisecond)})
	runAll(cmd)
	if n := len(b.matching("GET /jobs")); n != 0 {
		t.Errorf("idle Jobs view polled at the fast interval (%d requests)", n)
	}
}

func TestPolling_DashboardPollSpec(t *testing.T) {
	dm := ui.NewDashboardModel(api.NewClient("http://127.0.0.1:1", "", "t"))
	spec := dm.PollSpec()
	if spec.FastInterval != 2*time.Second || spec.SlowInterval != 30*time.Second {
		t.Errorf("intervals %v/%v", spec.FastInterval, spec.SlowInterval)
	}
	if spec.IsActive(&api.AggregateStatusResponse{OverallState: api.SyncStateIdle}) {
		t.Error("idle is not active")
	}
	if !spec.IsActive(&api.AggregateStatusResponse{OverallState: api.SyncStatePulling}) {
		t.Error("pulling is active")
	}
	if spec.IsActive("not an aggregate") {
		t.Error("unknown data is not active")
	}
}
