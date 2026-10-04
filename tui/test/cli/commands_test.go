package cli_test

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/pan-fire/OmniSync/tui/internal/cli"
)

// fixture returns a response fixture generated from the backend's models.
func fixture(t *testing.T, model string) json.RawMessage {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("..", "fixtures", "responses", model+".json"))
	if err != nil {
		t.Fatal(err)
	}
	return data
}

// fixtureList wraps a fixture in a JSON array.
func fixtureList(t *testing.T, model string) json.RawMessage {
	return json.RawMessage("[" + string(fixture(t, model)) + "]")
}

// request is one request the fake backend received.
type request struct {
	Method, Path, RawPath, Query string
	Body                         map[string]any
}

// fakeAPI is a backend whose routes the test sets; it records every request.
type fakeAPI struct {
	t   *testing.T
	mux *http.ServeMux
	mu  sync.Mutex
	got []request
	url string
}

func newFakeAPI(t *testing.T) *fakeAPI {
	isolate(t)
	f := &fakeAPI{t: t, mux: http.NewServeMux()}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		data, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(data, &body)
		r.Body = io.NopCloser(bytes.NewReader(data)) // for handlers that read it too
		f.mu.Lock()
		f.got = append(f.got, request{r.Method, r.URL.Path, r.URL.EscapedPath(), r.URL.RawQuery, body})
		f.mu.Unlock()
		f.mux.ServeHTTP(w, r)
	}))
	t.Cleanup(srv.Close)
	f.url = srv.URL
	return f
}

// on answers pattern with status and body (a json.RawMessage is sent as is).
func (f *fakeAPI) on(pattern string, status int, body any) {
	f.mux.HandleFunc(pattern, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		if raw, ok := body.(json.RawMessage); ok {
			_, _ = w.Write(raw)
			return
		}
		if body != nil {
			_ = json.NewEncoder(w).Encode(body)
		}
	})
}

func (f *fakeAPI) requests() []request {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]request(nil), f.got...)
}

// last returns the last request to method and path, failing if there is none.
func (f *fakeAPI) last(method, path string) request {
	f.t.Helper()
	reqs := f.requests()
	for i := len(reqs) - 1; i >= 0; i-- {
		if reqs[i].Method == method && reqs[i].Path == path {
			return reqs[i]
		}
	}
	f.t.Fatalf("no %s %s among %+v", method, path, reqs)
	return request{}
}

func (f *fakeAPI) run(args ...string) (string, string, int) {
	f.t.Helper()
	out, errOut, err := runCLI(f.t, append(args, "--url", f.url)...)
	return out, errOut, cli.ExitCode(err)
}

func (f *fakeAPI) runInput(input string, args ...string) (string, string, int) {
	f.t.Helper()
	out, errOut, err := runCLIWithInput(f.t, input, append(args, "--url", f.url)...)
	return out, errOut, cli.ExitCode(err)
}

func decodeJSON(t *testing.T, out string, v any) {
	t.Helper()
	if err := json.Unmarshal([]byte(out), v); err != nil {
		t.Fatalf("not one JSON value: %v\n%s", err, out)
	}
}

