package ui_test

import (
	"errors"
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// wvSignIn opens Google Drive in ownAppBackend, fills the app and submits:
// the wizard waits for the sign-in afterwards.
func wvSignIn(t *testing.T, b *backend) ui.WizardModel {
	t.Helper()
	m := fillDrive(t, openDrive(t, b))
	m = drive(t, m, press("enter"))
	if !strings.Contains(content(m), "Waiting for the sign-in") {
		t.Fatalf("not signing in:\n%s", content(m))
	}
	return m
}

// The provider list shows "Loading providers..." first; a failed load
// shows the backend's message.
func TestWizardView_ProvidersLoadError(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/wizard/providers", 503, map[string]any{"detail": "rclone not installed", "code": "rclone_missing"})
	m := ui.NewWizardModel(b.client())
	if content(m) != "" || m.ViewID() != ui.ViewWizard {
		t.Errorf("unsized view %q, id %v", content(m), m.ViewID())
	}
	m = drive(t, m, tea.WindowSizeMsg{Width: 140, Height: 40})
	if !strings.Contains(content(m), "Loading providers...") {
		t.Errorf("loading:\n%s", content(m))
	}
	m = drive(t, m, runAll(m.Init())...)
	if v := content(m); !strings.Contains(v, "Error: API error 503: rclone not installed") {
		t.Errorf("error not shown:\n%s", v)
	}
}

// The selected provider's setup guide is shown under the list, and Esc on
// the list goes back to Remotes.
func TestWizardView_ListShowsGuideAndEscGoesBack(t *testing.T) {
	b := ownAppBackend(t)
	m := open(t, ui.NewWizardModel(b.client()))
	v := content(m)
	if !strings.Contains(v, "Google Cloud console") || !strings.Contains(v, "browser sign-in") {
		t.Errorf("list:\n%s", v)
	}
	m = drive(t, m, press("down"))
	if v = content(m); !strings.Contains(v, "Dropbox App Console") {
		t.Errorf("guide does not follow the selection:\n%s", v)
	}
	_, out := rvDrive(t, m, press("esc"))
	if len(out.navs) != 1 || out.navs[0].Target != ui.ViewRemotes {
		t.Errorf("navs = %+v", out.navs)
	}
}

// Esc in the fields form goes back to the provider list, sending nothing.
func TestWizardView_EscInFormBackToList(t *testing.T) {
	b := wizardBackend(t)
	m := open(t, ui.NewWizardModel(b.client()))
	m = drive(t, m, press("down"), press("enter"))
	if !m.CapturesInput() {
		t.Fatal("form does not own the keyboard")
	}
	b.reset()
	m = drive(t, m, press("esc"))
	if m.CapturesInput() || !strings.Contains(content(m), "Select Provider") {
		t.Errorf("Esc:\n%s", content(m))
	}
	if reqs := b.log(); len(reqs) != 0 {
		t.Errorf("sent %v", reqs)
	}
}

// Select fields: a required one starts at its default, an optional one at
// "(rclone default)" which is not sent; folder fields explain the format;
// pasted text lands in the focused field.
func TestWizardView_SelectAndRemotePathFields(t *testing.T) {
	b := wizardBackend(t)
	b.json("GET", "/wizard/providers", 200, []any{
		map[string]any{"id": "crypt", "display_name": "Encrypted", "icon": "c", "auth_type": "key", "default_name": "vault", "setup_guide": "",
			"fields": []any{
				map[string]any{"name": "remote", "label": "Remote", "field_type": "remote_path", "required": true, "help_text": "What to encrypt."},
				map[string]any{"name": "filename_encryption", "label": "File names", "field_type": "select", "required": true, "help_text": "",
					"options": []any{"standard", "off"}, "default": "standard"},
				map[string]any{"name": "suffix", "label": "Suffix", "field_type": "select", "required": false, "help_text": "",
					"options": []any{".bin", "none"}, "default": ""},
			}},
	})
	m := open(t, ui.NewWizardModel(b.client()))
	m = drive(t, m, press("enter"), press("tab"))
	v := content(m)
	if !strings.Contains(v, "What to encrypt. Format: <remote>:<folder>, e.g. gdrive:Encrypted.") || !strings.Contains(v, "(rclone default)") {
		t.Fatalf("form:\n%s", v)
	}
	m = drive(t, m, tea.PasteMsg{Content: "box:Secret"})
	_ = drive(t, m, press("enter"))
	body := bodyOf(b, "POST /wizard/create")
	params, _ := body["params"].(map[string]any)
	if params["remote"] != "box:Secret" || params["filename_encryption"] != "standard" {
		t.Errorf("params = %v", params)
	}
	if _, ok := params["suffix"]; ok {
		t.Errorf("an unset choice was sent: %v", params)
	}
}

// A create the backend refuses names the reason and returns to the
// provider list with the error shown.
func TestWizardView_CreateRefusal(t *testing.T) {
	b := wizardBackend(t)
	b.json("POST", "/wizard/create", 409, map[string]any{"detail": "Remote 's3' already exists", "code": "remote_exists", "details": map[string]any{"name": "s3"}})
	m := open(t, ui.NewWizardModel(b.client()))
	m = drive(t, m, press("down"), press("enter"), press("tab"))
	m = drive(t, m, typeKeys("A")...)
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("B")...)
	m, out := rvDrive(t, m, press("enter"))
	if !out.has("Creating the remote failed: API error 409: Remote 's3' already exists") {
		t.Errorf("flashes = %v", out.flashes)
	}
	if v := content(m); !strings.Contains(v, "Select Provider") || !strings.Contains(v, "already exists") {
		t.Errorf("view:\n%s", v)
	}
	if len(b.matching("POST /wizard/test")) != 0 {
		t.Error("tested a remote that was not created")
	}
}

