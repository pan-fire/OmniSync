package components

import (
	"fmt"
	"unicode/utf8"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
)

// ConfirmResultMsg carries the result of a confirmation dialog.
type ConfirmResultMsg struct {
	Confirmed bool
	Tag       string // identifies which action was being confirmed
}

// Confirm is a modal yes/no dialog. With Word set it asks for an explicit
// acknowledgement instead: 'y' does not confirm, the user has to type Word
// and press Enter (Esc, or 'n' before anything is typed, cancels).
type Confirm struct {
	Prompt string
	Tag    string
	Active bool
	// Word, when set, must be typed to confirm.
	Word string
	// WordHint says what typing Word does, e.g. "to sync without a preview".
	WordHint string
	typed    string
}

// NewConfirm creates a new confirmation dialog.
func NewConfirm(prompt, tag string) Confirm {
	return Confirm{Prompt: prompt, Tag: tag, Active: true}
}

// NewConfirmWord creates a dialog that only confirms once the user typed
// word and pressed Enter. hint completes "Type "word" and press Enter ...".
func NewConfirmWord(prompt, tag, word, hint string) Confirm {
	return Confirm{Prompt: prompt, Tag: tag, Active: true, Word: word, WordHint: hint}
}

func (c *Confirm) answer(confirmed bool) (bool, tea.Cmd) {
	c.Active = false
	c.typed = ""
	tag := c.Tag
	return true, func() tea.Msg {
		return ConfirmResultMsg{Confirmed: confirmed, Tag: tag}
	}
}

// Update handles key input for the confirm dialog.
func (c *Confirm) Update(msg tea.Msg) (bool, tea.Cmd) {
	if !c.Active {
		return false, nil
	}
	km, ok := msg.(tea.KeyPressMsg)
	if !ok {
		return false, nil
	}
	if c.Word != "" {
		return c.updateWord(km)
	}
	switch km.String() {
	case "y", "Y":
		return c.answer(true)
	case "n", "N", "esc":
		return c.answer(false)
	}
	return true, nil // consume other keys while active
}

func (c *Confirm) updateWord(km tea.KeyPressMsg) (bool, tea.Cmd) {
	key := km.String()
	switch key {
	case "esc":
		return c.answer(false)
	case "enter":
		if c.typed == c.Word {
			return c.answer(true)
		}
		return true, nil
	case "backspace":
		if c.typed != "" {
			_, size := utf8.DecodeLastRuneInString(c.typed)
			c.typed = c.typed[:len(c.typed)-size]
		}
		return true, nil
	case "n", "N":
		if c.typed == "" {
			return c.answer(false)
		}
	}
	if utf8.RuneCountInString(key) == 1 && len(c.typed) < 64 {
		c.typed += key
	}
	return true, nil
}

// View renders the confirm dialog.
func (c *Confirm) View() string {
	if !c.Active {
		return ""
	}
	style := lipgloss.NewStyle().
		Border(theme.Current.Border).
		BorderForeground(theme.Current.Warning).
		Padding(1, 2)
	if c.Word != "" {
		return style.Render(fmt.Sprintf("%s\n\nType %q and press Enter %s, or Esc to cancel.\n> %s",
			c.Prompt, c.Word, c.WordHint, c.typed))
	}
	return style.Render(c.Prompt + "\n\n[y]es  [n]o (Esc)")
}