func TestExitCodes_FollowTheBackendAnswer(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET /profiles/docs", 200, fixture(t, "ProfileStatusResponse"))
	f.on("GET /profiles/gone", 404, map[string]any{"detail": "Profile 'gone' not found"})
	f.on("POST /profiles/docs/sync/resume-intervals", 409, map[string]any{"detail": "Intervals are not paused"})
	f.on("POST /profiles/docs/sync/stop", 500, map[string]any{"detail": "boom"})

	for _, tc := range []struct {
		args []string
		want int
	}{
		{[]string{"profile", "show", "docs"}, cli.ExitOK},
		{[]string{"profile", "show", "gone"}, cli.ExitNotFound},
		{[]string{"profile", "resume", "docs"}, cli.ExitRefused},
		{[]string{"profile", "stop", "docs"}, cli.ExitError},
		{[]string{"profile", "show"}, cli.ExitUsage},
		{[]string{"profile", "show", "a", "b"}, cli.ExitUsage},
		{[]string{"profile", "nonsense"}, cli.ExitUsage},
		{[]string{"nonsense"}, cli.ExitUsage},
		{[]string{"profiles", "--no-such-flag"}, cli.ExitUsage},
		{[]string{"conflicts", "resolve", "5", "--keep", "sideways"}, cli.ExitUsage},
		{[]string{"conflicts", "resolve", "five", "--keep", "local"}, cli.ExitUsage},
		{[]string{"conflicts", "resolve", "5"}, cli.ExitUsage},
		{[]string{"logs", "--level", "LOUD"}, cli.ExitUsage},
		{[]string{"logs", "--limit", "500"}, cli.ExitUsage},
		{[]string{"logs", "--category", "secrets"}, cli.ExitUsage},
		{[]string{"backups", "run", "docs", "x"}, cli.ExitUsage},
		{[]string{"backups", "restore", "docs", "3", "snap", "--scope", "everything", "--yes"}, cli.ExitUsage},
	} {
		_, errOut, code := f.run(tc.args...)
		if code != tc.want {
			t.Errorf("%v: exit %d, want %d (stderr %q)", tc.args, code, tc.want, errOut)
		}
	}
	// Usage errors send nothing.
	for _, r := range f.requests() {
		if strings.Contains(r.Path, "conflicts") || strings.Contains(r.Path, "logs") || strings.Contains(r.Path, "backups") {
			t.Errorf("a usage error sent %s %s", r.Method, r.Path)
		}
	}
	if cli.ExitCode(nil) != cli.ExitOK {
		t.Error("nil error must exit 0")
	}
}

// The commands that existed before keep exit 1 for a failure, and now say
// "not found" and "refused" with 4 and 3 too.
func TestExitCodes_ExistingCommands(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET /profiles/gone/sync/status", 404, map[string]any{"detail": "not running"})
	f.on("GET /profiles/busy/sync/status", 200, map[string]any{"state": "pushing"})
	f.on("GET /profiles/docs", 200, fixture(t, "ProfileStatusResponse"))
	asTerminal(t, false)
	if _, _, code := f.run("push", "gone"); code != cli.ExitNotFound {
		t.Errorf("push of a missing profile: exit %d", code)
	}
	if _, _, code := f.run("pull", "busy"); code != cli.ExitRefused {
		t.Errorf("pull of a busy profile: exit %d", code)
	}
	if _, _, code := f.runInput("", "resync", "docs"); code != cli.ExitRefused {
		t.Errorf("unconfirmed resync: exit %d", code)
	}
}

func TestProfileCommands_CallTheirEndpoints(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET /profiles/docs", 200, fixture(t, "ProfileStatusResponse"))
	f.on("POST /profiles/docs/enable", 200, fixture(t, "ProfileResponse"))
	f.on("POST /profiles/docs/disable", 200, fixture(t, "ProfileResponse"))
	f.on("POST /profiles/docs/sync/stop", 200, fixture(t, "SyncStopResponse"))
	f.on("POST /profiles/docs/sync/resume-intervals", 200, fixture(t, "ResumeIntervalsResponse"))

	for _, tc := range []struct {
		args         []string
		method, path string
	}{
		{[]string{"profile", "show", "docs"}, "GET", "/profiles/docs"},
		{[]string{"profile", "enable", "docs"}, "POST", "/profiles/docs/enable"},
		{[]string{"profile", "disable", "docs"}, "POST", "/profiles/docs/disable"},
		{[]string{"profile", "stop", "docs"}, "POST", "/profiles/docs/sync/stop"},
		{[]string{"profile", "resume", "docs"}, "POST", "/profiles/docs/sync/resume-intervals"},
	} {
		out, errOut, code := f.run(tc.args...)
		if code != 0 || out == "" {
			t.Errorf("%v: exit %d, stdout %q, stderr %q", tc.args, code, out, errOut)
		}
		f.last(tc.method, tc.path)

		out, _, code = f.run(append(tc.args, "--json")...)
		var decoded map[string]any
		decodeJSON(t, out, &decoded)
		if code != 0 || len(decoded) == 0 {
			t.Errorf("%v --json: exit %d, %v", tc.args, code, decoded)
		}
	}

	out, _, _ := f.run("profile", "show", "docs")
	for _, want := range []string{"docs", "two-way", "gdrive:Backup/Dokumente", "pulling"} {
		if !strings.Contains(out, want) {
			t.Errorf("show lacks %q:\n%s", want, out)
		}
	}
}

