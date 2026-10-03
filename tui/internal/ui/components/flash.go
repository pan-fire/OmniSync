package components

import (
	"time"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
)

// FlashDuration is how long flash messages are displayed.
const FlashDuration = 5 * time.Second

// FlashExpiredMsg signals that the flash message has expired.
type FlashExpiredMsg struct{}

// Flash holds a timed status bar message.
type Flash struct {
	Text    string
	IsError bool
	Expiry  time.Time
}

// NewFlash creates a new flash message and returns a command to expire it.
func NewFlash(text string, isError bool) (Flash, tea.Cmd) {
	f := Flash{
		Text:    text,
		IsError: isError,
		Expiry:  time.Now().Add(FlashDuration),
	}
	cmd := tea.Tick(FlashDuration, func(t time.Time) tea.Msg {
		return FlashExpiredMsg{}
	})
	return f, cmd
}

// View renders the flash message.
func (f *Flash) View() string {
	if f.Text == "" || time.Now().After(f.Expiry) {
		return ""
	}
	var style lipgloss.Style
	if f.IsError {
		style = lipgloss.NewStyle().Foreground(theme.Current.Error)
	} else {
		style = lipgloss.NewStyle().Foreground(theme.Current.Success)
	}
	return style.Render(f.Text)
}

// Clear removes the flash message.
func (f *Flash) Clear() {
	f.Text = ""
}
