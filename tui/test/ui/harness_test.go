package ui_test

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// backend is a fake OmniSync backend that records every request.
type backend struct {
	t        *testing.T
	mu       sync.Mutex
	requests []string // "METHOD /path?query"
	bodies   []map[string]any
	replies  map[string]func(w http.ResponseWriter, r *http.Request)
	srv      *httptest.Server
}

func newBackend(t *testing.T) *backend {
	t.Helper()
	b := &backend{t: t, replies: map[string]func(http.ResponseWriter, *http.Request){}}
	b.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		data, _ := io.ReadAll(r.Body)
		r.Body = io.NopCloser(bytes.NewReader(data)) // for handlers registered with handle
		var body map[string]any
		_ = json.Unmarshal(data, &body)
		line := r.Method + " " + r.URL.Path
		if r.URL.RawQuery != "" {
			line += "?" + r.URL.RawQuery
		}
		b.mu.Lock()
		b.requests = append(b.requests, line)
		b.bodies = append(b.bodies, body)
		h, ok := b.replies[r.Method+" "+r.URL.Path]
		b.mu.Unlock()
		if !ok {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		h(w, r)
	}))
	t.Cleanup(b.srv.Close)
	return b
}

// json registers a JSON reply for METHOD /path.
func (b *backend) json(method, path string, status int, body any) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.replies[method+" "+path] = func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(status)
		if body != nil {
			_ = json.NewEncoder(w).Encode(body)
		}
	}
}

// handle registers a custom handler for METHOD /path. body is the decoded
// JSON request body (nil if none).
func (b *backend) handle(method, path string, h func(w http.ResponseWriter, body map[string]any)) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.replies[method+" "+path] = func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		h(w, body)
	}
}

// bodiesOf returns the JSON bodies of the recorded requests equal to line
// ("METHOD /path").
func (b *backend) bodiesOf(line string) []map[string]any {
	b.mu.Lock()
	defer b.mu.Unlock()
	var out []map[string]any
	for i, r := range b.requests {
		if r == line {
			out = append(out, b.bodies[i])
		}
	}
	return out
}

func (b *backend) client() *api.Client {
	return api.NewClient(b.srv.URL, "", "test")
}

// log returns all recorded requests.
func (b *backend) log() []string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return append([]string(nil), b.requests...)
}

// reset forgets the recorded requests.
func (b *backend) reset() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.requests = nil
	b.bodies = nil
}

// matching returns the recorded requests that start with prefix.
func (b *backend) matching(prefix string) []string {
	var out []string
	for _, r := range b.log() {
		if strings.HasPrefix(r, prefix) {
			out = append(out, r)
		}
	}
	return out
}

// run executes a command tree and returns the messages it produced.
// Batches are flattened; commands that do not finish within wait (timers
// such as ticks, spinners and flash expiry) are dropped.
func run(cmd tea.Cmd, wait time.Duration) []tea.Msg {
	if cmd == nil {
		return nil
	}
	ch := make(chan tea.Msg, 1)
	go func() { ch <- cmd() }()
	var msg tea.Msg
	select {
	case msg = <-ch:
	case <-time.After(wait):
		return nil
	}
	if batch, ok := msg.(tea.BatchMsg); ok {
		var out []tea.Msg
		for _, c := range batch {
			out = append(out, run(c, wait)...)
		}
		return out
	}
	if msg == nil {
		return nil
	}
	return []tea.Msg{msg}
}

// runAll executes cmd with a short wait, suitable for API calls against the
// fake backend.
func runAll(cmd tea.Cmd) []tea.Msg {
	return run(cmd, 500*time.Millisecond)
}

// press builds a key press as a terminal sends it.
func press(s string) tea.KeyPressMsg {
	switch s {
	case "enter":
		return tea.KeyPressMsg(tea.Key{Code: tea.KeyEnter})
	case "esc":
		return tea.KeyPressMsg(tea.Key{Code: tea.KeyEscape})
	case "tab":
		return tea.KeyPressMsg(tea.Key{Code: tea.KeyTab})
	case "shift+tab":
		return tea.KeyPressMsg(tea.Key{Code: tea.KeyTab, Mod: tea.ModShift})
	case "up":
		return tea.KeyPressMsg(tea.Key{Code: tea.KeyUp})
	case "down":
		return tea.KeyPressMsg(tea.Key{Code: tea.KeyDown})
	case "left":
		return tea.KeyPressMsg(tea.Key{Code: tea.KeyLeft})
	case "right":
		return tea.KeyPressMsg(tea.Key{Code: tea.KeyRight})
	case "space", " ":
		return tea.KeyPressMsg(tea.Key{Code: tea.KeySpace, Text: " "})
	case "backspace":
		return tea.KeyPressMsg(tea.Key{Code: tea.KeyBackspace})
	case "ctrl+c":
		return tea.KeyPressMsg(tea.Key{Code: 'c', Mod: tea.ModCtrl})
	case "ctrl+t":
		return tea.KeyPressMsg(tea.Key{Code: 't', Mod: tea.ModCtrl})
	}
	r := []rune(s)
	return tea.KeyPressMsg(tea.Key{Code: r[0], Text: s})
}

// typeInto sends one key press per character of s to an updater.
func typeKeys(s string) []tea.Msg {
	var out []tea.Msg
	for _, r := range s {
		out = append(out, press(string(r)))
	}
	return out
}

// find returns the first message of type T.
func find[T any](msgs []tea.Msg) (T, bool) {
	for _, m := range msgs {
		if v, ok := m.(T); ok {
			return v, true
		}
	}
	var zero T
	return zero, false
}

// drive feeds msgs to a view model and keeps feeding back the messages its
// commands produce (API answers, form and prompt results) until none are
// left. Flash and navigation messages are for the app and are dropped.
func drive[M tea.Model](t *testing.T, m M, msgs ...tea.Msg) M {
	t.Helper()
	queue := append([]tea.Msg(nil), msgs...)
	for i := 0; i < 100 && len(queue) > 0; i++ {
		next := queue[0]
		queue = queue[1:]
		switch next.(type) {
		case ui.FlashMsg, ui.NavigateMsg:
			continue
		}
		updated, cmd := m.Update(next)
		m = updated.(M)
		queue = append(queue, runAll(cmd)...)
	}
	return m
}

// open sizes a view and runs its Init.
func open[M tea.Model](t *testing.T, m M) M {
	t.Helper()
	m = drive(t, m, tea.WindowSizeMsg{Width: 140, Height: 40})
	return drive(t, m, runAll(m.Init())...)
}

func content(m tea.Model) string {
	return stripANSI(m.View().Content)
}