func TestProfileCheck_ListsDifferencesAndFailsOnError(t *testing.T) {
	f := newFakeAPI(t)
	f.on("POST /profiles/docs/sync/check", 200, map[string]any{
		"has_changes": true, "local_only": []string{"new.txt"}, "remote_only": []string{"cloud.txt"},
		"differ": []string{"both.txt"}, "error": nil,
	})
	f.on("POST /profiles/bad/sync/check", 200, map[string]any{
		"has_changes": false, "local_only": []string{}, "remote_only": []string{}, "differ": []string{},
		"error": "remote unreachable",
	})
	out, _, code := f.run("profile", "check", "docs")
	if code != 0 {
		t.Fatalf("exit %d", code)
	}
	for _, want := range []string{"local only", "new.txt", "remote only", "cloud.txt", "differs", "both.txt"} {
		if !strings.Contains(out, want) {
			t.Errorf("output lacks %q:\n%s", want, out)
		}
	}
	out, errOut, code := f.run("profile", "check", "bad", "--json")
	var decoded map[string]any
	decodeJSON(t, out, &decoded)
	if code != cli.ExitError || decoded["error"] != "remote unreachable" {
		t.Errorf("exit %d, json %v, stderr %q", code, decoded, errOut)
	}
}

func TestProfileDiff_SummaryAndFiles(t *testing.T) {
	f := newFakeAPI(t)
	diff := map[string]any{
		"files": []map[string]any{
			{"path": "a.txt", "category": "local_only", "is_conflict": false, "manual_flag": false},
			{"path": "b.txt", "category": "modified_both", "is_conflict": true, "manual_flag": true},
		},
		"summary":    map[string]any{"local_only": 1, "remote_only": 0, "modified_local": 0, "modified_remote": 0, "modified_both": 1, "manual": 1, "total": 2},
		"pagination": nil, "error": nil,
	}
	f.on("POST /profiles/docs/diff", 200, diff)
	out, _, code := f.run("profile", "diff", "docs")
	if code != 0 {
		t.Fatalf("exit %d", code)
	}
	for _, want := range []string{"Local only", "Total", "a.txt", "changed on both", "conflict, manual"} {
		if !strings.Contains(out, want) {
			t.Errorf("output lacks %q:\n%s", want, out)
		}
	}
	// The whole list in one answer: limit=0.
	if q := f.last("POST", "/profiles/docs/diff").Query; !strings.Contains(q, "limit=0") {
		t.Errorf("query = %q", q)
	}
	f.on("POST /profiles/bad/diff", 200, fixture(t, "DiffResponse")) // carries "error"
	if _, _, code := f.run("profile", "diff", "bad"); code != cli.ExitError {
		t.Errorf("a diff with an error: exit %d", code)
	}
}

func TestProfileManualFlags_ListAndClear(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET /profiles/docs/manual-flags", 200, map[string]any{"flags": []string{"dir/a b.txt"}})
	f.mux.HandleFunc("DELETE /profiles/docs/manual-flags/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/profiles/docs/manual-flags/dir/a b.txt" {
			w.WriteHeader(204)
			return
		}
		jsonHandler(404, map[string]any{"detail": "Manual flag not found for path"})(w, r)
	})
	out, _, code := f.run("profile", "manual-flags", "docs")
	if code != 0 || !strings.Contains(out, "dir/a b.txt") {
		t.Errorf("exit %d:\n%s", code, out)
	}
	out, _, code = f.run("profile", "manual-flags", "docs", "--clear", "dir/a b.txt", "--json")
	var decoded map[string]any
	decodeJSON(t, out, &decoded)
	if code != 0 || decoded["cleared"] != "dir/a b.txt" {
		t.Errorf("exit %d, %v", code, decoded)
	}
	// The '/' stays a separator; the space is escaped.
	if raw := f.last("DELETE", "/profiles/docs/manual-flags/dir/a b.txt").RawPath; raw != "/profiles/docs/manual-flags/dir/a%20b.txt" {
		t.Errorf("raw path = %q", raw)
	}
	if _, _, code := f.run("profile", "manual-flags", "docs", "--clear", "other.txt"); code != cli.ExitNotFound {
		t.Errorf("clearing a missing flag: exit %d", code)
	}
}

