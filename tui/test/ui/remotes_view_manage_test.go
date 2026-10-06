package ui_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// rvImportFile writes text to an rclone.conf in a temporary folder.
func rvImportFile(t *testing.T, text string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "rclone.conf")
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

// rvImportPath opens the import form, enters path and submits it.
func rvImportPath(t *testing.T, m ui.RemotesModel, path string) ui.RemotesModel {
	t.Helper()
	m = drive(t, m, press("I"), ctrlU)
	m = drive(t, m, tea.PasteMsg{Content: path})
	return drive(t, m, press("enter"))
}

// e on a remote that cannot be edited explains why instead of opening an
// empty form; OAuth remotes are pointed at a.
func TestRemotesView_EditRefusedForNonEditable(t *testing.T) {
	b := manageBackend(t)
	b.json("GET", "/remotes", 200, []any{
		map[string]any{"name": "gdrive", "type": "drive", "last_verified": nil, "provider_id": "gdrive", "editable": false, "reconnectable": true},
		map[string]any{"name": "odd", "type": "union", "last_verified": nil, "provider_id": nil, "editable": false, "reconnectable": false},
	})
	m := open(t, ui.NewRemotesModel(b.client()))
	m, out := rvDrive(t, m, press("e"))
	if !out.has("gdrive signs in with a browser: press a to reconnect it") {
		t.Errorf("flashes = %v", out.flashes)
	}
	m, out = rvDrive(t, m, press("down"), press("e"))
	if !out.has("Remotes of type union cannot be edited here") {
		t.Errorf("flashes = %v", out.flashes)
	}
	if m.CapturesInput() || len(b.matching("GET /remotes/odd/config")) != 0 {
		t.Errorf("edit started: %v", b.log())
	}
}

// a on a key-based remote has nothing to reconnect: it points at e and
// does not open the wizard.
func TestRemotesView_ReconnectRefusedForKeyRemote(t *testing.T) {
	b := manageBackend(t)
	m := open(t, ui.NewRemotesModel(b.client()))
	_, out := rvDrive(t, m, press("a")) // box
	if !out.has("box does not sign in with a browser; press e to edit its credentials") || len(out.navs) != 0 {
		t.Errorf("flashes = %v navs = %v", out.flashes, out.navs)
	}
}

// If the stored settings cannot be read, the user is told and no form with
// made-up values opens.
func TestRemotesView_EditLoadError(t *testing.T) {
	b := manageBackend(t)
	b.json("GET", "/remotes/box/config", 404, map[string]any{"detail": "Remote 'box' not found", "code": "remote_not_found"})
	m := open(t, ui.NewRemotesModel(b.client()))
	m, out := rvDrive(t, m, press("e"))
	if !out.has("Could not load the settings of box: API error 404: Remote 'box' not found") || m.CapturesInput() {
		t.Errorf("flashes = %v\n%s", out.flashes, content(m))
	}
	if len(b.matching("PUT")) != 0 {
		t.Errorf("sent %v", b.log())
	}
}

// A remote whose provider the backend no longer offers cannot be edited:
// the form would not know its fields.
func TestRemotesView_EditUnknownProvider(t *testing.T) {
	b := manageBackend(t)
	b.json("GET", "/remotes/box/config", 200, map[string]any{
		"name": "box", "type": "weird", "provider_id": "weird", "other_keys": []any{}, "fields": []any{},
	})
	m := open(t, ui.NewRemotesModel(b.client()))
	m, out := rvDrive(t, m, press("e"))
	if !out.has(`Could not load the settings of box: unknown provider "weird"`) || m.CapturesInput() {
		t.Errorf("flashes = %v\n%s", out.flashes, content(m))
	}
}

// Esc while the settings load cancels; the late answer opens no form.
func TestRemotesView_EditEscWhileLoading(t *testing.T) {
	b := manageBackend(t)
	m := open(t, ui.NewRemotesModel(b.client()))
	m, answer := rvStep(m, press("e"))
	if v := content(m); !strings.Contains(v, `Loading the settings of "box"`) || m.KeyHints() != "Esc:cancel" {
		t.Fatalf("loading (%q):\n%s", m.KeyHints(), v)
	}
	m = drive(t, m, press("x"))
	m = drive(t, m, press("esc"))
	m = drive(t, m, answer...)
	if m.CapturesInput() || strings.Contains(content(m), "Edit SFTP") {
		t.Errorf("late answer opened the form:\n%s", content(m))
	}
}

