package ui_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

var ctrlU = tea.KeyPressMsg(tea.Key{Code: 'u', Mod: tea.ModCtrl})

// manageBackend has an editable SFTP remote and a reconnectable Drive
// remote whose sign-in was refused.
func manageBackend(t *testing.T) *backend {
	t.Helper()
	b := wizardBackend(t)
	b.json("GET", "/wizard/providers", 200, []any{
		map[string]any{"id": "gdrive", "display_name": "Google Drive", "icon": "g", "auth_type": "oauth", "default_name": "gdrive", "setup_guide": "",
			"fields": []any{
				map[string]any{"name": "client_id", "label": "Client ID", "field_type": "text", "required": true, "help_text": "", "options": []any{}, "default": ""},
			}},
		map[string]any{"id": "sftp", "display_name": "SFTP", "icon": "sftp", "auth_type": "key", "default_name": "sftp", "setup_guide": "",
			"fields": []any{
				map[string]any{"name": "host", "label": "Host", "field_type": "text", "required": true, "help_text": "", "options": []any{}, "default": ""},
				map[string]any{"name": "pass", "label": "Password", "field_type": "password", "required": false, "help_text": "", "options": []any{}, "default": ""},
				map[string]any{"name": "port", "label": "Port", "field_type": "text", "required": false, "help_text": "", "options": []any{}, "default": ""},
			}},
	})
	b.json("GET", "/remotes", 200, []any{
		map[string]any{"name": "box", "type": "sftp", "last_verified": nil, "provider_id": "sftp",
			"editable": true, "reconnectable": false, "auth_error": false},
		map[string]any{"name": "gdrive", "type": "drive", "last_verified": nil, "provider_id": "gdrive",
			"editable": false, "reconnectable": true, "auth_error": true},
	})
	b.json("GET", "/remotes/box/config", 200, map[string]any{
		"name": "box", "type": "sftp", "provider_id": "sftp", "other_keys": []any{"md5sum_command"},
		"fields": []any{
			map[string]any{"name": "host", "value": "old.example", "is_set": true, "secret": false},
			map[string]any{"name": "pass", "value": "", "is_set": true, "secret": true},
			map[string]any{"name": "port", "value": "22", "is_set": true, "secret": false},
		},
	})
	b.json("PUT", "/remotes/box", 200, map[string]any{"detail": "updated"})
	return b
}

func TestRemotes_EditSendsOnlyChangesAndKeepsSecrets(t *testing.T) {
	b := manageBackend(t)
	m := open(t, ui.NewRemotesModel(b.client()))
	m = drive(t, m, press("e")) // box is the first row
	v := content(m)
	if !strings.Contains(v, `Edit SFTP remote "box"`) || !strings.Contains(v, "old.example") ||
		!strings.Contains(v, "md5sum_command") {
		t.Fatalf("edit form:\n%s", v)
	}
	m = drive(t, m, press("tab"))
	if v = content(m); !strings.Contains(v, "Stored; leave empty") {
		t.Fatalf("the password's help must say it is kept:\n%s", v)
	}
	m = drive(t, m, press("shift+tab"))
	m = drive(t, m, ctrlU)
	m = drive(t, m, typeKeys("new.example")...)
	m = drive(t, m, press("enter"))
	body := bodyOf(b, "PUT /remotes/box")
	if body == nil {
		t.Fatalf("no update: %v", b.log())
	}
	params, _ := body["params"].(map[string]any)
	if len(params) != 1 || params["host"] != "new.example" {
		t.Errorf("params = %v", params)
	}
	if clear, _ := body["clear"].([]any); len(clear) != 0 {
		t.Errorf("clear = %v", clear)
	}
	if m.CapturesInput() {
		t.Errorf("form still open:\n%s", content(m))
	}
}

func TestRemotes_EditCanRemoveAStoredSecret(t *testing.T) {
	b := manageBackend(t)
	m := open(t, ui.NewRemotesModel(b.client()))
	m = drive(t, m, press("e"))
	m = drive(t, m, press("tab"), press("tab"), press("tab"), press("right")) // Remove a stored secret: Password
	drive(t, m, press("enter"))
	body := bodyOf(b, "PUT /remotes/box")
	clear, _ := body["clear"].([]any)
	if len(clear) != 1 || clear[0] != "pass" {
		t.Errorf("body = %v", body)
	}
}

func TestRemotes_RefusedSignInOffersReconnect(t *testing.T) {
	b := manageBackend(t)
	m := open(t, ui.NewRemotesModel(b.client()))
	if !strings.Contains(content(m), "drive (refused)") {
		t.Fatalf("table:\n%s", content(m))
	}
	m = drive(t, m, press("down"))
	if !strings.Contains(content(m), "press a to reconnect it") {
		t.Fatalf("no hint:\n%s", content(m))
	}
	// e on an OAuth remote points at a, a opens the wizard in reconnect mode.
	_, cmd := m.Update(press("a"))
	nav, ok := find[ui.NavigateMsg](runAll(cmd))
	if !ok || nav.Target != ui.ViewWizard {
		t.Fatalf("no navigation: %+v", nav)
	}
	payload, ok := nav.Payload.(ui.ReconnectRemoteMsg)
	if !ok || payload.Name != "gdrive" || payload.ProviderID != "gdrive" {
		t.Errorf("payload = %+v", nav.Payload)
	}
}

