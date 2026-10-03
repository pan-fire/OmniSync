package components_test

import (
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

func TestSpinner_InitNotActive(t *testing.T) {
	s := components.NewSpinner("Loading")
	if s.Active {
		t.Error("expected spinner to not be active initially")
	}
}

func TestSpinner_Start(t *testing.T) {
	s := components.NewSpinner("Loading")
	cmd := s.Start()
	if !s.Active {
		t.Error("expected spinner to be active after Start()")
	}
	if cmd == nil {
		t.Error("expected Start() to return a command")
	}
}

func TestSpinner_Stop(t *testing.T) {
	s := components.NewSpinner("Loading")
	s.Start()
	s.Stop()
	if s.Active {
		t.Error("expected spinner to not be active after Stop()")
	}
}

func TestSpinner_ViewWhenStopped(t *testing.T) {
	s := components.NewSpinner("Loading")
	view := s.View()
	if view != "" {
		t.Errorf("expected empty view when stopped, got %q", view)
	}
}
