package cli_test

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/cli"
)

// syncBuffer is the terminal the TUI draws on while the test reads it.
type syncBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *syncBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *syncBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

// runTUI runs osync without a subcommand (the TUI) on an in-memory
// terminal. Once the screen shows waitFor, it types keys; it returns what
// the TUI drew and the error the command returned.
func runTUI(t *testing.T, waitFor, keys string, args ...string) (string, error) {
	t.Helper()
	in, typed := io.Pipe()
	screen := &syncBuffer{}
	prev := cli.TUIProgramOptions
	cli.TUIProgramOptions = []tea.ProgramOption{tea.WithInput(in), tea.WithOutput(screen), tea.WithWindowSize(120, 30)}
	t.Cleanup(func() { cli.TUIProgramOptions = prev })

	cmd := cli.NewRootCommand("osync", "test", "none")
	cmd.SetArgs(args)
	var stderr bytes.Buffer
	cmd.SetErr(&stderr)
	done := make(chan error, 1)
	go func() { done <- cmd.ExecuteContext(context.Background()) }()

	deadline := time.Now().Add(10 * time.Second)
	for !strings.Contains(screen.String(), waitFor) {
		select {
		case err := <-done:
			_ = typed.Close()
			return screen.String(), err
		default:
		}
		if time.Now().After(deadline) {
			_ = typed.Close()
			t.Fatalf("the TUI never showed %q:\n%q", waitFor, screen.String())
		}
		time.Sleep(10 * time.Millisecond) // polling the screen, not synchronising
	}
	if _, err := io.WriteString(typed, keys); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		_ = typed.Close()
		return screen.String(), err
	case <-time.After(10 * time.Second):
		_ = typed.Close()
		t.Fatalf("the TUI did not quit after %q:\n%q", keys, screen.String())
	}
	return "", nil
}

// healthyBackend answers every request with a healthy /health body, so
// the TUI gets past its connection check.
func healthyBackend(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(jsonHandler(http.StatusOK, map[string]any{
		"status": "ok", "rclone_installed": true, "uptime_seconds": 5, "database_ok": true,
	}))
	t.Cleanup(srv.Close)
	return srv
}

// osync without a subcommand starts the TUI against the backend from
// --url: it draws the app with its views and quits on q, y without an
// error.
func TestTUI_StartsDrawsAndQuits(t *testing.T) {
	isolate(t)
	srv := healthyBackend(t)
	logFile := filepath.Join(t.TempDir(), "osync.log")
	t.Setenv("OMNISYNC_LOG_FILE", logFile)

	screen, err := runTUI(t, "Dashboard", "qy", "--url", srv.URL, "--no-mouse")
	if err != nil {
		t.Fatalf("err = %v", err)
	}
	for _, want := range []string{"OmniSync", "Dashboard", "Profiles", "Logs", "Quit"} {
		if !strings.Contains(screen, want) {
			t.Errorf("the screen lacks %q:\n%q", want, screen)
		}
	}
	// The debug log goes to the file, never to the terminal.
	data, err := os.ReadFile(logFile)
	if err != nil || !strings.Contains(string(data), "osync starting") {
		t.Errorf("log file = %q, %v", data, err)
	}
	if strings.Contains(screen, "osync starting") {
		t.Error("the debug log reached the terminal")
	}
}

// Ctrl+C quits at once, without the quit prompt.
func TestTUI_CtrlCQuits(t *testing.T) {
	isolate(t)
	srv := healthyBackend(t)
	if _, err := runTUI(t, "Dashboard", "\x03", "--url", srv.URL); err != nil {
		t.Fatalf("err = %v", err)
	}
}
