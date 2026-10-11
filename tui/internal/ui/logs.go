package ui

import (
	"context"
	"fmt"
	"image/color"
	"strings"

	"charm.land/bubbles/v2/textinput"
	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

type logLevel int

const (
	logLevelAll logLevel = iota
	logLevelDebug
	logLevelInfo
	logLevelWarning
	logLevelError
	logLevelCount
)

var logLevelLabels = []string{"ALL", "DEBUG", "INFO", "WARNING", "ERROR"}

// logCategories are the categories c cycles through: everything, the audit
// trail of user actions, and errors. GET /logs filters by them, so each page
// holds entries of that category however far back they are.
var (
	logCategoryLabels = []string{"all", "audit", "errors"}
	logCategoryParams = []string{"", api.LogCategoryAudit, api.LogCategoryErrors}
)

// logFetchLimit is the most entries GET /logs returns per request; it is
// also the page size for n/N.
const logFetchLimit = 200

// logsPage carries one page of GET /logs, tagged with the skip and category
// it answers.
type logsPage struct {
	Skip     int
	Category int
	Entries  []api.LogEntryResponse
}

// LogsModel is the Logs view. It shows one page of GET /logs (skip/limit);
// n/N move to older/newer pages, the arrow keys scroll inside the page, f
// filters by level, c by category (all, audit trail, errors) and / by
// message text. Live mode (F) re-reads the newest
// page every few seconds and keeps the newest line in view; scrolling up
// or paging to older entries leaves live mode.
type LogsModel struct {
	client *api.Client
	// entries are newest first, as the backend sends them.
	entries  []api.LogEntryResponse
	filtered []api.LogEntryResponse
	level    logLevel
	category int
	autoTail bool
	limit    int
	skip     int
	hasMore  bool
	// offset is how many filtered lines the view is scrolled back from the
	// newest one.
	offset int
	// search filters messages by substring (case-insensitive); searching is
	// true while the / prompt is open, prevSearch restores Esc.
	search      string
	prevSearch  string
	searching   bool
	searchInput textinput.Model
	loading     bool
	err         error
	width       int
	height      int
}

// NewLogsModel creates a new logs view.
func NewLogsModel(client *api.Client) LogsModel {
	ti := textinput.New()
	ti.Prompt = "/"
	ti.CharLimit = 256
	ti.SetWidth(40)
	styles := textinput.DefaultDarkStyles()
	styles.Cursor.Blink = false
	ti.SetStyles(styles)
	return LogsModel{
		client:      client,
		level:       logLevelAll,
		autoTail:    true,
		limit:       logFetchLimit,
		loading:     true,
		searchInput: ti,
	}
}

func (m LogsModel) ViewID() ViewID { return ViewLogs }

// CapturesInput is true while the search prompt is open.
func (m LogsModel) CapturesInput() bool { return m.searching }

// KeyHints names the keys of the current mode for the bottom bar.
func (m LogsModel) KeyHints() string {
	if m.searching {
		return "type to search  Enter:keep  Esc:cancel"
	}
	hints := "Up/Down/PgUp/PgDn:scroll  n/N:older/newer page  /:search  f:level  c:category  F:live  r:refresh"
	if m.search != "" {
		hints += "  Esc:clear search"
	}
	return hints
}

func (m LogsModel) KeyBindings() []components.KeyBinding {
	return []components.KeyBinding{
		{Key: "Up/Down, j/k", Desc: "Scroll one line (up = older; leaves live mode)"},
		{Key: "PgUp/PgDn", Desc: "Scroll one screen"},
		{Key: "Home/End, g/G", Desc: "Oldest / newest line of the page"},
		{Key: "n/N", Desc: "Older/newer page of entries"},
		{Key: "/", Desc: "Search messages (Enter keeps, Esc cancels)"},
		{Key: "Esc", Desc: "Clear the search"},
		{Key: "f", Desc: "Cycle level filter"},
		{Key: "c", Desc: "Cycle category: all, audit trail of user actions, errors"},
		{Key: "F", Desc: "Toggle live updates (jumps to the newest entries)"},
		{Key: "r", Desc: "Refresh"},
	}
}

// PollSpec refreshes live logs every few seconds.
func (m LogsModel) PollSpec() *PollSpec {
	return &PollSpec{
		FastInterval: FastPollInterval,
		SlowInterval: SlowTickInterval,
		IsActive:     func(interface{}) bool { return m.autoTail },
	}
}

func (m LogsModel) Init() tea.Cmd {
	return m.fetchLogs()
}

func (m LogsModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.clampOffset()

	case PollResultMsg:
		return m.handlePollResult(msg)

	case TickMsg:
		if m.autoTail {
			return m, m.fetchLogs()
		}
		return m, nil

	case tea.PasteMsg:
		if m.searching {
			var cmd tea.Cmd
			m.searchInput, cmd = m.searchInput.Update(msg)
			m.setSearch(m.searchInput.Value())
			return m, cmd
		}

	case tea.KeyPressMsg:
		return m.handleKey(msg)
	}
	return m, nil
}

