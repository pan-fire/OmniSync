package cli_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/cli"
)

// jsonKeys fails unless out is one JSON object holding every key in keys.
// Scripts read these fields by name, so renaming one breaks them.
func jsonKeys(t *testing.T, out string, keys ...string) map[string]any {
	t.Helper()
	var obj map[string]any
	decodeJSON(t, out, &obj)
	for _, k := range keys {
		if _, ok := obj[k]; !ok {
			t.Errorf("--json output lacks %q: %s", k, out)
		}
	}
	return obj
}

// status: a summary for people, the backend's answer as is for scripts,
// and exit 1 with the envelope's detail when the backend fails.
func TestStatus_TextJSONAndErrors(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET /sync/status/aggregate", 200, fixture(t, "AggregateStatusResponse"))
	out, _, code := f.run("status")
	if code != cli.ExitOK {
		t.Fatalf("exit %d", code)
	}
	for _, want := range []string{"State", "pushing", "1 total, 1 syncing", "8 changes", "Paused", "docs (8 unresolved differences)"} {
		if !strings.Contains(out, want) {
			t.Errorf("output lacks %q:\n%s", want, out)
		}
	}
	out, _, code = f.run("status", "--json")
	obj := jsonKeys(t, out, "overall_state", "total_pending_changes", "paused_profiles", "profiles_summary")
	if code != cli.ExitOK || obj["overall_state"] != "pushing" {
		t.Errorf("exit %d, %v", code, obj)
	}

	g := newFakeAPI(t)
	g.on("GET /sync/status/aggregate", 503, map[string]any{"detail": "Database is locked", "code": "db_busy", "details": map[string]any{}})
	out, errOut, code := g.run("status", "--json")
	if code != cli.ExitError || out != "" || !strings.Contains(errOut, "failed to fetch status") || !strings.Contains(errOut, "Database is locked") {
		t.Errorf("exit %d, stdout %q, stderr %q", code, out, errOut)
	}
}

// jobs: the --profile and --limit flags reach the query; a job without a
// profile shows the filter or "-".
func TestJobs_TextJSONAndFilters(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET /jobs", 200, []any{
		map[string]any{"id": 42, "direction": "push", "status": "completed", "started_at": "2026-09-27T08:30:00Z", "files_changed": 17, "errors": 2, "profile_slug": "docs"},
		map[string]any{"id": 43, "direction": "pull", "status": "failed", "started_at": "2026-09-27T09:00:00Z", "files_changed": 0, "errors": 1, "profile_slug": nil},
	})
	out, _, code := f.run("jobs", "--limit", "5")
	if code != cli.ExitOK || !strings.Contains(out, "ID") || !strings.Contains(out, "42") || !strings.Contains(out, "docs") {
		t.Fatalf("exit %d:\n%s", code, out)
	}
	lines := strings.Split(strings.TrimSpace(out), "\n")
	if len(lines) != 3 || !strings.Contains(lines[2], "43") || !strings.Contains(lines[2], " - ") {
		t.Errorf("a job without a profile:\n%s", out)
	}
	if q := f.last("GET", "/jobs").Query; !strings.Contains(q, "limit=5") || strings.Contains(q, "profile=") {
		t.Errorf("query = %q", q)
	}
	out, _, _ = f.run("jobs", "--profile", "pics")
	if q := f.last("GET", "/jobs").Query; !strings.Contains(q, "profile=pics") || !strings.Contains(out, "pics") {
		t.Errorf("query = %q:\n%s", q, out)
	}
	out, _, code = f.run("jobs", "--json")
	var jobs []map[string]any
	decodeJSON(t, out, &jobs)
	if code != cli.ExitOK || len(jobs) != 2 || jobs[0]["id"] != float64(42) || jobs[1]["profile_slug"] != nil {
		t.Errorf("exit %d, %v", code, jobs)
	}

	g := newFakeAPI(t)
	g.on("GET /jobs", 422, map[string]any{"detail": "limit must be at most 100", "code": "validation_error", "details": map[string]any{}})
	if _, errOut, code := g.run("jobs", "--limit", "500"); code != cli.ExitError || !strings.Contains(errOut, "limit must be at most 100") {
		t.Errorf("exit %d, stderr %q", code, errOut)
	}
}