// While the remote is created keys do nothing.
func TestWizardView_CreatingIgnoresKeys(t *testing.T) {
	b := wizardBackend(t)
	m := open(t, ui.NewWizardModel(b.client()))
	m = drive(t, m, press("down"), press("enter"), press("tab"))
	m = drive(t, m, typeKeys("A")...)
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("B")...)
	m, submit := rvStep(m, press("enter"))
	m, _ = rvStep(m, submit[0])
	if v := content(m); !strings.Contains(v, "Talking to the backend...") {
		t.Fatalf("creating:\n%s", v)
	}
	m, out := rvDrive(t, m, press("esc"))
	if len(out.navs) != 0 || !strings.Contains(content(m), "Talking to the backend") {
		t.Errorf("Esc while creating: %v\n%s", out.navs, content(m))
	}
}

// A failing connection test after create is shown on the done page (from
// the test's answer or from the API error); Enter goes back to Remotes and
// the wizard starts over.
func TestWizardView_DoneShowsTestFailureAndReturns(t *testing.T) {
	cases := []struct {
		name   string
		status int
		reply  map[string]any
		want   string
	}{
		{"test failed", 200, map[string]any{"success": false, "error": "bucket missing"}, "connection test failed: bucket missing"},
		{"test failed without reason", 200, map[string]any{"success": false, "error": nil}, "connection test failed: unknown error"},
		{"api error", 404, map[string]any{"detail": "Remote 's3' not found", "code": "remote_not_found"}, "connection test failed: API error 404: Remote 's3' not found"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			b := wizardBackend(t)
			b.json("POST", "/wizard/test", tc.status, tc.reply)
			m := open(t, ui.NewWizardModel(b.client()))
			m = drive(t, m, press("down"), press("enter"), press("tab"))
			m = drive(t, m, typeKeys("A")...)
			m = drive(t, m, press("tab"))
			m = drive(t, m, typeKeys("B")...)
			m = drive(t, m, press("enter"))
			v := content(m)
			if !strings.Contains(v, `Remote "s3" created.`) || !strings.Contains(v, tc.want) {
				t.Fatalf("done:\n%s", v)
			}
			m = drive(t, m, press("x")) // only Enter and Esc leave
			m, out := rvDrive(t, m, press("enter"))
			if len(out.navs) != 1 || out.navs[0].Target != ui.ViewRemotes {
				t.Errorf("navs = %+v", out.navs)
			}
			if m.CapturesInput() || !strings.Contains(content(m), "Select Provider") {
				t.Errorf("not reset:\n%s", content(m))
			}
		})
	}
}

