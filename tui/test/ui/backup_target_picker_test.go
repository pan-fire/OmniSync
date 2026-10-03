package ui_test

import (
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// backupPickerBackend is the Profile Detail backend (profile docs syncs
// with gdrive:docs) plus folders and remotes to browse.
func backupPickerBackend(t *testing.T) *backend {
	t.Helper()
	b := detailBackend(t)
	b.json("GET", "/profiles/docs/backups", 200, []any{})
	b.json("POST", "/profiles/docs/backups", 201, map[string]any{"id": 5, "profile_id": 1, "name": "x",
		"target_path": "x", "target_type": "local", "remote_name": nil, "retention_days": 7, "frequency_hours": 24,
		"backup_mode": "mirror", "enabled": true, "created_at": "x", "updated_at": "x"})
	b.byQuery("/browse/local", map[string]any{
		"":                  browseJSON("/home/u", nil, "Bilder", "Dokumente"),
		"/home/u/Dokumente": browseJSON("/home/u/Dokumente", "/home/u", "Arbeit"),
	})
	b.json("GET", "/remotes", 200, []any{
		map[string]any{"name": "gdrive", "type": "drive", "last_verified": nil},
		map[string]any{"name": "nas", "type": "sftp", "last_verified": nil},
	})
	b.byQuery("/browse/remote", map[string]any{
		"gdrive:":       browseJSON("gdrive:", nil, "Backup", "Fotos"),
		"gdrive:Backup": browseJSON("gdrive:Backup", "gdrive:", "Docs"),
		"nas:":          browseJSON("nas:", nil, "Backups"),
		"nas:Backups":   browseJSON("nas:Backups", "nas:", "docs"),
	})
	return b
}

// openBackupForm opens the Backups tab and the create form, types the name,
// sets the type (moving right typeSteps times) and focuses the given field
// after it: 1 = Remote name, 2 = Path.
func openBackupForm(t *testing.T, b *backend, typeSteps int, remoteName string) ui.ProfileDetailModel {
	t.Helper()
	m := openDetail(t, b)
	m = detailStep(t, m, press("right"))
	m = detailStep(t, m, press("right"))
	m = detailStep(t, m, press("c"))
	for _, k := range typeKeys("Nightly") {
		m = detailStep(t, m, k)
	}
	m = detailStep(t, m, press("tab"))
	for i := 0; i < typeSteps; i++ {
		m = detailStep(t, m, press("right"))
	}
	m = detailStep(t, m, press("tab"))
	for _, k := range typeKeys(remoteName) {
		m = detailStep(t, m, k)
	}
	return detailStep(t, m, press("tab"))
}

func steps(t *testing.T, m ui.ProfileDetailModel, msgs ...tea.Msg) ui.ProfileDetailModel {
	t.Helper()
	for _, msg := range msgs {
		m = detailStep(t, m, msg)
	}
	return m
}

// A local target's Path browses local folders and submits the chosen one.
func TestBackupPicker_LocalTarget(t *testing.T) {
	b := backupPickerBackend(t)
	m := openBackupForm(t, b, 0, "")
	if !strings.Contains(m.KeyHints(), "Ctrl+O:browse folders") {
		t.Errorf("hints = %q", m.KeyHints())
	}
	m = steps(t, m, ctrlO)
	if v := view(m); !strings.Contains(v, "Choose the local folder") || !strings.Contains(v, "Dokumente/") {
		t.Fatalf("picker:\n%s", v)
	}
	if !strings.Contains(m.KeyHints(), "s:use this folder") {
		t.Errorf("picker hints = %q", m.KeyHints())
	}
	m = steps(t, m, press("down"), press("down"), press("enter"), press("s"))
	if v := view(m); !strings.Contains(v, "Create Backup Target") || !strings.Contains(v, "/home/u/Dokumente") {
		t.Fatalf("form lacks the folder:\n%s", v)
	}
	_ = steps(t, m, press("enter"))
	body := bodyOf(b, "POST /profiles/docs/backups")
	if body == nil || body["target_path"] != "/home/u/Dokumente" || body["target_type"] != "local" || body["name"] != "Nightly" {
		t.Errorf("body = %v", body)
	}
}

// A "same remote" target browses the profile's remote (gdrive, from
// remote_dir gdrive:docs) straight away; paths stay without a leading slash.
func TestBackupPicker_SameRemoteBrowsesProfileRemote(t *testing.T) {
	b := backupPickerBackend(t)
	m := openBackupForm(t, b, 1, "")
	m = steps(t, m, ctrlO)
	if v := view(m); !strings.Contains(v, "Choose the folder on the remote") || !strings.Contains(v, "In: gdrive:") {
		t.Fatalf("picker:\n%s", v)
	}
	if got := b.matching("GET /remotes"); len(got) != 0 {
		t.Errorf("listed the remotes although the profile's is known: %v", got)
	}
	// [use this folder], .. (all remotes), Backup/, Fotos/
	m = steps(t, m, press("down"), press("down"), press("enter"), press("enter"))
	v := view(m)
	if !strings.Contains(v, "gdrive:Backup") || strings.Contains(v, "gdrive:/Backup") || strings.Contains(v, "Choose") {
		t.Fatalf("form:\n%s", v)
	}
	_ = steps(t, m, press("enter"))
	body := bodyOf(b, "POST /profiles/docs/backups")
	if body == nil || body["target_path"] != "gdrive:Backup" || body["target_type"] != "remote" {
		t.Errorf("body = %v", body)
	}
	if got := b.matching("GET /browse/remote"); len(got) != 2 || got[0] != "GET /browse/remote?path=gdrive%3A" {
		t.Errorf("requests = %v", got)
	}
}

// A custom_remote target without a Remote name lists the remotes first; the
// chosen folder fills Path and its remote fills Remote name.
func TestBackupPicker_CustomRemoteWithoutNamePicksRemoteFirst(t *testing.T) {
	b := backupPickerBackend(t)
	m := openBackupForm(t, b, 2, "")
	m = steps(t, m, ctrlO)
	if v := view(m); !strings.Contains(v, "Choose a remote") || !strings.Contains(v, "nas:") {
		t.Fatalf("remote list:\n%s", v)
	}
	m = steps(t, m, press("down"), press("enter")) // nas
	m = steps(t, m, press("down"), press("down"), press("enter"))
	if v := view(m); !strings.Contains(v, "In: nas:Backups") {
		t.Fatalf("did not open nas:Backups:\n%s", view(m))
	}
	m = steps(t, m, press("s"))
	_ = steps(t, m, press("enter"))
	body := bodyOf(b, "POST /profiles/docs/backups")
	if body == nil || body["target_path"] != "nas:Backups" || body["remote_name"] != "nas" || body["target_type"] != "custom_remote" {
		t.Errorf("body = %v", body)
	}
}

// A custom_remote target with a Remote name browses that remote directly
// and keeps the name.
func TestBackupPicker_CustomRemoteBrowsesNamedRemote(t *testing.T) {
	b := backupPickerBackend(t)
	m := openBackupForm(t, b, 2, "nas")
	m = steps(t, m, ctrlO)
	if v := view(m); !strings.Contains(v, "In: nas:") || !strings.Contains(v, "Backups/") {
		t.Fatalf("picker:\n%s", v)
	}
	if got := b.matching("GET /remotes"); len(got) != 0 {
		t.Errorf("listed the remotes: %v", got)
	}
	m = steps(t, m, press("down"), press("down"), press("enter"), press("s"))
	_ = steps(t, m, press("enter"))
	body := bodyOf(b, "POST /profiles/docs/backups")
	if body == nil || body["target_path"] != "nas:Backups" || body["remote_name"] != "nas" {
		t.Errorf("body = %v", body)
	}
}

// Esc closes the picker and keeps the form as it was; Ctrl+O elsewhere
// than on Path opens nothing.
func TestBackupPicker_EscAndOtherFields(t *testing.T) {
	b := backupPickerBackend(t)
	m := openBackupForm(t, b, 0, "")
	m = steps(t, m, ctrlO, press("esc"))
	if v := view(m); !strings.Contains(v, "Create Backup Target") || strings.Contains(v, "Choose") || !m.CapturesInput() {
		t.Fatalf("Esc did not return to the form:\n%s", v)
	}
	b.reset()
	m = steps(t, m, press("shift+tab"), ctrlO) // Remote name
	if v := view(m); strings.Contains(v, "Choose") || len(b.matching("GET /browse")) != 0 {
		t.Errorf("Ctrl+O on Remote name opened the picker:\n%s", v)
	}
	if len(mutations(b)) != 0 {
		t.Errorf("mutations = %v", mutations(b))
	}
}
