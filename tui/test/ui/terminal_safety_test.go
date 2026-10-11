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
// behaviour of the real renderer, behind the views' own filter
// (components.SafeLine; the CLI's is internal/cli/safeout.go).
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
	for _, seq := range []string{"\x1b]52;", "\x1b]0;owned", "\x1bP", "\x07", "\u009d", "\u009c"} {
		if strings.Contains(got, seq) {
			t.Errorf("the terminal received %q from server text:\n%q", seq, got)
		}
	}
	// The view shows each control character as U+FFFD; the rest of the
	// text stays readable.
	if !strings.Contains(got, "a\ufffd]52;c;") {
		t.Errorf("the escape is not shown as U+FFFD:\n%q", got)
	}
}

// A multi-line log message keeps its entry on one line: the first line of
// the message, then how many more lines the message and its traceback
// hold. A carriage return in it shows as U+FFFD instead of drawing the
// rest of the message over the start of the line.
func TestLogsView_MultiLineMessageStaysOnItsLine(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/logs", 200, []any{
		map[string]any{"timestamp": "2026-10-05T10:00:00", "level": "ERROR",
			"message": "copy failed\r\nretrying\nretry failed\n", "exc": "Traceback\nOSError"},
		map[string]any{"timestamp": "2026-10-05T10:00:01", "level": "INFO", "message": "progress 10%\rprogress 99%"},
	})
	v := content(open(t, ui.NewLogsModel(b.client())))
	if !strings.Contains(v, "[ERROR] copy failed (+4 lines)") {
		t.Errorf("multi-line entry not on one line:\n%s", v)
	}
	if strings.Contains(v, "retrying") {
		t.Errorf("the second line of the message started a line of its own:\n%s", v)
	}
	if !strings.Contains(v, "progress 10%�progress 99%") {
		t.Errorf("carriage return not shown as U+FFFD:\n%s", v)
	}
}