// A command that only groups others prints its help; anything after it
// that is not a subcommand is a usage error, sent nowhere.
func TestGroupCommands_HelpAndUsage(t *testing.T) {
	f := newFakeAPI(t)
	for _, group := range []string{"notifications", "profile"} {
		out, _, code := f.run(group)
		if code != cli.ExitOK || !strings.Contains(out, "Usage:") {
			t.Errorf("%s: exit %d:\n%s", group, code, out)
		}
	}
	if _, _, code := f.run("notifications", "ring"); code != cli.ExitUsage {
		t.Errorf("unknown subcommand: exit %d", code)
	}
	if reqs := f.requests(); len(reqs) != 0 {
		t.Errorf("sent %v", reqs)
	}
}

// Shell completion offers remote names, profile slugs in first position
// only, and the notification channels; a backend error completes nothing
// instead of failing the shell.
func TestCompletion_RemotesTargetsAndChannels(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET /remotes", 200, []any{map[string]any{"name": "gdrive", "type": "drive"}, map[string]any{"name": "nas", "type": "sftp"}})
	f.on("GET /profiles", 200, fixtureList(t, "ProfileStatusResponse"))
	f.on("GET /notifications/config", 200, fixture(t, "NotificationConfigResponse"))
	complete := func(args ...string) string {
		t.Helper()
		out, _, _ := runCLI(t, append([]string{"__complete", "--url", f.url}, args...)...)
		return out
	}
	if out := complete("remotes", "test", ""); !strings.Contains(out, "gdrive\tdrive") || !strings.Contains(out, "nas\tsftp") {
		t.Errorf("remotes:\n%s", out)
	}
	if out := complete("remotes", "test", "gdrive", ""); strings.Contains(out, "nas") {
		t.Errorf("a second remote offered:\n%s", out)
	}
	if out := complete("backups", "snapshots", ""); !strings.Contains(out, "docs") {
		t.Errorf("profile of backups snapshots:\n%s", out)
	}
	if out := complete("backups", "snapshots", "docs", ""); strings.Contains(out, "docs") {
		t.Errorf("target id offered profiles:\n%s", out)
	}
	if out := complete("notifications", "test", "--channel", ""); !strings.Contains(out, "desktop\nemail\nntfy\nwebhook\n") {
		t.Errorf("channels:\n%s", out)
	}

	down := newFakeAPI(t)
	down.on("GET /remotes", 500, map[string]any{"detail": "boom"})
	down.on("GET /notifications/config", 500, map[string]any{"detail": "boom"})
	out, _, _ := runCLI(t, "__complete", "remotes", "test", "--url", down.url, "")
	if strings.Contains(out, "boom") || !strings.Contains(out, ":4") { // ShellCompDirectiveNoFileComp
		t.Errorf("failed remote completion:\n%s", out)
	}
	out, _, _ = runCLI(t, "__complete", "notifications", "test", "--url", down.url, "--channel", "")
	if strings.Contains(out, "boom") || !strings.Contains(out, ":4") {
		t.Errorf("failed channel completion:\n%s", out)
	}
}

// With no channel enabled nothing is delivered: that is a failure for a
// script checking its alerting, with the reason.
func TestNotificationsTest_NothingEnabled(t *testing.T) {
	f := newFakeAPI(t)
	f.on("POST /notifications/test", 200, map[string]any{"success": false, "channels_delivered": []string{}, "errors": map[string]string{}})
	out, errOut, code := f.run("notifications", "test")
	if code != cli.ExitError || !strings.Contains(out, "none") || !strings.Contains(errOut, "no channel is enabled") {
		t.Errorf("exit %d, stdout %q, stderr %q", code, out, errOut)
	}
	g := newFakeAPI(t)
	g.on("POST /notifications/test", 503, map[string]any{"detail": "Notifications are starting", "code": "unavailable", "details": map[string]any{}})
	if _, errOut, code := g.run("notifications", "test"); code != cli.ExitError || !strings.Contains(errOut, "Notifications are starting") {
		t.Errorf("exit %d, stderr %q", code, errOut)
	}
}

