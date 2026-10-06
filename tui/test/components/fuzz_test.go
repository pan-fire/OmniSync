package components_test

import (
	"strings"
	"testing"
	"unicode/utf8"

	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

// Truncate gets file, remote and profile names from the server: it keeps
// at most max terminal columns, never splits a character, and leaves a
// string that fits as it is.
func FuzzTruncate(f *testing.F) {
	f.Fuzz(func(t *testing.T, s string, max int) {
		max %= 200
		got := components.Truncate(s, max)
		if max <= 0 {
			if got != "" {
				t.Errorf("Truncate(%q, %d) = %q, want empty", s, max, got)
			}
			return
		}
		if n := lipgloss.Width(got); n > max {
			t.Errorf("Truncate(%q, %d) = %q: %d columns", s, max, got, n)
		}
		if components.Columns(s) <= max && got != s {
			t.Errorf("Truncate(%q, %d) = %q, want it unchanged", s, max, got)
		}
		if utf8.ValidString(s) && !utf8.ValidString(got) {
			t.Errorf("Truncate(%q, %d) = %q split a character", s, max, got)
		}
	})
}

// A table row stays one line no wider than its columns, whatever a cell
// holds: a file name with a newline, a tab, an escape sequence or wide
// characters must not add a row, push the next column or restyle the rest
// of the screen.
func FuzzTableRow(f *testing.F) {
	f.Fuzz(func(t *testing.T, a, b string, w uint8) {
		cols := []components.Column{{Title: "Name", Width: 4 + int(w)%40}, {Title: "Size", Width: 8}}
		table := components.NewTable(cols, 10)
		table.SetRows([]components.Row{{Key: "k", Values: []string{a, b}}, {Key: "j", Values: []string{"x", "y"}}})
		lines := strings.Split(strings.TrimRight(table.View(), "\n"), "\n")
		if len(lines) != 3 {
			t.Fatalf("%d lines for a header and two rows:\n%q", len(lines), lines)
		}
		limit := 3 + cols[0].Width + 1 + cols[1].Width + 1
		for _, line := range lines[1:] {
			if width := lipgloss.Width(line); width > limit {
				t.Errorf("row is %d cells wide, columns allow %d: %q", width, limit, line)
			}
		}
		for _, seq := range []string{"\x1b]", "\x1bP", "\x07", "\r", "\b"} {
			if strings.Contains(lines[1], seq) {
				t.Errorf("row passes %q from the cell text: %q", seq, lines[1])
			}
		}
	})
}
