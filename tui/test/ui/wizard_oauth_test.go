package ui_test

import (
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

const (
	redirectURI = "http://127.0.0.1:8000/wizard/oauth/callback"
	remotesDocs = "https://github.com/pan-fire/OmniSync/blob/main/docs/gem/remotes.md"
)

// ownAppBackend offers the OAuth providers the way the backend's registry
// does: every one needs the client ID of the user's own app, Google Drive
// also its secret.
func ownAppBackend(t *testing.T) *backend {
	t.Helper()
	b := wizardBackend(t)
	clientID := map[string]any{"name": "client_id", "label": "Client ID", "field_type": "text", "required": true, "help_text": ""}
	b.json("GET", "/wizard/providers", 200, []any{
		map[string]any{"id": "drive", "display_name": "Google Drive", "icon": "gdrive", "auth_type": "oauth", "default_name": "gdrive",
			"setup_guide": "Create an OAuth client of type Desktop app in the Google Cloud console.",
			"fields": []any{
				clientID,
				map[string]any{"name": "client_secret", "label": "Client Secret", "field_type": "password", "required": true, "help_text": ""},
			}},
		map[string]any{"id": "dropbox", "display_name": "Dropbox", "icon": "dropbox", "auth_type": "oauth", "default_name": "dropbox",
			"setup_guide": "Create an app in the Dropbox App Console.",
			"fields": []any{
				clientID,
				map[string]any{"name": "client_secret", "label": "Client Secret", "field_type": "password", "required": false, "help_text": ""},
			}},
	})
	b.json("DELETE", "/wizard/sessions/sess-9", 200, map[string]any{"detail": "Session cancelled"})
	return b
}

// openDrive opens the Google Drive form (the first provider).
func openDrive(t *testing.T, b *backend) ui.WizardModel {
	t.Helper()
	m := open(t, ui.NewWizardModel(b.client()))
	return drive(t, m, press("enter"))
}

// fillDrive enters the own app's client ID and secret into the open form.
func fillDrive(t *testing.T, m ui.WizardModel) ui.WizardModel {
	t.Helper()
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("my-app.apps.example.com")...)
	m = drive(t, m, press("tab"))
	return drive(t, m, typeKeys("s3cr3t")...)
}

func TestWizardOAuth_FormRequiresClientIDAndSecret(t *testing.T) {
	b := ownAppBackend(t)
	m := openDrive(t, b)
	m = drive(t, m, press("enter")) // only the default name
	if !strings.Contains(content(m), "Client ID is required") {
		t.Fatalf("no error for the missing client ID:\n%s", content(m))
	}
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("my-app.apps.example.com")...)
	m = drive(t, m, press("enter"))
	if !strings.Contains(content(m), "Client Secret is required") {
		t.Fatalf("no error for the missing secret:\n%s", content(m))
	}
	if n := len(b.matching("POST /wizard/authorize")); n != 0 {
		t.Fatalf("authorized %d times without the own app", n)
	}
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("s3cr3t")...)
	_ = drive(t, m, press("enter"))
	auth := bodyOf(b, "POST /wizard/authorize")
	if auth["provider_id"] != "drive" || auth["client_id"] != "my-app.apps.example.com" || auth["client_secret"] != "s3cr3t" {
		t.Errorf("authorize body = %v", auth)
	}
}

// The form says how to create the app: the provider's guide, the redirect
// URI the backend uses and the docs section with the full steps.
func TestWizardOAuth_HintShowsRedirectURIAndDocs(t *testing.T) {
	b := ownAppBackend(t)
	m := openDrive(t, b)
	v := content(m)
	for _, want := range []string{
		"Desktop app in the Google Cloud console",
		"Redirect URI to register with your app: " + redirectURI,
		remotesDocs + "#google-drive",
	} {
		if !strings.Contains(v, want) {
			t.Errorf("form lacks %q:\n%s", want, v)
		}
	}
	// The URI is asked once; it does not change between providers.
	m = drive(t, m, press("esc"))
	m = drive(t, m, press("down"), press("enter")) // Dropbox
	v = content(m)
	if !strings.Contains(v, redirectURI) || !strings.Contains(v, remotesDocs+"#dropbox") ||
		!strings.Contains(v, "Dropbox App Console") {
		t.Errorf("Dropbox form:\n%s", v)
	}
	if n := len(b.matching("GET /wizard/oauth/redirect-uri")); n != 1 {
		t.Errorf("asked for the redirect URI %d times", n)
	}
}

