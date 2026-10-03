package ui_test

import (
	"fmt"
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// --- Remotes ---

func remotesBackend(t *testing.T) *backend {
	t.Helper()
	b := newBackend(t)
	b.json("GET", "/remotes", 200, []any{
		map[string]any{"name": "gdrive", "type": "drive", "last_verified": nil},
		map[string]any{"name": "s3", "type": "s3", "last_verified": nil},
	})
	b.json("GET", "/remotes/gdrive/dependencies", 200, map[string]any{
		"profiles":       []any{map[string]any{"slug": "docs", "name": "Dokumente"}},
		"backup_targets": []any{},
	})
	b.json("GET", "/remotes/s3/dependencies", 200, map[string]any{"profiles": []any{}, "backup_targets": []any{}})
	b.json("DELETE", "/remotes/gdrive", 200, map[string]any{"detail": "deleted"})
	b.json("DELETE", "/remotes/s3", 200, map[string]any{"detail": "deleted"})
	b.json("POST", "/remotes/s3/test", 200, map[string]any{"success": true, "latency_ms": 42, "error": nil})
	return b
}

// The prompt names what uses the remote, and the
// delete acts on the row captured when it opened.
func TestRemotes_DeleteShowsDependenciesAndUsesCapturedRow(t *testing.T) {
	b := remotesBackend(t)
	m := open(t, ui.NewRemotesModel(b.client()))
	m = drive(t, m, press("d")) // gdrive
	v := content(m)
	if !strings.Contains(v, "Delete remote \"gdrive\"") || !strings.Contains(v, "profile Dokumente (docs)") {
		t.Fatalf("prompt:\n%s", v)
	}
	// Poll reorders the rows; the prompt still means gdrive.
	m = drive(t, m, ui.PollResultMsg{ViewID: ui.ViewRemotes, Data: []api.RemoteResponse{{Name: "s3"}, {Name: "gdrive"}}})
	_ = drive(t, m, press("y"))
	if got := b.matching("DELETE"); len(got) != 1 || got[0] != "DELETE /remotes/gdrive?force=true" {
		t.Errorf("delete = %v", got)
	}
}

func TestRemotes_DeleteWithoutDependenciesNoForce(t *testing.T) {
	b := remotesBackend(t)
	m := open(t, ui.NewRemotesModel(b.client()))
	m = drive(t, m, press("down"), press("d"))
	m = drive(t, m, press("y"))
	if got := b.matching("DELETE"); len(got) != 1 || got[0] != "DELETE /remotes/s3" {
		t.Errorf("delete = %v", got)
	}
	_ = m
}

func TestRemotes_TestShowsLatency(t *testing.T) {
	b := remotesBackend(t)
	m := open(t, ui.NewRemotesModel(b.client()))
	m = drive(t, m, press("down"), press("t"))
	if !strings.Contains(content(m), "Test s3: PASS (42ms)") {
		t.Errorf("view:\n%s", content(m))
	}
}

// --- Conflicts ---

func conflictsBackend(t *testing.T) *backend {
	t.Helper()
	b := newBackend(t)
	b.json("GET", "/conflicts", 200, []any{
		map[string]any{"id": 1, "job_id": 7, "file_path": "a.txt", "local_modified": "2026-09-27T08:00:00Z", "remote_modified": nil,
			"resolved": false, "resolution": nil, "profile_slug": "docs", "profile_name": "Dokumente"},
		map[string]any{"id": 2, "job_id": nil, "file_path": "docs/plan.odt", "local_modified": "2026-09-27T08:00:00Z",
			"remote_modified": "2026-09-27T09:45:30Z", "resolved": false, "resolution": nil, "profile_slug": "work", "profile_name": "Arbeit"},
	})
	b.json("POST", "/conflicts/2/resolve", 200, map[string]any{"id": 2, "job_id": nil, "file_path": "docs/plan.odt", "resolved": true})
	return b
}

func TestConflicts_ListShowsProfileAndTimes(t *testing.T) {
	b := conflictsBackend(t)
	m := open(t, ui.NewConflictsModel(b.client()))
	v := content(m)
	for _, want := range []string{"Profile", "Dokumente", "Arbeit", "docs/plan.odt", "a.txt"} {
		if !strings.Contains(v, want) {
			t.Errorf("list lacks %q:\n%s", want, v)
		}
	}
	m = drive(t, m, press("down"), press("enter"))
	v = content(m)
	for _, want := range []string{"Resolve conflict 2: docs/plan.odt", "Arbeit", "Local modified:", "Remote modified:",
		"Keep local:", "copy the local file over the remote one (remote version to .omnisync-trash)",
		"Keep remote:", "copy the remote file over the local one (local version to .omnisync-trash)",
		"Keep both:", "keep both versions on both sides", "plan.conflict-<time>.odt",
		"Dismiss:", "without changing any file", "Esc"} {
		if !strings.Contains(v, want) {
			t.Errorf("choices lack %q:\n%s", want, v)
		}
	}
	if strings.Contains(v, "only marks") {
		t.Errorf("stale wording:\n%s", v)
	}
	// The confirmation spells out what happens to the files.
	for key, want := range map[string]string{
		"l": "the remote version is moved to .omnisync-trash on the remote",
		"r": "the local version is moved to .omnisync-trash in the local folder",
		"b": "the remote version is saved next to the local one as plan.conflict-<time>.odt",
		"d": "close this conflict without changing any file",
	} {
		m = drive(t, m, press(key))
		if v := content(m); !strings.Contains(v, want) {
			t.Errorf("%s: confirmation lacks %q:\n%s", key, want, v)
		}
		m = drive(t, m, press("n")) // the answer must arrive before Enter
		m = drive(t, m, press("enter"))
	}
	if len(b.matching("POST")) != 0 {
		t.Errorf("cancelled confirmations sent %v", b.matching("POST"))
	}
}

// Each action asks first, naming the file and profile, then sends its
// resolution for the conflict captured on Enter.
func TestConflicts_EachActionConfirmsThenSendsResolution(t *testing.T) {
	for key, want := range map[string]string{"l": "keep_local", "r": "keep_remote", "b": "keep_both", "d": "dismiss"} {
		t.Run(want, func(t *testing.T) {
			b := conflictsBackend(t)
			m := open(t, ui.NewConflictsModel(b.client()))
			m = drive(t, m, press("down"), press("enter"))
			// A refresh reorders the list; the captured conflict stays.
			m = drive(t, m, ui.PollResultMsg{ViewID: ui.ViewConflicts, Data: []api.ConflictResponse{{ID: 3, FilePath: "c.txt"}, {ID: 2, FilePath: "docs/plan.odt"}, {ID: 1, FilePath: "a.txt"}}})
			m = drive(t, m, press(key))
			if len(b.matching("POST")) != 0 {
				t.Fatal("resolved before confirmation")
			}
			v := content(m)
			if !m.CapturesInput() || !strings.Contains(v, `"docs/plan.odt"`) || !strings.Contains(v, `profile "Arbeit" (work)`) || !strings.Contains(v, "[y]es") {
				t.Fatalf("confirmation:\n%s", v)
			}
			b.reset()
			m = drive(t, m, press("y"))
			got := b.matching("POST")
			if len(got) != 1 || got[0] != "POST /conflicts/2/resolve" {
				t.Fatalf("resolve = %v", got)
			}
			if body := bodyOf(b, "POST /conflicts/2/resolve"); body["resolution"] != want {
				t.Errorf("body = %v, want %s", body, want)
			}
			if len(b.matching("GET /conflicts")) == 0 {
				t.Error("list not refreshed after resolving")
			}
			if m.CapturesInput() {
				t.Error("still capturing input after resolving")
			}
		})
	}
}

func TestConflicts_CancelSendsNothing(t *testing.T) {
	b := conflictsBackend(t)
	m := open(t, ui.NewConflictsModel(b.client()))
	// 'n' at the confirmation.
	m = drive(t, m, press("enter"), press("l"), press("n"))
	// Esc at the confirmation.
	m = drive(t, m, press("enter"), press("r"), press("esc"))
	// Esc at the choices.
	m = drive(t, m, press("enter"), press("esc"), press("l"))
	if len(b.matching("POST")) != 0 || m.CapturesInput() {
		t.Errorf("cancel sent %v or left input captured", b.matching("POST"))
	}
}

// 409 and 502 answers show the backend's reason.
func TestConflicts_ShowsBackendDetailOnFailure(t *testing.T) {
	for status, detail := range map[int]string{
		409: "The remote file changed since the conflict was found; not overwriting it unseen. Run the diff again to refresh the conflict.",
		502: "Could not resolve the conflict: rclone copy failed",
	} {
		b := conflictsBackend(t)
		b.json("POST", "/conflicts/2/resolve", status, map[string]any{"detail": detail})
		m := open(t, ui.NewConflictsModel(b.client()))
		m = drive(t, m, press("down"), press("enter"), press("l"))
		updated, cmd := m.Update(press("y"))
		m = updated.(ui.ConflictsModel)
		var flashes []string
		queue := runAll(cmd)
		for i := 0; i < 20 && len(queue) > 0; i++ {
			next := queue[0]
			queue = queue[1:]
			if f, ok := next.(ui.FlashMsg); ok {
				flashes = append(flashes, f.Text)
				continue
			}
			updated, c := m.Update(next)
			m = updated.(ui.ConflictsModel)
			queue = append(queue, runAll(c)...)
		}
		if v := content(m); !strings.Contains(strings.Join(strings.Fields(v), " "), detail) {
			t.Errorf("%d: view lacks the detail:\n%s", status, v)
		}
		if !strings.Contains(strings.Join(flashes, "\n"), detail) {
			t.Errorf("%d: flash lacks the detail: %v", status, flashes)
		}
	}
}

// --- Jobs ---

func jobsJSON(n, start int) []any {
	out := []any{}
	for i := 0; i < n; i++ {
		out = append(out, map[string]any{"id": start - i, "direction": "push", "started_at": "2026-09-27T08:00:00Z",
			"finished_at": nil, "status": "completed", "files_changed": 1, "conflicts": 0, "errors": 0,
			"profile_slug": "docs", "profile_name": "Dokumente"})
	}
	return out
}

// The filter cycles through real slugs and never sends profile=all.
func TestJobs_FilterCyclesSlugs(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/jobs", 200, jobsJSON(3, 100))
	b.json("GET", "/profiles", 200, []any{profileJSON("docs", "D", "idle"), profileJSON("pics", "P", "idle")})
	m := open(t, ui.NewJobsModel(b.client()))
	if !strings.Contains(content(m), "Dokumente") {
		t.Errorf("profile column empty:\n%s", content(m))
	}
	_ = drive(t, m, press("f"), press("f"), press("f"))
	var queries []string
	for _, r := range b.matching("GET /jobs") {
		if strings.Contains(r, "profile=all") {
			t.Errorf("sent %s", r)
		}
		queries = append(queries, r)
	}
	want := []string{
		"GET /jobs?limit=20&skip=0",
		"GET /jobs?limit=20&profile=docs&skip=0",
		"GET /jobs?limit=20&profile=pics&skip=0",
		"GET /jobs?limit=20&skip=0",
	}
	if fmt.Sprint(queries) != fmt.Sprint(want) {
		t.Errorf("queries = %v, want %v", queries, want)
	}
}

// n/N page through the job history on the server.
func TestJobs_Paging(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/jobs", 200, jobsJSON(20, 100))
	b.json("GET", "/profiles", 200, []any{})
	m := open(t, ui.NewJobsModel(b.client()))
	m = drive(t, m, press("n"))
	if !strings.Contains(content(m), "Page 2") {
		t.Errorf("view:\n%s", content(m))
	}
	_ = drive(t, m, press("N"), press("N"))
	got := b.matching("GET /jobs")
	want := []string{"GET /jobs?limit=20&skip=0", "GET /jobs?limit=20&skip=20", "GET /jobs?limit=20&skip=0"}
	if fmt.Sprint(got) != fmt.Sprint(want) {
		t.Errorf("queries = %v, want %v", got, want)
	}
}

// --- Logs ---

// The backend sends newest first; the view shows the newest lines.
func TestLogs_ShowsNewestLines(t *testing.T) {
	b := newBackend(t)
	entries := []any{}
	for i := 200; i > 0; i-- { // newest first
		entries = append(entries, map[string]any{"timestamp": "2026-09-27T08:00:00Z", "level": "INFO", "message": fmt.Sprintf("line-%03d", i)})
	}
	b.json("GET", "/logs", 200, entries)
	m := open(t, ui.NewLogsModel(b.client()))
	v := content(m)
	if !strings.Contains(v, "line-200") || !strings.Contains(v, "line-199") {
		t.Errorf("newest lines missing:\n%s", v)
	}
	if strings.Contains(v, "line-001") {
		t.Error("oldest line shown instead of newest")
	}
	// Newest at the bottom, like tail.
	if strings.Index(v, "line-199") > strings.Index(v, "line-200") {
		t.Error("lines not in chronological order")
	}
}

// --- Wizard ---

func wizardBackend(t *testing.T) *backend {
	t.Helper()
	b := newBackend(t)
	b.json("GET", "/wizard/providers", 200, []any{
		map[string]any{"id": "gdrive", "display_name": "Google Drive", "icon": "g", "auth_type": "oauth", "fields": []any{}, "default_name": "gdrive", "setup_guide": ""},
		map[string]any{"id": "s3", "display_name": "Amazon S3", "icon": "s3", "auth_type": "key", "default_name": "s3", "setup_guide": "",
			"fields": []any{
				map[string]any{"name": "access_key_id", "label": "Access key", "field_type": "text", "required": true, "help_text": ""},
				map[string]any{"name": "secret_access_key", "label": "Secret", "field_type": "password", "required": true, "help_text": ""},
			}},
	})
	b.json("POST", "/wizard/create", 200, map[string]any{"detail": "created"})
	b.json("POST", "/wizard/test", 200, map[string]any{"success": true, "error": nil})
	b.json("POST", "/wizard/authorize", 200, map[string]any{"session_id": "sess-9", "auth_url": "https://accounts.example.com/auth?state=sess-9"})
	b.json("GET", "/wizard/sessions/sess-9", 200, map[string]any{"session_id": "sess-9", "status": "completed", "auth_url": nil, "error": nil})
	b.json("GET", "/wizard/oauth/redirect-uri", 200, map[string]any{"redirect_uri": "http://127.0.0.1:8000/wizard/oauth/callback"})
	return b
}

// Key-based providers use their fields, not OAuth, and the params
// are sent.
func TestWizard_KeyProviderCreatesWithParams(t *testing.T) {
	b := wizardBackend(t)
	m := open(t, ui.NewWizardModel(b.client()))
	m = drive(t, m, press("down"), press("enter")) // Amazon S3, not the first row
	if !strings.Contains(content(m), "Set up Amazon S3") {
		t.Fatalf("form:\n%s", content(m))
	}
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("AKIA 123")...)
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("gehe1m q")...)
	m = drive(t, m, press("enter"))

	if len(b.matching("POST /wizard/authorize")) != 0 {
		t.Error("key provider went through OAuth")
	}
	body := bodyOf(b, "POST /wizard/create")
	params, _ := body["params"].(map[string]any)
	if body["provider_id"] != "s3" || body["name"] != "s3" || params["access_key_id"] != "AKIA 123" || params["secret_access_key"] != "gehe1m q" {
		t.Errorf("create body = %v", body)
	}
	if _, ok := body["session_id"]; ok {
		t.Error("session_id sent for a key provider")
	}
	if _, ok := body["token"]; ok {
		t.Error("token sent")
	}
	if !strings.Contains(content(m), "Connection test passed") {
		t.Errorf("done view:\n%s", content(m))
	}
}

