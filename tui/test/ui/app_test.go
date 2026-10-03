package ui_test

import (
	"regexp"
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/config"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

var ansiRE = regexp.MustCompile(`\x1b\[[0-9;]*m`)

func stripANSI(s string) string {
	return ansiRE.ReplaceAllString(s, "")
}

// stubView records what it receives. It uses a pointer receiver on purpose:
// the app must work with both pointer and value views.
type stubView struct {
	id       ui.ViewID
	capture  bool
	keys     []string
	msgs     []tea.Msg
	initd    int
	openSlug string
}

func newStubView(id ui.ViewID) *stubView { return &stubView{id: id} }

func (s *stubView) Init() tea.Cmd {
	s.initd++
	return nil
}

func (s *stubView) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	s.msgs = append(s.msgs, msg)
	switch m := msg.(type) {
	case tea.KeyPressMsg:
		s.keys = append(s.keys, m.String())
	case ui.OpenProfileMsg:
		s.openSlug = m.Slug
	}
	return s, nil
}

func (s *stubView) View() tea.View                        { return tea.NewView("stub:" + ui.ViewLabels[s.id]) }
func (s *stubView) ViewID() ui.ViewID                     { return s.id }
func (s *stubView) KeyBindings() []components.KeyBinding  { return nil }
func (s *stubView) CapturesInput() bool                   { return s.capture }
func (s *stubView) PollSpec() *ui.PollSpec                { return nil }
func (s *stubView) received(pred func(tea.Msg) bool) bool { return anyMsg(s.msgs, pred) }

func anyMsg(msgs []tea.Msg, pred func(tea.Msg) bool) bool {
	for _, m := range msgs {
		if pred(m) {
			return true
		}
	}
	return false
}

func newApp(t *testing.T, url string) (ui.App, map[ui.ViewID]*stubView) {
	t.Helper()
	client := api.NewClient(url, "", "test")
	app := ui.NewApp(client, &config.Config{URL: url})
	stubs := map[ui.ViewID]*stubView{}
	for i := 0; i < ui.ViewCount; i++ {
		s := newStubView(ui.ViewID(i))
		stubs[s.id] = s
		app.RegisterView(s)
	}
	return app, stubs
}

func step(t *testing.T, app ui.App, msg tea.Msg) (ui.App, tea.Cmd) {
	t.Helper()
	m, cmd := app.Update(msg)
	return m.(ui.App), cmd
}

func connected(t *testing.T, app ui.App) ui.App {
	t.Helper()
	app, _ = step(t, app, tea.WindowSizeMsg{Width: 100, Height: 30})
	app, _ = step(t, app, ui.ConnectionStatusMsg{Connected: true, Healthy: true, LatencyMs: 5})
	return app
}

func TestApp_NumberKeySwitchesView(t *testing.T) {
	app, _ := newApp(t, "http://127.0.0.1:1")
	app = connected(t, app)
	app, _ = step(t, app, press("2"))
	if !strings.Contains(app.View().Content, "stub:Profiles") {
		t.Errorf("expected Profiles, got %q", app.View().Content)
	}
}

func TestApp_TabCycles(t *testing.T) {
	app, _ := newApp(t, "http://127.0.0.1:1")
	app = connected(t, app)
	app, _ = step(t, app, press("tab"))
	if app.ActiveView() != ui.ViewProfiles {
		t.Errorf("Tab: active = %v", app.ActiveView())
	}
	app, _ = step(t, app, press("shift+tab"))
	app, _ = step(t, app, press("shift+tab"))
	if app.ActiveView() != ui.ViewConfig {
		t.Errorf("Shift+Tab: active = %v", app.ActiveView())
	}
}

func TestApp_HelpAndQuit(t *testing.T) {
	app, _ := newApp(t, "http://127.0.0.1:1")
	app = connected(t, app)
	app, _ = step(t, app, press("?"))
	if !strings.Contains(app.View().Content, "Help") {
		t.Fatal("expected help")
	}
	app, _ = step(t, app, press("esc"))
	if strings.Contains(app.View().Content, "Help") {
		t.Fatal("help not dismissed")
	}
	app, _ = step(t, app, press("q"))
	if !strings.Contains(app.View().Content, "Quit OmniSync") {
		t.Fatal("expected quit prompt")
	}
	app, _ = step(t, app, press("n"))
	if strings.Contains(app.View().Content, "Quit OmniSync") {
		t.Fatal("quit prompt not dismissed")
	}
}

// TUI-4: while a view captures input, q ? 1-8 Tab and Ctrl+T go to it.
func TestApp_CapturingViewGetsGlobalKeys(t *testing.T) {
	app, stubs := newApp(t, "http://127.0.0.1:1")
	app = connected(t, app)
	stubs[ui.ViewDashboard].capture = true

	for _, k := range []string{"q", "?", "1", "5", "tab", "shift+tab", "ctrl+t", "space", "ä"} {
		app, _ = step(t, app, press(k))
	}
	if app.ActiveView() != ui.ViewDashboard {
		t.Errorf("view switched to %v while capturing", app.ActiveView())
	}
	if strings.Contains(app.View().Content, "Quit OmniSync") || strings.Contains(app.View().Content, "Help") {
		t.Error("global overlay opened while capturing")
	}
	got := strings.Join(stubs[ui.ViewDashboard].keys, ",")
	if got != "q,?,1,5,tab,shift+tab,ctrl+t,space,ä" {
		t.Errorf("view received %q", got)
	}

	// Ctrl+C always quits.
	_, cmd := step(t, app, press("ctrl+c"))
	if _, ok := find[tea.QuitMsg](runAll(cmd)); !ok {
		t.Error("Ctrl+C did not quit while capturing")
	}
}

