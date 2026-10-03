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
	"testing"
	"time"

	"github.com/pan-fire/OmniSync/tui/internal/cli"
)

// twoWayBackend simulates one profile "docs" for sync and resync: GET
// /profiles/docs, the status, and the two start endpoints. A start takes
// duration; with async it answers at once with state "syncing" (the sync
// then runs in the background), otherwise only when the sync is done.
type twoWayBackend struct {
	mu             sync.Mutex
	mode           string
	resyncRequired bool
	finalErr       string
	duration       time.Duration
	async          bool
	running        bool
	finished       bool
	starts         []string // path of each start request
	bodies         []map[string]any
}

func (b *twoWayBackend) handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /profiles/docs", func(w http.ResponseWriter, r *http.Request) {
		b.mu.Lock()
		defer b.mu.Unlock()
		p := map[string]any{
			"id": 1, "slug": "docs", "name": "Docs", "local_dir": "/home/u/Docs", "remote_dir": "gdrive:Docs",
			"enabled": true, "sync_mode": b.mode, "state": "idle", "resync_required": b.resyncRequired,
		}
		if b.resyncRequired {
			p["last_error"] = "bisync state lost"
		}
		jsonHandler(200, p)(w, r)
	})
	mux.HandleFunc("GET /profiles/docs/sync/status", func(w http.ResponseWriter, r *http.Request) {
		b.mu.Lock()
		defer b.mu.Unlock()
		st := map[string]any{"state": "idle", "files_processed": 4, "errors": 0}
		switch {
		case b.running:
			st["state"] = "syncing"
		case b.finished && b.finalErr != "":
			st["state"] = "error"
			st["last_error"] = b.finalErr
		}
		jsonHandler(200, st)(w, r)
	})
	start := func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		b.mu.Lock()
		b.running = true
		b.starts = append(b.starts, r.URL.Path)
		b.bodies = append(b.bodies, body)
		async := b.async
		b.mu.Unlock()
		finish := func() {
			time.Sleep(b.duration)
			b.mu.Lock()
			b.running, b.finished = false, true
			b.mu.Unlock()
		}
		if async {
			go finish()
			jsonHandler(202, map[string]any{"job_id": 9, "state": "syncing"})(w, r)
			return
		}
		finish()
		state := "idle"
		if b.finalErr != "" {
			state = "error"
		}
		jsonHandler(200, map[string]any{"job_id": 9, "state": state})(w, r)
	}
	mux.HandleFunc("POST /profiles/docs/sync/start", start)
	mux.HandleFunc("POST /profiles/docs/sync/resync", start)
	return mux
}

func (b *twoWayBackend) startCount() int {
	b.mu.Lock()
	defer b.mu.Unlock()
	return len(b.starts)
}

func serveTwoWay(t *testing.T, b *twoWayBackend) string {
	t.Helper()
	srv := httptest.NewServer(b.handler())
	t.Cleanup(srv.Close)
	return srv.URL
}

// runCLIWithInput is runCLI with stdin set to input.
func runCLIWithInput(t *testing.T, input string, args ...string) (string, string, error) {
	t.Helper()
	cmd := cli.NewRootCommand("osync", "test", "none")
	var out, errOut bytes.Buffer
	cmd.SetOut(&out)
	cmd.SetErr(&errOut)
	cmd.SetIn(strings.NewReader(input))
	cmd.SetArgs(args)
	err := cmd.ExecuteContext(context.Background())
	return out.String(), errOut.String(), err
}

// asTerminal makes the CLI treat stdin as a terminal (or not).
func asTerminal(t *testing.T, tty bool) {
	t.Helper()
	prev := cli.IsTerminal
	cli.IsTerminal = func(io.Reader) bool { return tty }
	t.Cleanup(func() { cli.IsTerminal = prev })
}

func TestSync_RunsTwoWaySyncAndWaits(t *testing.T) {
	isolate(t)
	withFastPolling(t)
	b := &twoWayBackend{mode: "two_way", duration: 300 * time.Millisecond}
	url := serveTwoWay(t, b)

	out, _, err := runCLI(t, "sync", "docs", "--url", url)
	if err != nil {
		t.Fatalf("sync failed: %v\n%s", err, out)
	}
	if len(b.bodies) != 1 || b.starts[0] != "/profiles/docs/sync/start" ||
		b.bodies[0]["direction"] != "two_way" || b.bodies[0]["force"] != nil {
		t.Errorf("starts %v bodies %v, want one two_way start without force", b.starts, b.bodies)
	}
	if !strings.Contains(out, "syncing...") || !strings.Contains(out, "two-way sync of docs complete (files: 4, errors: 0)") {
		t.Errorf("output:\n%s", out)
	}
}