// OAuth shows the URL and creates from the completed session.
func TestWizard_OAuthShowsURLAndCreatesFromSession(t *testing.T) {
	b := wizardBackend(t)
	opened := ""
	prev := ui.BrowserOpener
	ui.BrowserOpener = func(u string) error { opened = u; return nil }
	t.Cleanup(func() { ui.BrowserOpener = prev })

	m := open(t, ui.NewWizardModel(b.client()))
	m = drive(t, m, press("enter")) // Google Drive
	m = drive(t, m, press("enter")) // submit with the default name
	if body := bodyOf(b, "POST /wizard/authorize"); body["provider_id"] != "gdrive" {
		t.Fatalf("authorize body = %v", body)
	}
	v := content(m)
	if !strings.Contains(v, "https://accounts.example.com/auth?state=sess-9") || !strings.Contains(v, "o: open it") {
		t.Fatalf("auth URL not shown:\n%s", v)
	}
	m = drive(t, m, press("o"))
	if opened != "https://accounts.example.com/auth?state=sess-9" {
		t.Errorf("opened %q", opened)
	}
	if len(b.matching("POST /wizard/create")) != 0 {
		t.Fatal("created before the sign-in finished")
	}
	m = drive(t, m, ui.TickMsg{})
	body := bodyOf(b, "POST /wizard/create")
	if body["session_id"] != "sess-9" || body["provider_id"] != "gdrive" || body["name"] != "gdrive" {
		t.Errorf("create body = %v", body)
	}
	if _, ok := body["token"]; ok {
		t.Error("token sent")
	}
	if !strings.Contains(content(m), "Remote \"gdrive\" created") {
		t.Errorf("done view:\n%s", content(m))
	}
}

