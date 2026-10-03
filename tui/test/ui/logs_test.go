package ui_test

import (
	"fmt"
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// logEntries returns n entries, newest first, numbered from newest down.
func logEntries(newest, n int, level string) []any {
	out := []any{}
	for i := newest; i > newest-n; i-- {
		out = append(out, map[string]any{"timestamp": "2026-09-27T08:00:00Z", "level": level, "message": fmt.Sprintf("line-%03d", i)})
	}
	return out
}

// The arrow keys scroll through the page; scrolling up leaves live
// mode so polls do not move the lines under the reader.
func TestLogs_ScrollsAndLeavesLive(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/logs", 200, logEntries(200, 200, "INFO"))
	m := open(t, ui.NewLogsModel(b.client()))
	if !strings.Contains(content(m), "LIVE") {
		t.Fatal("live mode off at start")
	}
	m = drive(t, m, press("up"), press("up"), press("up"))
	v := content(m)
	if strings.Contains(v, "line-200") || !strings.Contains(v, "line-197") {
		t.Errorf("up did not scroll to older lines:\n%s", v)
	}
	if strings.Contains(v, "LIVE") {
		t.Error("still live after scrolling up")
	}
	if !strings.Contains(v, "lines 4-") {
		t.Errorf("position not shown:\n%s", v)
	}
	m = drive(t, m, press("down"), press("down"), press("down"))
	if !strings.Contains(content(m), "line-200") {
		t.Error("down did not come back to the newest line")
	}
	m = drive(t, m, press("g"))
	if v := content(m); !strings.Contains(v, "line-001") || strings.Contains(v, "line-200") {
		t.Errorf("g did not jump to the oldest line:\n%s", v)
	}
	// F turns live mode back on and shows the newest line again.
	m = drive(t, m, press("F"))
	if v := content(m); !strings.Contains(v, "LIVE") || !strings.Contains(v, "line-200") {
		t.Errorf("F did not return to the newest lines:\n%s", v)
	}
}

// / searches messages; the prompt owns the keyboard, Enter keeps the
// search, Esc clears it.
func TestLogs_SearchByMessage(t *testing.T) {
	b := newBackend(t)
	entries := logEntries(50, 50, "INFO")
	entries = append([]any{map[string]any{"timestamp": "2026-09-27T08:00:00Z", "level": "ERROR", "message": "Upload FAILED for docs"}}, entries...)
	b.json("GET", "/logs", 200, entries)
	m := open(t, ui.NewLogsModel(b.client()))
	m = drive(t, m, press("/"))
	if !m.CapturesInput() {
		t.Fatal("the search prompt must own the keyboard")
	}
	m = drive(t, m, typeKeys("failed fx")...) // "f" is text here, not the level filter
	if v := content(m); !strings.Contains(v, "No log entries on this page match") {
		t.Errorf("search did not filter:\n%s", v)
	}
	m = drive(t, m, press("backspace"), press("backspace"), press("backspace"), press("enter"))
	if m.CapturesInput() {
		t.Fatal("prompt still open after Enter")
	}
	v := content(m)
	if !strings.Contains(v, "Upload FAILED") || strings.Contains(v, "line-050") || !strings.Contains(v, `Search: "failed"`) {
		t.Errorf("case-insensitive search not applied:\n%s", v)
	}
	if !strings.Contains(v, "Filter: ALL") {
		t.Errorf("'f' typed into the prompt changed the level filter:\n%s", v)
	}
	m = drive(t, m, press("esc"))
	if v := content(m); !strings.Contains(v, "line-050") || strings.Contains(v, "Search:") {
		t.Errorf("Esc did not clear the search:\n%s", v)
	}

	// Esc while typing restores the previous search.
	m = drive(t, m, press("/"))
	m = drive(t, m, typeKeys("zzz")...)
	m = drive(t, m, press("esc"))
	if v := content(m); !strings.Contains(v, "line-050") || m.CapturesInput() {
		t.Errorf("Esc in the prompt kept the typed search:\n%s", v)
	}
}

// n/N page through older entries with skip; live mode pins the view
// to the newest page.
func TestLogs_PaginationUsesSkip(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/logs", 200, logEntries(400, 200, "INFO"))
	m := open(t, ui.NewLogsModel(b.client()))
	b.json("GET", "/logs", 200, logEntries(200, 200, "INFO"))
	m = drive(t, m, press("n"))
	v := content(m)
	if !strings.Contains(v, "Page 2") || !strings.Contains(v, "line-200") || strings.Contains(v, "LIVE") {
		t.Errorf("older page not shown or still live:\n%s", v)
	}
	// Ticks do not re-read while not live.
	b.reset()
	m = drive(t, m, ui.TickMsg{})
	if n := len(b.matching("GET /logs")); n != 0 {
		t.Errorf("tick fetched %d times while paused", n)
	}
	m = drive(t, m, press("N"))
	m = drive(t, m, press("n"), press("F"))
	got := b.matching("GET /logs")
	want := []string{"GET /logs?limit=200&skip=0", "GET /logs?limit=200&skip=200", "GET /logs?limit=200&skip=0"}
	if fmt.Sprint(got) != fmt.Sprint(want) {
		t.Errorf("queries = %v, want %v", got, want)
	}
	if !strings.Contains(content(m), "LIVE") || !strings.Contains(content(m), "Page 1") {
		t.Errorf("F did not return to the newest page:\n%s", content(m))
	}
}

// A short page is the oldest one: n does not ask for more.
func TestLogs_NoOlderPage(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/logs", 200, logEntries(10, 10, "INFO"))
	m := open(t, ui.NewLogsModel(b.client()))
	b.reset()
	m = drive(t, m, press("n"))
	if len(b.matching("GET /logs")) != 0 || !strings.Contains(content(m), "(oldest)") {
		t.Errorf("requests %v, view:\n%s", b.log(), content(m))
	}
}