// The done page says "Testing the connection..." until the test answers.
func TestWizardView_DoneWaitsForTest(t *testing.T) {
	b := wizardBackend(t)
	m := open(t, ui.NewWizardModel(b.client()))
	m = drive(t, m, press("down"), press("enter"), press("tab"))
	m = drive(t, m, typeKeys("A")...)
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("B")...)
	m, submit := rvStep(m, press("enter"))
	m, created := rvStep(m, submit[0])
	m, _ = rvStep(m, created[0]) // create_remote; the test is not answered
	if v := content(m); !strings.Contains(v, "Testing the connection...") {
		t.Errorf("done:\n%s", v)
	}
}

// A failed sign-in shows the provider's reason and starts over at the
// provider list; the session is no longer polled.
func TestWizardView_SignInFailedStartsOver(t *testing.T) {
	b := ownAppBackend(t)
	b.json("GET", "/wizard/sessions/sess-9", 200, map[string]any{"session_id": "sess-9", "status": "failed", "auth_url": nil, "error": "access_denied"})
	m := wvSignIn(t, b)
	m = drive(t, m, ui.TickMsg{})
	v := content(m)
	if !strings.Contains(v, "Select Provider") || !strings.Contains(v, "Error: access_denied") {
		t.Fatalf("view:\n%s", v)
	}
	if m.PollSpec().IsActive(nil) || len(b.matching("POST /wizard/create")) != 0 {
		t.Errorf("still polling or created: %v", b.log())
	}
	_ = drive(t, m, ui.TickMsg{})
	if n := len(b.matching("GET /wizard/sessions/")); n != 1 {
		t.Errorf("polled %d times", n)
	}
}

// A failed sign-in without a reason still says it failed.
func TestWizardView_SignInFailedWithoutReason(t *testing.T) {
	b := ownAppBackend(t)
	b.json("GET", "/wizard/sessions/sess-9", 200, map[string]any{"session_id": "sess-9", "status": "failed", "auth_url": nil, "error": nil})
	m := wvSignIn(t, b)
	m = drive(t, m, ui.TickMsg{})
	if v := content(m); !strings.Contains(v, "Error: authorization failed") {
		t.Errorf("view:\n%s", v)
	}
}

// A session poll that fails (e.g. the session expired) is shown while
// waiting; an answer for another session is ignored.
func TestWizardView_PollErrorAndForeignSession(t *testing.T) {
	b := ownAppBackend(t)
	b.json("GET", "/wizard/sessions/sess-9", 404, map[string]any{"detail": "Session not found", "code": "session_not_found"})
	m := wvSignIn(t, b)
	m = drive(t, m, ui.TickMsg{})
	if v := content(m); !strings.Contains(v, "Error: API error 404: Session not found") || !strings.Contains(v, "Waiting for the sign-in") {
		t.Errorf("view:\n%s", v)
	}
	m = drive(t, m, ui.PollResultMsg{ViewID: ui.ViewWizard, Data: &api.WizardSessionResponse{SessionID: "other", Status: api.WizardCompleted}})
	if len(b.matching("POST /wizard/create")) != 0 || !strings.Contains(content(m), "Waiting for the sign-in") {
		t.Errorf("another session's answer created a remote: %v", b.log())
	}
	// Keys other than o and Esc do nothing while waiting.
	m = drive(t, m, press("x"), press("enter"))
	if !strings.Contains(content(m), "Waiting for the sign-in") {
		t.Errorf("left the sign-in:\n%s", content(m))
	}
}