func (m LogsModel) handlePollResult(msg PollResultMsg) (tea.Model, tea.Cmd) {
	page, ok := msg.Data.(logsPage)
	if ok && (page.Skip != m.skip || page.Category != m.category) {
		return m, nil // answer for a page the user already left
	}
	m.loading = false
	if msg.Err != nil {
		m.err = msg.Err
		return m, nil
	}
	if ok {
		m.err = nil
		m.entries = page.Entries
		m.hasMore = len(page.Entries) == m.limit
		m.applyFilter()
	}
	return m, nil
}

func (m LogsModel) handleKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	if m.searching {
		switch msg.String() {
		case "enter":
			m.searching = false
			m.searchInput.Blur()
			return m, nil
		case "esc":
			m.searching = false
			m.searchInput.Blur()
			m.setSearch(m.prevSearch)
			return m, nil
		}
		var cmd tea.Cmd
		m.searchInput, cmd = m.searchInput.Update(msg)
		m.setSearch(m.searchInput.Value())
		return m, cmd
	}

	switch msg.String() {
	case "f":
		m.level = (m.level + 1) % logLevelCount
		m.offset = 0
		m.applyFilter()
		return m, nil
	case "c":
		m.category = (m.category + 1) % len(logCategoryLabels)
		m.skip, m.offset = 0, 0
		m.loading = true
		return m, m.fetchLogs()
	case "F":
		m.autoTail = !m.autoTail
		if m.autoTail {
			// Live mode shows the newest entries.
			m.offset = 0
			if m.skip != 0 {
				m.skip = 0
				m.loading = true
			}
			return m, m.fetchLogs()
		}
		return m, nil
	case "/":
		m.searching = true
		m.prevSearch = m.search
		m.searchInput.SetValue(m.search)
		m.searchInput.CursorEnd()
		return m, m.searchInput.Focus()
	case "esc":
		if m.search != "" {
			m.setSearch("")
		}
		return m, nil
	case "up", "k":
		m.scroll(1)
	case "down", "j":
		m.scroll(-1)
	case "pgup":
		m.scroll(m.pageLines())
	case "pgdown":
		m.scroll(-m.pageLines())
	case "home", "g":
		m.scroll(len(m.filtered))
	case "end", "G":
		m.scroll(-len(m.filtered))
	case "n":
		if m.hasMore {
			m.autoTail = false
			m.skip += m.limit
			m.offset = 0
			m.loading = true
			return m, m.fetchLogs()
		}
		return m, flash("No older log entries", false)
	case "N":
		if m.skip > 0 {
			m.skip -= m.limit
			if m.skip < 0 {
				m.skip = 0
			}
			m.offset = 0
			m.loading = true
			return m, m.fetchLogs()
		}
		return m, flash("Already at the newest entries", false)
	case "r":
		m.loading = true
		return m, m.fetchLogs()
	}
	return m, nil
}

// scroll moves the view by delta lines; positive is towards older lines.
// Leaving the newest line ends live mode, which would otherwise move the
// lines under the reader.
func (m *LogsModel) scroll(delta int) {
	m.offset += delta
	m.clampOffset()
	if m.offset > 0 {
		m.autoTail = false
	}
}

func (m *LogsModel) clampOffset() {
	if last := len(m.filtered) - m.pageLines(); m.offset > last {
		m.offset = last
	}
	if m.offset < 0 {
		m.offset = 0
	}
}

func (m *LogsModel) setSearch(q string) {
	m.search = q
	m.offset = 0
	m.applyFilter()
}

func (m *LogsModel) applyFilter() {
	target := ""
	if m.level != logLevelAll {
		target = logLevelLabels[m.level]
	}
	needle := strings.ToLower(strings.TrimSpace(m.search))
	if target == "" && needle == "" {
		m.filtered = m.entries
		m.clampOffset()
		return
	}
	var result []api.LogEntryResponse
	for _, e := range m.entries {
		if target != "" && !strings.EqualFold(e.Level, target) {
			continue
		}
		if needle != "" && !strings.Contains(strings.ToLower(e.Message), needle) {
			continue
		}
		result = append(result, e)
	}
	m.filtered = result
	m.clampOffset()
}

