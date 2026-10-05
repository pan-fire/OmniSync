package components

import (
	"fmt"
	"image/color"
	"strings"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
)

// Column defines a table column.
type Column struct {
	Title string
	Width int
}

// Row represents a table row with a key for selection. Colors optionally
// gives a cell its foreground colour (nil or missing: the default); the
// text must still say what the colour means.
type Row struct {
	Key    string
	Values []string
	Colors []color.Color
}

// Table is an interactive table with a cursor, multi-selection and
// client-side pagination (n/N).
type Table struct {
	Columns   []Column
	Rows      []Row
	Cursor    int // index within the visible page
	Selected  map[string]bool
	paginator Paginator
	width     int
}

// NewTable creates a new table.
func NewTable(columns []Column, pageSize int) Table {
	return Table{
		Columns:   columns,
		Selected:  make(map[string]bool),
		paginator: NewPaginator(pageSize),
	}
}

// SetRows replaces the table rows. The cursor stays on the row with the same
// key when that row still exists, so a background refresh never moves the
// selection to another row. Selections of rows that are gone are dropped.
func (t *Table) SetRows(rows []Row) {
	prevKey := ""
	if row := t.SelectedRow(); row != nil {
		prevKey = row.Key
	}
	prevAbs := t.paginator.Offset() + t.Cursor

	t.Rows = rows
	t.paginator.Total = len(rows)

	present := make(map[string]bool, len(rows))
	for _, r := range rows {
		present[r.Key] = true
	}
	for k := range t.Selected {
		if !present[k] {
			delete(t.Selected, k)
		}
	}

	abs := -1
	if prevKey != "" {
		for i, r := range rows {
			if r.Key == prevKey {
				abs = i
				break
			}
		}
	}
	if abs < 0 {
		abs = prevAbs
	}
	t.moveTo(abs)
}

// moveTo puts the cursor on the absolute row index, clamped to the rows.
func (t *Table) moveTo(abs int) {
	if len(t.Rows) == 0 {
		t.paginator.Page = 0
		t.Cursor = 0
		return
	}
	if abs >= len(t.Rows) {
		abs = len(t.Rows) - 1
	}
	if abs < 0 {
		abs = 0
	}
	size := t.paginator.PageSize
	if size <= 0 {
		size = len(t.Rows)
	}
	t.paginator.Page = abs / size
	t.Cursor = abs % size
}

// SetWidth sets the table width for column adaption.
func (t *Table) SetWidth(w int) {
	t.width = w
}

// VisibleRows returns the rows visible on the current page.
func (t *Table) VisibleRows() []Row {
	start := t.paginator.Offset()
	end := start + t.paginator.PageSize
	if end > len(t.Rows) {
		end = len(t.Rows)
	}
	if start >= len(t.Rows) {
		return nil
	}
	return t.Rows[start:end]
}

// SelectedRow returns a copy of the currently highlighted row, or nil.
func (t *Table) SelectedRow() *Row {
	visible := t.VisibleRows()
	if t.Cursor >= 0 && t.Cursor < len(visible) {
		row := visible[t.Cursor]
		return &row
	}
	return nil
}

// SelectedCount returns the number of selected rows.
func (t *Table) SelectedCount() int {
	count := 0
	for _, v := range t.Selected {
		if v {
			count++
		}
	}
	return count
}

// SelectedKeys returns all selected row keys in table order.
func (t *Table) SelectedKeys() []string {
	var keys []string
	for _, r := range t.Rows {
		if t.Selected[r.Key] {
			keys = append(keys, r.Key)
		}
	}
	return keys
}

// ClearSelection clears all selections.
func (t *Table) ClearSelection() {
	t.Selected = make(map[string]bool)
}

