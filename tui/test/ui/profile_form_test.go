package ui_test

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

var ctrlO = tea.KeyPressMsg(tea.Key{Code: 'o', Mod: tea.ModCtrl})

// byQuery registers a GET handler that answers by the "path" query value.
func (b *backend) byQuery(path string, answers map[string]any) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.replies["GET "+path] = func(w http.ResponseWriter, r *http.Request) {
		ans, ok := answers[r.URL.Query().Get("path")]
		if !ok {
			w.WriteHeader(http.StatusNotFound)
			_ = json.NewEncoder(w).Encode(map[string]any{"detail": "Directory not found"})
			return
		}
		_ = json.NewEncoder(w).Encode(ans)
	}
}

func browseJSON(current string, parent any, names ...string) map[string]any {
	entries := []any{}
	for _, n := range names {
		// Like the real backend, remote entry paths may carry a leading
		// slash; the picker builds remote paths itself.
		p := strings.TrimSuffix(current, "/") + "/" + n
		if strings.HasSuffix(current, ":") {
			p = current + "/" + n
		}
		entries = append(entries, map[string]any{"name": n, "path": p})
	}
	return map[string]any{"current": current, "parent": parent, "entries": entries}
}

func pickerBackend(t *testing.T) *backend {
	b := profilesBackend(t)
	b.byQuery("/browse/local", map[string]any{
		"":                  browseJSON("/home/u", nil, "Bilder", "Dokumente"),
		"/home/u/Dokumente": browseJSON("/home/u/Dokumente", "/home/u", "Arbeit"),
		"/home/u":           browseJSON("/home/u", nil, "Bilder", "Dokumente"),
	})
	b.json("GET", "/remotes", 200, []any{
		map[string]any{"name": "gdrive", "type": "drive", "last_verified": nil},
		map[string]any{"name": "nas", "type": "sftp", "last_verified": nil},
	})
	b.byQuery("/browse/remote", map[string]any{
		"gdrive:":       browseJSON("gdrive:", nil, "Backup", "Fotos"),
		"gdrive:Backup": browseJSON("gdrive:Backup", "gdrive:", "Docs"),
	})
	return b
}

// R5.7: Ctrl+O on Local Dir browses GET /browse/local and fills the field.
func TestProfileForm_LocalDirPicker(t *testing.T) {
	b := pickerBackend(t)
	m := open(t, ui.NewProfilesModel(b.client()))
	m = drive(t, m, press("c"))
	m = drive(t, m, typeKeys("Docs")...)
	m = drive(t, m, press("tab"), ctrlO)
	v := content(m)
	if !strings.Contains(v, "Choose the local folder") || !strings.Contains(v, "Dokumente/") || !strings.Contains(v, "In: /home/u") {
		t.Fatalf("picker:\n%s", v)
	}
	// Rows: [use this folder], Bilder/, Dokumente/.
	m = drive(t, m, press("down"), press("down"), press("enter"))
	if v = content(m); !strings.Contains(v, "In: /home/u/Dokumente") || !strings.Contains(v, "Arbeit/") {
		t.Fatalf("did not open Dokumente:\n%s", v)
	}
	m = drive(t, m, press("s"))
	if !m.CapturesInput() || !strings.Contains(content(m), "/home/u/Dokumente") || strings.Contains(content(m), "Choose the local folder") {
		t.Fatalf("form not back with the folder:\n%s", content(m))
	}
	// The name typed before is still there, and the form submits the folder.
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("gdrive:Docs")...)
	_ = drive(t, m, press("enter"))
	body := bodyOf(b, "POST /profiles")
	if body == nil || body["name"] != "Docs" || body["local_dir"] != "/home/u/Dokumente" {
		t.Errorf("body = %v", body)
	}
	if got := b.matching("GET /browse/local"); len(got) != 2 || got[0] != "GET /browse/local" || got[1] != "GET /browse/local?path=%2Fhome%2Fu%2FDokumente" {
		t.Errorf("requests = %v", got)
	}
}