// pageLines is how many log lines fit on the screen.
func (m LogsModel) pageLines() int {
	lines := m.height - 7
	if lines < 5 {
		lines = 5
	}
	return lines
}

// visibleLines returns the lines in view, oldest first, so the newest line
// is at the bottom like `tail`.
func (m LogsModel) visibleLines() []api.LogEntryResponse {
	start := m.offset
	if start > len(m.filtered) {
		start = len(m.filtered)
	}
	end := start + m.pageLines()
	if end > len(m.filtered) {
		end = len(m.filtered)
	}
	window := m.filtered[start:end]
	out := make([]api.LogEntryResponse, len(window))
	for i, e := range window {
		out[len(window)-1-i] = e
	}
	return out
}

func (m LogsModel) View() tea.View {
	if m.width == 0 {
		return tea.NewView("")
	}

	var b strings.Builder
	labelStyle := lipgloss.NewStyle().Foreground(theme.Current.Muted)

	b.WriteString(headerText("  Logs"))
	if m.autoTail {
		liveStyle := lipgloss.NewStyle().Foreground(theme.Current.Success).Bold(true)
		b.WriteString("  " + liveStyle.Render("LIVE"))
	}
	status := "Filter: " + logLevelLabels[m.level]
	if m.category != 0 {
		status += "  Category: " + logCategoryLabels[m.category]
	}
	if m.search != "" && !m.searching {
		status += fmt.Sprintf("  Search: %q", m.search)
	}
	status += "  " + m.positionLabel()
	b.WriteString("  " + labelStyle.Render(status))
	b.WriteString("\n")
	if m.searching {
		b.WriteString("  " + m.searchInput.View() + "  " + labelStyle.Render("Enter: keep  Esc: cancel"))
	}
	b.WriteString("\n")

	if m.err != nil {
		b.WriteString(errorLine(m.err))
	}

	if m.loading && len(m.entries) == 0 && m.err == nil {
		b.WriteString("  Loading...")
		return tea.NewView(b.String())
	}

	for _, e := range m.visibleLines() {
		// One line per entry: the first line of the message, and how many
		// more the message and its traceback hold (osync logs prints them
		// whole). Server text, so through safeText: a carriage return or
		// an escape sequence in it must not draw over the line.
		message, rest, multiline := strings.Cut(strings.TrimRight(safeText(e.Message), "\n"), "\n")
		extra := 0
		if multiline {
			extra = strings.Count(rest, "\n") + 1
		}
		if e.Exc != "" {
			extra += strings.Count(e.Exc, "\n") + 1
		}
		more := ""
		if extra > 0 {
			more = labelStyle.Render(fmt.Sprintf(" (+%d lines)", extra))
		}
		fmt.Fprintf(&b, "  %s %s %s%s\n",
			labelStyle.Render(formatTime(e.Timestamp)), levelBadge(e.Level), message, more)
	}

	if len(m.filtered) == 0 {
		if m.search != "" {
			fmt.Fprintf(&b, "  No log entries on this page match %q", m.search)
		} else {
			b.WriteString("  No log entries")
		}
	}

	return tea.NewView(b.String())
}

// positionLabel says which page and lines are shown.
func (m LogsModel) positionLabel() string {
	page := m.skip/m.limit + 1
	label := fmt.Sprintf("Page %d", page)
	if page == 1 {
		label += " (newest)"
	}
	if !m.hasMore && len(m.entries) > 0 {
		label += " (oldest)"
	}
	if n := len(m.filtered); n > 0 {
		shown := len(m.visibleLines())
		label += fmt.Sprintf("  lines %d-%d of %d from newest", m.offset+1, m.offset+shown, n)
	}
	return label
}

func levelBadge(level string) string {
	var c color.Color
	switch strings.ToLower(level) {
	case "debug":
		c = theme.Current.Muted
	case "info":
		c = theme.Current.Info
	case "warning":
		c = theme.Current.Warning
	case "error", "critical":
		c = theme.Current.Error
	default:
		c = theme.Current.Foreground
	}
	style := lipgloss.NewStyle().Foreground(c)
	return style.Render("[" + safeLine(strings.ToUpper(level)) + "]")
}

func (m LogsModel) fetchLogs() tea.Cmd {
	client, skip, limit, category := m.client, m.skip, m.limit, m.category
	return func() tea.Msg {
		data, err := client.GetLogsFiltered(context.Background(), skip, limit, "", logCategoryParams[category])
		return PollResultMsg{ViewID: ViewLogs, Data: logsPage{Skip: skip, Category: category, Entries: data}, Err: err}
	}
}
