package logging_test

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/logging"
)

// syncBuffer is a bytes.Buffer safe for the HTTP client's goroutines.
type syncBuffer struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (s *syncBuffer) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.Write(p)
}

func (s *syncBuffer) String() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.String()
}

func captureLog(t *testing.T) *syncBuffer {
	t.Helper()
	buf := &syncBuffer{}
	logging.SetOutput(buf)
	t.Cleanup(func() { logging.SetOutput(nil) })
	return buf
}

// With OMNISYNC_LOG_FILE set, API calls are logged at debug level to
// that file.
func TestOpen_WritesDebugRecordsToFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "osync.log")
	closeLog, err := logging.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	}))
	defer srv.Close()
	if _, err = api.NewClient(srv.URL, "secret-key", "t").Health(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err = closeLog(); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	got := string(data)
	if !strings.Contains(got, "level=DEBUG") || !strings.Contains(got, "path=/health") || !strings.Contains(got, "status=200") {
		t.Errorf("log file:\n%s", got)
	}
	if strings.Contains(got, "secret-key") {
		t.Error("the API key was logged")
	}
	// Windows has no such mode bits (its ACLs decide); os.Stat reports 0666.
	if runtime.GOOS != "windows" {
		if info, err := os.Stat(path); err != nil {
			t.Error(err)
		} else if info.Mode().Perm() != 0o600 {
			t.Errorf("log file mode = %v", info.Mode().Perm())
		}
	}
}

// Without a log file nothing is written anywhere.
func TestOpen_EmptyPathDiscards(t *testing.T) {
	closeLog, err := logging.Open("")
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = closeLog() }()
	if logging.L().Enabled(context.Background(), slog.LevelDebug) {
		t.Error("logger enabled without a log file")
	}
}

// The raw body of a malformed response is logged (truncated); the
// error says "unexpected response".
func TestMalformedResponseBodyIsLogged(t *testing.T) {
	buf := captureLog(t)
	body := "<html>proxy error</html>" + strings.Repeat("x", 5000)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(body))
	}))
	defer srv.Close()
	_, err := api.NewClient(srv.URL, "", "t").Health(context.Background())
	if err == nil || !strings.Contains(err.Error(), "unexpected response") {
		t.Fatalf("err = %v", err)
	}
	got := buf.String()
	if !strings.Contains(got, "malformed api response") || !strings.Contains(got, "proxy error") {
		t.Errorf("log:\n%s", got)
	}
	if !strings.Contains(got, "(truncated)") || strings.Count(got, "x") > 3000 {
		t.Error("body not truncated")
	}
}

// Error responses are logged with their status and detail.
func TestErrorResponseIsLogged(t *testing.T) {
	buf := captureLog(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusConflict)
		_, _ = w.Write([]byte(`{"detail":"already running"}`))
	}))
	defer srv.Close()
	_, _ = api.NewClient(srv.URL, "", "t").Health(context.Background())
	if got := buf.String(); !strings.Contains(got, "status=409") || !strings.Contains(got, "already running") {
		t.Errorf("log:\n%s", got)
	}
}

type panicModel struct {
	inUpdate bool
	inCmd    bool
}

func (m panicModel) Init() tea.Cmd { return nil }

func (m panicModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	if m.inUpdate {
		panic("boom in update")
	}
	if m.inCmd {
		return m, tea.Batch(func() tea.Msg { return nil }, func() tea.Msg { panic("boom in cmd") })
	}
	return m, nil
}

func (m panicModel) View() tea.View { return tea.NewView("") }

func mustPanic(t *testing.T, f func()) {
	t.Helper()
	defer func() {
		if recover() == nil {
			t.Error("the panic was swallowed; Bubble Tea must still see it")
		}
	}()
	f()
}

// A panic in Update is logged with its stack and re-raised.
func TestPanicGuard_UpdatePanicLoggedAndReraised(t *testing.T) {
	buf := captureLog(t)
	g := logging.NewPanicGuard(panicModel{inUpdate: true})
	mustPanic(t, func() { g.Update(nil) })
	v, stack, ok := g.Panicked()
	if !ok || v != "boom in update" || len(stack) == 0 {
		t.Fatalf("Panicked() = %v, %d bytes, %v", v, len(stack), ok)
	}
	if got := buf.String(); !strings.Contains(got, "boom in update") || !strings.Contains(got, "goroutine") {
		t.Errorf("log lacks the panic and its stack:\n%s", got)
	}
}

// A panic in a command of a batch is caught too.
func TestPanicGuard_CommandPanicInBatch(t *testing.T) {
	_ = captureLog(t)
	g := logging.NewPanicGuard(panicModel{inCmd: true})
	_, cmd := g.Update(nil)
	batch, ok := cmd().(tea.BatchMsg)
	if !ok || len(batch) != 2 {
		t.Fatalf("batch = %#v", batch)
	}
	_ = batch[0]()
	mustPanic(t, func() { batch[1]() })
	if v, _, ok := g.Panicked(); !ok || v != "boom in cmd" {
		t.Errorf("Panicked() = %v, %v", v, ok)
	}
}

// After a crash the fatal message points at the bug tracker and the
// log file.
func TestPanicGuard_ReportPrintsFatalMessage(t *testing.T) {
	_ = captureLog(t)
	g := logging.NewPanicGuard(panicModel{inUpdate: true})
	mustPanic(t, func() { g.Update(nil) })
	var out bytes.Buffer
	err := g.Report(errors.New("program was killed"), "/tmp/osync.log", &out)
	if !errors.Is(err, logging.ErrFatal) {
		t.Errorf("err = %v", err)
	}
	for _, want := range []string{"fatal error: boom in update", logging.BugReportURL, "/tmp/osync.log"} {
		if !strings.Contains(out.String(), want) {
			t.Errorf("message lacks %q:\n%s", want, out.String())
		}
	}

	// Without a panic the error passes through and nothing is printed.
	clean := logging.NewPanicGuard(panicModel{})
	out.Reset()
	want := errors.New("other")
	if got := clean.Report(want, "", &out); got != want || out.Len() != 0 {
		t.Errorf("Report = %v, printed %q", got, out.String())
	}
}

// The whole path: a panic inside a running program ends Run with an error,
// and Report turns it into the fatal message.
func TestPanicGuard_ProgramRunCrash(t *testing.T) {
	_ = captureLog(t)
	g := logging.NewPanicGuard(panicModel{inUpdate: true})
	p := tea.NewProgram(g, tea.WithInput(nil), tea.WithOutput(&bytes.Buffer{}), tea.WithoutSignals())
	go p.Send(tea.KeyPressMsg{})
	_, err := p.Run()
	if err == nil {
		t.Fatal("Run returned no error after a panic")
	}
	var out bytes.Buffer
	if !errors.Is(g.Report(err, "", &out), logging.ErrFatal) || !strings.Contains(out.String(), "report it at") {
		t.Errorf("report: %q", out.String())
	}
}
