package theme_test

import (
	"testing"

	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
)

func TestDarkTheme_HasColors(t *testing.T) {
	d := theme.DarkTheme()
	if d.Primary == nil {
		t.Error("expected non-nil Primary color")
	}
	if d.Name != "dark" {
		t.Errorf("expected name 'dark', got %s", d.Name)
	}
}

func TestLightTheme_DifferentPrimary(t *testing.T) {
	d := theme.DarkTheme()
	l := theme.LightTheme()
	if d.Primary == l.Primary {
		t.Error("expected dark and light to have different Primary colors")
	}
	if l.Name != "light" {
		t.Errorf("expected name 'light', got %s", l.Name)
	}
}

func TestToggle(t *testing.T) {
	theme.Current = theme.DarkTheme()

	theme.Toggle()
	if theme.Current.Name != "light" {
		t.Errorf("expected light after toggle, got %s", theme.Current.Name)
	}

	theme.Toggle()
	if theme.Current.Name != "dark" {
		t.Errorf("expected dark after second toggle, got %s", theme.Current.Name)
	}
}

func TestSetASCII(t *testing.T) {
	theme.Current = theme.DarkTheme()

	theme.SetASCII(true)
	asciiBorder := lipgloss.ASCIIBorder()
	if theme.Current.Border.Top != asciiBorder.Top {
		t.Error("expected ASCIIBorder after SetASCII(true)")
	}

	theme.SetASCII(false)
	roundedBorder := lipgloss.RoundedBorder()
	if theme.Current.Border.Top != roundedBorder.Top {
		t.Error("expected RoundedBorder after SetASCII(false)")
	}
}

func TestStatusBadge_DistinctStates(t *testing.T) {
	th := theme.DarkTheme()
	states := []string{"idle", "pushing", "completed", "error", "paused", "pulling"}

	for _, s := range states {
		badge := th.StatusBadge(s)
		rendered := badge.Render(s)
		if rendered == "" {
			t.Errorf("StatusBadge(%q) rendered empty string", s)
		}
	}
}

func TestStatusBadge_IdleIsMuted(t *testing.T) {
	th := theme.DarkTheme()
	// Idle should use Muted color, error should use Error color — they should not be the same
	idle := th.StatusBadge("idle")
	errBadge := th.StatusBadge("error")
	// We can compare the rendered output to ensure they're different
	if idle.Render("test") == errBadge.Render("test") {
		t.Error("expected idle and error badges to render differently")
	}
}