// oauthCredentialBackend offers Google Drive the way the backend's provider
// registry does: its own client_id / client_secret fields, both required.
func oauthCredentialBackend(t *testing.T) *backend {
	t.Helper()
	b := wizardBackend(t)
	b.json("GET", "/wizard/providers", 200, []any{
		map[string]any{"id": "drive", "display_name": "Google Drive", "icon": "gdrive", "auth_type": "oauth", "default_name": "gdrive", "setup_guide": "",
			"fields": []any{
				map[string]any{"name": "client_id", "label": "Client ID", "field_type": "text", "required": true, "help_text": ""},
				map[string]any{"name": "client_secret", "label": "Client Secret", "field_type": "password", "required": true, "help_text": ""},
			}},
	})
	return b
}

// The own OAuth app's credentials: one pair in the form, and the same pair goes to
// /wizard/authorize (consent URL, token exchange) and into the remote's
// params (rclone.conf, which refreshes the token with it).
func TestWizard_OAuthCustomCredentialsAreOnePairSentToBoth(t *testing.T) {
	b := oauthCredentialBackend(t)
	m := open(t, ui.NewWizardModel(b.client()))
	m = drive(t, m, press("enter")) // Google Drive
	form := content(m)
	if n := strings.Count(strings.ToLower(form), "client id"); n != 1 {
		t.Fatalf("form shows %d client ID fields:\n%s", n, form)
	}
	if n := strings.Count(strings.ToLower(form), "client secret"); n != 1 {
		t.Fatalf("form shows %d client secret fields:\n%s", n, form)
	}
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys(" my-app.apps.example.com ")...)
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("s3cr3t")...)
	m = drive(t, m, press("enter"))

	auth := bodyOf(b, "POST /wizard/authorize")
	if auth["provider_id"] != "drive" || auth["client_id"] != "my-app.apps.example.com" || auth["client_secret"] != "s3cr3t" {
		t.Fatalf("authorize body = %v", auth)
	}
	_ = drive(t, m, ui.TickMsg{})
	create := bodyOf(b, "POST /wizard/create")
	params, _ := create["params"].(map[string]any)
	if create["session_id"] != "sess-9" || params["client_id"] != auth["client_id"] || params["client_secret"] != auth["client_secret"] {
		t.Errorf("create body = %v, want the authorize pair in params", create)
	}
	for key := range params {
		if strings.HasPrefix(key, "_") {
			t.Errorf("form-internal key %q sent as a remote setting", key)
		}
	}
}

