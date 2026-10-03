package components_test

import (
	"regexp"
	"unicode"

	tea "charm.land/bubbletea/v2"
)

var ansiPattern = regexp.MustCompile(`\x1b\[[0-9;]*m`)

// stripANSI removes ANSI escape codes from a string for test assertions.
func stripANSI(s string) string {
	return ansiPattern.ReplaceAllString(s, "")
}

// keyPress creates a KeyPressMsg for testing. Printable keys carry their
// text, as real terminals send them.
func keyPress(code rune) tea.KeyPressMsg {
	k := tea.Key{Code: code}
	if unicode.IsPrint(code) {
		k.Text = string(code)
	}
	return tea.KeyPressMsg(k)
}

// typeText sends one key press per rune of s.
func typeText(u interface {
	Update(tea.Msg) (bool, tea.Cmd)
}, s string) {
	for _, r := range s {
		if r == ' ' {
			u.Update(tea.KeyPressMsg(tea.Key{Code: tea.KeySpace, Text: " "}))
			continue
		}
		u.Update(keyPress(r))
	}
}