// R5.8 / R5.9: Ctrl+O on Remote Dir picks a remote from GET /remotes, then a
// folder on it from GET /browse/remote; paths stay relative to the remote.
func TestProfileForm_RemotePickerAndRemoteDirs(t *testing.T) {
	b := pickerBackend(t)
	m := open(t, ui.NewProfilesModel(b.client()))
	m = drive(t, m, press("c"), press("tab"), press("tab"), ctrlO)
	v := content(m)
	if !strings.Contains(v, "Choose a remote") || !strings.Contains(v, "gdrive:") || !strings.Contains(v, "nas:") {
		t.Fatalf("remote list:\n%s", v)
	}
	m = drive(t, m, press("enter")) // gdrive
	if v = content(m); !strings.Contains(v, "Choose the folder on the remote") || !strings.Contains(v, "Backup/") {
		t.Fatalf("gdrive root:\n%s", v)
	}
	// Left at the remote's root goes back to the remote list.
	m = drive(t, m, press("left"))
	if !strings.Contains(content(m), "Choose a remote") {
		t.Fatalf("left did not return to the remotes:\n%s", content(m))
	}
	// [use this folder], .. (all remotes), Backup/, Fotos/
	m = drive(t, m, press("enter"))
	m = drive(t, m, press("down"), press("down"), press("enter"))
	if v = content(m); !strings.Contains(v, "In: gdrive:Backup") {
		t.Fatalf("did not open Backup:\n%s", v)
	}
	m = drive(t, m, press("enter")) // [use this folder]
	if v = content(m); !strings.Contains(v, "gdrive:Backup") || strings.Contains(v, "Choose") {
		t.Fatalf("form lacks the remote folder:\n%s", v)
	}
	if got := b.matching("GET /browse/remote"); len(got) != 3 || got[2] != "GET /browse/remote?path=gdrive%3ABackup" {
		t.Errorf("requests = %v", got)
	}
}

// A remote_dir value opens straight in that folder; an error keeps a way
// back to the remotes; Esc returns to the form unchanged.
func TestProfileForm_RemotePickerStartsAtValue(t *testing.T) {
	b := pickerBackend(t)
	m := open(t, ui.NewProfilesModel(b.client()))
	m = drive(t, m, press("c"), press("tab"), press("tab"))
	m = drive(t, m, typeKeys("gdrive:Missing")...)
	m = drive(t, m, ctrlO)
	v := content(m)
	if !strings.Contains(v, "Directory not found") || !strings.Contains(v, ".. (all remotes)") {
		t.Fatalf("error view:\n%s", v)
	}
	m = drive(t, m, press("esc"))
	if v = content(m); !strings.Contains(v, "gdrive:Missing") || !strings.Contains(v, "Create Profile") || !m.CapturesInput() {
		t.Fatalf("Esc did not return to the form:\n%s", v)
	}
}

// R5.11: a refused create shows the backend's message in the form and keeps
// every value.
func TestProfileForm_ErrorKeepsInput(t *testing.T) {
	b := profilesBackend(t)
	b.json("POST", "/profiles", 422, map[string]any{"detail": []any{
		map[string]any{"loc": []any{"body", "local_dir"}, "msg": "Value error, local_dir must be an absolute path"},
	}})
	m := open(t, ui.NewProfilesModel(b.client()))
	msgs := []tea.Msg{press("c")}
	msgs = append(msgs, typeKeys("Docs")...)
	msgs = append(msgs, press("tab"))
	msgs = append(msgs, typeKeys("relative/dir")...)
	msgs = append(msgs, press("tab"))
	msgs = append(msgs, typeKeys("gdrive:Docs")...)
	msgs = append(msgs, press("enter"))
	m = drive(t, m, msgs...)
	v := content(m)
	for _, want := range []string{"Create Profile", "local_dir must be an absolute path", "Docs", "relative/dir", "gdrive:Docs"} {
		if !strings.Contains(v, want) {
			t.Errorf("form lacks %q:\n%s", want, v)
		}
	}
	if !m.CapturesInput() {
		t.Error("form closed after the error")
	}
	// Fixing the field and submitting again works.
	b.json("POST", "/profiles", 201, profileJSON("docs", "Docs", "idle"))
	m = drive(t, m, press("up"))
	for range "relative/dir" {
		m = drive(t, m, press("backspace"))
	}
	m = drive(t, m, typeKeys("/abs/dir")...)
	m = drive(t, m, press("enter"))
	if m.CapturesInput() {
		t.Errorf("form still open after a successful create:\n%s", content(m))
	}
	bodies := b.bodiesOf("POST /profiles")
	if len(bodies) != 2 || bodies[1]["local_dir"] != "/abs/dir" {
		t.Errorf("bodies = %v", bodies)
	}
}

