package ui

import (
	"context"
	"fmt"
	"strings"
	"time"

	"charm.land/bubbles/v2/textinput"
	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/config"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

// TickInterval is the period of the app's single timer loop.
const TickInterval = 1 * time.Second

// FastPollInterval is the view polling interval while a sync is running.
const FastPollInterval = 2 * time.Second

// SlowTickInterval is the idle polling interval.
const SlowTickInterval = 30 * time.Second

// HealthInterval is how often the backend health is re-checked while
// connected.
const HealthInterval = 10 * time.Second

// HealthTimeout bounds a single health check.
const HealthTimeout = 5 * time.Second

// MaxBackoff is the maximum reconnection backoff.
const MaxBackoff = 30 * time.Second

// View is the interface that all view models implement.
type View interface {
	tea.Model
	ViewID() ViewID
	KeyBindings() []components.KeyBinding
}

// Poller is an optional interface for views that support adaptive polling.
type Poller interface {
	PollSpec() *PollSpec
}

// App is the root Bubble Tea model.
//
// Timing: Init starts one tick loop (TickMsg every TickInterval) that runs
// for the life of the program. Every tick re-schedules itself exactly once;
// nothing else schedules ticks, so retries and reconnects never add loops.
// From the tick the app re-checks health (every HealthInterval while
// connected, with exponential backoff while not) and polls the active view
// when its interval is due.
type App struct {
	// Navigation
	activeView ViewID
	views      map[ViewID]View

	// Shared state
	apiClient *api.Client
	config    *config.Config

	// Startup
	initializing bool
	startupError error
	editingURL   bool
	urlInput     textinput.Model

	// Top bar
	connected    bool
	healthy      bool
	latencyMs    int64
	overallState api.SyncState
	now          time.Time

	// Bottom bar
	flash components.Flash

	// Dimensions
	width  int
	height int

	// Overlays
	showHelp    bool
	confirmQuit bool

	// Health checking
	checking        bool
	backoff         time.Duration
	nextHealth      time.Time
	wasDisconnected bool

	// Per-view poll state
	pollData map[ViewID]interface{}
	nextPoll map[ViewID]time.Time
}

// NewApp creates a new App model.
func NewApp(client *api.Client, cfg *config.Config) App {
	ti := textinput.New()
	ti.Placeholder = "http://127.0.0.1:8000"
	ti.CharLimit = 256
	ti.Prompt = ""
	ti.SetWidth(50)

	return App{
		activeView:   ViewDashboard,
		views:        make(map[ViewID]View),
		apiClient:    client,
		config:       cfg,
		initializing: true,
		urlInput:     ti,
		pollData:     make(map[ViewID]interface{}),
		nextPoll:     make(map[ViewID]time.Time),
	}
}

// RegisterView adds a view to the app.
func (a *App) RegisterView(v View) {
	a.views[v.ViewID()] = v
}

// Init performs the initial health check and starts the tick loop.
func (a App) Init() tea.Cmd {
	cmds := []tea.Cmd{a.checkHealth(), scheduleTick()}
	// Startup warnings (also printed to stderr, which the TUI's screen hides).
	if a.config != nil && len(a.config.Warnings) > 0 {
		text := "Warning: " + strings.Join(a.config.Warnings, "; ")
		cmds = append(cmds, func() tea.Msg { return FlashMsg{Text: text, IsError: true} })
	}
	return tea.Batch(cmds...)
}

func (a *App) checkHealth() tea.Cmd {
	client := a.apiClient
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), HealthTimeout)
		defer cancel()
		start := time.Now()
		h, err := client.Health(ctx)
		latency := time.Since(start).Milliseconds()
		if err != nil {
			return ConnectionStatusMsg{Connected: false, LatencyMs: latency, Err: err}
		}
		msg := ConnectionStatusMsg{Connected: true, LatencyMs: latency, Healthy: h.Healthy()}
		// The top bar's state badge; unknown if this call fails.
		if agg, err := client.AggregateStatus(ctx); err == nil && agg != nil {
			msg.OverallState = agg.OverallState
		}
		return msg
	}
}

func scheduleTick() tea.Cmd {
	return tea.Tick(TickInterval, func(t time.Time) tea.Msg {
		return TickMsg{Time: t}
	})
}