// --json prints one object on stdout; progress goes to stderr.
func TestSync_JSONOutput(t *testing.T) {
	isolate(t)
	withFastPolling(t)
	b := &twoWayBackend{mode: "two_way", duration: 200 * time.Millisecond}
	url := serveTwoWay(t, b)

	out, errOut, err := runCLI(t, "sync", "docs", "--json", "--url", url)
	if err != nil {
		t.Fatal(err)
	}
	var res map[string]any
	if jerr := json.Unmarshal([]byte(out), &res); jerr != nil {
		t.Fatalf("stdout is not one JSON value: %v\n%s", jerr, out)
	}
	if res["profile"] != "docs" || res["action"] != "two_way" || res["state"] != "idle" ||
		res["job_id"] != float64(9) || res["files_processed"] != float64(4) || res["last_error"] != nil {
		t.Errorf("result = %v", res)
	}
	if !strings.Contains(errOut, "Starting two-way sync for docs") {
		t.Errorf("progress not on stderr:\n%s", errOut)
	}
}

func TestSync_FailureExitsNonZeroWithReason(t *testing.T) {
	isolate(t)
	withFastPolling(t)
	b := &twoWayBackend{mode: "two_way", duration: 50 * time.Millisecond, finalErr: "Sync stopped: would delete 80 files on the remote (limit 50)"}
	url := serveTwoWay(t, b)

	out, _, err := runCLI(t, "sync", "docs", "--json", "--url", url)
	if err == nil || !strings.Contains(err.Error(), "limit 50") {
		t.Errorf("err = %v", err)
	}
	var res map[string]any
	if json.Unmarshal([]byte(out), &res) != nil || res["state"] != "error" || !strings.Contains(res["last_error"].(string), "limit 50") {
		t.Errorf("--json output = %q", out)
	}
}

// A start that is accepted and finishes later (202 with a busy state) is
// followed until the sync ends.
func TestSync_FollowsAnAcceptedSync(t *testing.T) {
	isolate(t)
	withFastPolling(t)
	b := &twoWayBackend{mode: "two_way", duration: 300 * time.Millisecond, async: true}
	url := serveTwoWay(t, b)

	out, _, err := runCLI(t, "sync", "docs", "--url", url)
	if err != nil {
		t.Fatalf("sync failed: %v\n%s", err, out)
	}
	b.mu.Lock()
	finished := b.finished
	b.mu.Unlock()
	if !finished || !strings.Contains(out, "two-way sync of docs complete") {
		t.Errorf("returned before the sync finished (finished %v):\n%s", finished, out)
	}
}

func TestSync_RefusesMirrorProfile(t *testing.T) {
	isolate(t)
	b := &twoWayBackend{mode: "mirror"}
	url := serveTwoWay(t, b)
	_, _, err := runCLI(t, "sync", "docs", "--url", url)
	if err == nil || !strings.Contains(err.Error(), "mirror mode") || !strings.Contains(err.Error(), "two-way") {
		t.Errorf("err = %v", err)
	}
	if b.startCount() != 0 {
		t.Error("a sync was started for a mirror profile")
	}
}

func TestSync_RefusesWhileResyncRequired(t *testing.T) {
	isolate(t)
	b := &twoWayBackend{mode: "two_way", resyncRequired: true}
	url := serveTwoWay(t, b)
	_, _, err := runCLI(t, "sync", "docs", "--url", url)
	if err == nil || !strings.Contains(err.Error(), "resync docs") || !strings.Contains(err.Error(), "bisync state lost") {
		t.Errorf("err = %v", err)
	}
	if b.startCount() != 0 {
		t.Error("a sync was started although a resync is required")
	}
}