func TestConflicts_ListAndResolve(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET /conflicts", 200, fixtureList(t, "ConflictResponse"))
	f.on("POST /conflicts/5/resolve", 200, fixture(t, "ConflictResponse"))
	f.on("POST /conflicts/6/resolve", 409, map[string]any{"detail": "This conflict is already resolved."})
	f.on("POST /conflicts/7/resolve", 404, map[string]any{"detail": "Conflict 7 not found"})

	out, _, code := f.run("conflicts", "--profile", "docs")
	if code != 0 || !strings.Contains(out, "docs/plan.odt") || !strings.Contains(out, "both versions") {
		t.Errorf("exit %d:\n%s", code, out)
	}
	if q := f.last("GET", "/conflicts").Query; q != "profile=docs" {
		t.Errorf("query = %q", q)
	}
	out, _, _ = f.run("conflicts", "--json")
	var list []map[string]any
	decodeJSON(t, out, &list)
	if len(list) != 1 {
		t.Errorf("--json = %v", list)
	}

	for keep, want := range map[string]string{"local": "keep_local", "remote": "keep_remote", "both": "keep_both", "dismiss": "dismiss"} {
		if _, errOut, code := f.run("conflicts", "resolve", "5", "--keep", keep); code != 0 {
			t.Errorf("--keep %s: exit %d %s", keep, code, errOut)
		}
		if got := f.last("POST", "/conflicts/5/resolve").Body["resolution"]; got != want {
			t.Errorf("--keep %s sent %v, want %s", keep, got, want)
		}
	}
	if _, _, code := f.run("conflicts", "resolve", "6", "--keep", "local"); code != cli.ExitRefused {
		t.Errorf("already resolved: exit %d", code)
	}
	if _, _, code := f.run("conflicts", "resolve", "7", "--keep", "local"); code != cli.ExitNotFound {
		t.Errorf("missing conflict: exit %d", code)
	}
}

func TestRemotes_ListTestAbout(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET /remotes", 200, fixtureList(t, "RemoteResponse"))
	f.on("POST /remotes/gdrive/test", 200, map[string]any{"success": true, "latency_ms": 42, "error": nil})
	f.on("POST /remotes/down/test", 200, map[string]any{"success": false, "latency_ms": nil, "error": "token expired"})
	f.on("GET /remotes/gdrive/about", 200, fixture(t, "RemoteStorageInfoResponse"))
	f.on("GET /remotes/local/about", 200, map[string]any{"supported": false})

	if out, _, code := f.run("remotes"); code != 0 || !strings.Contains(out, "NAME") {
		t.Errorf("exit %d:\n%s", code, out)
	}
	if out, _, code := f.run("remotes", "test", "gdrive"); code != 0 || !strings.Contains(out, "42 ms") {
		t.Errorf("exit %d:\n%s", code, out)
	}
	out, _, code := f.run("remotes", "test", "down", "--json")
	var decoded map[string]any
	decodeJSON(t, out, &decoded)
	if code != cli.ExitError || decoded["error"] != "token expired" {
		t.Errorf("failed test: exit %d, %v", code, decoded)
	}
	if out, _, code := f.run("remotes", "about", "gdrive"); code != 0 || !strings.Contains(out, "1000 B") || !strings.Contains(out, "Free") {
		t.Errorf("exit %d:\n%s", code, out)
	}
	if out, _, code := f.run("remotes", "about", "local"); code != 0 || !strings.Contains(out, "does not report") {
		t.Errorf("exit %d:\n%s", code, out)
	}
}