// Update handles messages.
func (a App) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	var cmds []tea.Cmd

	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		a.width = msg.Width
		a.height = msg.Height
		cmds = append(cmds, a.updateView(a.activeView, msg))

	case ConnectionStatusMsg:
		cmds = append(cmds, a.handleConnection(msg))

	case TickMsg:
		// The one place that schedules the next tick.
		cmds = append(cmds, scheduleTick())
		a.now = msg.Time
		if !a.checking && !msg.Time.Before(a.nextHealth) {
			a.checking = true
			cmds = append(cmds, a.checkHealth())
		}
		if a.connected && a.startupError == nil {
			if due := a.nextPoll[a.activeView]; !msg.Time.Before(due) {
				a.nextPoll[a.activeView] = msg.Time.Add(a.pollInterval(a.activeView))
				cmds = append(cmds, a.updateView(a.activeView, msg))
			}
		}

	case FlashMsg:
		flash, cmd := components.NewFlash(msg.Text, msg.IsError)
		a.flash = flash
		cmds = append(cmds, cmd)

	case components.FlashExpiredMsg:
		if !a.flash.Expiry.After(time.Now()) {
			a.flash.Clear()
		}

	case NavigateMsg:
		if msg.Param != "" {
			a.updateView(msg.Target, OpenProfileMsg{Slug: msg.Param})
		}
		if msg.Payload != nil {
			cmds = append(cmds, a.updateView(msg.Target, msg.Payload))
		}
		cmds = append(cmds, a.switchTo(msg.Target))

	case PollResultMsg:
		if msg.Err == nil && msg.Data != nil {
			a.pollData[msg.ViewID] = msg.Data
		}
		cmds = append(cmds, a.updateView(msg.ViewID, msg))

	case ActionResultMsg:
		cmds = append(cmds, a.updateView(msg.ViewID, msg))

	case tea.KeyPressMsg:
		cmds = append(cmds, a.handleKey(msg))

	case tea.MouseClickMsg:
		cmds = append(cmds, a.handleClick(msg.Mouse()))

	case tea.MouseWheelMsg:
		cmds = append(cmds, a.handleWheel(msg.Mouse()))

	default:
		// Spinner ticks, form/confirm results, paste events and the like
		// belong to the view the user is working in.
		cmds = append(cmds, a.updateView(a.activeView, msg))
	}

	return a, tea.Batch(cmds...)
}

// updateView delivers msg to one view and stores the updated model.
func (a *App) updateView(id ViewID, msg tea.Msg) tea.Cmd {
	v, ok := a.views[id]
	if !ok {
		return nil
	}
	updated, cmd := v.Update(msg)
	if uv, ok := updated.(View); ok {
		a.views[id] = uv
	}
	return cmd
}

func (a *App) handleConnection(msg ConnectionStatusMsg) tea.Cmd {
	var cmds []tea.Cmd
	a.checking = false
	a.latencyMs = msg.LatencyMs
	now := time.Now()

	if msg.Connected {
		a.overallState = msg.OverallState
		wasConnected := a.connected && a.startupError == nil && !a.initializing
		a.connected = true
		a.healthy = msg.Healthy
		a.backoff = 0
		a.nextHealth = now.Add(HealthInterval)
		if !wasConnected {
			a.initializing = false
			a.startupError = nil
			a.editingURL = false
			if a.wasDisconnected {
				a.wasDisconnected = false
				cmds = append(cmds, flash("Reconnected", false))
			}
			cmds = append(cmds, a.initView(a.activeView))
		}
		return tea.Batch(cmds...)
	}

	a.connected = false
	a.healthy = false
	a.overallState = ""
	if a.initializing {
		a.startupError = fmt.Errorf("cannot reach backend at %s", a.config.URL)
		if msg.Err != nil {
			a.startupError = fmt.Errorf("cannot reach backend at %s: %w", a.config.URL, msg.Err)
		}
	} else if !a.wasDisconnected {
		a.wasDisconnected = true
		cmds = append(cmds, flash("Reconnecting...", true))
	}
	a.backoff = a.nextBackoff()
	a.nextHealth = now.Add(a.backoff)
	return tea.Batch(cmds...)
}

// initView (re)loads a view and restarts its poll timer.
func (a *App) initView(id ViewID) tea.Cmd {
	v, ok := a.views[id]
	if !ok {
		return nil
	}
	a.nextPoll[id] = time.Now().Add(a.pollInterval(id))
	return v.Init()
}

