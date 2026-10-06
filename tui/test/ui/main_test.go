package ui_test

import (
	"errors"
	"os"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// TestMain replaces the real browser opener: on a desktop or a macOS or
// Windows runner, `open`, xdg-open or rundll32 exist and a test pressing "o"
// would start a browser. Tests that check the opener install their own.
func TestMain(m *testing.M) {
	ui.BrowserOpener = func(string) error { return errors.New("no browser in tests") }
	os.Exit(m.Run())
}
