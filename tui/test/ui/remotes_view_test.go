package ui_test

import (
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// rvOut is what rvDrive saw besides the model: the flashes and navigations
// the view asked the app for.
type rvOut struct {
	flashes []string
	navs    []ui.NavigateMsg
}

// has reports whether a flash contains sub.
func (o rvOut) has(sub string) bool {
	for _, f := range o.flashes {
		if strings.Contains(f, sub) {
			return true
		}
	}
	return false
}

// rvDrive is drive that also records the flashes and navigations.
func rvDrive[M tea.Model](t *testing.T, m M, msgs ...tea.Msg) (M, rvOut) {
	t.Helper()
	var out rvOut
	queue := append([]tea.Msg(nil), msgs...)
	for i := 0; i < 100 && len(queue) > 0; i++ {
		next := queue[0]
		queue = queue[1:]
		switch next := next.(type) {
		case ui.FlashMsg:
			out.flashes = append(out.flashes, next.Text)
			continue
		case ui.NavigateMsg:
			out.navs = append(out.navs, next)
			continue
		}
		updated, cmd := m.Update(next)
		m = updated.(M)
		queue = append(queue, runAll(cmd)...)
	}
	return m, out
}

// rvStep sends one message and returns the command's messages without
// feeding them back, so the in-between state can be inspected.
func rvStep[M tea.Model](m M, msg tea.Msg) (M, []tea.Msg) {
	updated, cmd := m.Update(msg)
	return updated.(M), runAll(cmd)
}

// A refused delete prompt must not touch the backend: n and Esc both close
// it without a DELETE.
func TestRemotesView_DeleteNoAndEscSendNothing(t *testing.T) {
	b := remotesBackend(t)
	m := open(t, ui.NewRemotesModel(b.client()))
	for _, key := range []string{"n", "esc"} {
		m = drive(t, m, press("d"))
		if !strings.Contains(content(m), `Delete remote "gdrive"`) || m.KeyHints() != "y:yes  n/Esc:no" {
			t.Fatalf("%s: prompt not open (%q):\n%s", key, m.KeyHints(), content(m))
		}
		m = drive(t, m, press(key))
		if m.CapturesInput() || !strings.Contains(content(m), "Remotes") {
			t.Errorf("%s: prompt still open:\n%s", key, content(m))
		}
	}
	if got := b.matching("DELETE"); len(got) != 0 {
		t.Errorf("declined delete sent %v", got)
	}
}

// A delete the backend refuses names the backend's reason, and the list is
// read again so it shows what is really there.
func TestRemotesView_DeleteRefusalShowsDetail(t *testing.T) {
	b := remotesBackend(t)
	b.json("DELETE", "/remotes/s3", 409, map[string]any{
		"detail": "Remote 's3' is in use by a running sync", "code": "remote_busy", "details": map[string]any{"job_id": 4},
	})
	m := open(t, ui.NewRemotesModel(b.client()))
	m = drive(t, m, press("down"), press("d"))
	b.reset()
	_, out := rvDrive(t, m, press("y"))
	if !out.has("Delete failed: API error 409: Remote 's3' is in use by a running sync") {
		t.Errorf("flashes = %v", out.flashes)
	}
	if len(b.matching("GET /remotes")) != 1 {
		t.Errorf("list not refreshed: %v", b.log())
	}
}

// A successful delete says so and refreshes the list.
func TestRemotesView_DeleteSuccessFlashes(t *testing.T) {
	b := remotesBackend(t)
	m := open(t, ui.NewRemotesModel(b.client()))
	m = drive(t, m, press("down"), press("d"))
	_, out := rvDrive(t, m, press("y"))
	if !out.has("Remote deleted") {
		t.Errorf("flashes = %v", out.flashes)
	}
}

// If the dependency check fails no prompt opens: deleting blind could break
// profiles the user was never told about.
func TestRemotesView_DependencyCheckErrorOpensNoPrompt(t *testing.T) {
	b := remotesBackend(t)
	b.json("GET", "/remotes/s3/dependencies", 503, map[string]any{"detail": "database is locked", "code": "db_busy"})
	m := open(t, ui.NewRemotesModel(b.client()))
	m, out := rvDrive(t, m, press("down"), press("d"))
	if !out.has("Could not check what uses s3: API error 503: database is locked") {
		t.Errorf("flashes = %v", out.flashes)
	}
	if m.CapturesInput() {
		t.Errorf("prompt opened:\n%s", content(m))
	}
	_ = drive(t, m, press("y"))
	if got := b.matching("DELETE"); len(got) != 0 {
		t.Errorf("delete sent: %v", got)
	}
}

// Esc while the dependencies load cancels: the late answer must not open a
// prompt the user already walked away from.
func TestRemotesView_EscDuringDependencyCheckIgnoresLateAnswer(t *testing.T) {
	b := remotesBackend(t)
	m := open(t, ui.NewRemotesModel(b.client()))
	m, answer := rvStep(m, press("d"))
	if v := content(m); !strings.Contains(v, `Checking what uses "gdrive"`) || m.KeyHints() != "Esc:cancel" {
		t.Fatalf("checking view (%q):\n%s", m.KeyHints(), v)
	}
	m = drive(t, m, press("x")) // other keys wait
	if !m.CapturesInput() {
		t.Fatal("a key other than Esc left the check")
	}
	m = drive(t, m, press("esc"))
	m = drive(t, m, answer...)
	if m.CapturesInput() || strings.Contains(content(m), "Delete remote") {
		t.Errorf("late answer opened the prompt:\n%s", content(m))
	}
}

// A failed test says why, and for a refused sign-in names the key that
// fixes it for this kind of remote.
func TestRemotesView_TestFailureNamesTheFix(t *testing.T) {
	b := manageBackend(t)
	b.json("POST", "/remotes/box/test", 200, map[string]any{"success": false, "latency_ms": 12, "error": "permission denied", "auth_error": true})
	b.json("POST", "/remotes/gdrive/test", 200, map[string]any{"success": false, "latency_ms": nil, "error": nil, "auth_error": true})
	m := open(t, ui.NewRemotesModel(b.client()))
	m, out := rvDrive(t, m, press("t"))
	if !out.has("Test of box failed: the provider refused the sign-in; press e to update its credentials") {
		t.Errorf("box flashes = %v", out.flashes)
	}
	if !strings.Contains(content(m), "Test box: FAIL (12ms) permission denied") {
		t.Errorf("view:\n%s", content(m))
	}
	_, out = rvDrive(t, m, press("down"), press("t"))
	if !out.has("Test of gdrive failed: the provider refused the sign-in; press a to reconnect it") {
		t.Errorf("gdrive flashes = %v", out.flashes)
	}
}

// A plain test failure shows the remote's error; a remote that is neither
// editable nor reconnectable gets the generic advice.
func TestRemotesView_TestFailureWithoutAuthError(t *testing.T) {
	b := remotesBackend(t)
	b.json("POST", "/remotes/s3/test", 200, map[string]any{"success": false, "latency_ms": nil, "error": "bucket not found"})
	b.json("POST", "/remotes/gdrive/test", 200, map[string]any{"success": false, "error": nil, "auth_error": true})
	m := open(t, ui.NewRemotesModel(b.client()))
	m, out := rvDrive(t, m, press("down"), press("t"))
	if !out.has("Test of s3 failed: bucket not found") || !strings.Contains(content(m), "Test s3: FAIL bucket not found") {
		t.Errorf("flashes = %v\n%s", out.flashes, content(m))
	}
	_, out = rvDrive(t, m, press("up"), press("t"))
	if !out.has("Test of gdrive failed: the provider refused the sign-in; check its credentials") {
		t.Errorf("flashes = %v", out.flashes)
	}
}

// A test the backend cannot run (unknown remote) shows the API error
// envelope's detail instead of a PASS/FAIL line.
func TestRemotesView_TestAPIErrorShowsDetail(t *testing.T) {
	b := remotesBackend(t)
	b.json("POST", "/remotes/s3/test", 404, map[string]any{"detail": "Remote 's3' not found", "code": "remote_not_found", "details": map[string]any{}})
	m := open(t, ui.NewRemotesModel(b.client()))
	m, out := rvDrive(t, m, press("down"), press("t"))
	if !out.has("Testing s3...") || !out.has("Test of s3 failed: API error 404: Remote 's3' not found") {
		t.Errorf("flashes = %v", out.flashes)
	}
	if strings.Contains(content(m), "Test s3:") {
		t.Errorf("a result line for a test that did not run:\n%s", content(m))
	}
}

// i shows the remote's usage; unknown parts are "?", and remotes that do
// not report usage say so instead of showing zeros.
func TestRemotesView_StorageInfo(t *testing.T) {
	b := remotesBackend(t)
	b.json("GET", "/remotes/s3/about", 200, map[string]any{"supported": true, "used_bytes": 1024, "total_bytes": 1048576, "free_bytes": nil})
	b.json("GET", "/remotes/gdrive/about", 200, map[string]any{"supported": false})
	m := open(t, ui.NewRemotesModel(b.client()))
	m = drive(t, m, press("down"), press("i"))
	if v := content(m); !strings.Contains(v, "Storage s3: used 1.0 KB  total 1.0 MB  free ?") {
		t.Errorf("view:\n%s", v)
	}
	m = drive(t, m, press("up"), press("i"))
	if v := content(m); !strings.Contains(v, "Storage gdrive: this remote does not report usage") {
		t.Errorf("view:\n%s", v)
	}
}

// A failed storage query is a flash naming the remote, not a broken view.
func TestRemotesView_StorageInfoError(t *testing.T) {
	b := remotesBackend(t)
	b.json("GET", "/remotes/s3/about", 400, map[string]any{"detail": "rclone about failed", "code": "about_failed"})
	m := open(t, ui.NewRemotesModel(b.client()))
	m, out := rvDrive(t, m, press("down"), press("i"))
	if !out.has("Storage info for s3 failed: API error 400: rclone about failed") {
		t.Errorf("flashes = %v", out.flashes)
	}
	if strings.Contains(content(m), "Storage s3") {
		t.Errorf("view:\n%s", content(m))
	}
}

// The list shows "Loading..." until the first answer, the backend's error
// if it fails, and r recovers once the backend answers again.
func TestRemotesView_LoadErrorAndRefresh(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/remotes", 503, map[string]any{"detail": "rclone not installed", "code": "rclone_missing"})
	m := ui.NewRemotesModel(b.client())
	if content(m) != "" {
		t.Errorf("unsized view: %q", content(m))
	}
	m = drive(t, m, tea.WindowSizeMsg{Width: 140, Height: 40})
	if !strings.Contains(content(m), "Loading...") {
		t.Errorf("loading:\n%s", content(m))
	}
	m = drive(t, m, runAll(m.Init())...)
	if v := content(m); !strings.Contains(v, "Error: API error 503: rclone not installed") {
		t.Fatalf("error not shown:\n%s", v)
	}
	b.json("GET", "/remotes", 200, []any{map[string]any{"name": "nas", "type": "sftp", "last_verified": nil}})
	m = drive(t, m, press("r"))
	if v := content(m); strings.Contains(v, "Error") || !strings.Contains(v, "nas") {
		t.Errorf("refresh did not recover:\n%s", v)
	}
	_ = drive(t, m, ui.TickMsg{})
	if n := len(b.matching("GET /remotes")); n != 3 {
		t.Errorf("tick did not poll: %d reads", n)
	}
}

// Every key the bottom bar offers in the list has a help entry, and the
// view is the Remotes view.
func TestRemotesView_HintsHaveHelpEntries(t *testing.T) {
	b := remotesBackend(t)
	m := open(t, ui.NewRemotesModel(b.client()))
	if m.ViewID() != ui.ViewRemotes {
		t.Errorf("ViewID = %v", m.ViewID())
	}
	var help strings.Builder
	for _, kb := range m.KeyBindings() {
		help.WriteString(" " + kb.Key + " ")
	}
	for _, hint := range strings.Split(m.KeyHints(), "  ") {
		key := strings.SplitN(strings.TrimSpace(hint), ":", 2)[0]
		if !strings.Contains(help.String(), " "+key+" ") {
			t.Errorf("hint %q has no help entry", hint)
		}
	}
}

// w opens the wizard; the list itself sends nothing.
func TestRemotesView_WOpensWizard(t *testing.T) {
	b := remotesBackend(t)
	m := open(t, ui.NewRemotesModel(b.client()))
	b.reset()
	_, out := rvDrive(t, m, press("w"))
	if len(out.navs) != 1 || out.navs[0].Target != ui.ViewWizard {
		t.Errorf("navs = %+v", out.navs)
	}
	if reqs := b.log(); len(reqs) != 0 {
		t.Errorf("sent %v", reqs)
	}
}

// Quick add when the types cannot be loaded: the user is told and the list
// is back.
func TestRemotesView_QuickAddProvidersError(t *testing.T) {
	b := quickAddBackend(t)
	b.json("GET", "/wizard/providers", 503, map[string]any{"detail": "rclone not installed", "code": "rclone_missing"})
	m := open(t, ui.NewRemotesModel(b.client()))
	m, out := rvDrive(t, m, press("c"))
	if !out.has("Could not load the remote types: API error 503: rclone not installed") || m.CapturesInput() {
		t.Errorf("flashes = %v\n%s", out.flashes, content(m))
	}
}

// When every type needs a browser sign-in, quick add points at the wizard
// instead of opening an empty form.
func TestRemotesView_QuickAddOnlyOAuthPointsAtWizard(t *testing.T) {
	b := quickAddBackend(t)
	b.json("GET", "/wizard/providers", 200, []any{
		map[string]any{"id": "gdrive", "display_name": "Google Drive", "icon": "g", "auth_type": "oauth", "fields": []any{}, "default_name": "gdrive", "setup_guide": ""},
	})
	m := open(t, ui.NewRemotesModel(b.client()))
	m, out := rvDrive(t, m, press("c"))
	if !out.has("Every remote type needs a browser sign-in: use the wizard (w)") || m.CapturesInput() {
		t.Errorf("flashes = %v\n%s", out.flashes, content(m))
	}
}

// Esc while the types load cancels; their late answer opens no form.
func TestRemotesView_QuickAddEscWhileLoading(t *testing.T) {
	b := quickAddBackend(t)
	m := open(t, ui.NewRemotesModel(b.client()))
	m, answer := rvStep(m, press("c"))
	if v := content(m); !strings.Contains(v, "Loading the remote types") || m.KeyHints() != "Esc:cancel" {
		t.Fatalf("loading (%q):\n%s", m.KeyHints(), v)
	}
	m = drive(t, m, press("esc"))
	m = drive(t, m, answer...)
	if m.CapturesInput() || strings.Contains(content(m), "Quick add remote") {
		t.Errorf("late answer opened the form:\n%s", content(m))
	}
}

// A type without settings is created right after name and type, with no
// empty second step; while it is created keys do nothing.
func TestRemotesView_QuickAddTypeWithoutFields(t *testing.T) {
	b := quickAddBackend(t)
	b.json("GET", "/wizard/providers", 200, []any{
		map[string]any{"id": "local", "display_name": "Local disk", "icon": "l", "auth_type": "key", "fields": []any{}, "default_name": "local", "setup_guide": ""},
	})
	m := open(t, ui.NewRemotesModel(b.client()))
	m = drive(t, m, press("c"))
	if m.KeyHints() != "Enter:submit  Esc:cancel  Tab/Shift+Tab:field  Left/Right:choice" {
		t.Errorf("form hints = %q", m.KeyHints())
	}
	m = drive(t, m, typeKeys("disk")...)
	m, submit := rvStep(m, press("enter"))
	m, create := rvStep(m, submit[0])
	if v := content(m); !strings.Contains(v, `Creating remote "disk"`) || m.KeyHints() != "Working..." {
		t.Errorf("creating (%q):\n%s", m.KeyHints(), v)
	}
	m = drive(t, m, press("d"), press("esc"))
	m, out := rvDrive(t, m, create...)
	body := bodyOf(b, "POST /wizard/create")
	if params, _ := body["params"].(map[string]any); body["name"] != "disk" || body["provider_id"] != "local" || len(params) != 0 {
		t.Errorf("create body = %v", body)
	}
	if !out.has("Remote disk created; press t to test it") || m.CapturesInput() {
		t.Errorf("flashes = %v\n%s", out.flashes, content(m))
	}
	if len(b.matching("GET /remotes/disk/dependencies")) != 0 {
		t.Error("d while creating started a delete")
	}
}
