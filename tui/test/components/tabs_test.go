package components_test

import (
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

func TestTabs_DefaultActive(t *testing.T) {
	tabs := components.NewTabs("A", "B", "C")
	if tabs.ActiveIdx != 0 {
		t.Errorf("expected active=0, got %d", tabs.ActiveIdx)
	}
}

func TestTabs_Next(t *testing.T) {
	tabs := components.NewTabs("A", "B", "C")
	tabs.Next()
	if tabs.ActiveIdx != 1 {
		t.Errorf("expected active=1, got %d", tabs.ActiveIdx)
	}
}

func TestTabs_NextWraps(t *testing.T) {
	tabs := components.NewTabs("A", "B")
	tabs.Next()
	tabs.Next()
	if tabs.ActiveIdx != 0 {
		t.Errorf("expected wrap to 0, got %d", tabs.ActiveIdx)
	}
}

func TestTabs_Prev(t *testing.T) {
	tabs := components.NewTabs("A", "B", "C")
	tabs.Next()
	tabs.Next()
	tabs.Prev()
	if tabs.ActiveIdx != 1 {
		t.Errorf("expected active=1, got %d", tabs.ActiveIdx)
	}
}

func TestTabs_PrevWraps(t *testing.T) {
	tabs := components.NewTabs("A", "B")
	tabs.Prev()
	if tabs.ActiveIdx != 1 {
		t.Errorf("expected wrap to 1, got %d", tabs.ActiveIdx)
	}
}

func TestTabs_SetActive(t *testing.T) {
	tabs := components.NewTabs("A", "B", "C")
	tabs.SetActive(2)
	if tabs.ActiveIdx != 2 {
		t.Errorf("expected active=2, got %d", tabs.ActiveIdx)
	}
}

func TestTabs_ActiveReturnsLabel(t *testing.T) {
	tabs := components.NewTabs("Overview", "Jobs")
	if tabs.Active() != "Overview" {
		t.Errorf("expected 'Overview', got %q", tabs.Active())
	}
	tabs.Next()
	if tabs.Active() != "Jobs" {
		t.Errorf("expected 'Jobs', got %q", tabs.Active())
	}
}

func TestTabs_ViewContainsTitles(t *testing.T) {
	tabs := components.NewTabs("Overview", "Jobs")
	view := stripANSI(tabs.View())
	if !strings.Contains(view, "Overview") {
		t.Errorf("expected view to contain 'Overview', got %q", view)
	}
	if !strings.Contains(view, "Jobs") {
		t.Errorf("expected view to contain 'Jobs', got %q", view)
	}
}