// TUI-10: every frame uses the alternate screen.
func TestApp_AltScreenOnEveryFrame(t *testing.T) {
	app, _ := newApp(t, "http://127.0.0.1:1")
	if !app.View().AltScreen {
		t.Error("zero-size frame without AltScreen")
	}
	app, _ = step(t, app, tea.WindowSizeMsg{Width: 80, Height: 24})
	app, _ = step(t, app, ui.ConnectionStatusMsg{Connected: false})
	if v := app.View(); !v.AltScreen || !strings.Contains(v.Content, "Connection Error") {
		t.Error("startup error frame without AltScreen")
	}
	app, _ = step(t, app, ui.ConnectionStatusMsg{Connected: true, Healthy: true})
	app, _ = step(t, app, press("?"))
	if !app.View().AltScreen {
		t.Error("help frame without AltScreen")
	}
	app, _ = step(t, app, press("esc"))
	app, _ = step(t, app, press("q"))
	if !app.View().AltScreen {
		t.Error("quit frame without AltScreen")
	}
}

// TUI-6: opening a profile works for every profile, with a value-receiver
// Profile Detail view as main registers it.
func TestApp_NavigateOpensEveryProfile(t *testing.T) {
	b := newBackend(t)
	client := b.client()
	app := ui.NewApp(client, &config.Config{URL: b.srv.URL})
	app.RegisterView(ui.NewProfilesModel(client))
	app.RegisterView(ui.NewProfileDetailModel(client))
	app, _ = step(t, app, tea.WindowSizeMsg{Width: 100, Height: 30})
	app, _ = step(t, app, ui.ConnectionStatusMsg{Connected: true, Healthy: true})

	for _, slug := range []string{"alpha", "beta", "gamma"} {
		var cmd tea.Cmd
		app, cmd = step(t, app, ui.NavigateMsg{Target: ui.ViewProfileDetail, Param: slug})
		b.reset()
		runAll(cmd)
		if app.ActiveView() != ui.ViewProfileDetail {
			t.Fatalf("not on detail view")
		}
		if !strings.Contains(app.View().Content, slug) {
			t.Errorf("detail shows %q, want %s", app.View().Content, slug)
		}
		if reqs := b.matching("GET /profiles/" + slug); len(reqs) == 0 {
			t.Errorf("opening %s fetched %v", slug, b.log())
		}
		app, _ = step(t, app, press("esc"))
	}
}

func TestApp_NavigateParamDeliveredToPointerView(t *testing.T) {
	app, _ := newApp(t, "http://127.0.0.1:1")
	detail := newStubView(ui.ViewProfileDetail)
	app.RegisterView(detail)
	app = connected(t, app)
	app, _ = step(t, app, ui.NavigateMsg{Target: ui.ViewProfileDetail, Param: "one"})
	app, _ = step(t, app, ui.NavigateMsg{Target: ui.ViewProfileDetail, Param: "two"})
	if detail.openSlug != "two" || app.ActiveView() != ui.ViewProfileDetail {
		t.Errorf("slug = %q, active = %v", detail.openSlug, app.ActiveView())
	}
}

// Results go to the view that asked, even when it is no longer active.
func TestApp_ResultsRoutedByViewID(t *testing.T) {
	app, stubs := newApp(t, "http://127.0.0.1:1")
	app = connected(t, app)
	app, _ = step(t, app, press("3"))
	app, _ = step(t, app, ui.ActionResultMsg{ViewID: ui.ViewDashboard, Action: "sync_all"})
	app, _ = step(t, app, ui.PollResultMsg{ViewID: ui.ViewRemotes, Data: []api.RemoteResponse{}})
	_ = app
	if !stubs[ui.ViewDashboard].received(func(m tea.Msg) bool { _, ok := m.(ui.ActionResultMsg); return ok }) {
		t.Error("action result not delivered to the dashboard")
	}
	if stubs[ui.ViewJobs].received(func(m tea.Msg) bool { _, ok := m.(ui.ActionResultMsg); return ok }) {
		t.Error("action result delivered to the active view instead")
	}
	if !stubs[ui.ViewRemotes].received(func(m tea.Msg) bool { _, ok := m.(ui.PollResultMsg); return ok }) {
		t.Error("poll result not delivered to its view")
	}
}

func TestApp_StartupErrorScreen(t *testing.T) {
	app, _ := newApp(t, "http://127.0.0.1:1")
	app, _ = step(t, app, tea.WindowSizeMsg{Width: 80, Height: 24})
	app, _ = step(t, app, ui.ConnectionStatusMsg{Connected: false})
	if !strings.Contains(app.View().Content, "Connection Error") {
		t.Fatal("expected the connection error screen")
	}
	// Editing the URL takes ordinary text, including 'q'.
	app, _ = step(t, app, press("e"))
	for _, k := range typeKeys("q") {
		app, _ = step(t, app, k)
	}
	if !strings.Contains(app.View().Content, "Connection Error") {
		t.Error("'q' while editing the URL quit or left the screen")
	}
}

func TestApp_TopBarShowsReconnecting(t *testing.T) {
	app, _ := newApp(t, "http://127.0.0.1:1")
	app = connected(t, app)
	app, _ = step(t, app, ui.ConnectionStatusMsg{Connected: false})
	if !strings.Contains(stripANSI(app.View().Content), "reconnecting") {
		t.Error("expected the reconnecting indicator")
	}
	app, _ = step(t, app, ui.ConnectionStatusMsg{Connected: true, Healthy: false, LatencyMs: 3})
	if !strings.Contains(stripANSI(app.View().Content), "degraded") {
		t.Error("expected the degraded indicator")
	}
}