// Without an answer the hint falls back to the backend's usual callback
// and says that it is assumed.
func TestWizardOAuth_RedirectURIFallback(t *testing.T) {
	b := ownAppBackend(t)
	b.json("GET", "/wizard/oauth/redirect-uri", 500, map[string]any{"detail": "boom"})
	m := openDrive(t, b)
	v := content(m)
	if !strings.Contains(v, b.srv.URL+"/wizard/oauth/callback") || !strings.Contains(v, "assumed") ||
		!strings.Contains(v, remotesDocs+"#google-drive") {
		t.Errorf("fallback not shown:\n%s", v)
	}
	// The form still works.
	m = fillDrive(t, m)
	_ = drive(t, m, press("enter"))
	if len(b.matching("POST /wizard/authorize")) != 1 {
		t.Errorf("requests: %v", b.log())
	}
}

// Dropbox's public client needs only the client ID.
func TestWizardOAuth_DropboxNeedsOnlyTheClientID(t *testing.T) {
	b := ownAppBackend(t)
	m := open(t, ui.NewWizardModel(b.client()))
	m = drive(t, m, press("down"), press("enter"), press("tab"))
	m = drive(t, m, typeKeys("dbx-app-key")...)
	_ = drive(t, m, press("enter"))
	auth := bodyOf(b, "POST /wizard/authorize")
	if auth["provider_id"] != "dropbox" || auth["client_id"] != "dbx-app-key" {
		t.Fatalf("authorize body = %v", auth)
	}
	if _, ok := auth["client_secret"]; ok {
		t.Errorf("an empty secret was sent: %v", auth)
	}
}

// The sign-in finishes by itself: the TUI polls the session and creates
// the remote with the app's credentials in its params.
func TestWizardOAuth_PollsAndCreatesWithTheApp(t *testing.T) {
	b := ownAppBackend(t)
	b.json("GET", "/wizard/sessions/sess-9", 200, map[string]any{
		"session_id": "sess-9", "status": "pending", "auth_url": nil, "error": nil,
	})
	opened := ""
	prev := ui.BrowserOpener
	ui.BrowserOpener = func(u string) error { opened = u; return nil }
	t.Cleanup(func() { ui.BrowserOpener = prev })

	m := fillDrive(t, openDrive(t, b))
	m = drive(t, m, press("enter"))
	v := content(m)
	if !strings.Contains(v, "https://accounts.example.com/auth?state=sess-9") || !strings.Contains(v, "Waiting for the sign-in") {
		t.Fatalf("sign-in step:\n%s", v)
	}
	if !m.PollSpec().IsActive(nil) {
		t.Fatal("the sign-in must be polled")
	}
	m = drive(t, m, press("o"))
	if opened != "https://accounts.example.com/auth?state=sess-9" {
		t.Errorf("opened %q", opened)
	}
	m = drive(t, m, ui.TickMsg{})
	if len(b.matching("POST /wizard/create")) != 0 {
		t.Fatal("created while the session is pending")
	}
	b.json("GET", "/wizard/sessions/sess-9", 200, map[string]any{
		"session_id": "sess-9", "status": "completed", "auth_url": nil, "error": nil,
	})
	m = drive(t, m, ui.TickMsg{})
	if n := len(b.matching("GET /wizard/sessions/sess-9")); n != 2 {
		t.Errorf("polled %d times", n)
	}
	create := bodyOf(b, "POST /wizard/create")
	params, _ := create["params"].(map[string]any)
	if create["session_id"] != "sess-9" || create["provider_id"] != "drive" || create["name"] != "gdrive" ||
		params["client_id"] != "my-app.apps.example.com" || params["client_secret"] != "s3cr3t" {
		t.Errorf("create body = %v", create)
	}
	if !strings.Contains(content(m), `Remote "gdrive" created`) {
		t.Errorf("done view:\n%s", content(m))
	}
}

// The backend's refusal (e.g. a missing client ID) shows in the form, which
// keeps what the user entered.
func TestWizardOAuth_AuthorizeRefusalShowsInTheForm(t *testing.T) {
	b := ownAppBackend(t)
	b.json("POST", "/wizard/authorize", 422, map[string]any{
		"detail": "Google Drive needs the client secret of your own OAuth app.",
		"code":   "oauth_client_secret_required",
	})
	m := fillDrive(t, openDrive(t, b))
	m = drive(t, m, press("enter"))
	v := content(m)
	if !strings.Contains(v, "needs the client secret of your own OAuth app") || !strings.Contains(v, "Set up Google Drive") {
		t.Fatalf("refusal not shown in the form:\n%s", v)
	}
	if !m.CapturesInput() {
		t.Error("the form must stay open")
	}

	b.json("POST", "/wizard/authorize", 200, map[string]any{
		"session_id": "sess-9", "auth_url": "https://accounts.example.com/auth?state=sess-9", "redirect_uri": redirectURI,
	})
	_ = drive(t, m, press("enter"))
	bodies := b.bodiesOf("POST /wizard/authorize")
	if len(bodies) != 2 || bodies[1]["client_id"] != "my-app.apps.example.com" || bodies[1]["client_secret"] != "s3cr3t" {
		t.Errorf("authorize bodies = %v", bodies)
	}
}