// When the browser cannot be opened the URL stays and the reason is shown;
// without any opener there is no o hint and o does nothing.
func TestWizardView_OpenBrowserErrorAndNoOpener(t *testing.T) {
	b := ownAppBackend(t)
	b.json("GET", "/wizard/sessions/sess-9", 200, map[string]any{"session_id": "sess-9", "status": "pending", "auth_url": nil, "error": nil})
	prev := ui.BrowserOpener
	t.Cleanup(func() { ui.BrowserOpener = prev })
	ui.BrowserOpener = func(string) error { return errors.New("no display") }
	m := wvSignIn(t, b)
	m = drive(t, m, press("o"))
	if v := content(m); !strings.Contains(v, "could not open the browser: no display") || !strings.Contains(v, "https://accounts.example.com/auth?state=sess-9") {
		t.Errorf("view:\n%s", v)
	}
	ui.BrowserOpener = nil
	if v := content(m); strings.Contains(v, "o: open it") {
		t.Errorf("o offered without an opener:\n%s", v)
	}
	m, cmd := rvStep(m, press("o"))
	if len(cmd) != 0 || !strings.Contains(content(m), "Waiting for the sign-in") {
		t.Errorf("o without an opener: %v", cmd)
	}
}

// Esc during a reconnect's sign-in cancels the session and goes back to
// Remotes, where the reconnect started.
func TestWizardView_ReconnectEscCancelsToRemotes(t *testing.T) {
	b := ownAppBackend(t)
	b.json("GET", "/wizard/sessions/sess-9", 200, map[string]any{"session_id": "sess-9", "status": "pending", "auth_url": nil, "error": nil})
	m := open(t, ui.NewWizardModel(b.client()))
	m = drive(t, m, ui.ReconnectRemoteMsg{Name: "gdrive", ProviderID: "drive"}, press("enter"))
	if !strings.Contains(content(m), "Waiting for the sign-in") {
		t.Fatalf("not signing in:\n%s", content(m))
	}
	m, out := rvDrive(t, m, press("esc"))
	if len(b.matching("DELETE /wizard/sessions/sess-9")) != 1 {
		t.Errorf("session not cancelled: %v", b.log())
	}
	if len(out.navs) != 1 || out.navs[0].Target != ui.ViewRemotes || m.CapturesInput() {
		t.Errorf("navs = %+v\n%s", out.navs, content(m))
	}
}

// Esc in the reconnect form goes back to Remotes, not to the provider list.
func TestWizardView_ReconnectFormEscToRemotes(t *testing.T) {
	b := ownAppBackend(t)
	m := open(t, ui.NewWizardModel(b.client()))
	m = drive(t, m, ui.ReconnectRemoteMsg{Name: "gdrive", ProviderID: "drive"})
	m, out := rvDrive(t, m, press("esc"))
	if len(out.navs) != 1 || out.navs[0].Target != ui.ViewRemotes || m.CapturesInput() {
		t.Errorf("navs = %+v\n%s", out.navs, content(m))
	}
}

