package components

import (
	"fmt"
	"strings"

	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
)

// KeyBinding represents a single keybinding for the help overlay.
type KeyBinding struct {
	Key  string
	Desc string
}

// RenderHelp renders a full-screen help overlay showing keybindings.
func RenderHelp(title string, bindings []KeyBinding, width, height int) string {
	var b strings.Builder

	headerStyle := lipgloss.NewStyle().
		Foreground(theme.Current.Primary).
		Bold(true)
	keyStyle := lipgloss.NewStyle().
		Foreground(theme.Current.Info).
		Width(16)
	descStyle := lipgloss.NewStyle().
		Foreground(theme.Current.Foreground)

	b.WriteString(headerStyle.Render(title))
	b.WriteString("\n\n")

	for _, kb := range bindings {
		b.WriteString(fmt.Sprintf("  %s %s\n",
			keyStyle.Render(kb.Key),
			descStyle.Render(kb.Desc),
		))
	}

	b.WriteString("\nPress any key to close")

	style := lipgloss.NewStyle().
		Width(width).
		Height(height).
		Padding(2, 4)

	return style.Render(b.String())
}

// GlobalBindings returns the keybindings available in all views. They are
// not active while a form, text field or prompt is open; there Esc cancels
// and Ctrl+C still quits.
func GlobalBindings() []KeyBinding {
	return []KeyBinding{
		{Key: "1-8", Desc: "Switch view"},
		{Key: "Tab", Desc: "Next view"},
		{Key: "Shift+Tab", Desc: "Previous view"},
		{Key: "?", Desc: "Show help (any key closes it)"},
		{Key: "Ctrl+T", Desc: "Toggle dark/light theme (saved)"},
		{Key: "q", Desc: "Quit (asks first)"},
		{Key: "Ctrl+C", Desc: "Quit immediately"},
	}
}