// Reconnect may leave the app empty: the backend uses the one the remote
// stores. The form says so, and that a remote made with rclone's app needs
// the own one now.
func TestWizardOAuth_ReconnectAllowsEmptyApp(t *testing.T) {
	b := ownAppBackend(t)
	b.json("POST", "/wizard/reconnect", 200, map[string]any{"detail": "reconnected"})
	m := open(t, ui.NewWizardModel(b.client()))
	m = drive(t, m, ui.ReconnectRemoteMsg{Name: "gdrive", ProviderID: "drive"})
	v := content(m)
	for _, want := range []string{
		`Reconnect "gdrive"`, "Leave the client ID and secret empty", "rclone's own app",
		redirectURI, remotesDocs + "#google-drive",
	} {
		if !strings.Contains(v, want) {
			t.Errorf("reconnect form lacks %q:\n%s", want, v)
		}
	}
	if strings.Contains(v, "Client ID *") {
		t.Errorf("the client ID must be optional on reconnect:\n%s", v)
	}
	m = drive(t, m, press("enter"))
	auth := bodyOf(b, "POST /wizard/authorize")
	if auth["remote_name"] != "gdrive" || auth["provider_id"] != "drive" {
		t.Fatalf("authorize body = %v", auth)
	}
	for _, key := range []string{"client_id", "client_secret"} {
		if _, ok := auth[key]; ok {
			t.Errorf("an empty %s was sent: %v", key, auth)
		}
	}
	m = drive(t, m, ui.TickMsg{})
	if rec := bodyOf(b, "POST /wizard/reconnect"); rec["name"] != "gdrive" || rec["session_id"] != "sess-9" {
		t.Errorf("reconnect body = %v", rec)
	}
	if !strings.Contains(content(m), `Remote "gdrive" reconnected`) {
		t.Errorf("done:\n%s", content(m))
	}
}

// A reconnect the backend refuses (no stored app) shows why in the form.
func TestWizardOAuth_ReconnectRefusalShowsInTheForm(t *testing.T) {
	b := ownAppBackend(t)
	b.json("POST", "/wizard/authorize", 422, map[string]any{
		"detail": "This remote has no OAuth app of its own; enter your app's client ID.",
		"code":   "oauth_client_id_required",
	})
	m := open(t, ui.NewWizardModel(b.client()))
	m = drive(t, m, ui.ReconnectRemoteMsg{Name: "gdrive", ProviderID: "drive"})
	m = drive(t, m, press("enter"))
	v := content(m)
	if !strings.Contains(v, "no OAuth app of its own") || !strings.Contains(v, `Reconnect "gdrive"`) {
		t.Errorf("refusal not shown in the form:\n%s", v)
	}
}

// There is no step that asks for a pasted address: pasting during the
// sign-in sends nothing, and only the session is polled.
func TestWizardOAuth_NoPasteStep(t *testing.T) {
	b := ownAppBackend(t)
	b.json("GET", "/wizard/sessions/sess-9", 200, map[string]any{
		"session_id": "sess-9", "status": "pending", "auth_url": nil, "error": nil,
	})
	m := fillDrive(t, openDrive(t, b))
	m = drive(t, m, press("enter"))
	b.reset()
	m = drive(t, m, tea.PasteMsg{Content: "http://127.0.0.1:8000/?code=c&state=sess-9"}, press("enter"))
	if reqs := b.log(); len(reqs) != 0 {
		t.Errorf("paste or Enter sent %v", reqs)
	}
	v := strings.ToLower(content(m))
	if strings.Contains(v, "paste") || strings.Contains(v, "ctrl+o") {
		t.Errorf("the sign-in step mentions pasting:\n%s", content(m))
	}
	for _, kb := range m.KeyBindings() {
		if strings.Contains(strings.ToLower(kb.Key+kb.Desc), "paste") {
			t.Errorf("key binding %+v", kb)
		}
	}
	m = drive(t, m, press("esc"))
	if len(b.matching("DELETE /wizard/sessions/sess-9")) != 1 || !strings.Contains(content(m), "Select Provider") {
		t.Errorf("Esc: %v\n%s", b.log(), content(m))
	}
}
