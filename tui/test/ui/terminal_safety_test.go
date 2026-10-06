package ui_test

import (
	"bytes"
	"strings"
	"sync"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// lockedBuffer is a terminal the program writes to while the test reads it.
type lockedBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (l *lockedBuffer) Write(p []byte) (int, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.buf.Write(p)
}

func (l *lockedBuffer) String() string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.buf.String()
}

// A log message can carry a file name from a synced folder, which anyone
// with write access to it chooses. Its control sequences must not reach
// the terminal: the renderer draws the frame into cells and writes only its
// own sequences, so the clipboard (OSC 52), the title (OSC 0) and DCS
// requests from server text never leave the program. This pins that
// behaviour of the real renderer for the TUI, which has no filter of its
// own (the CLI's is internal/cli/safeout.go).
func TestTerminalSafety_ServerTextCannotInjectSequences(t *testing.T) {
	b := newBackend(t)
	hostile := "a\x1b]52;c;cm0gLXJmIH4=\x07b\x1b]0;owned\x1b\\c\x1bP+q544e\x1b\\d\u009d0;x\u009ce"
	b.json("GET", "/logs", 200, []any{map[string]any{
		"timestamp": "2026-10-05T10:00:00", "level": "ERROR", "message": "upload " + hostile + " MARK-END",
	}})
	out := &lockedBuffer{}
	p := tea.NewProgram(ui.NewLogsModel(b.client()),
		tea.WithOutput(out), tea.WithInput(strings.NewReader("")), tea.WithWindowSize(200, 20))
	done := make(chan error, 1)
	go func() { _, err := p.Run(); done <- err }()

	deadline := time.Now().Add(10 * time.Second)
	for !strings.Contains(out.String(), "MARK-END") {
		if time.Now().After(deadline) {
			p.Kill()
			t.Fatalf("the log entry was never drawn:\n%q", out.String())
		}
		time.Sleep(10 * time.Millisecond) // polling the output, not synchronising
	}
	p.Quit()
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	got := out.String()
	for _, seq := range []string{"]52;", "]0;owned", "\x1bP", "\x07", "\u009d", "\u009c"} {
		if strings.Contains(got, seq) {
			t.Errorf("the terminal received %q from server text:\n%q", seq, got)
		}
	}
}