// Editing a crypt remote warns that changed passwords make stored files
// unreadable.
func TestRemotesView_EditCryptWarns(t *testing.T) {
	b := manageBackend(t)
	b.json("GET", "/wizard/providers", 200, []any{
		map[string]any{"id": "crypt", "display_name": "Encrypted", "icon": "c", "auth_type": "key", "default_name": "crypt", "setup_guide": "",
			"fields": []any{
				map[string]any{"name": "remote", "label": "Remote", "field_type": "remote_path", "required": true, "help_text": ""},
				map[string]any{"name": "password", "label": "Password", "field_type": "password", "required": true, "help_text": ""},
			}},
	})
	b.json("GET", "/remotes", 200, []any{
		map[string]any{"name": "vault", "type": "crypt", "last_verified": nil, "provider_id": "crypt", "editable": true},
	})
	b.json("GET", "/remotes/vault/config", 200, map[string]any{
		"name": "vault", "type": "crypt", "provider_id": "crypt", "other_keys": []any{},
		"fields": []any{
			map[string]any{"name": "remote", "value": "box:v", "is_set": true, "secret": false},
			map[string]any{"name": "password", "value": "", "is_set": true, "secret": true},
		},
	})
	m := open(t, ui.NewRemotesModel(b.client()))
	m = drive(t, m, press("e"))
	v := content(m)
	for _, want := range []string{`Edit Encrypted remote "vault"`, "can only be read with the passwords", "Format: <remote>:<folder>"} {
		if !strings.Contains(v, want) {
			t.Errorf("form lacks %q:\n%s", want, v)
		}
	}
	// A required secret cannot be removed, so there is no dropdown for it.
	if strings.Contains(v, "Remove secret") {
		t.Errorf("required secret offered for removal:\n%s", v)
	}
}

// A refused save keeps the form open with the backend's message and what
// the user typed; nothing else may be sent meanwhile, and Esc closes it.
func TestRemotesView_EditSaveRefusalKeepsForm(t *testing.T) {
	b := manageBackend(t)
	b.json("PUT", "/remotes/box", 422, map[string]any{
		"detail": "host: not a valid host name", "code": "validation_error", "details": map[string]any{"field": "host"},
	})
	m := open(t, ui.NewRemotesModel(b.client()))
	m = drive(t, m, press("e"), ctrlU)
	m = drive(t, m, typeKeys("bad host")...)
	m, submit := rvStep(m, press("enter"))
	m, saved := rvStep(m, submit[0])
	if v := content(m); !strings.Contains(v, `Saving "box"`) || m.KeyHints() != "Working..." {
		t.Errorf("saving (%q):\n%s", m.KeyHints(), v)
	}
	m = drive(t, m, press("d"))
	m, out := rvDrive(t, m, saved...)
	if !out.has("Saving box failed: API error 422: host: not a valid host name") {
		t.Errorf("flashes = %v", out.flashes)
	}
	v := content(m)
	if !m.CapturesInput() || !strings.Contains(v, "host: not a valid host name") || !strings.Contains(v, "bad host") {
		t.Fatalf("form not reopened with the detail:\n%s", v)
	}
	m = drive(t, m, press("esc"))
	if m.CapturesInput() || len(b.matching("PUT /remotes/box")) != 1 || len(b.matching("GET /remotes/box/dependencies")) != 0 {
		t.Errorf("after Esc: %v\n%s", b.log(), content(m))
	}
}

// A saved edit closes the form, names the next step and refreshes the list.
func TestRemotesView_EditSavedFlashes(t *testing.T) {
	b := manageBackend(t)
	m := open(t, ui.NewRemotesModel(b.client()))
	m = drive(t, m, press("e"))
	b.reset()
	m, out := rvDrive(t, m, press("enter"))
	if !out.has("Remote box updated; press t to test it") || m.CapturesInput() {
		t.Errorf("flashes = %v\n%s", out.flashes, content(m))
	}
	if len(b.matching("GET /remotes")) != 1 {
		t.Errorf("list not refreshed: %v", b.log())
	}
}

