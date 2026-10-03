package ui_test

import (
	"fmt"
	"image/color"
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/theme"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// fg is the SGR parameter lipgloss writes for a foreground colour.
func fg(c color.Color) string {
	r, g, b, _ := c.RGBA()
	return fmt.Sprintf("38;2;%d;%d;%d", r>>8, g>>8, b>>8)
}

// rawLine returns the rendered (ANSI) line whose text contains s.
func rawLine(t *testing.T, raw, s string) string {
	t.Helper()
	for _, line := range strings.Split(raw, "\n") {
		if strings.Contains(stripANSI(line), s) {
			return line
		}
	}
	t.Fatalf("no line contains %q:\n%s", s, stripANSI(raw))
	return ""
}

// Dashboard state cells are coloured (idle gray, running
// blue, error red) and still say the state in words.
func TestDashboard_StateBadgesColoured(t *testing.T) {
	b := dashboardBackend(t)
	run := profileJSON("run", "Laufend", "pushing")
	idle := profileJSON("idle", "Ruhig", "idle")
	bad := profileJSON("bad", "Kaputt", "error")
	bad["last_error"] = "disk full"
	b.json("GET", "/profiles", 200, []any{idle, run, bad})
	m := openDashboard(t, b)
	raw := m.View().Content
	for _, tc := range []struct {
		name, text string
		c          color.Color
	}{
		{"Laufend", "pushing", theme.Current.Info},
		{"Kaputt", "error: disk full", theme.Current.Error},
	} {
		line := rawLine(t, raw, tc.name)
		if !strings.Contains(stripANSI(line), tc.text) || !strings.Contains(line, fg(tc.c)) {
			t.Errorf("%s: want %q in %s, line %q", tc.name, tc.text, fg(tc.c), line)
		}
	}
	// The Profiles list colours its state column the same way.
	p := open(t, ui.NewProfilesModel(b.client()))
	line := rawLine(t, p.View().Content, "Laufend")
	if !strings.Contains(line, fg(theme.Current.Info)) {
		t.Errorf("profiles list state not coloured: %q", line)
	}
}

// Job status cells are coloured and keep the word.
func TestJobs_StatusColouredAndPageCue(t *testing.T) {
	b := newBackend(t)
	jobs := jobsJSON(3, 10)
	jobs[1].(map[string]any)["status"] = "running"
	jobs[2].(map[string]any)["status"] = "failed"
	b.json("GET", "/jobs", 200, jobs)
	b.json("GET", "/profiles", 200, []any{})
	m := open(t, ui.NewJobsModel(b.client()))
	raw := m.View().Content
	if line := rawLine(t, raw, "running"); !strings.Contains(line, fg(theme.Current.Info)) {
		t.Errorf("running not blue: %q", line)
	}
	if line := rawLine(t, raw, "failed"); !strings.Contains(line, fg(theme.Current.Error)) {
		t.Errorf("failed not red: %q", line)
	}
	if !strings.Contains(content(m), "Page 1 (only page)") {
		t.Errorf("header:\n%s", content(m))
	}
}

// GET /jobs has no total, so the header says whether more pages exist.
func TestJobs_PageCueMoreAndLast(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/jobs", 200, jobsJSON(20, 100))
	b.json("GET", "/profiles", 200, []any{})
	m := open(t, ui.NewJobsModel(b.client()))
	if !strings.Contains(content(m), "Page 1 (more: n)") {
		t.Errorf("header:\n%s", content(m))
	}
	b.json("GET", "/jobs", 200, jobsJSON(4, 80))
	m = drive(t, m, press("n"))
	if !strings.Contains(content(m), "Page 2 (last page)") {
		t.Errorf("header:\n%s", content(m))
	}
}