// R5.2: the form has the interval, debounce, retry and rclone-args fields;
// numbers are checked before anything is sent.
func TestProfileForm_NumericFields(t *testing.T) {
	b := profilesBackend(t)
	m := open(t, ui.NewProfilesModel(b.client()))
	msgs := []tea.Msg{press("c")}
	msgs = append(msgs, typeKeys("Docs")...)
	msgs = append(msgs, press("tab"))
	msgs = append(msgs, typeKeys("/home/u/Docs")...)
	msgs = append(msgs, press("tab"))
	msgs = append(msgs, typeKeys("gdrive:Docs")...)
	msgs = append(msgs, press("tab"), press("tab"), press("tab")) // filters, mode, pull interval
	msgs = append(msgs, typeKeys("abc")...)
	msgs = append(msgs, press("enter"))
	m = drive(t, m, msgs...)
	if !strings.Contains(content(m), "Pull every (min) must be a whole number") || len(b.matching("POST /profiles")) != 0 {
		t.Fatalf("bad number accepted:\n%s", content(m))
	}
	m = drive(t, m, press("backspace"), press("backspace"), press("backspace"))
	m = drive(t, m, typeKeys("15")...)
	m = drive(t, m, press("tab"), press("tab"))
	m = drive(t, m, typeKeys("4")...)
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("--transfers 8")...)
	_ = drive(t, m, press("enter"))
	body := bodyOf(b, "POST /profiles")
	if body == nil || body["pull_interval_minutes"] != float64(15) || body["max_retries"] != float64(4) {
		t.Fatalf("body = %v", body)
	}
	if _, ok := body["debounce_seconds"]; ok {
		t.Errorf("empty debounce sent: %v", body)
	}
	if args, _ := body["rclone_args"].([]any); len(args) != 2 || args[0] != "--transfers" || args[1] != "8" {
		t.Errorf("args = %v", body["rclone_args"])
	}
}

// The edit form shows the profile's numbers and does not resend unchanged
// rclone args.
func TestProfileForm_EditKeepsArgsUnlessChanged(t *testing.T) {
	b := profilesBackend(t)
	docs := profileJSON("docs", "Dokumente", "idle")
	docs["rclone_args"] = []string{"--transfers", "4"}
	docs["pull_interval_minutes"] = 15
	b.json("GET", "/profiles", 200, []any{docs})
	m := open(t, ui.NewProfilesModel(b.client()))
	m = drive(t, m, press("e"))
	for i := 0; i < 5; i++ {
		m = drive(t, m, press("tab"))
	}
	if v := content(m); !strings.Contains(v, "15") || !strings.Contains(v, "--transfers 4") {
		t.Fatalf("edit form lacks the values:\n%s", v)
	}
	_ = drive(t, m, press("enter"))
	body := bodyOf(b, "PUT /profiles/docs")
	if body == nil || body["pull_interval_minutes"] != float64(15) {
		t.Fatalf("body = %v", body)
	}
	if _, ok := body["rclone_args"]; ok {
		t.Errorf("unchanged rclone_args sent: %v", body)
	}
}
