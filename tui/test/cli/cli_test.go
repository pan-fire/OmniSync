package cli_test

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/pan-fire/OmniSync/tui/internal/cli"
)

func isolate(t *testing.T) {
	t.Helper()
	// os.UserConfigDir: XDG_CONFIG_HOME on Linux, $HOME/Library/Application
	// Support on macOS, %AppData% on Windows. None may be the user's own.
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("HOME", t.TempDir())
	t.Setenv("AppData", t.TempDir())
	for _, k := range []string{"OMNISYNC_URL", "OMNISYNC_API_KEY", "OMNISYNC_THEME", "OMNISYNC_ASCII_MODE", "OMNISYNC_LOG_FILE", "OMNISYNC_NO_MOUSE"} {
		t.Setenv(k, "")
	}
}

// runCLI runs the command line with args and returns stdout, stderr and the
// error Execute returned (non-nil means exit status 1).
func runCLI(t *testing.T, args ...string) (string, string, error) {
	t.Helper()
	cmd := cli.NewRootCommand("osync", "test", "none")
	var out, errOut bytes.Buffer
	cmd.SetOut(&out)
	cmd.SetErr(&errOut)
	cmd.SetArgs(args)
	err := cmd.ExecuteContext(context.Background())
	return out.String(), errOut.String(), err
}

func jsonHandler(status int, body any) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_ = json.NewEncoder(w).Encode(body)
	}
}

func TestHealth_ExitStatusFollowsBackendStatus(t *testing.T) {
	isolate(t)
	for _, tc := range []struct {
		status  string
		wantErr bool
	}{{"ok", false}, {"degraded", true}} {
		srv := httptest.NewServer(jsonHandler(200, map[string]any{
			"status": tc.status, "rclone_installed": false, "uptime_seconds": 1, "database_ok": tc.status == "ok",
		}))
		for _, args := range [][]string{{"health"}, {"health", "--json"}} {
			out, _, err := runCLI(t, append(args, "--url", srv.URL)...)
			if (err != nil) != tc.wantErr {
				t.Errorf("status %s %v: err = %v, want error %v", tc.status, args, err, tc.wantErr)
			}
			if len(args) == 2 {
				var decoded map[string]any
				if json.Unmarshal([]byte(out), &decoded) != nil || decoded["status"] != tc.status {
					t.Errorf("--json output = %q", out)
				}
			}
		}
		// rclone being false alone does not make it unhealthy:
		// the backend's own status decides.
		srv.Close()
	}
}

func TestHealth_Unreachable(t *testing.T) {
	isolate(t)
	if _, _, err := runCLI(t, "health", "--json", "--url", "http://127.0.0.1:1"); err == nil {
		t.Error("expected a non-zero exit when the backend is unreachable")
	}
}

func TestUnauthorized_TellsHowToFix(t *testing.T) {
	isolate(t)
	var auth atomic.Value
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		auth.Store(r.Header.Get("Authorization"))
		jsonHandler(401, map[string]any{"detail": "Not authenticated"})(w, r)
	}))
	defer srv.Close()
	_, _, err := runCLI(t, "profiles", "--url", srv.URL)
	if err == nil || !strings.Contains(err.Error(), "--api-key") || !strings.Contains(err.Error(), "OMNISYNC_API_KEY") {
		t.Errorf("err = %v", err)
	}
	t.Setenv("OMNISYNC_API_KEY", "from-env")
	_, _, _ = runCLI(t, "profiles", "--url", srv.URL)
	if auth.Load() != "Bearer from-env" {
		t.Errorf("Authorization = %v", auth.Load())
	}
	_, _, _ = runCLI(t, "profiles", "--url", srv.URL, "--api-key", "from-flag")
	if auth.Load() != "Bearer from-flag" {
		t.Errorf("Authorization = %v", auth.Load())
	}
}

// syncBackend simulates a profile whose sync takes `duration`; POST
// /sync/start answers only when it is done, like the real backend.
type syncBackend struct {
	mu        sync.Mutex
	running   bool
	finalErr  string
	duration  time.Duration
	dropStart bool
	// accept answers the start at once (202), like the backend does now.
	accept bool
	starts int
}

func (s *syncBackend) handler(t *testing.T) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /profiles/docs/sync/status", func(w http.ResponseWriter, r *http.Request) {
		s.mu.Lock()
		defer s.mu.Unlock()
		st := map[string]any{"state": "idle", "files_processed": 3, "errors": 0}
		if s.running {
			st["state"] = "pushing"
		} else if s.finalErr != "" && s.starts > 0 {
			st["state"] = "error"
			st["last_error"] = s.finalErr
		}
		jsonHandler(200, st)(w, r)
	})
	mux.HandleFunc("POST /profiles/docs/sync/start", func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		s.mu.Lock()
		s.running = true
		s.starts++
		drop := s.dropStart
		s.mu.Unlock()
		finish := func() {
			time.Sleep(s.duration)
			s.mu.Lock()
			s.running = false
			s.mu.Unlock()
		}
		if s.accept {
			go finish()
			jsonHandler(202, map[string]any{"job_id": 5, "state": "pushing"})(w, r)
			return
		}
		if drop {
			go finish()
			hj, ok := w.(http.Hijacker)
			if !ok {
				t.Error("cannot hijack")
				return
			}
			conn, _, _ := hj.Hijack()
			_ = conn.Close() // connection lost while the sync keeps running
			return
		}
		finish()
		state := "idle"
		if s.finalErr != "" {
			state = "error"
		}
		jsonHandler(200, map[string]any{"job_id": 5, "state": state})(w, r)
	})
	return mux
}

