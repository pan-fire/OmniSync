package components_test

import (
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

func TestFlash_NewFlash(t *testing.T) {
	f, cmd := components.NewFlash("Test message", false)
	if f.Text != "Test message" {
		t.Errorf("expected text 'Test message', got %q", f.Text)
	}
	if f.IsError {
		t.Error("expected IsError=false")
	}
	if cmd == nil {
		t.Error("expected tick command")
	}
}

func TestFlash_NewFlashError(t *testing.T) {
	f, _ := components.NewFlash("error!", true)
	if !f.IsError {
		t.Error("expected IsError=true")
	}
}

func TestFlash_ViewContainsText(t *testing.T) {
	f, _ := components.NewFlash("Hello", false)
	view := f.View()
	if view == "" {
		t.Error("expected non-empty view")
	}
}

func TestFlash_ClearedViewIsEmpty(t *testing.T) {
	f, _ := components.NewFlash("msg", false)
	f.Clear()
	view := f.View()
	if view != "" {
		t.Errorf("expected empty view after Clear, got %q", view)
	}
}