func TestWizard_ReconnectReplacesOnlyTheToken(t *testing.T) {
	b := manageBackend(t)
	b.json("POST", "/wizard/reconnect", 200, map[string]any{"detail": "reconnected"})
	w := open(t, ui.NewWizardModel(b.client()))
	w = drive(t, w, ui.ReconnectRemoteMsg{Name: "gdrive", ProviderID: "gdrive"})
	v := content(w)
	if !strings.Contains(v, `Reconnect "gdrive"`) || strings.Contains(v, "Remote name") {
		t.Fatalf("reconnect form:\n%s", v)
	}
	w = drive(t, w, press("enter"))
	auth := bodyOf(b, "POST /wizard/authorize")
	if auth["remote_name"] != "gdrive" || auth["provider_id"] != "gdrive" {
		t.Fatalf("authorize body = %v", auth)
	}
	if _, ok := auth["client_id"]; ok {
		t.Errorf("an empty client ID must keep the remote's app: %v", auth)
	}
	w = drive(t, w, ui.TickMsg{}) // the session is completed: store the token
	rec := bodyOf(b, "POST /wizard/reconnect")
	if rec["name"] != "gdrive" || rec["session_id"] != "sess-9" {
		t.Fatalf("reconnect body = %v (%v)", rec, b.log())
	}
	if len(b.matching("POST /wizard/create")) != 0 {
		t.Error("a reconnect must not create a remote")
	}
	if !strings.Contains(content(w), `Remote "gdrive" reconnected`) {
		t.Errorf("done:\n%s", content(w))
	}
}

func TestRemotes_ImportFromAFile(t *testing.T) {
	b := manageBackend(t)
	conf := filepath.Join(t.TempDir(), "rclone.conf")
	text := "[box]\ntype = sftp\nhost = h\n\n[vault]\ntype = crypt\nremote = box:v\n"
	if err := os.WriteFile(conf, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	b.json("POST", "/remotes/import/preview", 200, map[string]any{
		"errors": []any{},
		"remotes": []any{
			map[string]any{"name": "box", "type": "sftp", "exists": true, "problems": []any{}, "keys": []any{"host"}},
			map[string]any{"name": "vault", "type": "crypt", "exists": false, "problems": []any{}, "keys": []any{"remote"}},
			map[string]any{"name": "evil", "type": "sftp", "exists": false,
				"problems": []any{"'ssh' runs a program on this machine and is not imported"}, "keys": []any{"ssh"}},
		},
	})
	b.json("POST", "/remotes/import", 200, map[string]any{"imported": []any{"box2", "vault"}})
	m := open(t, ui.NewRemotesModel(b.client()))
	m = drive(t, m, press("I"))
	if !strings.Contains(content(m), "Import an rclone.conf") {
		t.Fatalf("import form:\n%s", content(m))
	}
	m = drive(t, m, ctrlU)
	m = drive(t, m, tea.PasteMsg{Content: conf})
	m = drive(t, m, press("enter"))
	if p := bodyOf(b, "POST /remotes/import/preview"); p["content"] != text {
		t.Fatalf("preview body = %v", p)
	}
	v := content(m)
	if !strings.Contains(v, "evil (sftp): 'ssh' runs a program") || !strings.Contains(v, "box-imported") {
		t.Fatalf("selection:\n%s", v)
	}
	// box clashes and starts empty (skipped): give it a name.
	m = drive(t, m, typeKeys("box2")...)
	m = drive(t, m, press("enter"))
	bodies := b.bodiesOf("POST /remotes/import")
	if len(bodies) != 1 {
		t.Fatalf("no import: %v", b.log())
	}
	body := bodies[0]
	sel, _ := body["remotes"].([]any)
	if body["content"] != text || len(sel) != 2 {
		t.Fatalf("import body = %v", body)
	}
	first, _ := sel[0].(map[string]any)
	second, _ := sel[1].(map[string]any)
	if first["source"] != "box" || first["name"] != "box2" || second["source"] != "vault" {
		t.Errorf("selection = %v", sel)
	}
	if _, renamed := second["name"]; renamed {
		t.Errorf("vault keeps its name: %v", second)
	}
	if m.CapturesInput() {
		t.Errorf("form still open:\n%s", content(m))
	}
}

func TestRemotes_ImportRefusesMissingFile(t *testing.T) {
	b := manageBackend(t)
	m := open(t, ui.NewRemotesModel(b.client()))
	m = drive(t, m, press("I"), ctrlU)
	m = drive(t, m, tea.PasteMsg{Content: filepath.Join(t.TempDir(), "missing.conf")})
	m = drive(t, m, press("enter"))
	if !strings.Contains(content(m), "no such file") || len(b.matching("POST /remotes/import")) != 0 {
		t.Errorf("missing file not reported:\n%s", content(m))
	}
}
