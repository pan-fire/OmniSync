package ui_test

import (
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// logsMixedLevels returns one entry per level, newest first.
func logsMixedLevels() []any {
	out := []any{}
	for _, l := range []string{"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "TRACE"} {
		out = append(out, map[string]any{"timestamp": "2026-09-27T08:00:00Z", "level": l, "message": "msg-" + strings.ToLower(l)})
	}
	return out
}

// f cycles the level filter on the page already loaded: it asks the
// backend nothing, shows only that level, and comes back to ALL.
func TestLogsView_LevelFilterCycles(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/logs", 200, logsMixedLevels())
	m := open(t, ui.NewLogsModel(b.client()))
	if v := content(m); !strings.Contains(v, "[CRITICAL] msg-critical") || !strings.Contains(v, "[TRACE] msg-trace") {
		t.Fatalf("all levels not shown:\n%s", v)
	}
	b.reset()
	for _, level := range []string{"DEBUG", "INFO", "WARNING", "ERROR"} {
		m = drive(t, m, press("f"))
		v := content(m)
		if !strings.Contains(v, "Filter: "+level) || !strings.Contains(v, "msg-"+strings.ToLower(level)) {
			t.Errorf("%s: view:\n%s", level, v)
		}
		if n := strings.Count(v, "msg-"); n != 1 {
			t.Errorf("%s: %d lines shown:\n%s", level, n, v)
		}
	}
	m = drive(t, m, press("f"))
	if v := content(m); !strings.Contains(v, "Filter: ALL") || strings.Count(v, "msg-") != 6 {
		t.Errorf("back to ALL:\n%s", v)
	}
	if len(b.log()) != 0 {
		t.Errorf("level filter asked the backend: %v", b.log())
	}
}

// A level with no entry on the page says so instead of showing nothing.
func TestLogsView_EmptyStates(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/logs", 200, logEntries(5, 5, "INFO"))
	m := open(t, ui.NewLogsModel(b.client()))
	m = drive(t, m, press("f")) // DEBUG
	if v := content(m); !strings.Contains(v, "No log entries") || strings.Contains(v, "match") {
		t.Errorf("filtered empty state:\n%s", v)
	}
	b.json("GET", "/logs", 200, []any{})
	m = drive(t, m, press("r"))
	if v := content(m); !strings.Contains(v, "No log entries") || strings.Contains(v, "lines ") {
		t.Errorf("empty page:\n%s", v)
	}
}

// PgUp/PgDn move one screen, End/G return to the newest line.
func TestLogsView_PagingKeys(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/logs", 200, logEntries(200, 200, "INFO"))
	m := open(t, ui.NewLogsModel(b.client())) // 40 rows: 33 lines per screen
	m = drive(t, m, tea.KeyPressMsg(tea.Key{Code: tea.KeyPgUp}))
	v := content(m)
	if !strings.Contains(v, "lines 34-66 of 200") || strings.Contains(v, "line-200") || strings.Contains(v, "LIVE") {
		t.Errorf("PgUp:\n%s", v)
	}
	m = drive(t, m, tea.KeyPressMsg(tea.Key{Code: tea.KeyPgUp}), tea.KeyPressMsg(tea.Key{Code: tea.KeyPgDown}))
	if v := content(m); !strings.Contains(v, "lines 34-66 of 200") {
		t.Errorf("PgDn:\n%s", v)
	}
	m = drive(t, m, press("G"))
	if v := content(m); !strings.Contains(v, "lines 1-33 of 200") || !strings.Contains(v, "line-200") {
		t.Errorf("G:\n%s", v)
	}
	m = drive(t, m, tea.KeyPressMsg(tea.Key{Code: tea.KeyHome}))
	if v := content(m); !strings.Contains(v, "lines 168-200 of 200") || !strings.Contains(v, "line-001") {
		t.Errorf("Home:\n%s", v)
	}
	m = drive(t, m, tea.KeyPressMsg(tea.Key{Code: tea.KeyEnd}))
	if !strings.Contains(content(m), "line-200") {
		t.Errorf("End:\n%s", content(m))
	}
	// A taller window shows more lines and keeps the position valid.
	m = drive(t, m, press("g"), tea.WindowSizeMsg{Width: 140, Height: 60})
	if v := content(m); !strings.Contains(v, "lines 148-200 of 200") {
		t.Errorf("resize:\n%s", v)
	}
}

// At either end n and N explain why nothing happens and send nothing.
func TestLogsView_PagingAtTheEndsExplains(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/logs", 200, logEntries(10, 10, "INFO"))
	m := open(t, ui.NewLogsModel(b.client()))
	b.reset()
	m, flashes := listsFlashes(t, m, press("n"))
	if strings.Join(flashes, "|") != "No older log entries" {
		t.Errorf("n flashes = %v", flashes)
	}
	_, flashes = listsFlashes(t, m, press("N"))
	if strings.Join(flashes, "|") != "Already at the newest entries" {
		t.Errorf("N flashes = %v", flashes)
	}
	if len(b.log()) != 0 {
		t.Errorf("sent %v", b.log())
	}
}

// The view says it is loading at first; a failed read shows the backend's
// reason, and a refresh that works clears it.
func TestLogsView_LoadingAndPollError(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/logs", 503, map[string]any{"detail": "log file not readable", "code": "log_unavailable", "details": nil})
	m := drive(t, ui.NewLogsModel(b.client()), tea.WindowSizeMsg{Width: 140, Height: 40})
	if !strings.Contains(content(m), "Loading...") {
		t.Errorf("no loading state:\n%s", content(m))
	}
	m = drive(t, m, runAll(m.Init())...)
	if v := content(m); !strings.Contains(v, "Error: API error 503: log file not readable") || strings.Contains(v, "Loading...") {
		t.Errorf("error not shown:\n%s", v)
	}
	b.json("GET", "/logs", 200, logEntries(3, 3, "INFO"))
	m = drive(t, m, ui.TickMsg{}) // live: the tick re-reads
	if v := content(m); strings.Contains(v, "Error:") || !strings.Contains(v, "line-003") {
		t.Errorf("tick did not recover:\n%s", v)
	}
}

// An answer for a page the user already left is dropped.
func TestLogsView_StaleAnswerDropped(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/logs", 200, logEntries(400, 200, "INFO"))
	m := open(t, ui.NewLogsModel(b.client()))
	stale := m.Init() // page 1
	updated, _ := m.Update(press("n"))
	m = updated.(ui.LogsModel)
	b.json("GET", "/logs", 200, []any{map[string]any{"timestamp": "2026-09-27T08:00:00Z", "level": "ERROR", "message": "stale-entry"}})
	m = drive(t, m, runAll(stale)...)
	if v := content(m); !strings.Contains(v, "Page 2") || strings.Contains(v, "stale-entry") {
		t.Errorf("stale page shown:\n%s", v)
	}
}

// Pasting into the search prompt filters like typing; the hints follow the
// prompt and the active search.
func TestLogsView_PasteSearchAndHints(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/logs", 200, logsMixedLevels())
	m := open(t, ui.NewLogsModel(b.client()))
	if !m.PollSpec().IsActive(nil) || strings.Contains(m.KeyHints(), "Esc:clear search") {
		t.Errorf("live view: hints %q", m.KeyHints())
	}
	m = drive(t, m, press("/"))
	if h := m.KeyHints(); h != "type to search  Enter:keep  Esc:cancel" {
		t.Errorf("prompt hints = %q", h)
	}
	m = drive(t, m, tea.PasteMsg{Content: "WARN"})
	m = drive(t, m, press("enter"))
	v := content(m)
	if !strings.Contains(v, "msg-warning") || strings.Count(v, "msg-") != 1 {
		t.Errorf("paste did not filter:\n%s", v)
	}
	if h := m.KeyHints(); !strings.Contains(h, "Esc:clear search") {
		t.Errorf("hints = %q", h)
	}
	// Paste outside the prompt is ignored.
	m = drive(t, m, tea.PasteMsg{Content: "zzz"})
	if !strings.Contains(content(m), `Search: "WARN"`) {
		t.Errorf("paste outside the prompt changed the search:\n%s", content(m))
	}
	// F leaves live mode; the view stops polling fast.
	m = drive(t, m, press("F"))
	if m.PollSpec().IsActive(nil) {
		t.Error("paused view still polls fast")
	}
	if m.ViewID() != ui.ViewLogs || len(m.KeyBindings()) == 0 {
		t.Error("view id or bindings missing")
	}
}