// profile check: "Both folders match", the differences by side, and a
// failed comparison exits 1 with --json still printing the answer.
func TestProfileCheck_MatchDifferencesAndFailure(t *testing.T) {
	f := newFakeAPI(t)
	f.on("POST /profiles/same/sync/check", 200, map[string]any{"has_changes": false, "local_only": []string{}, "remote_only": []string{}, "differ": []string{}, "error": nil})
	f.on("POST /profiles/docs/sync/check", 200, map[string]any{"has_changes": true, "local_only": []string{"a.txt"}, "remote_only": []string{"b.txt"}, "differ": []string{"c.txt"}, "error": nil})
	f.on("POST /profiles/bad/sync/check", 200, map[string]any{"has_changes": false, "local_only": []string{}, "remote_only": []string{}, "differ": []string{}, "error": "remote not reachable"})
	f.on("POST /profiles/gone/sync/check", 404, map[string]any{"detail": "Profile 'gone' not found", "code": "profile_not_found", "details": map[string]any{}})
	if out, _, code := f.run("profile", "check", "same"); code != cli.ExitOK || !strings.Contains(out, "Both folders match.") {
		t.Errorf("exit %d:\n%s", code, out)
	}
	out, _, code := f.run("profile", "check", "docs")
	for _, want := range []string{"local only", "a.txt", "remote only", "b.txt", "differs", "c.txt"} {
		if code != cli.ExitOK || !strings.Contains(out, want) {
			t.Errorf("exit %d, lacks %q:\n%s", code, want, out)
		}
	}
	out, errOut, code := f.run("profile", "check", "bad", "--json")
	obj := jsonKeys(t, out, "has_changes", "local_only", "remote_only", "differ", "error")
	if code != cli.ExitError || obj["error"] != "remote not reachable" || !strings.Contains(errOut, "check of bad failed: remote not reachable") {
		t.Errorf("exit %d, %v, stderr %q", code, obj, errOut)
	}
	if _, _, code := f.run("profile", "check", "gone"); code != cli.ExitNotFound {
		t.Errorf("missing profile: exit %d", code)
	}
}

// profile diff names every category in words.
func TestProfileDiff_NamesEveryCategory(t *testing.T) {
	f := newFakeAPI(t)
	f.on("POST /profiles/docs/diff", 200, map[string]any{
		"files": []map[string]any{
			{"path": "l.txt", "category": "local_only"},
			{"path": "r.txt", "category": "remote_only"},
			{"path": "ml.txt", "category": "modified_local"},
			{"path": "mr.txt", "category": "modified_remote"},
			{"path": "x.txt", "category": "renamed_somehow"},
		},
		"summary": map[string]any{"local_only": 1, "remote_only": 1, "modified_local": 1, "modified_remote": 1, "total": 5},
	})
	out, _, code := f.run("profile", "diff", "docs")
	for _, want := range []string{"local only", "remote only", "changed locally", "changed remotely", "renamed_somehow"} {
		if code != cli.ExitOK || !strings.Contains(out, want) {
			t.Errorf("exit %d, lacks %q:\n%s", code, want, out)
		}
	}
}

// remotes test: a failed connection exits 1 with the reason; --json keeps
// the answer's fields; a missing remote is 4.
func TestRemoteTest_FailureAndNotFound(t *testing.T) {
	f := newFakeAPI(t)
	f.on("POST /remotes/nas/test", 200, map[string]any{"success": false, "latency_ms": nil, "error": "connection refused"})
	f.on("POST /remotes/quiet/test", 200, map[string]any{"success": false, "latency_ms": nil, "error": nil})
	f.on("POST /remotes/fast/test", 200, map[string]any{"success": true, "latency_ms": nil, "error": nil})
	f.on("POST /remotes/gone/test", 404, map[string]any{"detail": "Remote 'gone' not found", "code": "remote_not_found", "details": map[string]any{}})
	out, errOut, code := f.run("remotes", "test", "nas", "--json")
	obj := jsonKeys(t, out, "success", "latency_ms", "error")
	if code != cli.ExitError || obj["success"] != false || !strings.Contains(errOut, "remote nas is not reachable: connection refused") {
		t.Errorf("exit %d, %v, stderr %q", code, obj, errOut)
	}
	if _, errOut, code := f.run("remotes", "test", "quiet"); code != cli.ExitError || !strings.Contains(errOut, "not reachable: -") {
		t.Errorf("exit %d, stderr %q", code, errOut)
	}
	if out, _, code := f.run("remotes", "test", "fast"); code != cli.ExitOK || strings.TrimSpace(out) != "Remote fast is reachable" {
		t.Errorf("exit %d, %q", code, out)
	}
	if _, _, code := f.run("remotes", "test", "gone"); code != cli.ExitNotFound {
		t.Errorf("missing remote: exit %d", code)
	}
}