// Update handles keyboard input for the table and reports whether it used
// the key.
func (t *Table) Update(msg tea.Msg) bool {
	km, ok := msg.(tea.KeyPressMsg)
	if !ok {
		return false
	}

	visible := t.VisibleRows()
	switch km.String() {
	case "up", "k":
		if t.Cursor > 0 {
			t.Cursor--
		} else if t.paginator.PrevPage() {
			t.Cursor = len(t.VisibleRows()) - 1
		}
		return true
	case "down", "j":
		if t.Cursor < len(visible)-1 {
			t.Cursor++
		} else if t.paginator.NextPage() {
			t.Cursor = 0
		}
		return true
	case "space", " ":
		if row := t.SelectedRow(); row != nil {
			if t.Selected[row.Key] {
				delete(t.Selected, row.Key)
			} else {
				t.Selected[row.Key] = true
			}
		}
		return true
	case "a":
		if t.SelectedCount() == len(t.Rows) {
			t.ClearSelection()
		} else {
			for _, r := range t.Rows {
				t.Selected[r.Key] = true
			}
		}
		return true
	case "n":
		if t.paginator.NextPage() {
			t.Cursor = 0
		}
		return true
	case "N":
		if t.paginator.PrevPage() {
			t.Cursor = 0
		}
		return true
	}
	return false
}

// View renders the table. The highlighted row carries a '>' marker and is
// drawn in reverse video, so it stays visible without colours (NO_COLOR).
func (t *Table) View() string {
	var b strings.Builder
	g := theme.Glyphs()
	headerStyle := lipgloss.NewStyle().Bold(true).Foreground(theme.Current.Primary)
	selectedStyle := lipgloss.NewStyle().Reverse(true).Bold(true)
	normalStyle := lipgloss.NewStyle().Foreground(theme.Current.Foreground)
	checkStyle := lipgloss.NewStyle().Foreground(theme.Current.Success)

	// Header
	headerCells := []string{headerStyle.Width(3).Render(" ")}
	for _, col := range t.Columns {
		headerCells = append(headerCells, headerStyle.Width(col.Width).Render(col.Title))
	}
	b.WriteString(strings.Join(headerCells, " "))
	b.WriteString("\n")

	visible := t.VisibleRows()
	if len(visible) == 0 {
		b.WriteString(normalStyle.Render("  (empty)"))
	}
	for i, row := range visible {
		marker := " "
		if i == t.Cursor {
			marker = g.Cursor
		}
		check := " "
		if t.Selected[row.Key] {
			check = checkStyle.Render(g.Check)
		}
		cells := []string{fmt.Sprintf("%s%s ", marker, check)}

		style := normalStyle
		if i == t.Cursor {
			style = selectedStyle
		}
		for j, val := range row.Values {
			w := 10
			if j < len(t.Columns) {
				w = t.Columns[j].Width
			}
			cellStyle := style
			if j < len(row.Colors) && row.Colors[j] != nil {
				cellStyle = cellStyle.Foreground(row.Colors[j])
			}
			cells = append(cells, cellStyle.Width(w).MaxWidth(w).Render(Truncate(SafeLine(val), w)))
		}
		b.WriteString(strings.Join(cells, " "))
		b.WriteString("\n")
	}

	if t.paginator.TotalPages() > 1 {
		b.WriteString("\n")
		b.WriteString(t.paginator.View())
	}

	return b.String()
}

// Truncate shortens s to at most max terminal columns, ending in an
// ellipsis when cut. It never splits a character, and a wide one (CJK, most
// emoji) counts as the two columns it takes.
func Truncate(s string, max int) string {
	if max <= 0 {
		return ""
	}
	if lipgloss.Width(s) <= max {
		return s
	}
	ell := theme.Glyphs().Ellipsis
	room := max - lipgloss.Width(ell)
	if room <= 0 {
		room, ell = max, ""
	}
	var b strings.Builder
	used := 0
	for _, r := range s {
		w := lipgloss.Width(string(r))
		if used+w > room {
			break
		}
		b.WriteRune(r)
		used += w
	}
	return b.String() + ell
}