// A reconnect that fails at the sign-in or at storing the token returns to
// the reconnect form with the reason, so the user can retry at once.
func TestWizardView_ReconnectFailuresReturnToForm(t *testing.T) {
	t.Run("sign-in failed", func(t *testing.T) {
		b := ownAppBackend(t)
		b.json("GET", "/wizard/sessions/sess-9", 200, map[string]any{"session_id": "sess-9", "status": "failed", "auth_url": nil, "error": "access_denied"})
		m := open(t, ui.NewWizardModel(b.client()))
		m = drive(t, m, ui.ReconnectRemoteMsg{Name: "gdrive", ProviderID: "drive"}, press("enter"))
		m = drive(t, m, ui.TickMsg{})
		if v := content(m); !strings.Contains(v, `Reconnect "gdrive"`) || !strings.Contains(v, "access_denied") || !m.CapturesInput() {
			t.Errorf("view:\n%s", v)
		}
	})
	t.Run("token refused", func(t *testing.T) {
		b := ownAppBackend(t)
		b.json("POST", "/wizard/reconnect", 404, map[string]any{"detail": "Remote 'gdrive' not found", "code": "remote_not_found"})
		m := open(t, ui.NewWizardModel(b.client()))
		m = drive(t, m, ui.ReconnectRemoteMsg{Name: "gdrive", ProviderID: "drive"}, press("enter"))
		m, out := rvDrive(t, m, ui.TickMsg{})
		if !out.has("Reconnecting the remote failed: API error 404: Remote 'gdrive' not found") {
			t.Errorf("flashes = %v", out.flashes)
		}
		if v := content(m); !strings.Contains(v, `Reconnect "gdrive"`) || !strings.Contains(v, "Remote 'gdrive' not found") {
			t.Errorf("view:\n%s", v)
		}
		if len(b.matching("POST /wizard/create")) != 0 {
			t.Error("a failed reconnect created a remote")
		}
	})
}

// A reconnect for a key-based remote is refused with a reason instead of
// showing a sign-in that cannot work.
func TestWizardView_ReconnectNonOAuthRefused(t *testing.T) {
	b := wizardBackend(t)
	m := open(t, ui.NewWizardModel(b.client()))
	m = drive(t, m, ui.ReconnectRemoteMsg{Name: "box", ProviderID: "s3"})
	if v := content(m); !strings.Contains(v, `remote "box" does not sign in with OAuth and cannot be reconnected`) || m.CapturesInput() {
		t.Errorf("view:\n%s", v)
	}
	if len(b.matching("POST /wizard/authorize")) != 0 {
		t.Errorf("sent %v", b.log())
	}
}

// A reconnect that arrives before the providers are loaded opens its form
// once Init has loaded them.
func TestWizardView_ReconnectBeforeProvidersLoaded(t *testing.T) {
	b := ownAppBackend(t)
	m := ui.NewWizardModel(b.client())
	m = drive(t, m, tea.WindowSizeMsg{Width: 140, Height: 40})
	m = drive(t, m, ui.ReconnectRemoteMsg{Name: "gdrive", ProviderID: "drive"})
	if !strings.Contains(content(m), "Loading providers...") {
		t.Errorf("loading:\n%s", content(m))
	}
	m = drive(t, m, runAll(m.Init())...)
	if v := content(m); !strings.Contains(v, `Reconnect "gdrive" (Google Drive)`) {
		t.Errorf("form:\n%s", v)
	}
}

// Showing the view again during a sign-in must not reload the providers,
// and the help names the sign-in keys.
func TestWizardView_InitDuringSignInLoadsNothing(t *testing.T) {
	b := ownAppBackend(t)
	b.json("GET", "/wizard/sessions/sess-9", 200, map[string]any{"session_id": "sess-9", "status": "pending", "auth_url": nil, "error": nil})
	m := wvSignIn(t, b)
	if cmd := m.Init(); cmd != nil {
		t.Error("Init during a sign-in returned a command")
	}
	keys := map[string]bool{}
	for _, kb := range m.KeyBindings() {
		keys[kb.Key] = true
	}
	if !keys["o"] || !keys["Esc"] {
		t.Errorf("bindings = %v", m.KeyBindings())
	}
}

// An unrelated poll error (no data type) is shown on the list.
func TestWizardView_UntypedPollError(t *testing.T) {
	b := wizardBackend(t)
	m := open(t, ui.NewWizardModel(b.client()))
	m = drive(t, m, ui.PollResultMsg{ViewID: ui.ViewWizard, Err: errors.New("connection refused")})
	if v := content(m); !strings.Contains(v, "Error: connection refused") {
		t.Errorf("view:\n%s", v)
	}
}
