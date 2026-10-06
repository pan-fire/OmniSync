package ui_test

import (
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"
)

// listsFlashes feeds msg to a view model, keeps feeding back what its
// commands produce (polls are dropped, see flashesOf) and returns the
// updated model and the flash texts the user would see.
func listsFlashes[M tea.Model](t *testing.T, m M, msg tea.Msg) (M, []string) {
	t.Helper()
	flashes := flashesOf(func(next tea.Msg) tea.Cmd {
		updated, cmd := m.Update(next)
		m = updated.(M)
		return cmd
	}, msg, 500*time.Millisecond)
	return m, flashes
}