func withFastPolling(t *testing.T) {
	t.Helper()
	prev := cli.SyncPollInterval
	cli.SyncPollInterval = 50 * time.Millisecond
	t.Cleanup(func() { cli.SyncPollInterval = prev })
}

// Push waits for the whole sync instead of failing on a timeout.
func TestPush_WaitsForLongSync(t *testing.T) {
	isolate(t)
	withFastPolling(t)
	sb := &syncBackend{duration: 600 * time.Millisecond}
	srv := httptest.NewServer(sb.handler(t))
	defer srv.Close()

	out, _, err := runCLI(t, "push", "docs", "--url", srv.URL)
	if err != nil {
		t.Fatalf("push failed while the sync was still running: %v\n%s", err, out)
	}
	if !strings.Contains(out, "pushing...") || !strings.Contains(out, "push of docs complete") {
		t.Errorf("output:\n%s", out)
	}
}

func TestPush_ReportsFailureWithReason(t *testing.T) {
	isolate(t)
	withFastPolling(t)
	sb := &syncBackend{duration: 50 * time.Millisecond, finalErr: "Sync stopped: it would delete 120 files (limit 50)"}
	srv := httptest.NewServer(sb.handler(t))
	defer srv.Close()

	_, _, err := runCLI(t, "push", "docs", "--url", srv.URL)
	if err == nil || !strings.Contains(err.Error(), "limit 50") {
		t.Errorf("err = %v", err)
	}
}

// If the connection drops while waiting, the command follows the status
// until the sync ends instead of reporting a failure.
func TestPush_FollowsStatusAfterConnectionLoss(t *testing.T) {
	isolate(t)
	withFastPolling(t)
	sb := &syncBackend{duration: 400 * time.Millisecond, dropStart: true}
	srv := httptest.NewServer(sb.handler(t))
	defer srv.Close()

	out, errOut, err := runCLI(t, "push", "docs", "--url", srv.URL)
	if err != nil {
		t.Fatalf("push reported failure although the sync succeeded: %v\n%s%s", err, out, errOut)
	}
	if !strings.Contains(errOut, "following the sync status") || !strings.Contains(out, "complete") {
		t.Errorf("stdout:\n%s\nstderr:\n%s", out, errOut)
	}
}

// The backend answers a start once the sync runs (202); the command then
// follows the status until the sync has ended.
func TestPush_FollowsAnAcceptedSync(t *testing.T) {
	isolate(t)
	withFastPolling(t)
	sb := &syncBackend{duration: 300 * time.Millisecond, accept: true}
	srv := httptest.NewServer(sb.handler(t))
	defer srv.Close()

	out, _, err := runCLI(t, "push", "docs", "--url", srv.URL)
	if err != nil || !strings.Contains(out, "push of docs complete") {
		t.Fatalf("err = %v, output:\n%s", err, out)
	}
	sb.mu.Lock()
	running := sb.running
	sb.mu.Unlock()
	if running {
		t.Error("reported complete while the sync was still running")
	}

	sb = &syncBackend{duration: 100 * time.Millisecond, accept: true, finalErr: "Stopped by user."}
	srv2 := httptest.NewServer(sb.handler(t))
	defer srv2.Close()
	_, _, err = runCLI(t, "push", "docs", "--url", srv2.URL)
	if err == nil || !strings.Contains(err.Error(), "Stopped by user.") {
		t.Errorf("err = %v", err)
	}
}

func TestPull_RefusesWhileRunning(t *testing.T) {
	isolate(t)
	sb := &syncBackend{running: true}
	srv := httptest.NewServer(sb.handler(t))
	defer srv.Close()
	_, _, err := runCLI(t, "pull", "docs", "--url", srv.URL)
	if err == nil || !strings.Contains(err.Error(), "already pushing") {
		t.Errorf("err = %v", err)
	}
	if sb.starts != 0 {
		t.Error("a second sync was started")
	}
}

func TestJobs_EscapesProfileFilter(t *testing.T) {
	isolate(t)
	var query atomic.Value
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		query.Store(r.URL.RawQuery)
		jsonHandler(200, []any{})(w, r)
	}))
	defer srv.Close()
	if _, _, err := runCLI(t, "jobs", "--profile", "a&b", "--url", srv.URL); err != nil {
		t.Fatal(err)
	}
	if q := query.Load(); q != "limit=20&profile=a%26b&skip=0" {
		t.Errorf("query = %v", q)
	}
}
