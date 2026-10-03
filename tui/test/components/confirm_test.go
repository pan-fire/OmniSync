package components_test

import (
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

func TestConfirm_NewConfirm(t *testing.T) {
	c := components.NewConfirm("Delete?", "delete-item")
	if !c.Active {
		t.Error("expected confirm to be active")
	}
	if c.Tag != "delete-item" {
		t.Errorf("expected tag 'delete-item', got %q", c.Tag)
	}
}

func TestConfirm_ViewContainsPrompt(t *testing.T) {
	c := components.NewConfirm("Are you sure?", "test")
	view := c.View()
	if !strings.Contains(view, "Are you sure?") {
		t.Errorf("expected view to contain prompt, got %q", view)
	}
}

func TestConfirm_YesKey(t *testing.T) {
	c := components.NewConfirm("Delete?", "delete")
	handled, cmd := c.Update(tea.KeyPressMsg(tea.Key{Code: 'y'}))
	if !handled {
		t.Error("expected 'y' to be handled")
	}
	if cmd == nil {
		t.Error("expected command from 'y' press")
	}
	msg := cmd()
	result, ok := msg.(components.ConfirmResultMsg)
	if !ok {
		t.Fatalf("expected ConfirmResultMsg, got %T", msg)
	}
	if !result.Confirmed {
		t.Error("expected Confirmed=true")
	}
	if result.Tag != "delete" {
		t.Errorf("expected tag 'delete', got %q", result.Tag)
	}
}

func TestConfirm_NoKey(t *testing.T) {
	c := components.NewConfirm("Delete?", "delete")
	handled, cmd := c.Update(tea.KeyPressMsg(tea.Key{Code: 'n'}))
	if !handled {
		t.Error("expected 'n' to be handled")
	}
	if cmd == nil {
		t.Error("expected command from 'n' press")
	}
	msg := cmd()
	result, ok := msg.(components.ConfirmResultMsg)
	if !ok {
		t.Fatalf("expected ConfirmResultMsg, got %T", msg)
	}
	if result.Confirmed {
		t.Error("expected Confirmed=false")
	}
}

func TestConfirm_InactiveReturnsNotHandled(t *testing.T) {
	c := components.NewConfirm("Delete?", "delete")
	c.Active = false
	handled, _ := c.Update(tea.KeyPressMsg(tea.Key{Code: 'y'}))
	if handled {
		t.Error("expected inactive confirm to not handle input")
	}
}

// With a word, 'y' does not confirm: only the typed word and Enter do.
func TestConfirm_WordNeedsTypedWordAndEnter(t *testing.T) {
	c := components.NewConfirmWord("Push?", "sync", "force", "to sync without a preview")
	if v := c.View(); !strings.Contains(v, `Type "force" and press Enter to sync without a preview`) || strings.Contains(v, "[y]es") {
		t.Errorf("view = %q", v)
	}
	for _, k := range []tea.Key{{Code: 'y', Text: "y"}, {Code: tea.KeyEnter}, {Code: 'f', Text: "f"}, {Code: 'o', Text: "o"},
		{Code: 'x', Text: "x"}, {Code: tea.KeyBackspace}, {Code: 'r', Text: "r"}, {Code: 'c', Text: "c"}, {Code: tea.KeyEnter}} {
		if handled, cmd := c.Update(tea.KeyPressMsg(k)); !handled || cmd != nil {
			t.Fatalf("key %v: handled=%v cmd=%v, want consumed without a result", k, handled, cmd != nil)
		}
	}
	// "yforc" is typed so far; clear the leading y.
	for range 5 {
		c.Update(tea.KeyPressMsg(tea.Key{Code: tea.KeyBackspace}))
	}
	for _, r := range "force" {
		c.Update(tea.KeyPressMsg(tea.Key{Code: r, Text: string(r)}))
	}
	_, cmd := c.Update(tea.KeyPressMsg(tea.Key{Code: tea.KeyEnter}))
	if cmd == nil {
		t.Fatal("typed word and Enter did not confirm")
	}
	if r := cmd().(components.ConfirmResultMsg); !r.Confirmed || r.Tag != "sync" || c.Active {
		t.Errorf("result = %+v, active = %v", r, c.Active)
	}
}

func TestConfirm_WordCancels(t *testing.T) {
	for _, k := range []tea.Key{{Code: tea.KeyEscape}, {Code: 'n', Text: "n"}} {
		c := components.NewConfirmWord("Push?", "sync", "force", "to sync")
		_, cmd := c.Update(tea.KeyPressMsg(k))
		if cmd == nil {
			t.Fatalf("key %v did not cancel", k)
		}
		if r := cmd().(components.ConfirmResultMsg); r.Confirmed {
			t.Errorf("key %v confirmed", k)
		}
	}
}