// The import form starts at rclone's own config file on this machine
// (os.UserConfigDir: a different place on Linux, macOS and Windows), so
// Enter alone imports it. Checked by what is sent, not by the field's text,
// which a long path scrolls.
func TestRemotesView_ImportDefaultsToRcloneConf(t *testing.T) {
	base := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", base) // Linux
	t.Setenv("HOME", base)            // macOS: $HOME/Library/Application Support
	t.Setenv("AppData", base)         // Windows
	dir, err := os.UserConfigDir()
	if err != nil {
		t.Fatal(err)
	}
	conf := filepath.Join(dir, "rclone", "rclone.conf")
	if err := os.MkdirAll(filepath.Dir(conf), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(conf, []byte("[nas]\ntype = sftp\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	b := manageBackend(t)
	b.json("POST", "/remotes/import/preview", 200, map[string]any{"errors": []any{}, "remotes": []any{}})
	m := open(t, ui.NewRemotesModel(b.client()))
	m = drive(t, m, press("I"))
	if m.KeyHints() != "Enter:submit  Esc:cancel  Tab/Shift+Tab:field  Left/Right:choice" {
		t.Errorf("hints = %q", m.KeyHints())
	}
	m = drive(t, m, press("esc"))
	if m.CapturesInput() || len(b.matching("POST /remotes/import")) != 0 {
		t.Errorf("Esc kept the form or sent something:\n%s", content(m))
	}
	_ = drive(t, m, press("I"), press("enter"))
	if p := bodyOf(b, "POST /remotes/import/preview"); p["content"] != "[nas]\ntype = sftp\n" {
		t.Errorf("preview body = %v (%v)", p, b.log())
	}
}

// A folder or an oversized file is refused before anything is sent.
func TestRemotesView_ImportRefusesFolderAndLargeFile(t *testing.T) {
	b := manageBackend(t)
	m := open(t, ui.NewRemotesModel(b.client()))
	dir := t.TempDir()
	m = rvImportPath(t, m, dir)
	if v := content(m); !strings.Contains(v, "is a folder") || !m.CapturesInput() {
		t.Errorf("folder:\n%s", v)
	}
	m = drive(t, m, press("esc"))
	big := filepath.Join(dir, "big.conf")
	if err := os.WriteFile(big, make([]byte, 512*1024+1), 0o600); err != nil {
		t.Fatal(err)
	}
	m = rvImportPath(t, m, big)
	if v := content(m); !strings.Contains(v, "too large for an rclone.conf (at most 512 KB)") {
		t.Errorf("large file:\n%s", v)
	}
	if got := b.matching("POST /remotes/import"); len(got) != 0 {
		t.Errorf("sent %v", got)
	}
}

// ~/ in the path means the user's home folder.
func TestRemotesView_ImportExpandsHome(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	if err := os.WriteFile(filepath.Join(home, "my.conf"), []byte("[nas]\ntype = sftp\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	b := manageBackend(t)
	b.json("POST", "/remotes/import/preview", 200, map[string]any{"errors": []any{}, "remotes": []any{}})
	m := open(t, ui.NewRemotesModel(b.client()))
	_ = rvImportPath(t, m, "~/my.conf")
	if p := bodyOf(b, "POST /remotes/import/preview"); p["content"] != "[nas]\ntype = sftp\n" {
		t.Errorf("preview body = %v (%v)", p, b.log())
	}
}

// The preview's refusals come back in the path form: the API error
// envelope, the file's parse errors, an empty file, and a file whose remotes
// are all refused.
func TestRemotesView_ImportPreviewProblems(t *testing.T) {
	cases := []struct {
		name   string
		status int
		reply  map[string]any
		want   string
	}{
		{"api error", 422, map[string]any{"detail": "Not an rclone.conf", "code": "import_invalid", "details": map[string]any{"line": 3}}, "Not an rclone.conf"},
		{"parse errors", 200, map[string]any{"errors": []any{"line 2: no section", "line 5: bad key"}, "remotes": []any{}}, "line 2: no section line 5: bad key"},
		{"empty", 200, map[string]any{"errors": []any{}, "remotes": []any{}}, "The file holds no remotes."},
		{"all refused", 200, map[string]any{"errors": []any{}, "remotes": []any{
			map[string]any{"name": "evil", "type": "sftp", "exists": false, "problems": []any{"'ssh' runs a program"}, "keys": []any{"ssh"}},
		}}, "None of its remotes can be imported:"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			b := manageBackend(t)
			b.json("POST", "/remotes/import/preview", tc.status, tc.reply)
			m := open(t, ui.NewRemotesModel(b.client()))
			m = rvImportPath(t, m, rvImportFile(t, "[x]\ntype = sftp\n"))
			v := content(m)
			if !strings.Contains(v, tc.want) || !strings.Contains(v, "Import an rclone.conf") || !m.CapturesInput() {
				t.Errorf("want %q in the path form:\n%s", tc.want, v)
			}
			if got := b.matching("POST /remotes/import"); len(got) != 1 {
				t.Errorf("requests = %v", got)
			}
		})
	}
}

// A clashing name suggests the next free one, skipping names that are
// already taken; leaving every name empty imports nothing.
func TestRemotesView_ImportClashSuggestsFreeNameAndNeedsOne(t *testing.T) {
	b := manageBackend(t)
	b.json("GET", "/remotes", 200, []any{
		map[string]any{"name": "box", "type": "sftp", "last_verified": nil},
		map[string]any{"name": "box-imported", "type": "sftp", "last_verified": nil},
	})
	b.json("POST", "/remotes/import/preview", 200, map[string]any{"errors": []any{}, "remotes": []any{
		map[string]any{"name": "box", "type": "sftp", "exists": true, "problems": []any{}, "keys": []any{"host"}},
	}})
	m := open(t, ui.NewRemotesModel(b.client()))
	m = rvImportPath(t, m, rvImportFile(t, "[box]\ntype = sftp\n"))
	if v := content(m); !strings.Contains(v, "e.g. box-imported-2") {
		t.Fatalf("suggestion:\n%s", v)
	}
	m = drive(t, m, press("enter"))
	if v := content(m); !strings.Contains(v, "Give at least one remote a name to import it") || !m.CapturesInput() {
		t.Errorf("empty selection:\n%s", v)
	}
	if got := b.bodiesOf("POST /remotes/import"); len(got) != 0 {
		t.Errorf("imported nothing anyway: %v", got)
	}
}

// A refused import keeps the selection open with the backend's message; a
// retry that imports one remote says so in the singular.
func TestRemotesView_ImportRefusalThenSuccess(t *testing.T) {
	b := manageBackend(t)
	b.json("POST", "/remotes/import/preview", 200, map[string]any{"errors": []any{}, "remotes": []any{
		map[string]any{"name": "nas", "type": "sftp", "exists": false, "problems": []any{}, "keys": []any{"host"}},
	}})
	b.json("POST", "/remotes/import", 409, map[string]any{"detail": "Remote 'nas' was created meanwhile", "code": "remote_exists"})
	m := open(t, ui.NewRemotesModel(b.client()))
	m = rvImportPath(t, m, rvImportFile(t, "[nas]\ntype = sftp\n"))
	m, submit := rvStep(m, press("enter"))
	m, done := rvStep(m, submit[0])
	if v := content(m); !strings.Contains(v, "Importing...") || m.KeyHints() != "Working..." {
		t.Errorf("importing (%q):\n%s", m.KeyHints(), v)
	}
	m, out := rvDrive(t, m, done...)
	if !out.has("Import failed: API error 409: Remote 'nas' was created meanwhile") {
		t.Errorf("flashes = %v", out.flashes)
	}
	if v := content(m); !strings.Contains(v, "was created meanwhile") || !strings.Contains(v, "nas (sftp)") {
		t.Fatalf("selection not reopened:\n%s", v)
	}
	b.json("POST", "/remotes/import", 200, map[string]any{"imported": []any{"nas"}})
	m, out = rvDrive(t, m, press("enter"))
	if !out.has("Imported 1 remote") || out.has("Imported 1 remotes") || m.CapturesInput() {
		t.Errorf("flashes = %v\n%s", out.flashes, content(m))
	}
}

// The path check waits for the backend: keys do nothing while the file is
// read.
func TestRemotesView_ImportCheckingIgnoresKeys(t *testing.T) {
	b := manageBackend(t)
	b.json("POST", "/remotes/import/preview", 200, map[string]any{"errors": []any{}, "remotes": []any{}})
	m := open(t, ui.NewRemotesModel(b.client()))
	m = drive(t, m, press("I"), ctrlU)
	m = drive(t, m, tea.PasteMsg{Content: rvImportFile(t, "[a]\ntype = sftp\n")})
	m, submit := rvStep(m, press("enter"))
	m, _ = rvStep(m, submit[0])
	if v := content(m); !strings.Contains(v, "Reading the rclone.conf...") {
		t.Errorf("checking:\n%s", v)
	}
	m = drive(t, m, press("esc"), press("d"))
	if !m.CapturesInput() || !strings.Contains(content(m), "Reading the rclone.conf") {
		t.Errorf("a key left the check:\n%s", content(m))
	}
}