func TestResync_YesStartsWithoutAsking(t *testing.T) {
	isolate(t)
	withFastPolling(t)
	asTerminal(t, false)
	b := &twoWayBackend{mode: "two_way", resyncRequired: true, duration: 100 * time.Millisecond}
	url := serveTwoWay(t, b)

	out, errOut, err := runCLIWithInput(t, "", "resync", "docs", "--yes", "--json", "--url", url)
	if err != nil {
		t.Fatalf("resync failed: %v\n%s%s", err, out, errOut)
	}
	if len(b.starts) != 1 || b.starts[0] != "/profiles/docs/sync/resync" || b.bodies[0]["confirm"] != true {
		t.Errorf("starts %v bodies %v", b.starts, b.bodies)
	}
	var res map[string]any
	if json.Unmarshal([]byte(out), &res) != nil || res["action"] != "resync" || res["state"] != "idle" {
		t.Errorf("--json output = %q", out)
	}
	if strings.Contains(errOut, "[y/N]") {
		t.Errorf("asked although --yes was given:\n%s", errOut)
	}
}

// Without --yes and without a terminal, resync refuses and changes nothing.
func TestResync_RefusesWithoutTerminal(t *testing.T) {
	isolate(t)
	asTerminal(t, false)
	b := &twoWayBackend{mode: "two_way"}
	url := serveTwoWay(t, b)

	_, _, err := runCLIWithInput(t, "y\n", "resync", "docs", "--url", url)
	if err == nil || !strings.Contains(err.Error(), "--yes") {
		t.Errorf("err = %v", err)
	}
	if b.startCount() != 0 {
		t.Error("resync started without confirmation")
	}
}

func TestResync_AsksOnTerminal(t *testing.T) {
	isolate(t)
	withFastPolling(t)
	asTerminal(t, true)
	for _, tc := range []struct {
		answer string
		starts int
	}{{"y\n", 1}, {"yes\n", 1}, {"n\n", 0}, {"\n", 0}, {"", 0}} {
		b := &twoWayBackend{mode: "two_way", duration: 50 * time.Millisecond}
		url := serveTwoWay(t, b)
		out, errOut, err := runCLIWithInput(t, tc.answer, "resync", "docs", "--url", url)
		if !strings.Contains(errOut, "nothing is deleted") || !strings.Contains(errOut, "Resync now? [y/N]") {
			t.Errorf("answer %q: prompt:\n%s", tc.answer, errOut)
		}
		if b.startCount() != tc.starts {
			t.Errorf("answer %q: %d starts, want %d", tc.answer, b.startCount(), tc.starts)
		}
		if tc.starts == 1 && (err != nil || !strings.Contains(out, "resync of docs complete")) {
			t.Errorf("answer %q: err %v, output:\n%s", tc.answer, err, out)
		}
		if tc.starts == 0 && (err == nil || !strings.Contains(errOut, "nothing was changed")) {
			t.Errorf("answer %q: declined resync must exit non-zero, err %v", tc.answer, err)
		}
	}
}

func TestResync_RefusesMirrorProfile(t *testing.T) {
	isolate(t)
	asTerminal(t, true)
	b := &twoWayBackend{mode: "mirror"}
	url := serveTwoWay(t, b)
	_, errOut, err := runCLIWithInput(t, "y\n", "resync", "docs", "--url", url)
	if err == nil || !strings.Contains(err.Error(), "mirror mode") {
		t.Errorf("err = %v", err)
	}
	if b.startCount() != 0 || strings.Contains(errOut, "[y/N]") {
		t.Errorf("asked or started for a mirror profile:\n%s", errOut)
	}
}

func TestProfiles_ShowsMode(t *testing.T) {
	isolate(t)
	srv := httptest.NewServer(jsonHandler(200, []any{
		map[string]any{"slug": "docs", "name": "Docs", "sync_mode": "two_way", "enabled": true, "state": "idle"},
		map[string]any{"slug": "pics", "name": "Pics", "sync_mode": "mirror", "enabled": true, "state": "idle"},
	}))
	defer srv.Close()
	out, _, err := runCLI(t, "profiles", "--url", srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(out), "\n")
	if len(lines) != 3 || !strings.Contains(lines[0], "MODE") ||
		!strings.Contains(lines[1], "two-way") || !strings.Contains(lines[2], "mirror") {
		t.Errorf("output:\n%s", out)
	}
	out, _, _ = runCLI(t, "profiles", "--json", "--url", srv.URL)
	var decoded []map[string]any
	if json.Unmarshal([]byte(out), &decoded) != nil || decoded[1]["sync_mode"] != "mirror" {
		t.Errorf("--json output = %q", out)
	}
}
