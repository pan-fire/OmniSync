package components

import (
	"charm.land/bubbles/v2/spinner"
	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
)

// Spinner wraps a bubbles spinner with a label.
type Spinner struct {
	spinner spinner.Model
	Label   string
	Active  bool
}

// NewSpinner creates a new spinner with a label.
func NewSpinner(label string) Spinner {
	s := spinner.New()
	s.Spinner = spinner.Dot
	s.Style = lipgloss.NewStyle().Foreground(theme.Current.Info)
	return Spinner{spinner: s, Label: label, Active: false}
}

// Start activates the spinner.
func (s *Spinner) Start() tea.Cmd {
	s.Active = true
	return s.spinner.Tick
}

// Stop deactivates the spinner.
func (s *Spinner) Stop() {
	s.Active = false
}

// Update handles spinner tick messages.
func (s *Spinner) Update(msg tea.Msg) tea.Cmd {
	if !s.Active {
		return nil
	}
	var cmd tea.Cmd
	s.spinner, cmd = s.spinner.Update(msg)
	return cmd
}

// View renders the spinner.
func (s *Spinner) View() string {
	if !s.Active {
		return ""
	}
	return s.spinner.View() + " " + s.Label
}