func (a *App) switchTo(target ViewID) tea.Cmd {
	a.navigate(target)
	return a.initView(target)
}

func (a *App) toggleTheme() tea.Cmd {
	name := theme.Toggle()
	if a.config != nil {
		a.config.Theme = name
	}
	return func() tea.Msg {
		if err := config.SaveTheme(name); err != nil {
			return FlashMsg{Text: "Theme not saved: " + err.Error(), IsError: true}
		}
		return nil
	}
}

func (a *App) activeCaptures() bool {
	if v, ok := a.views[a.activeView]; ok {
		if c, ok := v.(InputCapturer); ok {
			return c.CapturesInput()
		}
	}
	return false
}

func (a *App) handleKey(msg tea.KeyPressMsg) tea.Cmd {
	key := msg.String()

	if key == "ctrl+c" {
		return tea.Quit
	}

	// Startup error screen
	if a.startupError != nil {
		return a.handleStartupKey(msg)
	}

	if a.confirmQuit {
		switch key {
		case "y", "Y":
			return tea.Quit
		default:
			a.confirmQuit = false
			return nil
		}
	}

	if a.showHelp {
		a.showHelp = false
		return nil
	}

	// A form, text field or prompt owns the keyboard.
	if a.activeCaptures() {
		return a.updateView(a.activeView, msg)
	}

	switch key {
	case "q":
		a.confirmQuit = true
		return nil
	case "?":
		a.showHelp = true
		return nil
	case "ctrl+t":
		return a.toggleTheme()
	case "1", "2", "3", "4", "5", "6", "7", "8":
		target := ViewID(int(key[0] - '1'))
		if target != a.activeView {
			return a.switchTo(target)
		}
		return nil
	case "tab":
		return a.switchTo(ViewID((int(a.mainView()) + 1) % ViewCount))
	case "shift+tab":
		return a.switchTo(ViewID((int(a.mainView()) - 1 + ViewCount) % ViewCount))
	}

	return a.updateView(a.activeView, msg)
}

// tabRow is the screen row of the view tabs (the second top-bar line).
const tabRow = 1

// tabSpans returns the screen columns [start, end) of each tab label on the
// tab row, in view order.
func tabSpans() [][2]int {
	spans := make([][2]int, 0, ViewCount)
	x := 0
	for i := 0; i < ViewCount; i++ {
		w := lipgloss.Width(tabLabel(ViewID(i)))
		spans = append(spans, [2]int{x, x + w})
		x += w + 1
	}
	return spans
}

func tabLabel(id ViewID) string {
	return fmt.Sprintf("%d:%s", int(id)+1, ViewLabels[id])
}

// clickable reports whether the main screen takes mouse navigation now: not
// on the startup, help or quit screens, and not while a view owns the
// keyboard (a click must not throw away a half-filled form).
func (a *App) clickable() bool {
	return a.startupError == nil && !a.showHelp && !a.confirmQuit && !a.activeCaptures()
}

// handleClick switches to the view whose tab was clicked.
func (a *App) handleClick(m tea.Mouse) tea.Cmd {
	if m.Button != tea.MouseLeft || m.Y != tabRow || !a.clickable() {
		return nil
	}
	for i, span := range tabSpans() {
		if m.X >= span[0] && m.X < span[1] {
			if target := ViewID(i); target != a.activeView {
				return a.switchTo(target)
			}
			return nil
		}
	}
	return nil
}

// handleWheel scrolls the active view like the Up/Down keys.
func (a *App) handleWheel(m tea.Mouse) tea.Cmd {
	if !a.clickable() {
		return nil
	}
	switch m.Button {
	case tea.MouseWheelUp:
		return a.updateView(a.activeView, tea.KeyPressMsg(tea.Key{Code: tea.KeyUp}))
	case tea.MouseWheelDown:
		return a.updateView(a.activeView, tea.KeyPressMsg(tea.Key{Code: tea.KeyDown}))
	}
	return nil
}

// mainView maps sub-views to the main view they belong to, for Tab cycling.
func (a *App) mainView() ViewID {
	switch a.activeView {
	case ViewProfileDetail:
		return ViewProfiles
	case ViewWizard:
		return ViewRemotes
	}
	return a.activeView
}