// --- Config ---

func TestConfig_EditLogLevel(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/config", 200, map[string]any{"log_level": "INFO", "history_days": 90})
	b.json("PUT", "/config", 200, map[string]any{"log_level": "WARNING", "history_days": 90})
	m := open(t, ui.NewConfigModel(b.client()))
	m = drive(t, m, press("e"), press("right"), press("enter"))
	if body := bodyOf(b, "PUT /config"); body["log_level"] != "WARNING" || body["history_days"] != float64(90) {
		t.Errorf("body = %v", body)
	}
	_ = m
}

func TestConfig_EditHistoryDays(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/config", 200, map[string]any{"log_level": "INFO", "history_days": 90})
	b.json("PUT", "/config", 200, map[string]any{"log_level": "INFO", "history_days": 0})
	m := open(t, ui.NewConfigModel(b.client()))
	if !strings.Contains(content(m), "90 days") {
		t.Errorf("view does not show the retention:\n%s", content(m))
	}
	m = drive(t, m, press("e"), press("tab"), press("backspace"), press("backspace"))
	m = drive(t, m, typeKeys("0")...)
	m = drive(t, m, press("enter"))
	if body := bodyOf(b, "PUT /config"); body["history_days"] != float64(0) {
		t.Errorf("body = %v", body)
	}
	_ = m
}