// The profile list names the mode in words, "-" when the backend sends
// none and the raw value for one this client does not know yet.
func TestProfiles_ModeLabels(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET /profiles", 200, []any{
		map[string]any{"slug": "a", "name": "A", "sync_mode": "", "enabled": true, "state": "idle", "local_dir": "/l", "remote_dir": "r:"},
		map[string]any{"slug": "b", "name": "B", "sync_mode": "three_way", "enabled": true, "state": "idle", "local_dir": "/l", "remote_dir": "r:"},
	})
	out, _, code := f.run("profiles")
	lines := strings.Split(strings.TrimSpace(out), "\n")
	if code != cli.ExitOK || len(lines) != 3 || !strings.Contains(lines[1], " - ") || !strings.Contains(lines[2], "three_way") {
		t.Errorf("exit %d:\n%s", code, out)
	}
}

// sync and resync read the profile first: a missing one is 4, a backend
// failure 1, and neither starts anything.
func TestSync_ProfileReadErrors(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET /profiles/gone", 404, map[string]any{"detail": "Profile 'gone' not found", "code": "profile_not_found", "details": map[string]any{}})
	f.on("GET /profiles/sick", 500, map[string]any{"detail": "Internal error", "code": "internal_error", "details": map[string]any{}})
	if _, errOut, code := f.run("sync", "gone"); code != cli.ExitNotFound || !strings.Contains(errOut, `profile "gone" not found`) {
		t.Errorf("exit %d, stderr %q", code, errOut)
	}
	if _, errOut, code := f.run("resync", "sick", "--yes"); code != cli.ExitError || !strings.Contains(errOut, `cannot read profile "sick"`) {
		t.Errorf("exit %d, stderr %q", code, errOut)
	}
	for _, r := range f.requests() {
		if r.Method == "POST" {
			t.Errorf("started %s", r.Path)
		}
	}
}

// A config file that cannot be parsed is reported, and the command still
// runs with its flags (it used to fall back to the default URL).
func TestConfig_BrokenFileWarnsAndKeepsFlags(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET /health", 200, map[string]any{"status": "ok", "rclone_installed": true, "database_ok": true, "uptime_seconds": 1})
	dir, err := os.UserConfigDir()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(dir, "osync"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "osync", "tui.toml"), []byte("url = [unclosed"), 0o600); err != nil {
		t.Fatal(err)
	}
	_, errOut, code := f.run("health")
	if code != cli.ExitOK || !strings.Contains(errOut, "tui.toml is not used") {
		t.Errorf("exit %d, stderr %q", code, errOut)
	}
}

// The TUI refuses to start, with the reason, when its log file cannot be
// opened; it never takes over the terminal in that case.
func TestTUI_UnopenableLogFile(t *testing.T) {
	isolate(t)
	notADir := filepath.Join(t.TempDir(), "file")
	if err := os.WriteFile(notADir, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("OMNISYNC_LOG_FILE", filepath.Join(notADir, "osync.log"))
	_, errOut, err := runCLI(t, "--url", "http://127.0.0.1:1")
	if cli.ExitCode(err) != cli.ExitError || !strings.Contains(errOut, "failed to open log file") {
		t.Errorf("exit %d, stderr %q", cli.ExitCode(err), errOut)
	}
}