func (a *App) handleStartupKey(msg tea.KeyPressMsg) tea.Cmd {
	key := msg.String()

	if a.editingURL {
		switch key {
		case "enter":
			a.config.URL = strings.TrimSpace(a.urlInput.Value())
			a.apiClient.SetBaseURL(a.config.URL)
			a.editingURL = false
			a.urlInput.Blur()
			return a.retryHealth()
		case "esc":
			a.editingURL = false
			a.urlInput.Blur()
			return nil
		default:
			var cmd tea.Cmd
			a.urlInput, cmd = a.urlInput.Update(msg)
			return cmd
		}
	}

	switch key {
	case "r":
		return a.retryHealth()
	case "e":
		a.editingURL = true
		a.urlInput.SetValue(a.config.URL)
		a.urlInput.CursorEnd()
		return a.urlInput.Focus()
	case "q":
		return tea.Quit
	}
	return nil
}

// retryHealth runs one health check now, unless one is already running. It
// does not start another tick loop.
func (a *App) retryHealth() tea.Cmd {
	if a.checking {
		return nil
	}
	a.checking = true
	return a.checkHealth()
}

func (a *App) navigate(target ViewID) {
	a.activeView = target
	// Forward current dimensions so the view can render immediately
	// without waiting for a terminal resize event.
	if a.width > 0 {
		a.updateView(target, tea.WindowSizeMsg{Width: a.width, Height: a.height})
	}
}

func (a *App) nextBackoff() time.Duration {
	if a.backoff == 0 {
		return 2 * time.Second
	}
	next := a.backoff * 2
	if next > MaxBackoff {
		return MaxBackoff
	}
	return next
}

// Backoff returns the current reconnect backoff (0 while connected).
func (a App) Backoff() time.Duration {
	return a.backoff
}

// ActiveView returns the view currently shown.
func (a App) ActiveView() ViewID {
	return a.activeView
}

// pollInterval returns the polling interval for a view, based on that
// view's own last poll data.
func (a *App) pollInterval(id ViewID) time.Duration {
	if v, ok := a.views[id]; ok {
		if p, ok := v.(Poller); ok {
			if spec := p.PollSpec(); spec != nil {
				if spec.IsActive != nil && spec.IsActive(a.pollData[id]) {
					return spec.FastInterval
				}
				return spec.SlowInterval
			}
		}
	}
	return SlowTickInterval
}

// View renders the app. Every frame, including the startup, help and quit
// screens, uses the alternate screen, so switching between them does not
// flicker or leave output in the scrollback.
func (a App) View() tea.View {
	v := tea.NewView(a.render())
	v.AltScreen = true
	// Clicks on the tabs switch views; the wheel scrolls. Most terminals
	// (also inside tmux or over SSH) still select text with Shift+drag.
	// no_mouse / OMNISYNC_NO_MOUSE / --no-mouse leave the mouse to the
	// terminal.
	if a.config == nil || !a.config.NoMouse {
		v.MouseMode = tea.MouseModeCellMotion
	}
	return v
}

func (a App) render() string {
	if a.width == 0 {
		return ""
	}

	if a.startupError != nil {
		return a.renderStartupError()
	}

	if a.showHelp {
		var bindings []components.KeyBinding
		bindings = append(bindings, components.GlobalBindings()...)
		if v, ok := a.views[a.activeView]; ok {
			bindings = append(bindings, v.KeyBindings()...)
		}
		return components.RenderHelp("Help: "+ViewLabels[a.activeView], bindings, a.width, a.height)
	}

	if a.confirmQuit {
		return a.renderConfirmQuit()
	}

	var b strings.Builder
	b.WriteString(a.renderTopBar())
	b.WriteString("\n")

	viewHeight := a.height - 4 // two top-bar lines + bottom bar + margin
	if v, ok := a.views[a.activeView]; ok {
		content := v.View().Content
		b.WriteString(content)
		lines := strings.Count(content, "\n") + 1
		for i := lines; i < viewHeight; i++ {
			b.WriteString("\n")
		}
	} else {
		for i := 0; i < viewHeight; i++ {
			b.WriteString("\n")
		}
	}

	b.WriteString(a.renderBottomBar())
	return b.String()
}