func TestConfig_TestSyncFormTakesPaths(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/config", 200, map[string]any{"log_level": "INFO"})
	b.json("POST", "/config/test-sync", 200, map[string]any{"success": true, "steps": []any{}, "error": nil})
	m := open(t, ui.NewConfigModel(b.client()))
	m = drive(t, m, press("t"))
	m = drive(t, m, typeKeys("/home/u/Mein Test")...)
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("gdrive:Test 1")...)
	m = drive(t, m, press("enter"))
	if body := bodyOf(b, "POST /config/test-sync"); body["local_dir"] != "/home/u/Mein Test" || body["remote_dir"] != "gdrive:Test 1" {
		t.Errorf("body = %v", body)
	}
	_ = m
}

// --- Notifications ---

func TestNotifications_ToggleChannel(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/notifications/channels/status", 200, map[string]any{"channels": map[string]any{"desktop": map[string]any{"available": true}}})
	b.json("GET", "/notifications/config", 200, map[string]any{"channels": map[string]any{"desktop": map[string]any{"enabled": true, "min_severity": "warning"}}})
	b.json("GET", "/notifications/history", 200, map[string]any{"items": []any{
		map[string]any{"id": 1, "event_type": "sync_failed", "severity": "error", "title": "Sync fehlgeschlagen", "body": "b", "timestamp": "2026-09-27T08:00:00+00:00", "channels_delivered": []string{"desktop"}},
	}, "total": 1})
	b.json("PUT", "/notifications/config", 200, map[string]any{"channels": map[string]any{"desktop": map[string]any{"enabled": false, "min_severity": "warning"}}})
	m := open(t, ui.NewNotificationsModel(b.client()))
	if !strings.Contains(content(m), "desktop") {
		t.Fatalf("view:\n%s", content(m))
	}
	m = drive(t, m, press("e"))
	body := bodyOf(b, "PUT /notifications/config")
	ch, _ := body["channels"].(map[string]any)
	desk, _ := ch["desktop"].(map[string]any)
	if desk["enabled"] != false {
		t.Errorf("body = %v", body)
	}
	m = drive(t, m, press("right"))
	if !strings.Contains(content(m), "Sync fehlgeschlagen") {
		t.Errorf("history:\n%s", content(m))
	}
}
