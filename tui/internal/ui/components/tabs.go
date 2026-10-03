package components

import (
	"strings"

	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
)

// Tabs is a horizontal tab bar component.
type Tabs struct {
	Labels    []string
	ActiveIdx int
}

// NewTabs creates a new tab bar.
func NewTabs(labels ...string) Tabs {
	return Tabs{Labels: labels, ActiveIdx: 0}
}

// Next moves to the next tab.
func (t *Tabs) Next() {
	t.ActiveIdx = (t.ActiveIdx + 1) % len(t.Labels)
}

// Prev moves to the previous tab.
func (t *Tabs) Prev() {
	t.ActiveIdx = (t.ActiveIdx - 1 + len(t.Labels)) % len(t.Labels)
}

// SetActive sets the active tab by index.
func (t *Tabs) SetActive(idx int) {
	if idx >= 0 && idx < len(t.Labels) {
		t.ActiveIdx = idx
	}
}

// Active returns the label of the currently active tab.
func (t *Tabs) Active() string {
	return t.Labels[t.ActiveIdx]
}

// View renders the tab bar.
func (t *Tabs) View() string {
	activeStyle := lipgloss.NewStyle().
		Foreground(theme.Current.Primary).
		Bold(true).
		Underline(true).
		Padding(0, 2)
	inactiveStyle := lipgloss.NewStyle().
		Foreground(theme.Current.Muted).
		Padding(0, 2)

	var tabs []string
	for i, label := range t.Labels {
		if i == t.ActiveIdx {
			tabs = append(tabs, activeStyle.Render(label))
		} else {
			tabs = append(tabs, inactiveStyle.Render(label))
		}
	}
	return strings.Join(tabs, " ")
}
