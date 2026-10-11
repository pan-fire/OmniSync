package ui_test

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// frame shows a view's last content; the program renders it once and quits.
type frame struct{ content string }

func (frame) Init() tea.Cmd                         { return tea.Quit }
func (f frame) Update(tea.Msg) (tea.Model, tea.Cmd) { return f, nil }
func (f frame) View() tea.View                      { return tea.NewView(f.content) }

// renderToTerminal returns the bytes the real renderer writes for content.
func renderToTerminal(t *testing.T, content string) string {
	t.Helper()
	var out bytes.Buffer
	p := tea.NewProgram(frame{content}, tea.WithOutput(&out), tea.WithInput(strings.NewReader("")), tea.WithWindowSize(120, 30))
	if _, err := p.Run(); err != nil {
		t.Fatal(err)
	}
	return out.String()
}

// terminalInjection returns what in out a terminal would act on beyond the
// renderer's own CSI sequences: OSC (clipboard, title, hyperlinks), DCS,
// APC, PM, SOS, a lone escape, BEL or an 8-bit C1 control.
func terminalInjection(out string) (string, bool) {
	for i, r := range out {
		switch {
		case r == 0x1b && !strings.HasPrefix(out[i:], "\x1b["):
			return out[i:min(len(out), i+12)], true
		case r == 0x07 || (r >= 0x80 && r <= 0x9f):
			return out[max(0, i-8):min(len(out), i+8)], true
		}
	}
	return "", false
}

// Log messages are server text that may carry a file name anyone with
// write access to a synced folder chose. Whatever they hold, the Logs view
// renders without panicking, and what reaches the terminal holds no
// sequence from the message: only the renderer's own cursor and style
// sequences. Neither does a cell: a carriage return, a backspace or an SGR
// sequence in a message must not draw over or restyle its line.
func FuzzLogsViewTerminalOutput(f *testing.F) {
	var mu sync.Mutex
	var reply []byte
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		body := reply
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(body)
	}))
	f.Cleanup(srv.Close)
	client := api.NewClient(srv.URL, "", "fuzz")
	render := func(t *testing.T, message, level, exc string) string {
		body, err := json.Marshal([]map[string]string{{"timestamp": "2026-10-05T10:00:00Z", "level": level, "message": message, "exc": exc}})
		if err != nil {
			t.Skip()
		}
		mu.Lock()
		reply = body
		mu.Unlock()
		return open(t, ui.NewLogsModel(client)).View().Content
	}
	for _, s := range hostileSeeds {
		f.Add(s, "ERROR", "")
		f.Add("upload failed", s, s)
	}
	f.Fuzz(func(t *testing.T, message, level, exc string) {
		frame := render(t, message, level, exc)
		out := renderToTerminal(t, frame)
		if seq, found := terminalInjection(out); found {
			t.Errorf("the terminal received %q from server text", seq)
		}
		// The renderer's cells: no control character, no cursor movement
		// or carriage return from the message, and no style from it.
		checkFrame(t, "logs", frame)
		inert := strings.NewReplacer("\x1b", "\ufffd", "\u009b", "\ufffd")
		allowed := sgrSequences(render(t, inert.Replace(message), inert.Replace(level), inert.Replace(exc)))
		for seq := range sgrSequences(frame) {
			if !allowed[seq] {
				t.Errorf("the message restyles the view with %q", seq)
			}
		}
	})
}