func withFastBackupPolls(t *testing.T) {
	t.Helper()
	prev := cli.BackupPollInterval
	cli.BackupPollInterval = 10 * time.Millisecond
	t.Cleanup(func() { cli.BackupPollInterval = prev })
}

// backupJobJSON is the BackupJobResponse fixture with the given status and
// error code (nil for none).
func backupJobJSON(t *testing.T, status string, code any) map[string]any {
	t.Helper()
	var job map[string]any
	_ = json.Unmarshal(fixture(t, "BackupJobResponse"), &job)
	job["status"] = status
	job["error_code"] = code
	return job
}

// backgroundJob serves a backup or restore of target 3 like the backend: the
// start (POST start) answers 202 at once with job 77 running; GET
// .../jobs/77 answers "running" for the first runningPolls polls, then final
// (with code). It returns how many polls there were.
func (f *fakeAPI) backgroundJob(start string, runningPolls int, final string, code any) *atomic.Int32 {
	f.t.Helper()
	var polls atomic.Int32
	f.on(start, 202, backupJobJSON(f.t, "running", nil))
	f.mux.HandleFunc("GET /profiles/docs/backups/3/jobs/77", func(w http.ResponseWriter, r *http.Request) {
		job := backupJobJSON(f.t, final, code)
		if int(polls.Add(1)) <= runningPolls {
			job = backupJobJSON(f.t, "running", nil)
		}
		jsonHandler(200, job)(w, r)
	})
	return &polls
}

func TestBackups_ListAndSnapshots(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET /profiles/docs/backups", 200, fixtureList(t, "BackupTargetResponse"))
	f.on("GET /profiles/docs/backups/3/snapshots", 200, fixtureList(t, "SnapshotResponse"))
	if out, _, code := f.run("backups", "docs"); code != 0 || !strings.Contains(out, "Nightly") || !strings.Contains(out, "overdue") {
		t.Errorf("exit %d:\n%s", code, out)
	}
	if out, _, code := f.run("backups", "snapshots", "docs", "3"); code != 0 || !strings.Contains(out, "2026-09-27T08-30-00") {
		t.Errorf("exit %d:\n%s", code, out)
	}
	out, _, _ := f.run("backups", "snapshots", "docs", "3", "--json")
	var list []map[string]any
	decodeJSON(t, out, &list)
	if len(list) != 1 || list[0]["latest"] != true {
		t.Errorf("--json = %v", list)
	}
}

func TestBackupRun_WaitPollsTheJobToTheEnd(t *testing.T) {
	f := newFakeAPI(t)
	withFastBackupPolls(t)
	polls := f.backgroundJob("POST /profiles/docs/backups/3/run", 2, "completed", nil)

	out, _, code := f.run("backups", "run", "docs", "3", "--wait")
	if code != 0 || !strings.Contains(out, "job 77") || !strings.Contains(out, "backup of docs completed") ||
		!strings.Contains(out, "snapshot 2026-09-27T08-30-00") {
		t.Errorf("exit %d:\n%s", code, out)
	}
	if polls.Load() != 3 {
		t.Errorf("job polls = %d, want 3", polls.Load())
	}
	out, errOut, code := f.run("backups", "run", "docs", "3", "--wait", "--json")
	var job map[string]any
	decodeJSON(t, out, &job)
	if code != 0 || job["status"] != "completed" || !strings.Contains(errOut, "waiting") {
		t.Errorf("exit %d, %v, stderr %q", code, job, errOut)
	}
}

func TestBackupRun_WaitReportsAFailedOrSkippedJob(t *testing.T) {
	for _, tc := range []struct{ status, code string }{{"failed", "backup_failed"}, {"skipped", "target_unreachable"}} {
		f := newFakeAPI(t)
		withFastBackupPolls(t)
		f.backgroundJob("POST /profiles/docs/backups/3/run", 1, tc.status, tc.code)
		_, errOut, code := f.run("backups", "run", "docs", "3", "--wait")
		if code != cli.ExitError || !strings.Contains(errOut, "partial") || !strings.Contains(errOut, tc.code) {
			t.Errorf("%s backup: exit %d, stderr %q", tc.status, code, errOut)
		}
	}
}

