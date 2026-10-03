package ui_test

import (
	"reflect"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// Windows has no xdg-open or open: without a Windows command the wizard
// could never open the OAuth consent page there.
func TestBrowserCommands_PerOS(t *testing.T) {
	for goos, want := range map[string][][]string{
		"linux":   {{"xdg-open"}},
		"darwin":  {{"open"}, {"xdg-open"}},
		"windows": {{"rundll32", "url.dll,FileProtocolHandler"}, {"cmd", "/c", "start", ""}},
	} {
		if got := ui.BrowserCommands(goos); !reflect.DeepEqual(got, want) {
			t.Errorf("%s: %q, want %q", goos, got, want)
		}
	}
}