// renderTopBar draws two lines: the app name, backend URL, aggregate sync
// state and clock, then the numbered view tabs (clickable).
func (a App) renderTopBar() string {
	titleStyle := lipgloss.NewStyle().Bold(true).Foreground(theme.Current.Primary)
	muted := lipgloss.NewStyle().Foreground(theme.Current.Muted)

	url := ""
	if a.config != nil {
		url = a.config.URL
	}
	state := "unknown"
	if a.overallState != "" {
		state = string(a.overallState)
	}
	now := a.now
	if now.IsZero() {
		now = time.Now()
	}
	clock := now.Format("15:04:05")
	// The URL gives way first on a narrow terminal.
	room := a.width - lipgloss.Width("OmniSync  ") - lipgloss.Width("  ["+state+"]  ") - len(clock)
	line1 := titleStyle.Render("OmniSync") + "  " + muted.Render(components.Truncate(url, room)) +
		"  " + components.RenderBadge(state) + "  " + muted.Render(clock)

	var tabs []string
	for i := 0; i < ViewCount; i++ {
		vid := ViewID(i)
		label := tabLabel(vid)
		if vid == a.mainView() {
			tabs = append(tabs, lipgloss.NewStyle().Bold(true).Underline(true).Foreground(theme.Current.Primary).Render(label))
		} else {
			tabs = append(tabs, muted.Render(label))
		}
	}
	return line1 + "\n" + strings.Join(tabs, " ")
}

// renderConnection is the latency indicator at the start of the bottom bar.
func (a App) renderConnection() string {
	g := theme.Glyphs()
	style := lipgloss.NewStyle()
	switch {
	case a.connected && a.healthy:
		return style.Foreground(theme.Current.Success).Render(fmt.Sprintf("%s %dms", g.Connected, a.latencyMs))
	case a.connected:
		return style.Foreground(theme.Current.Warning).Render(fmt.Sprintf("%s degraded %dms", g.Connected, a.latencyMs))
	}
	return style.Foreground(theme.Current.Error).Render(fmt.Sprintf("%s reconnecting", g.Disconnect))
}

// globalHints are the keys that work in every view.
const globalHints = "?:help  q:quit  1-8/Tab:views  Ctrl+T:theme"

// renderBottomBar shows the connection, then a flash message or the active
// view's keys followed by the global ones.
func (a App) renderBottomBar() string {
	conn := a.renderConnection()
	room := a.width - lipgloss.Width(conn) - 2
	if flashView := a.flash.View(); flashView != "" {
		return conn + "  " + flashView
	}
	hints := ""
	if v, ok := a.views[a.activeView].(KeyHinter); ok {
		hints = v.KeyHints()
	}
	switch {
	case a.activeCaptures() && hints == "":
		hints = "Esc:cancel  Enter:confirm  Ctrl+C:quit"
	case a.activeCaptures():
		hints += "  Ctrl+C:quit"
	case hints == "":
		hints = globalHints
	default:
		hints += "  |  " + globalHints
	}
	style := lipgloss.NewStyle().Foreground(theme.Current.Muted)
	return conn + "  " + style.Render(components.Truncate(hints, room))
}

func (a App) renderStartupError() string {
	errStyle := lipgloss.NewStyle().Foreground(theme.Current.Error).Bold(true)
	infoStyle := lipgloss.NewStyle().Foreground(theme.Current.Foreground)
	mutedStyle := lipgloss.NewStyle().Foreground(theme.Current.Muted)

	var b strings.Builder
	b.WriteString("\n\n")
	b.WriteString(errStyle.Render("  Connection Error"))
	b.WriteString("\n\n")
	b.WriteString(infoStyle.Render(fmt.Sprintf("  Cannot reach backend at: %s", a.config.URL)))
	b.WriteString("\n")
	if a.startupError != nil {
		b.WriteString(mutedStyle.Render(fmt.Sprintf("  %s", a.startupError.Error())))
	}
	b.WriteString("\n\n")

	switch {
	case a.editingURL:
		b.WriteString(infoStyle.Render("  URL: "))
		b.WriteString(a.urlInput.View())
		b.WriteString("\n")
		b.WriteString(mutedStyle.Render("  Enter: confirm  Esc: cancel"))
	case a.checking:
		b.WriteString(mutedStyle.Render("  Checking..."))
	default:
		b.WriteString(mutedStyle.Render(fmt.Sprintf("  Retrying automatically every %s.  r: retry now  e: edit URL  q: quit", a.backoff)))
	}

	return b.String()
}

func (a App) renderConfirmQuit() string {
	style := lipgloss.NewStyle().Foreground(theme.Current.Warning).Bold(true)
	return "\n\n" + style.Render("  Quit OmniSync? (y/n)")
}