func TestBackupRun_RefusalsAreAnsweredAtOnce(t *testing.T) {
	f := newFakeAPI(t)
	f.on("POST /profiles/docs/backups/3/run", 409, map[string]any{"detail": "Backup already running for this target"})
	f.on("POST /profiles/docs/backups/4/run", 409, map[string]any{"detail": "A sync of this profile is running; try again when it has finished."})
	f.on("POST /profiles/docs/backups/5/run", 404, map[string]any{"detail": "Backup target not found"})
	if _, _, code := f.run("backups", "run", "docs", "3", "--wait"); code != cli.ExitRefused {
		t.Errorf("already running: exit %d", code)
	}
	if _, errOut, code := f.run("backups", "run", "docs", "4", "--wait"); code != cli.ExitRefused || !strings.Contains(errOut, "sync of this profile") {
		t.Errorf("sync busy: exit %d, stderr %q", code, errOut)
	}
	if _, _, code := f.run("backups", "run", "docs", "5"); code != cli.ExitNotFound {
		t.Errorf("no such target: exit %d", code)
	}
	for _, r := range f.requests() {
		if r.Method == "GET" {
			t.Errorf("polled a refused job: %s", r.Path)
		}
	}
}

func TestBackupRun_WithoutWaitPrintsTheStartedJob(t *testing.T) {
	f := newFakeAPI(t)
	polls := f.backgroundJob("POST /profiles/docs/backups/3/run", 100, "completed", nil)
	out, _, code := f.run("backups", "run", "docs", "3", "--json")
	var decoded map[string]any
	decodeJSON(t, out, &decoded)
	if code != 0 || decoded["status"] != "running" || decoded["target_id"] != float64(3) || decoded["job_id"] != float64(77) {
		t.Errorf("exit %d, %v", code, decoded)
	}
	out, _, code = f.run("backups", "run", "docs", "3")
	if code != 0 || !strings.Contains(out, "Started the backup of docs (target 3, job 77)") {
		t.Errorf("exit %d:\n%s", code, out)
	}
	if polls.Load() != 0 {
		t.Errorf("polled %d times without --wait", polls.Load())
	}
}

func TestBackupRestore_ConfirmsFirst(t *testing.T) {
	f := newFakeAPI(t)
	withFastBackupPolls(t)
	f.on("GET /profiles/docs", 200, fixture(t, "ProfileStatusResponse"))
	f.on("GET /profiles/docs/backups/3/snapshots", 200, fixtureList(t, "SnapshotResponse"))
	f.backgroundJob("POST /profiles/docs/backups/3/restore", 1, "completed", nil)
	restore := []string{"backups", "restore", "docs", "3", "2026-09-27T08-30-00", "--scope", "local_only", "--wait"}
	restores := func() int {
		n := 0
		for _, r := range f.requests() {
			if strings.HasSuffix(r.Path, "/restore") {
				n++
			}
		}
		return n
	}

	// A script without --yes is refused and nothing is restored.
	asTerminal(t, false)
	if _, errOut, code := f.runInput("y\n", restore...); code != cli.ExitRefused || !strings.Contains(errOut, "--yes") {
		t.Errorf("exit %d, stderr %q", code, errOut)
	}
	if restores() != 0 {
		t.Fatal("restored without confirmation")
	}

	// On a terminal it asks; "n" cancels.
	asTerminal(t, true)
	_, errOut, code := f.runInput("n\n", restore...)
	if code != cli.ExitRefused || !strings.Contains(errOut, "/home/user/Dokumente") || !strings.Contains(errOut, "removed") {
		t.Errorf("exit %d, stderr %q", code, errOut)
	}
	if restores() != 0 {
		t.Fatal("restored although the user said no")
	}
	if _, _, code := f.runInput("y\n", restore...); code != 0 || restores() != 1 {
		t.Errorf("confirmed restore: exit %d, %d restores", code, restores())
	}

	// --yes skips the question; the request carries snapshot and scope.
	asTerminal(t, false)
	if _, _, code := f.run(append(restore, "--yes")...); code != 0 {
		t.Errorf("--yes: exit %d", code)
	}
	body := f.last("POST", "/profiles/docs/backups/3/restore").Body
	if body["snapshot_id"] != "2026-09-27T08-30-00" || body["restore_scope"] != "local_only" {
		t.Errorf("body = %v", body)
	}
	if _, _, code := f.run("backups", "restore", "docs", "3", "snap", "--yes"); code != cli.ExitUsage {
		t.Errorf("missing --scope: exit %d", code)
	}
}

