package components_test

import (
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

// TUI-4: ordinary input must reach the field: spaces, digits, 'q' and
// multi-byte characters.
func TestForm_AcceptsOrdinaryInput(t *testing.T) {
	f := makeForm()
	typeText(&f, "Meine Dateien 2026 q Übersicht äöüß ✓")
	if got := f.Values()["name"]; got != "Meine Dateien 2026 q Übersicht äöüß ✓" {
		t.Errorf("name = %q", got)
	}
}

func TestForm_BackspaceRemovesWholeRune(t *testing.T) {
	f := makeForm()
	typeText(&f, "aä")
	f.Update(keyPress(tea.KeyBackspace))
	if got := f.Values()["name"]; got != "a" {
		t.Errorf("name = %q, want a", got)
	}
}

func TestForm_TabMovesFocusNotView(t *testing.T) {
	f := makeForm()
	handled, _ := f.Update(keyPress(tea.KeyTab))
	if !handled || f.FocusIdx != 1 {
		t.Fatalf("Tab: handled=%v focus=%d", handled, f.FocusIdx)
	}
	typeText(&f, "pw 1")
	if got := f.Values()["pass"]; got != "pw 1" {
		t.Errorf("pass = %q", got)
	}
	if got := f.Values()["name"]; got != "" {
		t.Errorf("typing into the second field changed the first: %q", got)
	}
	f.Update(tea.KeyPressMsg(tea.Key{Code: tea.KeyTab, Mod: tea.ModShift}))
	if f.FocusIdx != 0 {
		t.Errorf("Shift+Tab: focus = %d", f.FocusIdx)
	}
}

func TestForm_PasswordIsMasked(t *testing.T) {
	f := makeForm()
	f.Update(keyPress(tea.KeyTab))
	typeText(&f, "geheim")
	if strings.Contains(stripANSI(f.View()), "geheim") {
		t.Error("password shown in clear text")
	}
}

func TestForm_PasteGoesToField(t *testing.T) {
	f := makeForm()
	f.Update(tea.PasteMsg{Content: "/home/user/Mein Ordner"})
	if got := f.Values()["name"]; got != "/home/user/Mein Ordner" {
		t.Errorf("name = %q", got)
	}
}

func TestForm_SubmitCarriesID(t *testing.T) {
	f := components.NewFormWithID("create", "T", []components.Field{{Name: "a", Label: "A", Type: components.FieldText, Value: "x"}})
	_, cmd := f.Update(keyPress(tea.KeyEnter))
	msg, ok := cmd().(components.FormSubmitMsg)
	if !ok || msg.FormID != "create" || msg.Values["a"] != "x" {
		t.Errorf("unexpected submit %#v", msg)
	}
}

// TUI-5: a refresh must not move the cursor to another row.
func TestTable_SetRowsKeepsCursorOnSameKey(t *testing.T) {
	tbl := makeTable()
	tbl.Update(keyPress(tea.KeyDown))
	tbl.Update(keyPress(tea.KeyDown)) // on "c"
	if tbl.SelectedRow().Key != "c" {
		t.Fatalf("setup: on %q", tbl.SelectedRow().Key)
	}
	// New poll: order changed and a row was added in front.
	tbl.SetRows([]components.Row{
		{Key: "z", Values: []string{"Zeta", "idle"}},
		{Key: "c", Values: []string{"Gamma", "idle"}},
		{Key: "a", Values: []string{"Alpha", "idle"}},
		{Key: "b", Values: []string{"Beta", "idle"}},
	})
	if got := tbl.SelectedRow().Key; got != "c" {
		t.Errorf("cursor moved to %q after refresh, want c", got)
	}
}

func TestTable_SetRowsKeepsCursorAcrossPages(t *testing.T) {
	cols := []components.Column{{Title: "N", Width: 5}}
	tbl := components.NewTable(cols, 2)
	rows := []components.Row{{Key: "1"}, {Key: "2"}, {Key: "3"}, {Key: "4"}, {Key: "5"}}
	tbl.SetRows(rows)
	tbl.Update(keyPress('n')) // page 2
	tbl.Update(keyPress(tea.KeyDown))
	if tbl.SelectedRow().Key != "4" {
		t.Fatalf("setup: on %q", tbl.SelectedRow().Key)
	}
	tbl.SetRows(rows)
	if got := tbl.SelectedRow().Key; got != "4" {
		t.Errorf("after refresh on %q, want 4", got)
	}
}

func TestTable_SetRowsClampsWhenRowGone(t *testing.T) {
	tbl := makeTable()
	tbl.Update(keyPress(tea.KeyDown))
	tbl.Update(keyPress(tea.KeyDown)) // on "c", index 2
	tbl.Selected["c"] = true
	tbl.SetRows([]components.Row{{Key: "a"}, {Key: "b"}})
	if got := tbl.SelectedRow().Key; got != "b" {
		t.Errorf("cursor on %q, want the last row b", got)
	}
	if tbl.Selected["c"] {
		t.Error("selection of a removed row kept")
	}
}

// TUI-11: the highlighted row is marked without relying on colour.
func TestTable_CursorMarkerVisibleWithoutColour(t *testing.T) {
	tbl := makeTable()
	tbl.Update(keyPress(tea.KeyDown))
	lines := strings.Split(stripANSI(tbl.View()), "\n")
	var marked []string
	for _, l := range lines {
		if strings.HasPrefix(l, theme.Glyphs().Cursor) {
			marked = append(marked, l)
		}
	}
	if len(marked) != 1 || !strings.Contains(marked[0], "Beta") {
		t.Errorf("expected exactly the Beta row to carry the marker, got %q", marked)
	}
}

func TestTruncate_UTF8Safe(t *testing.T) {
	got := components.Truncate("Übersichtsdokumente", 6)
	if got != "Übers…" {
		t.Errorf("Truncate = %q", got)
	}
	if components.Truncate("äöü", 3) != "äöü" {
		t.Error("short strings must be unchanged")
	}
	for _, r := range components.Truncate("日本語のファイル名", 4) {
		if r == '�' {
			t.Error("truncate split a character")
		}
	}
}
