package components_test

import (
	"image/color"
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

func makeTable() components.Table {
	cols := []components.Column{
		{Title: "Name", Width: 20},
		{Title: "Status", Width: 10},
	}
	tbl := components.NewTable(cols, 5)
	tbl.SetRows([]components.Row{
		{Key: "a", Values: []string{"Alpha", "idle"}},
		{Key: "b", Values: []string{"Beta", "syncing"}},
		{Key: "c", Values: []string{"Gamma", "error"}},
	})
	return tbl
}

func TestTable_VisibleRows(t *testing.T) {
	tbl := makeTable()
	visible := tbl.VisibleRows()
	if len(visible) != 3 {
		t.Errorf("expected 3 visible rows, got %d", len(visible))
	}
}

func TestTable_CursorDown(t *testing.T) {
	tbl := makeTable()
	tbl.Update(keyPress(tea.KeyDown))
	if tbl.Cursor != 1 {
		t.Errorf("expected cursor=1, got %d", tbl.Cursor)
	}
}

func TestTable_CursorUp(t *testing.T) {
	tbl := makeTable()
	tbl.Update(keyPress(tea.KeyDown))
	tbl.Update(keyPress(tea.KeyUp))
	if tbl.Cursor != 0 {
		t.Errorf("expected cursor=0, got %d", tbl.Cursor)
	}
}

func TestTable_CursorUpAtTop(t *testing.T) {
	tbl := makeTable()
	tbl.Update(keyPress(tea.KeyUp))
	if tbl.Cursor != 0 {
		t.Errorf("expected cursor to stay 0, got %d", tbl.Cursor)
	}
}

func TestTable_Selection(t *testing.T) {
	tbl := makeTable()
	tbl.Update(keyPress(' ')) // space selects
	if tbl.SelectedCount() != 1 {
		t.Errorf("expected 1 selected, got %d", tbl.SelectedCount())
	}
	keys := tbl.SelectedKeys()
	if len(keys) != 1 || keys[0] != "a" {
		t.Errorf("expected key 'a' selected, got %v", keys)
	}
}

func TestTable_ToggleSelection(t *testing.T) {
	tbl := makeTable()
	tbl.Update(keyPress(' ')) // select
	tbl.Update(keyPress(' ')) // deselect
	if tbl.SelectedCount() != 0 {
		t.Errorf("expected 0 selected after toggle, got %d", tbl.SelectedCount())
	}
}

func TestTable_SelectAll(t *testing.T) {
	tbl := makeTable()
	tbl.Update(keyPress('a'))
	if tbl.SelectedCount() != 3 {
		t.Errorf("expected 3 selected, got %d", tbl.SelectedCount())
	}
}

func TestTable_DeselectAll(t *testing.T) {
	tbl := makeTable()
	tbl.Update(keyPress('a')) // select all
	tbl.Update(keyPress('a')) // deselect all
	if tbl.SelectedCount() != 0 {
		t.Errorf("expected 0 selected, got %d", tbl.SelectedCount())
	}
}

func TestTable_SelectedRow(t *testing.T) {
	tbl := makeTable()
	row := tbl.SelectedRow()
	if row == nil || row.Key != "a" {
		t.Errorf("expected row 'a', got %v", row)
	}
}

func TestTable_ClearSelection(t *testing.T) {
	tbl := makeTable()
	tbl.Update(keyPress('a'))
	tbl.ClearSelection()
	if tbl.SelectedCount() != 0 {
		t.Errorf("expected 0 after clear, got %d", tbl.SelectedCount())
	}
}

func TestTable_ViewNotEmpty(t *testing.T) {
	tbl := makeTable()
	view := tbl.View()
	if view == "" {
		t.Error("expected non-empty view")
	}
}

// Row.Colors colours single cells; the text is unchanged.
func TestTable_CellColors(t *testing.T) {
	tbl := components.NewTable([]components.Column{{Title: "Name", Width: 8}, {Title: "State", Width: 8}}, 5)
	red := lipgloss.Color("#FF0000")
	tbl.SetRows([]components.Row{
		{Key: "a", Values: []string{"first", "idle"}},
		{Key: "b", Values: []string{"second", "error"}, Colors: []color.Color{nil, red}},
	})
	out := tbl.View()
	var line string
	for _, l := range strings.Split(out, "\n") {
		if strings.Contains(l, "second") {
			line = l
		}
	}
	if !strings.Contains(line, "38;2;255;0;0") || !strings.Contains(line, "error") {
		t.Errorf("cell not coloured: %q", line)
	}
	if strings.Count(line, "38;2;255;0;0") != 1 {
		t.Errorf("colour applied to more than one cell: %q", line)
	}
}

// A name from the server stays on its row: wide characters count the two
// columns they take (they used to wrap the cell onto a second line and
// push the next column), and a newline, tab or escape sequence shows as
// U+FFFD instead of starting a row or restyling the screen.
func TestTable_ServerNamesStayOnTheirRow(t *testing.T) {
	table := components.NewTable([]components.Column{{Title: "Name", Width: 8}, {Title: "Size", Width: 6}}, 10)
	table.SetRows([]components.Row{
		{Key: "a", Values: []string{"日本語のファイル名.txt", "1 KB"}},
		{Key: "b", Values: []string{"evil\nfake\trow\x1b[8m", "2 KB"}},
	})
	lines := strings.Split(strings.TrimRight(table.View(), "\n"), "\n")
	if len(lines) != 3 {
		t.Fatalf("%d lines, want a header and two rows:\n%s", len(lines), strings.Join(lines, "\n"))
	}
	first, second := stripANSI(lines[1]), stripANSI(lines[2])
	if !strings.Contains(first, "日本語…") || !strings.Contains(first, "1 KB") || lipgloss.Width(first) > 3+8+1+6+1 {
		t.Errorf("wide name row = %q", first)
	}
	if !strings.Contains(second, "evil�fa") || strings.Contains(lines[2], "\x1b[8m") {
		t.Errorf("control characters row = %q", lines[2])
	}
}

// Emoji sequences are grapheme clusters the per-rune count got wrong: a
// variation selector (❤️, ☺️, keycaps) made it count too little, so the cell
// wrapped, and a ZWJ family or a flag pair was cut in the middle. Each name
// must keep its row to one line, fill its column exactly and keep whole
// clusters.
func TestTable_EmojiNamesFillTheirCell(t *testing.T) {
	cases := []struct {
		name  string
		width int
		want  string
	}{
		{"❤️❤️❤️❤️❤️❤️", 5, "❤️❤️…"},
		{"☺️☺️☺️abc", 6, "☺️☺️…"},
		{"✔️ done.txt", 6, "✔️ do…"},
		{"#️⃣#️⃣#️⃣", 4, "#️⃣…"},
		{"👨‍👩‍👧‍👦 family.jpg", 4, "👨‍👩‍👧‍👦 …"},
		{"🇩🇪🇫🇷🇮🇹", 4, "🇩🇪…"},
		{"0000000🏽", 8, "0000000🏽"},
		{"00000ꢶ", 5, "00000ꢶ"},
	}
	for _, c := range cases {
		table := components.NewTable([]components.Column{{Title: "Name", Width: c.width}, {Title: "Size", Width: 4}}, 10)
		table.SetRows([]components.Row{{Key: "a", Values: []string{c.name, "1 KB"}}, {Key: "b", Values: []string{"x", "y"}}})
		lines := strings.Split(strings.TrimRight(table.View(), "\n"), "\n")
		if len(lines) != 3 {
			t.Errorf("%q in %d columns: %d lines, want 3:\n%s", c.name, c.width, len(lines), strings.Join(lines, "\n"))
			continue
		}
		row := stripANSI(lines[1])
		if got, want := lipgloss.Width(row), 3+1+c.width+1+4; got != want {
			t.Errorf("%q in %d columns: row is %d columns, want %d: %q", c.name, c.width, got, want, row)
		}
		if !strings.Contains(row, c.want) {
			t.Errorf("%q in %d columns: row %q does not show %q", c.name, c.width, row, c.want)
		}
	}
}

// Truncate cuts between grapheme clusters and stays within max columns.
func TestTruncate_GraphemeClusters(t *testing.T) {
	cases := []struct {
		s    string
		max  int
		want string
	}{
		{"❤️❤️❤️❤️", 5, "❤️❤️…"},
		{"☺️☺️☺️", 4, "☺️…"},
		{"👩‍👩‍👧 family", 3, "👩‍👩‍👧…"},
		{"🇩🇪🇫🇷", 3, "🇩🇪…"},
		{"👍🏽👍🏽", 3, "👍🏽…"},
		{"0000000🏽", 8, "0000000🏽"},
	}
	for _, c := range cases {
		got := components.Truncate(c.s, c.max)
		if got != c.want {
			t.Errorf("Truncate(%q, %d) = %q, want %q", c.s, c.max, got, c.want)
		}
		if n := lipgloss.Width(got); n > c.max {
			t.Errorf("Truncate(%q, %d) = %q: %d columns", c.s, c.max, got, n)
		}
	}
}