func TestBackupRestore_WaitReportsAFailedRestore(t *testing.T) {
	f := newFakeAPI(t)
	withFastBackupPolls(t)
	f.backgroundJob("POST /profiles/docs/backups/3/restore", 1, "failed", "restore_failed")
	_, errOut, code := f.run("backups", "restore", "docs", "3", "snap", "--scope", "both", "--yes", "--wait")
	if code != cli.ExitError || !strings.Contains(errOut, "restore of docs failed") || !strings.Contains(errOut, "restore_failed") {
		t.Errorf("exit %d, stderr %q", code, errOut)
	}
}

func TestBackupRestore_BusyProfileIsRefused(t *testing.T) {
	f := newFakeAPI(t)
	f.on("POST /profiles/docs/backups/3/restore", 409, map[string]any{"detail": "A backup of this profile is running."})
	if _, _, code := f.run("backups", "restore", "docs", "3", "snap", "--scope", "both", "--yes", "--wait"); code != cli.ExitRefused {
		t.Errorf("sync busy: exit %d", code)
	}
}

func TestBackupRestore_WithoutWaitPrintsTheStartedJob(t *testing.T) {
	f := newFakeAPI(t)
	polls := f.backgroundJob("POST /profiles/docs/backups/3/restore", 100, "completed", nil)
	out, _, code := f.run("backups", "restore", "docs", "3", "snap", "--scope", "local_only", "--yes")
	if code != 0 || !strings.Contains(out, "Started the restore of docs (target 3, job 77)") || polls.Load() != 0 {
		t.Errorf("exit %d, %d polls:\n%s", code, polls.Load(), out)
	}
}

func TestLogs_LevelLimitAndOrder(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET /logs", 200, []map[string]any{
		{"timestamp": "2026-09-27T08:30:02Z", "level": "ERROR", "message": "newest"},
		{"timestamp": "2026-09-27T08:30:01Z", "level": "ERROR", "message": "oldest"},
	})
	out, _, code := f.run("logs", "--level", "error", "--limit", "2")
	if code != 0 || strings.Index(out, "oldest") > strings.Index(out, "newest") {
		t.Errorf("exit %d, want oldest first:\n%s", code, out)
	}
	if q := f.last("GET", "/logs").Query; q != "level=ERROR&limit=2&skip=0" {
		t.Errorf("query = %q", q)
	}
	out, _, _ = f.run("logs", "--json")
	var list []map[string]any
	decodeJSON(t, out, &list)
	if len(list) != 2 || list[0]["message"] != "oldest" {
		t.Errorf("--json = %v", list)
	}
}

// --category asks the backend for the audit trail or errors; a traceback is
// printed indented under its entry.
func TestLogs_CategoryAndTraceback(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET /logs", 200, []map[string]any{
		{"timestamp": "2026-09-27T08:30:02Z", "level": "ERROR", "message": "Sync crashed", "logger": "backend.engine",
			"request_id": "abcdef123456", "exc": "Traceback (most recent call last):\nValueError: broken"},
	})
	out, _, code := f.run("logs", "--category", "Errors")
	if code != 0 || !strings.Contains(out, "Sync crashed\n    Traceback (most recent call last):\n    ValueError: broken\n") {
		t.Errorf("exit %d:\n%s", code, out)
	}
	if q := f.last("GET", "/logs").Query; q != "category=errors&limit=50&skip=0" {
		t.Errorf("query = %q", q)
	}
}

// --follow polls and prints only entries it has not printed yet, also new
// ones with the same timestamp as the last printed one.
func TestLogs_FollowPrintsOnlyNewEntries(t *testing.T) {
	isolate(t)
	prev := cli.LogsPollInterval
	cli.LogsPollInterval = 30 * time.Millisecond
	t.Cleanup(func() { cli.LogsPollInterval = prev })

	var mu sync.Mutex
	entries := []map[string]any{ // newest first, like the backend
		{"timestamp": "2026-09-27T08:30:01Z", "level": "INFO", "message": "one"},
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		jsonHandler(200, entries)(w, r)
	}))
	defer srv.Close()

	cmd := cli.NewRootCommand("osync", "test", "none")
	var out, errOut bytes.Buffer
	cmd.SetOut(&out)
	cmd.SetErr(&errOut)
	cmd.SetArgs([]string{"logs", "--follow", "--json", "--url", srv.URL})
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- cmd.ExecuteContext(ctx) }()

	time.Sleep(100 * time.Millisecond)
	mu.Lock()
	entries = append([]map[string]any{
		{"timestamp": "2026-09-27T08:30:02Z", "level": "INFO", "message": "three"},
		{"timestamp": "2026-09-27T08:30:01Z", "level": "INFO", "message": "two"},
	}, entries...)
	mu.Unlock()
	time.Sleep(200 * time.Millisecond)
	cancel()
	if err := <-done; err != nil {
		t.Fatalf("follow ended with %v (stderr %q)", err, errOut.String())
	}

	var got []string
	for _, line := range strings.Split(strings.TrimSpace(out.String()), "\n") {
		var e map[string]any
		decodeJSON(t, line, &e)
		got = append(got, e["message"].(string))
	}
	if strings.Join(got, ",") != "one,two,three" {
		t.Errorf("printed %v, want one,two,three", got)
	}
}

func TestNotificationsTest(t *testing.T) {
	f := newFakeAPI(t)
	f.mux.HandleFunc("POST /notifications/test", func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		switch body["channel"] {
		case nil:
			jsonHandler(200, map[string]any{"success": true, "channels_delivered": []string{"webpush"}, "errors": map[string]string{}})(w, r)
		case "host_native":
			jsonHandler(200, map[string]any{"success": false, "channels_delivered": []string{}, "errors": map[string]string{"host_native": "unavailable"}})(w, r)
		default:
			jsonHandler(404, map[string]any{"detail": "Unknown notification channel"})(w, r)
		}
	})
	if out, _, code := f.run("notifications", "test"); code != 0 || !strings.Contains(out, "webpush") {
		t.Errorf("exit %d:\n%s", code, out)
	}
	if body := f.last("POST", "/notifications/test").Body; body != nil {
		t.Errorf("all channels: sent body %v", body)
	}
	out, errOut, code := f.run("notifications", "test", "--channel", "host_native", "--json")
	var decoded map[string]any
	decodeJSON(t, out, &decoded)
	if code != cli.ExitError || decoded["success"] != false || !strings.Contains(errOut, "unavailable") {
		t.Errorf("exit %d, %v, stderr %q", code, decoded, errOut)
	}
	if _, _, code := f.run("notifications", "test", "--channel", "pigeon"); code != cli.ExitNotFound {
		t.Errorf("unknown channel: exit %d", code)
	}
}

func TestCompletion_ScriptsAndProfileSlugs(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET /profiles", 200, fixtureList(t, "ProfileStatusResponse"))
	for _, shell := range []string{"bash", "zsh", "fish"} {
		if out, _, code := f.run("completion", shell); code != 0 || !strings.Contains(out, "osync") {
			t.Errorf("completion %s: exit %d", shell, code)
		}
	}
	// The word being completed comes last, so --url goes before it.
	out, _, _ := runCLI(t, "__complete", "profile", "show", "--url", f.url, "")
	if !strings.Contains(out, "docs") {
		t.Errorf("profile slugs not completed:\n%s", out)
	}
	out, _, _ = runCLI(t, "__complete", "conflicts", "resolve", "1", "--url", f.url, "--keep", "")
	if !strings.Contains(out, "dismiss") {
		t.Errorf("--keep not completed:\n%s", out)
	}
}
